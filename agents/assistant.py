"""Assistant Agent — general legal Q&A.

Handles simple questions, procedural queries, and general legal information.
This is the default fallback agent for most queries.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.base import (
    AgentState,
    latest_user_text,
    message_text,
    register_agent,
    to_lc_messages,
    unpack_stream_part,
)

_llm_cache: dict[str, Any] = {}

ASSISTANT_SYSTEM = """You are the Assistant agent of a legal AI system.
You handle general legal questions, procedural guidance, and legal information.

Guidelines:
- Give clear, practical answers in plain language.
- Mention that this is not formal legal advice when the topic is serious.
- If jurisdiction is specified, prefer that legal system.
- Use uploaded document context when provided.
- Use long-term memory facts when relevant.
- Keep answers concise but complete.
- Suggest consulting a lawyer for complex or high-stakes matters."""


def _get_llm(api_key: str, model: str):
    cache_key = f"asst:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=0.4)
    return _llm_cache[cache_key]


def assistant_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Generate a response for general legal questions."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "llama-3.3-70b-versatile")

    analysis = state.get("analysis") or {}
    system = ASSISTANT_SYSTEM

    # Inject analysis context
    if analysis:
        system += (
            f"\n\nQuery analysis:\n"
            f"- intent: {analysis.get('intent', 'question')}\n"
            f"- domain: {analysis.get('domain', 'general')}\n"
            f"- complexity: {analysis.get('complexity', 'simple')}\n"
            f"- jurisdiction: {analysis.get('jurisdiction', 'unspecified')}\n"
            f"- summary: {analysis.get('summary', '')}"
        )

    # Inject memory notes
    notes = (state.get("memory_notes") or "").strip()
    if notes:
        system += "\n\nLong-term memory about this user:\n" + notes

    # Inject RAG context
    rag = (state.get("rag_notes") or "").strip()
    if rag:
        system += (
            "\n\nRetrieved passages from uploaded documents. "
            "Use them when relevant and cite the filename.\n" + rag
        )

    llm = _get_llm(api_key, model)
    result = llm.invoke(
        to_lc_messages(state.get("messages") or [], system),
        config=config,
    )
    reply = str(result.content or "").strip()

    return {
        "reply": reply,
        "active_agent": "assistant",
        "agent_metadata": {"assistant": {"model": model}},
    }


register_agent(
    "assistant",
    description="General legal Q&A — handles questions, procedures, and legal info",
    handles=["question", "procedure", "other"],
)
