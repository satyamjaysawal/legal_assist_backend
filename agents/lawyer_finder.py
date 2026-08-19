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
- Present the available lawyer listings (even if dummy data).
- Explain how to connect via the real-time chat feature (WebSocket).
- Mention that the lawyer directory is being populated.
- If no specific lawyers are available, guide the user on what kind
  of lawyer to look for and suggest bar association directories."""


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

    # Get dummy lawyer listings
    lawyers = _get_dummy_lawyers(domain, jurisdiction)

    system = LAWYER_FINDER_SYSTEM
    system += (
        f"\n\nAvailable lawyers (demo data):\n"
        + json.dumps(lawyers, indent=2, ensure_ascii=False)
    )
    system += (
        "\n\nPresent these lawyers to the user. Mention that they can "
        "connect via real-time chat (Lawyer Connect feature) with lawyers "
        "who have 'available_for_chat: true'."
    )

    # Inject user profile
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile:\n" + profile

    if analysis:
        system += (
            f"\n\nUser's legal need:\n"
            f"- domain: {domain}\n"
            f"- jurisdiction: {jurisdiction}\n"
            f"- summary: {analysis.get('summary', '')}"
        )

    llm = _get_llm(api_key, model)
    result = llm.invoke(
        to_lc_messages(state.get("messages") or [], system),
        config=config,
    )
    reply = str(result.content or "").strip()

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
                "websocket_available": True,
            }
        },
    }


register_agent(
    "lawyer_finder",
    description="Find lawyers by domain/jurisdiction and enable real-time chat",
    handles=["find_lawyer"],
)
