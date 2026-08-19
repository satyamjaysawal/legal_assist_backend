"""Researcher Agent — deep legal research.

Handles queries that need case law analysis, statute lookup, document
review, or comparison between legal provisions.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.base import (
    AgentState,
    latest_user_text,
    register_agent,
    to_lc_messages,
)

_llm_cache: dict[str, Any] = {}

RESEARCHER_SYSTEM = """You are the Researcher agent of a legal AI system.
You specialise in deep legal research, case law analysis, and statute interpretation.

Guidelines:
- Analyse the legal question thoroughly.
- Reference uploaded documents (RAG) with proper citations (mention filename + page).
- If connector data (case law, bare acts) is provided, integrate it into your analysis.
- Structure your answer: Issue → Relevant Law → Analysis → Conclusion.
- Cite specific sections, case names, and years where possible.
- Flag when information might be outdated or jurisdiction-specific.
- Recommend consulting a lawyer for complex research findings.
- Mention this is not formal legal advice."""


def _get_llm(api_key: str, model: str):
    cache_key = f"rsrch:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=0.3)
    return _llm_cache[cache_key]


def researcher_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Generate a deep research response."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

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

    llm = _get_llm(api_key, model)
    result = llm.invoke(
        to_lc_messages(state.get("messages") or [], system),
        config=config,
    )
    reply = str(result.content or "").strip()

    return {
        "reply": reply,
        "active_agent": "researcher",
        "agent_metadata": {"researcher": {"model": model, "used_rag": bool(rag)}},
    }


register_agent(
    "researcher",
    description="Deep legal research — case law, statutes, document review, comparisons",
    handles=["review", "compare"],
)
