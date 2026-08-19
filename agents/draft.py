"""Draft Agent — legal document drafting.

Handles requests to draft legal notices, agreements, letters,
applications, and other legal documents.
"""

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

DRAFT_SYSTEM = """You are the Drafting agent of a legal AI system.
You specialise in creating legal documents — notices, agreements, letters,
applications, petitions, and affidavits.

Guidelines:
- Draft in proper legal format with appropriate headings, sections, and clauses.
- Use formal legal language but keep it clear and understandable.
- Include all standard clauses relevant to the document type.
- Use placeholders like [NAME], [DATE], [ADDRESS] for details you don't have.
- Reference any uploaded document templates or context.
- Add a disclaimer: "This draft is AI-generated and should be reviewed by a qualified lawyer before use."
- Structure the document with clear sections.
- If the user's jurisdiction is known, use that legal system's conventions."""


def _get_llm(api_key: str, model: str):
    cache_key = f"draft:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=0.3)
    return _llm_cache[cache_key]


def draft_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Generate a legal draft document."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

    analysis = state.get("analysis") or {}
    system = DRAFT_SYSTEM

    # Inject analysis
    if analysis:
        system += (
            f"\n\nDraft request analysis:\n"
            f"- intent: {analysis.get('intent', 'draft')}\n"
            f"- domain: {analysis.get('domain', 'general')}\n"
            f"- jurisdiction: {analysis.get('jurisdiction', 'unspecified')}\n"
            f"- summary: {analysis.get('summary', '')}"
        )

    # Inject user profile
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile (use for placeholders):\n" + profile

    # Inject episodic memory
    episodic = (state.get("episodic_notes") or "").strip()
    if episodic:
        system += "\n\nPast conversations:\n" + episodic

    # Inject procedural memory
    procedural = (state.get("procedural_notes") or "").strip()
    if procedural:
        system += "\n\nUser preferences (follow these for formatting/tone):\n" + procedural

    # Inject memory
    notes = (state.get("memory_notes") or "").strip()
    if notes:
        system += "\n\nUser context:\n" + notes

    # Inject RAG (templates, prior documents)
    rag = (state.get("rag_notes") or "").strip()
    if rag:
        system += (
            "\n\n=== REFERENCE DOCUMENTS (use as style/format guide) ===\n" + rag
        )

    # Try to use template connector
    try:
        from connectors.legal_templates import LegalTemplatesConnector
        templates = LegalTemplatesConnector().list_templates()
        if templates.get("templates"):
            system += "\n\nAvailable templates: " + str(
                [t["name"] for t in templates["templates"]]
            )
    except Exception:
        pass

    llm = _get_llm(api_key, model)
    reply = invoke_text(llm, to_lc_messages(state.get("messages") or [], system), config)

    return {
        "reply": reply,
        "active_agent": "draft",
        "agent_metadata": {
            "draft": {
                "model": model,
                "document_type": analysis.get("domain", "general"),
                "disclaimer": "AI-generated draft — review by a qualified lawyer recommended.",
            }
        },
    }


register_agent(
    "draft",
    description="Legal document drafting — notices, agreements, letters, petitions",
    handles=["draft"],
)
