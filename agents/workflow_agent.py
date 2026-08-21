"""Workflow supervisor agent for the Legal Assist POC.

This module intentionally demonstrates five orchestration topologies with the
same specialist agents used by normal chat: sequential hand-offs, parallel
delegation, supervisor-to-subagent delegation, bounded refinement loops, and
bounded cyclic review/revision.  All loops have fixed limits so a demo request
cannot recurse indefinitely.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.runnables import RunnableConfig

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
    return None


def _state_for_subagent(state: AgentState, context: str = "") -> AgentState:
    copied: AgentState = dict(state)
    copied["agent_metadata"] = dict(state.get("agent_metadata") or {})
    if context:
        copied["memory_notes"] = f"{state.get('memory_notes') or ''}\n\nWorkflow context:\n{context}".strip()
    return copied


def _run(agent_name: str, state: AgentState, config: RunnableConfig, context: str = "") -> dict[str, Any]:
    result = SPECIALISTS[agent_name](_state_for_subagent(state, context), config)
    return {"agent": agent_name, "reply": (result.get("reply") or "").strip(), "metadata": result.get("agent_metadata") or {}}


def _render(mode: str, results: list[dict[str, Any]]) -> str:
    definition = WORKFLOW_DEFINITIONS[mode]
    sections = [f"## {definition['label']}\n\n**Flow:** `{definition['pattern']}`"]
    for index, result in enumerate(results, start=1):
        sections.append(f"### {index}. {result['agent'].replace('_', ' ').title()}\n{result['reply']}")
    sections.append("\n*POC workflow: outputs are AI-generated and should be reviewed by a qualified lawyer before use.*")
    return "\n\n".join(sections)


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
    else:  # cycle
        research = _run("researcher", state, config, "Research the issue before drafting.")
        results.append(research)
        draft = _run("draft", state, config, f"Draft using the research:\n{research['reply']}")
        results.append(draft)
        gap_check = _run("researcher", state, config, f"Perform a gap-check on this draft and list only material corrections:\n{draft['reply']}")
        results.append(gap_check)
        results.append(_run("draft", state, config, f"Produce the final revised draft using the gap-check:\n{gap_check['reply']}"))

    definition = WORKFLOW_DEFINITIONS[mode]
    return {
        "reply": _render(mode, results),
        "active_agent": "workflow_supervisor",
        "workflow": {"mode": mode, **definition, "stages": [item["agent"] for item in results]},
        "agent_metadata": {"workflow_supervisor": {"mode": mode, "stages": [item["agent"] for item in results], "bounded": mode in {"loop", "cycle"}}},
    }


register_agent(
    "workflow_supervisor",
    description="POC supervisor demonstrating sequential, parallel, subagent, loop, and cyclic workflows",
    handles=["workflow"],
)
