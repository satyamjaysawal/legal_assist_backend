"""Assistant Agent — general legal Q&A (agentic).

Handles simple questions, procedural queries, and general legal information.
This is the default fallback agent for most queries.

Agentic pattern: the LLM has the legal-dictionary tool bound via
`bind_tools` and decides itself when a term lookup is needed
(see agents/tool_loop_runner.py).
"""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.tool_loop_runner import run_agent_with_tools
from agents.base import (
    AgentState,
    register_agent,
)
from agents.agent_tools import GENERAL_TOOLS

logger = logging.getLogger("legal_assist.agents.assistant")

ASSISTANT_SYSTEM = """You are the Assistant agent of a legal AI system.
You handle general legal questions, procedural guidance, and legal information.

Tools: you can call tools when they help — e.g. use define_legal_term to
look up the exact meaning of a legal term before explaining it. Only call
a tool when it actually adds value; otherwise answer directly.

Guidelines:
- Give clear, practical answers in plain language.
- Mention that this is not formal legal advice when the topic is serious.
- If jurisdiction is specified, prefer that legal system.
- Use uploaded document context when provided.
- Use long-term memory facts when relevant.
- Keep answers concise but complete.
- Suggest consulting a lawyer for complex or high-stakes matters.

Presentation (the UI renders GitHub-flavoured Markdown):
- Structure longer answers with short ### headings and bullet lists.
- Bold key terms, deadlines, and section numbers.
- When comparing items side by side, use a Markdown table with at most
  4 columns and short cells (never put long sentences inside table cells).
- Prefer scannable sections over one long paragraph."""


def assistant_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Generate a response for general legal questions (agentic tool loop)."""
    logger.info("assistant_generate invoked (tools=%s)", [t.name for t in GENERAL_TOOLS])
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

    # Inject user profile
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\n=== USER PROFILE (use this to personalize responses) ===\n" + profile
        system += "\nIMPORTANT: If the user shared their name, USE IT in your response."

    # Inject episodic memory
    episodic = (state.get("episodic_notes") or "").strip()
    if episodic:
        system += "\n\n=== PAST CONVERSATIONS (reference when relevant) ===\n" + episodic

    # Inject procedural memory
    procedural = (state.get("procedural_notes") or "").strip()
    if procedural:
        system += "\n\n=== USER PREFERENCES (follow these) ===\n" + procedural

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

    result = run_agent_with_tools(
        system,
        state.get("messages") or [],
        GENERAL_TOOLS,
        config,
        temperature=0.4,
    )

    return {
        "reply": result["reply"],
        "active_agent": "assistant",
        "agent_metadata": {
            "assistant": {
                "model": result["model"],
                "agentic": result["agentic"],
                "tools_used": [t["tool"] for t in result["tool_trace"]],
            }
        },
    }


register_agent(
    "assistant",
    description="General legal Q&A — handles questions, procedures, and legal info",
    handles=["question", "procedure", "other"],
)
