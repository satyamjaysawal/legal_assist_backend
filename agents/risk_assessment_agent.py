"""Legal risk assessment specialist."""

from langchain_core.runnables import RunnableConfig

from agents.agent_tools import RESEARCH_TOOLS
from agents.base import AgentState, register_agent
from agents.tool_loop_runner import run_agent_with_tools

SYSTEM = """You are the Risk Assessment agent in a legal AI POC. Analyse the
legal risks in a described situation, process, or document. Return assumptions,
a severity-ranked risk register, likely impact, mitigations, evidence to retain,
and escalation points for qualified counsel. Mark uncertainty clearly and use
research tools when helpful. This is educational risk triage, not legal advice."""


def risk_assessment_generate(state: AgentState, config: RunnableConfig) -> dict:
    result = run_agent_with_tools(SYSTEM, state.get("messages") or [], RESEARCH_TOOLS, config, temperature=0.25)
    return {
        "reply": result["reply"],
        "active_agent": "risk_assessment",
        "agent_metadata": {"risk_assessment": {"model": result["model"], "agentic": result["agentic"], "tools_used": [item["tool"] for item in result["tool_trace"]]}},
    }


register_agent("risk_assessment", description="Legal risk register, mitigations, and escalation guidance", handles=["risk_assessment"])
