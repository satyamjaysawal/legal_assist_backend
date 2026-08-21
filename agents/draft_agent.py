"""Draft Agent — legal document drafting (agentic).

Handles requests to draft legal notices, agreements, letters,
applications, and other legal documents.

Agentic pattern: template tools (list_legal_templates, get_legal_template)
are bound to the LLM — it fetches the matching template on demand and
drafts on top of it.
"""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.tool_loop_runner import run_agent_with_tools
from agents.base import (
    AgentState,
    register_agent,
)
from agents.agent_tools import TEMPLATE_TOOLS

logger = logging.getLogger("legal_assist.agents.draft")

DRAFT_SYSTEM = """You are the Drafting agent of a legal AI system.
You specialise in creating legal documents — notices, agreements, letters,
applications, petitions, and affidavits.

Tools: you have template tools bound to you:
- list_legal_templates: see available document templates and their ids.
- get_legal_template: fetch a template's structure/required fields by id.
When the requested document matches a template, fetch it FIRST and use
its structure as the base for your draft.

Guidelines:
- Draft in proper legal format with appropriate headings, sections, and clauses.
- Use formal legal language but keep it clear and understandable.
- Include all standard clauses relevant to the document type.
- Use placeholders like [NAME], [DATE], [ADDRESS] for details you don't have.
- Reference any uploaded document templates or context.
- Add a disclaimer: "This draft is AI-generated and should be reviewed by a qualified lawyer before use."
- Structure the document with clear sections.
- If the user's jurisdiction is known, use that legal system's conventions."""


def draft_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Generate a legal draft document (agentic tool loop)."""
    logger.info("draft_generate invoked (tools=%s)", [t.name for t in TEMPLATE_TOOLS])
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

    result = run_agent_with_tools(
        system,
        state.get("messages") or [],
        TEMPLATE_TOOLS,
        config,
        temperature=0.3,
    )

    return {
        "reply": result["reply"],
        "active_agent": "draft",
        "agent_metadata": {
            "draft": {
                "model": result["model"],
                "agentic": result["agentic"],
                "tools_used": [t["tool"] for t in result["tool_trace"]],
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
