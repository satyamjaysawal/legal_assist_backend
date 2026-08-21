"""Negotiation specialist for settlement and counterproposal preparation."""

from langchain_core.runnables import RunnableConfig

from agents.agent_tools import RESEARCH_TOOLS
from agents.base import AgentState, register_agent
from agents.tool_loop_runner import run_agent_with_tools

SYSTEM = """You are the Negotiation agent in a legal AI POC. Help a user prepare
for a lawful, good-faith negotiation or settlement discussion. Provide: goals,
interests, proposed terms, fallback options, a concise counterproposal, and
items that need a lawyer's review. Do not claim legal certainty or pressure the
other party. Use legal research tools when helpful."""


def negotiation_generate(state: AgentState, config: RunnableConfig) -> dict:
    result = run_agent_with_tools(SYSTEM, state.get("messages") or [], RESEARCH_TOOLS, config, temperature=0.3)
    return {
        "reply": result["reply"],
        "active_agent": "negotiation",
        "agent_metadata": {"negotiation": {"model": result["model"], "agentic": result["agentic"], "tools_used": [item["tool"] for item in result["tool_trace"]]}},
    }


register_agent("negotiation", description="Settlement options, counterproposals, and negotiation preparation", handles=["negotiation"])
