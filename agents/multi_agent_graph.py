"""Multi-agent LangGraph — routes queries to specialist agents.

Architecture:
    orchestrator ──┬──► assistant
                   ├──► researcher
                   ├──► draft
                   ├──► document_creator
                   ├──► email
                   ├──► lawyer_finder
                   └──► db_chat

Each specialist writes its reply into state["reply"].
The orchestrator's ``decide_route`` is used as a conditional edge.
"""

from typing import Any, Iterator

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agents.base import AgentState
from agents.orchestrator_agent import analyse_and_route, decide_route
from agents.assistant_agent import assistant_generate
from agents.researcher_agent import researcher_generate
from agents.draft_agent import draft_generate
from agents.document_creator_agent import document_creator_generate
from agents.email_agent import email_generate
from agents.lawyer_finder_agent import lawyer_finder_generate
from agents.db_chat_agent import db_chat_generate
from agents.workflow_agent import detect_workflow, stream_workflow, workflow_generate
from agents.case_strategy_agent import case_strategy_generate
from agents.compliance_agent import compliance_generate
from agents.negotiation_agent import negotiation_generate
from agents.risk_assessment_agent import risk_assessment_generate


def build_multi_graph(api_key: str, model: str):
    """Build and compile the multi-agent state graph."""

    graph = StateGraph(AgentState)

    # ── Add nodes ───────────────────────────────────────────────
    graph.add_node("orchestrator", analyse_and_route)
    graph.add_node("assistant", assistant_generate)
    graph.add_node("researcher", researcher_generate)
    graph.add_node("draft", draft_generate)
    graph.add_node("document_creator", document_creator_generate)
    graph.add_node("email", email_generate)
    graph.add_node("lawyer_finder", lawyer_finder_generate)
    graph.add_node("db_chat", db_chat_generate)
    graph.add_node("workflow_supervisor", workflow_generate)
    graph.add_node("case_strategy", case_strategy_generate)
    graph.add_node("compliance", compliance_generate)
    graph.add_node("negotiation", negotiation_generate)
    graph.add_node("risk_assessment", risk_assessment_generate)

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
            "email": "email",
            "lawyer_finder": "lawyer_finder",
            "db_chat": "db_chat",
            "workflow_supervisor": "workflow_supervisor",
            "case_strategy": "case_strategy",
            "compliance": "compliance",
            "negotiation": "negotiation",
            "risk_assessment": "risk_assessment",
        },
    )

    # All specialist agents → END
    for agent_name in ("assistant", "researcher", "draft", "document_creator", "email", "lawyer_finder", "db_chat", "workflow_supervisor", "case_strategy", "compliance", "negotiation", "risk_assessment"):
        graph.add_edge(agent_name, END)

    return graph.compile()


def stream_multi_graph(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    memory_notes: str = "",
    rag_notes: str = "",
    user_role: str = "user",
    user_profile: str = "",
    episodic_notes: str = "",
    procedural_notes: str = "",
) -> Iterator[dict[str, Any]]:
    """Stream events from the multi-agent graph.

    Uses LangGraph ``stream_mode="updates"`` so every node completion is
    surfaced to the caller.  Yields events with types:
      - agent_route: root agent finished — which specialist was selected
      - analysis: orchestrator intent classification result
      - agent_start: a specialist agent begins generating
      - agent_done: a specialist agent finished generating
      - token: the final reply text
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

    initial_state: AgentState = {
        "messages": messages,
        "analysis": None,
        "reply": "",
        "memory_notes": memory_notes,
        "rag_notes": rag_notes,
        "user_profile": user_profile,
        "episodic_notes": episodic_notes,
        "procedural_notes": procedural_notes,
        "active_agent": "",
        "routed_to": "",
        "agent_metadata": {},
        "user_role": user_role,
        "connectors_available": [],
    }

    try:
        # Workflows have their own streaming executor so every child-agent
        # call reaches the UI as it starts and completes.
        workflow_mode = detect_workflow(messages[-1].get("content", "") if messages else "")
        if workflow_mode:
            routed = analyse_and_route(initial_state, config)
            analysis = routed.get("analysis") or {}
            yield {
                "type": "agent_route",
                "routed_to": "workflow_supervisor",
                "analysis": analysis,
                "active_agent": "orchestrator",
                "agent_metadata": routed.get("agent_metadata") or {},
            }
            yield {"type": "analysis", "analysis": analysis, "model": model}
            user_query = messages[-1].get("content", "") if messages else ""
            yield {
                "type": "agent_start",
                "agent": "workflow_supervisor",
                "stage_id": "workflow_supervisor:0",
                "parent_stage_id": "orchestrator:0",
                "input": user_query,
            }
            for event in stream_workflow(initial_state, config, workflow_mode):
                yield event
            return

        routed_to = "assistant"
        reply = ""
        for update in compiled.stream(
            initial_state,
            config=config,
            stream_mode="updates",
        ):
            for node_name, node_state in (update or {}).items():
                if node_name == "orchestrator":
                    routed_to = node_state.get("routed_to") or "assistant"
                    analysis = node_state.get("analysis") or {}
                    yield {
                        "type": "agent_route",
                        "routed_to": routed_to,
                        "analysis": analysis,
                        "active_agent": "orchestrator",
                        "agent_metadata": node_state.get("agent_metadata") or {},
                    }
                    if analysis:
                        yield {"type": "analysis", "analysis": analysis, "model": model}
                    user_query = messages[-1].get("content", "") if messages else ""
                    yield {
                        "type": "agent_start",
                        "agent": routed_to,
                        "stage_id": f"{routed_to}:1",
                        "parent_stage_id": "orchestrator:0",
                        "input": user_query,
                    }
                else:
                    reply = (node_state.get("reply") or "").strip()
                    workflow = node_state.get("workflow")
                    if workflow:
                        yield {"type": "workflow", "workflow": workflow}
                    yield {
                        "type": "agent_done",
                        "agent": node_name,
                        "stage_id": f"{node_name}:1",
                        "parent_stage_id": "orchestrator:0",
                        "reply": reply[:6000],
                        "truncated": len(reply) > 6000,
                        "reply_chars": len(reply),
                        "agent_metadata": node_state.get("agent_metadata") or {},
                    }

        if reply:
            yield {"type": "token", "content": reply}
            yield {"type": "done", "model": model, "agent": routed_to}
        else:
            yield {"type": "error", "detail": "Agent returned an empty reply"}

    except Exception as exc:
        yield {"type": "error", "detail": f"Multi-agent error: {exc}"}
