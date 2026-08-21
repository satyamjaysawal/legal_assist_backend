"""Multi-agent system for Legal AI Assistant.

Each agent is a callable that takes (state, config) and returns a partial
state update dict.  The orchestrator in ``multi_agent_graph.py`` wires them
into a LangGraph StateGraph with conditional routing.
"""

from agents.base import AGENT_REGISTRY, AgentState, get_agent, list_agents
from agents.orchestrator_agent import analyse_and_route
from agents.assistant_agent import assistant_generate
from agents.researcher_agent import researcher_generate
from agents.draft_agent import draft_generate
from agents.document_creator_agent import document_creator_generate
from agents.lawyer_finder_agent import lawyer_finder_generate
from agents.db_chat_agent import db_chat_generate

__all__ = [
    "AGENT_REGISTRY",
    "AgentState",
    "get_agent",
    "list_agents",
    "analyse_and_route",
    "assistant_generate",
    "researcher_generate",
    "draft_generate",
    "document_creator_generate",
    "lawyer_finder_generate",
    "db_chat_generate",
]
