"""Lawyer Finder Agent — connect users with lawyers (agentic).

Searches for lawyers based on domain, jurisdiction, and specialisation.
The live directory comes from Neon Postgres; when unreachable, dummy
data is injected instead.

Agentic pattern: list_lawyers and query_lawyer_database tools are bound
to the LLM — it decides whether to browse the full directory or run a
filtered SQL query for the user's needs.

Sub-agent: Lawyer Connect (WebSocket) is handled separately in
websocket/lawyer_connect.py for real-time chat between user and lawyer.
"""

import json
import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.tool_loop_runner import run_agent_with_tools
from agents.base import (
    AgentState,
    invoke_text,
    register_agent,
    to_lc_messages,
)
from agents.agent_tools import LAWYER_TOOLS

logger = logging.getLogger("legal_assist.agents.lawyer_finder")

_llm_cache: dict[str, Any] = {}

LAWYER_FINDER_SYSTEM = """You are the Lawyer Finder agent of a legal AI system.
You help users find suitable lawyers based on their legal needs.

Tools (use them to get real data before answering):
- list_lawyers: browse the full lawyer directory.
- query_lawyer_database: run a read-only SELECT to filter/rank lawyers
  (by city, specialisation, experience, fees, rating…).
Call the appropriate tool first, then present the fetched lawyers.

Guidelines:
- Understand the user's legal domain and jurisdiction.
- Suggest the type of lawyer they need (criminal, civil, family, etc.).
- Present the fetched lawyer listings in a clean Markdown table.
- To start a live conversation, tell the user to click the
  "💬 Live Chat with Lawyer" button shown below your reply — it opens a
  real-time WebSocket chat room with the selected lawyer.
- Keep scheduling/booking answers simple: the live chat button is the
  entry point; the lawyer can arrange a time inside the chat.
- If no specific lawyers are available, guide the user on what kind
  of lawyer to look for and suggest bar association directories."""


def _get_directory_lawyers() -> list[dict[str, Any]]:
    """Fetch the live lawyer directory from Neon Postgres."""
    try:
        from connectors.neon_postgres import list_lawyers  # noqa: PLC0415
        rows = list_lawyers()
        return [
            {
                "id": row.get("id"),
                "name": row.get("name"),
                "specialisation": row.get("specialisation"),
                "jurisdiction": row.get("city") or row.get("state") or "",
                "experience": f"{row.get('experience_years') or 0} years",
                "rating": f"{row.get('rating') or 0}/5",
                "bar_id": row.get("bar_council_id") or "",
                "fees_per_hearing": row.get("fees_per_hearing"),
                "available_for_chat": bool(row.get("available_for_chat")),
            }
            for row in rows
        ]
    except Exception:
        return []


def _get_dummy_lawyers(domain: str, jurisdiction: str) -> list[dict[str, str]]:
    """Return dummy lawyer listings."""
    return [
        {
            "name": "[Demo] Adv. Rajesh Kumar",
            "specialisation": domain or "General Practice",
            "jurisdiction": jurisdiction or "Delhi",
            "experience": "12 years",
            "rating": "4.5/5",
            "bar_id": "D/1234/2012",
            "available_for_chat": True,
            "note": "Dummy profile — real directory coming soon",
        },
        {
            "name": "[Demo] Adv. Priya Sharma",
            "specialisation": domain or "Civil Law",
            "jurisdiction": jurisdiction or "Mumbai",
            "experience": "8 years",
            "rating": "4.7/5",
            "bar_id": "M/5678/2016",
            "available_for_chat": True,
            "note": "Dummy profile — real directory coming soon",
        },
        {
            "name": "[Demo] Adv. Mohammed Ali",
            "specialisation": domain or "Criminal Law",
            "jurisdiction": jurisdiction or "Bangalore",
            "experience": "15 years",
            "rating": "4.8/5",
            "bar_id": "B/9012/2010",
            "available_for_chat": False,
            "note": "Dummy profile — real directory coming soon",
        },
    ]


def _get_demo_llm(config, model):
    api_key = config.get("configurable", {}).get("api_key", "")
    cache_key = f"lfw:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=0.4)
    return _llm_cache[cache_key]


def lawyer_finder_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Find and present lawyer options to the user (agentic tool loop)."""
    logger.info("lawyer_finder_generate invoked (tools=%s)", [t.name for t in LAWYER_TOOLS])
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

    analysis = state.get("analysis") or {}
    domain = analysis.get("domain", "general")
    jurisdiction = analysis.get("jurisdiction", "unspecified")

    # Prefer the live Neon directory; fall back to demo listings.
    # When the live directory is reachable the agent fetches it itself
    # through its bound tools; the dummy data path keeps injection.
    lawyers = _get_directory_lawyers()
    directory_live = bool(lawyers)

    system = LAWYER_FINDER_SYSTEM
    if not directory_live:
        lawyers = _get_dummy_lawyers(domain, jurisdiction)
        system += (
            "\n\nDirectory database is unreachable — use these demo listings:\n"
            + json.dumps(lawyers, indent=2, ensure_ascii=False)
        )
    system += (
        "\n\nPresent the lawyers to the user in a clean Markdown table. "
        "End your reply by telling the user to click the '💬 Live Chat with "
        "Lawyer' button below this answer to open a real-time chat room "
        "with any lawyer who has 'available_for_chat: true'."
    )

    # Inject user profile
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile:\n" + profile

    # Inject episodic memory
    episodic = (state.get("episodic_notes") or "").strip()
    if episodic:
        system += "\n\nPast conversations:\n" + episodic

    # Inject procedural memory
    procedural = (state.get("procedural_notes") or "").strip()
    if procedural:
        system += "\n\nUser preferences:\n" + procedural

    if analysis:
        system += (
            f"\n\nUser's legal need:\n"
            f"- domain: {domain}\n"
            f"- jurisdiction: {jurisdiction}\n"
            f"- summary: {analysis.get('summary', '')}"
        )

    if directory_live:
        # Agentic path — the model fetches directory data via tools
        result = run_agent_with_tools(
            system,
            state.get("messages") or [],
            LAWYER_TOOLS,
            config,
            temperature=0.4,
        )
        reply = result["reply"]
        used_model = result["model"]
        tools_used = [t["tool"] for t in result["tool_trace"]]
        agentic = result["agentic"]
    else:
        # No database → plain generation over the injected demo data
        llm = _get_demo_llm(config, model)
        reply = invoke_text(llm, to_lc_messages(state.get("messages") or [], system), config)
        used_model = model
        tools_used = []
        agentic = False

    return {
        "reply": reply,
        "active_agent": "lawyer_finder",
        "agent_metadata": {
            "lawyer_finder": {
                "model": used_model,
                "agentic": agentic,
                "tools_used": tools_used,
                "lawyers_found": len(lawyers),
                "domain": domain,
                "jurisdiction": jurisdiction,
                "lawyers": lawyers,
                "directory_live": directory_live,
                "websocket_available": True,
            }
        },
    }


register_agent(
    "lawyer_finder",
    description="Find lawyers by domain/jurisdiction and enable real-time chat",
    handles=["find_lawyer"],
)
