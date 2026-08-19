"""Multi-agent LangGraph — routes queries to specialist agents.

Architecture:
    orchestrator ──┬──► assistant
                   ├──► researcher
                   ├──► draft
                   ├──► document_creator
                   └──► lawyer_finder

Each specialist writes its reply into state["reply"].
The orchestrator's ``decide_route`` is used as a conditional edge.
"""

from typing import Any, Iterator

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agents.base import AgentState
from agents.orchestrator import analyse_and_route, decide_route
from agents.assistant import assistant_generate
from agents.researcher import researcher_generate
from agents.draft import draft_generate
from agents.document_creator import document_creator_generate
from agents.lawyer_finder import lawyer_finder_generate


def build_multi_graph(api_key: str, model: str):
    """Build and compile the multi-agent state graph."""

    graph = StateGraph(AgentState)

    # ── Add nodes ───────────────────────────────────────────────
    graph.add_node("orchestrator", analyse_and_route)
    graph.add_node("assistant", assistant_generate)
    graph.add_node("researcher", researcher_generate)
    graph.add_node("draft", draft_generate)
    graph.add_node("document_creator", document_creator_generate)
    graph.add_node("lawyer_finder", lawyer_finder_generate)

    # ── Edges ───────────────────────────────────────────────────
    graph.add_edge(START, "orchestrator")

    # Conditional routing from orchestrator to specialist agents
    graph.add_conditional_edges(
        "orchestrator",
        decide_route,
        {
            "assistant": "assistant",
            "researcher": "researcher",
            "draft": "draft",
            "document_creator": "document_creator",
            "lawyer_finder": "lawyer_finder",
        },
    )

    # All specialist agents → END
    for agent_name in ("assistant", "researcher", "draft", "document_creator", "lawyer_finder"):
        graph.add_edge(agent_name, END)

    return graph.compile()


def stream_multi_graph(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    memory_notes: str = "",
    rag_notes: str = "",
    user_role: str = "user",
) -> Iterator[dict[str, Any]]:
    """Stream events from the multi-agent graph.

    Yields events with types:
      - agent_route: which agent was selected
      - analysis: orchestrator analysis result
      - token: generated text tokens (when available)
      - done: generation complete
      - error: something went wrong
    """
    compiled = build_multi_graph(api_key, model)
    config: RunnableConfig = {
        "configurable": {
            "api_key": api_key,
            "model": model,
        }
    }

    try:
        # Use invoke (non-streaming) for reliable state tracking
        # Each specialist agent writes to state["reply"]
        result = compiled.invoke(
            {
                "messages": messages,
                "analysis": None,
                "reply": "",
                "memory_notes": memory_notes,
                "rag_notes": rag_notes,
                "active_agent": "",
                "routed_to": "",
                "agent_metadata": {},
                "user_role": user_role,
                "connectors_available": [],
            },
            config=config,
        )

        # Emit routing event
        routed_to = result.get("routed_to") or "assistant"
        yield {
            "type": "agent_route",
            "routed_to": routed_to,
            "analysis": result.get("analysis") or {},
            "active_agent": result.get("active_agent") or routed_to,
            "agent_metadata": result.get("agent_metadata") or {},
        }

        # Emit analysis
        analysis = result.get("analysis") or {}
        if analysis:
            yield {"type": "analysis", "analysis": analysis, "model": model}

        # Emit the reply as a single token event
        reply = (result.get("reply") or "").strip()
        if reply:
            yield {"type": "token", "content": reply}
            yield {"type": "done", "model": model, "agent": routed_to}
        else:
            yield {"type": "error", "detail": "Agent returned an empty reply"}

    except Exception as exc:
        yield {"type": "error", "detail": f"Multi-agent error: {exc}"}
