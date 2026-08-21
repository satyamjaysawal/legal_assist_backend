"""Case strategy specialist for dispute planning and evidence checklists."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.agent_tools import RESEARCH_TOOLS
from agents.base import AgentState, register_agent
from agents.tool_loop_runner import run_agent_with_tools

SYSTEM = """You are the Case Strategy agent in a legal AI POC. Turn a dispute
description into a practical, non-binding case-preparation plan. Structure the
answer as: objectives, facts still needed, evidence checklist, possible legal
issues, procedural next steps, deadlines to verify, and risks. Use research
tools when helpful. Do not promise an outcome; recommend a qualified lawyer for
filing, limitation, or high-stakes decisions."""


def case_strategy_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    result = run_agent_with_tools(SYSTEM, state.get("messages") or [], RESEARCH_TOOLS, config, temperature=0.25)
    return {
        "reply": result["reply"], "active_agent": "case_strategy",
        "agent_metadata": {"case_strategy": {"model": result["model"], "agentic": result["agentic"], "tools_used": [item["tool"] for item in result["tool_trace"]]}},
    }


register_agent("case_strategy", description="Dispute strategy, evidence checklists, and procedural planning", handles=["case_strategy"])
