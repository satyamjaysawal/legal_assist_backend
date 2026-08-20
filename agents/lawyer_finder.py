"""Lawyer Finder Agent — connect users with lawyers (dummy).

Searches for lawyers based on domain, jurisdiction, and specialisation.
Currently returns dummy data.  When a real lawyer directory is available,
replace the search logic.

Sub-agent: Lawyer Connect (WebSocket) is handled separately in
websocket/lawyer_connect.py for real-time chat between user and lawyer.
"""

import json
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.base import (
    AgentState,
    invoke_text,
    latest_user_text,
    register_agent,
    to_lc_messages,
)

_llm_cache: dict[str, Any] = {}

LAWYER_FINDER_SYSTEM = """You are the Lawyer Finder agent of a legal AI system.
You help users find suitable lawyers based on their legal needs.

Guidelines:
- Understand the user's legal domain and jurisdiction.
- Suggest the type of lawyer they need (criminal, civil, family, etc.).
- Present the available lawyer listings from the directory data provided.
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


def _get_llm(api_key: str, model: str):
    cache_key = f"lfw:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=0.4)
    return _llm_cache[cache_key]


def lawyer_finder_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Find and present lawyer options to the user."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

    analysis = state.get("analysis") or {}
    domain = analysis.get("domain", "general")
    jurisdiction = analysis.get("jurisdiction", "unspecified")

    # Prefer the live Neon directory; fall back to demo listings
    lawyers = _get_directory_lawyers()
    directory_live = bool(lawyers)
    if not lawyers:
        lawyers = _get_dummy_lawyers(domain, jurisdiction)

    system = LAWYER_FINDER_SYSTEM
    system += (
        f"\n\nAvailable lawyers ({'live directory' if directory_live else 'demo data'}):\n"
        + json.dumps(lawyers, indent=2, ensure_ascii=False)
    )
    system += (
        "\n\nPresent these lawyers to the user in a clean Markdown table. "
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

    llm = _get_llm(api_key, model)
    reply = invoke_text(llm, to_lc_messages(state.get("messages") or [], system), config)

    return {
        "reply": reply,
        "active_agent": "lawyer_finder",
        "agent_metadata": {
            "lawyer_finder": {
                "model": model,
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
