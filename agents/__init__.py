"""Multi-agent system for Legal AI Assistant.

Each agent is a callable that takes (state, config) and returns a partial
state update dict.  The orchestrator in ``multi_graph.py`` wires them into
a LangGraph StateGraph with conditional routing.
"""

from agents.base import AGENT_REGISTRY, AgentState, get_agent, list_agents
from agents.orchestrator import analyse_and_route
from agents.assistant import assistant_generate
from agents.researcher import researcher_generate
from agents.draft import draft_generate
from agents.document_creator import document_creator_generate
from agents.lawyer_finder import lawyer_finder_generate
from agents.db_chat import db_chat_generate

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
