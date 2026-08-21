"""Researcher Agent — deep legal research (agentic).

Handles queries that need case law analysis, statute lookup, document
review, or comparison between legal provisions.

Agentic pattern: bare-acts and legal-dictionary tools are bound to the
LLM, which calls them on demand during its research loop.
"""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.tool_loop_runner import run_agent_with_tools
from agents.base import (
    AgentState,
    register_agent,
)
from agents.agent_tools import RESEARCH_TOOLS

logger = logging.getLogger("legal_assist.agents.researcher")

RESEARCHER_SYSTEM = """You are the Researcher agent of a legal AI system.
You specialise in deep legal research, case law analysis, and statute interpretation.

Tools: you have research tools bound to you — use them proactively:
- search_bare_acts: fetch the text of a statute/section you plan to cite.
- define_legal_term: get the precise definition of a legal term.
Call the tools BEFORE writing your analysis so your citations are grounded
in the returned data. If a tool returns dummy/placeholder data, say so.

Guidelines:
- Analyse the legal question thoroughly.
- Reference uploaded documents (RAG) with proper citations (mention filename + page).
- If connector data (case law, bare acts) is provided, integrate it into your analysis.
- Structure your answer: Issue → Relevant Law → Analysis → Conclusion.
- Cite specific sections, case names, and years where possible.
- Flag when information might be outdated or jurisdiction-specific.
- Recommend consulting a lawyer for complex research findings.
- Mention this is not formal legal advice."""


def researcher_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Generate a deep research response (agentic tool loop)."""
    logger.info("researcher_generate invoked (tools=%s)", [t.name for t in RESEARCH_TOOLS])
    analysis = state.get("analysis") or {}
    system = RESEARCHER_SYSTEM

    # Inject analysis context
    if analysis:
        system += (
            f"\n\nQuery analysis:\n"
            f"- intent: {analysis.get('intent', 'question')}\n"
            f"- domain: {analysis.get('domain', 'general')}\n"
            f"- complexity: {analysis.get('complexity', 'complex')}\n"
            f"- jurisdiction: {analysis.get('jurisdiction', 'unspecified')}\n"
            f"- refined_query: {analysis.get('refined_query', '')}"
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

    # Inject memory
    notes = (state.get("memory_notes") or "").strip()
    if notes:
        system += "\n\nLong-term memory about this user:\n" + notes

    # Inject RAG
    rag = (state.get("rag_notes") or "").strip()
    if rag:
        system += (
            "\n\n=== UPLOADED DOCUMENT PASSAGES (cite filename when used) ===\n" + rag
        )

    # Inject connector data if available in state
    connector_data = (state.get("agent_metadata") or {}).get("connector_results")
    if connector_data:
        system += "\n\n=== CONNECTOR DATA ===\n" + str(connector_data)

    result = run_agent_with_tools(
        system,
        state.get("messages") or [],
        RESEARCH_TOOLS,
        config,
        temperature=0.3,
    )

    return {
        "reply": result["reply"],
        "active_agent": "researcher",
        "agent_metadata": {
            "researcher": {
                "model": result["model"],
                "agentic": result["agentic"],
                "tools_used": [t["tool"] for t in result["tool_trace"]],
                "used_rag": bool(rag),
            }
        },
    }


register_agent(
    "researcher",
    description="Deep legal research — case law, statutes, document review, comparisons",
    handles=["review", "compare"],
)
