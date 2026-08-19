"""Orchestrator — the ROOT AGENT.

Analyses the user query, classifies intent, and decides which specialist
agent should handle the request.  This is the first node in the
multi-agent LangGraph.
"""

import json
import re
from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.base import (
    AgentState,
    DEFAULT_ANALYSIS,
    latest_user_text,
    register_agent,
    to_lc_messages,
)

# ── Import lazily to avoid circular imports at module level ──────
_llm_cache: dict[str, Any] = {}

ORCHESTRATOR_PROMPT = """You are the root orchestrator agent for a legal AI assistant.
Read the latest user message and return ONLY valid JSON:

{
  "intent": "question|draft|review|procedure|compare|document|find_lawyer|other",
  "domain": "contract|criminal|civil|family|employment|ip|property|tax|constitutional|general",
  "complexity": "simple|medium|complex",
  "jurisdiction": "country or state if mentioned, else unspecified",
  "on_topic": true,
  "summary": "one short sentence describing the user's need",
  "refined_query": "clearer rewrite of the latest user question",
  "route_to": "assistant|researcher|draft|document_creator|lawyer_finder"
}

Routing rules:
- "question" about general legal info → "assistant"
- "question" needing deep case-law / statute research → "researcher"
- "draft" (write a notice, agreement, letter) → "draft"
- "document" (create, format, fill a legal document) → "document_creator"
- "review" (review / analyse a contract or document) → "researcher"
- "procedure" (step-by-step legal process) → "assistant"
- "compare" (compare laws, options) → "researcher"
- "find_lawyer" (user wants a lawyer / advocate) → "lawyer_finder"
- Anything else → "assistant"

No markdown, no extra text."""

# ── Intent → Agent routing map ──────────────────────────────────
INTENT_AGENT_MAP: dict[str, str] = {
    "question": "assistant",
    "draft": "draft",
    "review": "researcher",
    "procedure": "assistant",
    "compare": "researcher",
    "document": "document_creator",
    "find_lawyer": "lawyer_finder",
    "other": "assistant",
}


def _get_analyser_llm(api_key: str, model: str):
    """Cached LLM instance for the analyser."""
    cache_key = f"orch:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=0.1)
    return _llm_cache[cache_key]


def parse_analysis(text: str, fallback_query: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(0)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        analysis = DEFAULT_ANALYSIS.copy()
        analysis["refined_query"] = fallback_query
        analysis["summary"] = fallback_query[:160]
        analysis["route_to"] = "assistant"
        return analysis

    route = str(data.get("route_to") or "assistant")
    if route not in INTENT_AGENT_MAP.values():
        route = INTENT_AGENT_MAP.get(str(data.get("intent") or "question"), "assistant")

    return {
        "intent": str(data.get("intent") or "question"),
        "domain": str(data.get("domain") or "general"),
        "complexity": str(data.get("complexity") or "simple"),
        "jurisdiction": str(data.get("jurisdiction") or "unspecified"),
        "on_topic": bool(data.get("on_topic", True)),
        "summary": str(data.get("summary") or fallback_query)[:240],
        "refined_query": str(data.get("refined_query") or fallback_query),
        "route_to": route,
    }


def analyse_and_route(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Root agent node: analyse intent and decide routing."""
    # Retrieve api_key and model from config
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "llama-3.3-70b-versatile")

    user_text = latest_user_text(state.get("messages") or [])
    analyser = _get_analyser_llm(api_key, model)

    result = analyser.invoke(
        to_lc_messages((state.get("messages") or [])[-6:], ORCHESTRATOR_PROMPT),
        config=config,
    )

    analysis = parse_analysis(str(result.content or ""), user_text)
    route_to = analysis.get("route_to") or "assistant"

    return {
        "analysis": analysis,
        "routed_to": route_to,
        "active_agent": "orchestrator",
        "agent_metadata": {
            "orchestrator": {
                "intent": analysis["intent"],
                "route_to": route_to,
                "domain": analysis["domain"],
                "complexity": analysis["complexity"],
            }
        },
    }


def decide_route(state: AgentState) -> str:
    """Conditional-edge function used by LangGraph to pick the next node."""
    route = (state.get("routed_to") or "assistant").strip()
    # validate against known agents
    valid = {"assistant", "researcher", "draft", "document_creator", "lawyer_finder"}
    return route if route in valid else "assistant"


# Register in the global agent registry
register_agent(
    "orchestrator",
    description="Root agent — analyses intent and routes to specialist agents",
    handles=["*"],
)
