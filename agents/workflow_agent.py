"""Workflow supervisor agent for the Legal Assist POC.

This module intentionally demonstrates five orchestration topologies with the
same specialist agents used by normal chat: sequential hand-offs, parallel
delegation, supervisor-to-subagent delegation, bounded refinement loops, and
bounded cyclic review/revision.  All loops have fixed limits so a demo request
cannot recurse indefinitely.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterator

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from agents.assistant_agent import assistant_generate
from agents.base import AgentState, register_agent
from agents.document_creator_agent import document_creator_generate
from agents.draft_agent import draft_generate
from agents.email_agent import email_generate
from agents.researcher_agent import researcher_generate

MAX_REFINEMENT_PASSES = 2

WORKFLOW_DEFINITIONS = {
    "sequential": {
        "label": "Sequential hand-off",
        "pattern": "researcher -> draft -> assistant",
        "agents": ["researcher", "draft", "assistant"],
    },
    "parallel": {
        "label": "Parallel specialist review",
        "pattern": "researcher || draft -> assistant",
        "agents": ["researcher", "draft", "assistant"],
    },
    "supervisor": {
        "label": "Supervisor to subagents",
        "pattern": "workflow_supervisor -> (researcher, document_creator, email) -> synthesis",
        "agents": ["researcher", "document_creator", "email"],
    },
    "loop": {
        "label": "Bounded refinement loop",
        "pattern": "draft -> researcher review -> draft revision (max 2 passes)",
        "agents": ["draft", "researcher"],
    },
    "cycle": {
        "label": "Bounded cyclic review",
        "pattern": "researcher -> draft -> researcher gap-check -> draft revision",
        "agents": ["researcher", "draft"],
    },
    "hitl": {
        "label": "Human-in-the-loop review",
        "pattern": "researcher -> draft -> human approval checkpoint -> researcher review -> final draft",
        "agents": ["researcher", "draft"],
    },
}

SPECIALISTS = {
    "assistant": assistant_generate,
    "researcher": researcher_generate,
    "draft": draft_generate,
    "document_creator": document_creator_generate,
    "email": email_generate,
}


def detect_workflow(text: str) -> str | None:
    """Return an explicit POC workflow mode requested by the user."""
    normalised = (text or "").lower()
    if "workflow: parallel" in normalised or "parallel agents" in normalised:
        return "parallel"
    if "workflow: sequential" in normalised or "sequential agents" in normalised:
        return "sequential"
    if "workflow: supervisor" in normalised or "agent to subagent" in normalised:
        return "supervisor"
    if "workflow: loop" in normalised or "refinement loop" in normalised:
        return "loop"
    if "workflow: cycle" in normalised or "cyclic agent" in normalised:
        return "cycle"
    if "workflow: hitl" in normalised or "human in the loop" in normalised:
        return "hitl"
    return None


def _state_for_subagent(state: AgentState, context: str = "") -> AgentState:
    copied: AgentState = dict(state)
    copied["agent_metadata"] = dict(state.get("agent_metadata") or {})
    if context:
        copied["memory_notes"] = f"{state.get('memory_notes') or ''}\n\nWorkflow context:\n{context}".strip()
    return copied


def build_workflow_stage_graph(agent_name: str):
    """Build a LangGraph node for one visible workflow specialist stage."""
    if agent_name not in SPECIALISTS:
        raise ValueError(f"Unknown workflow specialist: {agent_name}")

    def specialist_node(stage_state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        return SPECIALISTS[agent_name](stage_state, config)

    graph = StateGraph(AgentState)
    graph.add_node(agent_name, specialist_node)
    graph.add_edge(START, agent_name)
    graph.add_edge(agent_name, END)
    return graph.compile()


def _run(agent_name: str, state: AgentState, config: RunnableConfig, context: str = "") -> dict[str, Any]:
    """Run each workflow stage through LangGraph before returning its hand-off."""
    graph = build_workflow_stage_graph(agent_name)
    result = graph.invoke(_state_for_subagent(state, context), config=config)
    return {"agent": agent_name, "reply": (result.get("reply") or "").strip(), "metadata": result.get("agent_metadata") or {}}


def _render(mode: str, results: list[dict[str, Any]]) -> str:
    definition = WORKFLOW_DEFINITIONS[mode]
    sections = [f"## {definition['label']}\n\n**Flow:** `{definition['pattern']}`"]
    for index, result in enumerate(results, start=1):
        sections.append(f"### {index}. {result['agent'].replace('_', ' ').title()}\n{result['reply']}")
    sections.append("\n*POC workflow: outputs are AI-generated and should be reviewed by a qualified lawyer before use.*")
    return "\n\n".join(sections)


def _stage_event(event_type: str, result: dict[str, Any] | None, agent: str, stage_id: str) -> dict[str, Any]:
    """Create a UI-visible lifecycle event for one workflow stage."""
    if event_type == "agent_start":
        return {"type": event_type, "agent": agent, "stage_id": stage_id}
    result = result or {}
    return {
        "type": "agent_done",
        "agent": agent,
        "stage_id": stage_id,
        "reply_chars": len(result.get("reply") or ""),
        "agent_metadata": result.get("metadata") or {},
    }


def stream_workflow(state: AgentState, config: RunnableConfig, mode: str) -> Iterator[dict[str, Any]]:
    """Run a workflow and stream every specialist/HITL lifecycle event.

    This is deliberately separate from the compact LangGraph workflow node so
    the browser receives each stage while it is running, including repeated
    draft/review calls and the human approval checkpoint.
    """
    mode = mode if mode in WORKFLOW_DEFINITIONS else "sequential"
    results: list[dict[str, Any]] = []
    counter = 0

    def run_stage(agent: str, context: str) -> Iterator[dict[str, Any]]:
        nonlocal counter
        counter += 1
        stage_id = f"{agent}:{counter}"
        yield _stage_event("agent_start", None, agent, stage_id)
        result = _run(agent, state, config, context)
        results.append(result)
        yield _stage_event("agent_done", result, agent, stage_id)

    if mode == "sequential":
        yield from run_stage("researcher", "Research the issue and identify legal considerations for the drafting subagent.")
        research = results[-1]
        yield from run_stage("draft", f"Use this research hand-off to prepare the requested draft:\n{research['reply']}")
        draft = results[-1]
        yield from run_stage("assistant", f"Summarise next steps after this draft:\n{draft['reply']}")
    elif mode in {"parallel", "supervisor"}:
        assigned = WORKFLOW_DEFINITIONS[mode]["agents"]
        contexts = {
            "researcher": "Independently identify law, risks, and authorities.",
            "draft": "Independently produce the requested legal draft with placeholders.",
            "document_creator": "Independently outline the structured document and required fields.",
            "email": "Independently prepare a concise client-facing follow-up email.",
        }
        stage_ids = {}
        with ThreadPoolExecutor(max_workers=len(assigned)) as pool:
            futures = {}
            for agent in assigned:
                counter += 1
                stage_ids[agent] = f"{agent}:{counter}"
                yield _stage_event("agent_start", None, agent, stage_ids[agent])
                futures[pool.submit(_run, agent, state, config, contexts.get(agent, ""))] = agent
            completed = {}
            for future in as_completed(futures):
                agent = futures[future]
                result = future.result()
                completed[agent] = result
                yield _stage_event("agent_done", result, agent, stage_ids[agent])
        results.extend(completed[agent] for agent in assigned)
    elif mode == "loop":
        yield from run_stage("draft", "Create the first complete draft.")
        draft = results[-1]
        for pass_number in range(1, MAX_REFINEMENT_PASSES + 1):
            yield from run_stage("researcher", f"Review this draft for legal gaps and improvements (pass {pass_number}):\n{draft['reply']}")
            review = results[-1]
            yield from run_stage("draft", f"Revise the draft using this review (pass {pass_number}):\n{review['reply']}")
            draft = results[-1]
    elif mode == "cycle":
        yield from run_stage("researcher", "Research the issue before drafting.")
        research = results[-1]
        yield from run_stage("draft", f"Draft using the research:\n{research['reply']}")
        draft = results[-1]
        yield from run_stage("researcher", f"Perform a gap-check on this draft and list only material corrections:\n{draft['reply']}")
        gap_check = results[-1]
        yield from run_stage("draft", f"Produce the final revised draft using the gap-check:\n{gap_check['reply']}")
    else:  # hitl
        yield from run_stage("researcher", "Research the issue before preparing a human-review draft.")
        research = results[-1]
        yield from run_stage("draft", f"Create a review-ready draft using this research:\n{research['reply']}")
        draft = results[-1]
        counter += 1
        checkpoint = {"agent": "human_review", "reply": "Human approval checkpoint: verify facts, names, dates, amounts, jurisdiction, remedy, and tone. Approve or request changes before using this draft.", "metadata": {"human_in_loop": True}}
        stage_id = f"human_review:{counter}"
        yield _stage_event("agent_start", None, "human_review", stage_id)
        results.append(checkpoint)
        yield _stage_event("agent_done", checkpoint, "human_review", stage_id)
        yield from run_stage("researcher", f"Perform a legal quality review of this human-review draft and list material corrections:\n{draft['reply']}")
        review = results[-1]
        yield from run_stage("draft", f"Prepare a final draft that incorporates the legal review. It remains subject to the human approval checkpoint:\n{review['reply']}")

    definition = WORKFLOW_DEFINITIONS[mode]
    workflow = {"mode": mode, **definition, "stages": [item["agent"] for item in results], "human_in_loop": mode == "hitl"}
    yield {"type": "workflow", "workflow": workflow}
    yield {"type": "token", "content": _render(mode, results)}
    yield {"type": "done", "agent": "workflow_supervisor"}


def workflow_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Run the requested bounded orchestration topology."""
    mode = ((state.get("analysis") or {}).get("workflow_type") or "sequential").lower()
    if mode not in WORKFLOW_DEFINITIONS:
        mode = "sequential"

    results: list[dict[str, Any]] = []
    if mode == "sequential":
        research = _run("researcher", state, config, "Research the issue and identify legal considerations for the drafting subagent.")
        results.append(research)
        draft = _run("draft", state, config, f"Use this research hand-off to prepare the requested draft:\n{research['reply']}")
        results.append(draft)
        results.append(_run("assistant", state, config, f"Summarise next steps after this draft:\n{draft['reply']}"))
    elif mode in {"parallel", "supervisor"}:
        assigned = WORKFLOW_DEFINITIONS[mode]["agents"]
        contexts = {
            "researcher": "Independently identify law, risks, and authorities.",
            "draft": "Independently produce the requested legal draft with placeholders.",
            "document_creator": "Independently outline the structured document and required fields.",
            "email": "Independently prepare a concise client-facing follow-up email.",
        }
        with ThreadPoolExecutor(max_workers=len(assigned)) as pool:
            futures = [pool.submit(_run, agent, state, config, contexts.get(agent, "")) for agent in assigned]
            results = [future.result() for future in futures]
    elif mode == "loop":
        draft = _run("draft", state, config, "Create the first complete draft.")
        results.append(draft)
        for pass_number in range(1, MAX_REFINEMENT_PASSES + 1):
            review = _run("researcher", state, config, f"Review this draft for legal gaps and improvements (pass {pass_number}):\n{draft['reply']}")
            results.append(review)
            draft = _run("draft", state, config, f"Revise the draft using this review (pass {pass_number}):\n{review['reply']}")
            results.append(draft)
    elif mode == "cycle":
        research = _run("researcher", state, config, "Research the issue before drafting.")
        results.append(research)
        draft = _run("draft", state, config, f"Draft using the research:\n{research['reply']}")
        results.append(draft)
        gap_check = _run("researcher", state, config, f"Perform a gap-check on this draft and list only material corrections:\n{draft['reply']}")
        results.append(gap_check)
        results.append(_run("draft", state, config, f"Produce the final revised draft using the gap-check:\n{gap_check['reply']}"))
    else:  # hitl
        research = _run("researcher", state, config, "Research the issue before preparing a human-review draft.")
        results.append(research)
        draft = _run("draft", state, config, f"Create a review-ready draft using this research:\n{research['reply']}")
        results.append(draft)
        results.append({
            "agent": "human_review",
            "reply": (
                "**Human approval checkpoint (required):** Review the draft's facts, names, dates, "
                "amounts, jurisdiction, requested remedy, and tone. Reply with **Approve** or list "
                "the exact changes you want before relying on or sending the document."
            ),
            "metadata": {"human_in_loop": True},
        })
        review = _run("researcher", state, config, f"Perform a legal quality review of this human-review draft and list material corrections:\n{draft['reply']}")
        results.append(review)
        results.append(_run("draft", state, config, f"Prepare a final draft that incorporates the legal review. It remains subject to the human approval checkpoint:\n{review['reply']}"))

    definition = WORKFLOW_DEFINITIONS[mode]
    return {
        "reply": _render(mode, results),
        "active_agent": "workflow_supervisor",
        "workflow": {"mode": mode, **definition, "stages": [item["agent"] for item in results]},
        "agent_metadata": {"workflow_supervisor": {"mode": mode, "stages": [item["agent"] for item in results], "bounded": mode in {"loop", "cycle", "hitl"}, "human_in_loop": mode == "hitl"}},
    }


register_agent(
    "workflow_supervisor",
    description="POC supervisor demonstrating sequential, parallel, subagent, loop, and cyclic workflows",
    handles=["workflow"],
)
