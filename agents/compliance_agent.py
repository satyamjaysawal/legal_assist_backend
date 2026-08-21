"""Compliance specialist for policy, checklist, and risk-gap use cases."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.agent_tools import RESEARCH_TOOLS
from agents.base import AgentState, register_agent
from agents.tool_loop_runner import run_agent_with_tools

SYSTEM = """You are the Compliance agent in a legal AI POC. Assess a business
process, policy, or document for compliance gaps. Return: assumptions,
applicable areas to verify, risk rating, control checklist, owner/action, and
questions for counsel. Be explicit when jurisdiction or current regulations
must be verified. This is an educational compliance aid, not a certification."""


def compliance_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    result = run_agent_with_tools(SYSTEM, state.get("messages") or [], RESEARCH_TOOLS, config, temperature=0.2)
    return {
        "reply": result["reply"], "active_agent": "compliance",
        "agent_metadata": {"compliance": {"model": result["model"], "agentic": result["agentic"], "tools_used": [item["tool"] for item in result["tool_trace"]]}},
    }


register_agent("compliance", description="Compliance gap assessment, controls, and policy checklists", handles=["compliance"])
