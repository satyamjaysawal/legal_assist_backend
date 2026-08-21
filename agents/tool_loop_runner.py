"""Agentic tool-calling runner — LangChain bind_tools + LangGraph loop.

This is THE reusable pattern of the project.  Every specialist agent
runs through ``run_agent_with_tools``:

    ┌────────────────────────────────────────────────────┐
    │  StateGraph                                        │
    │                                                    │
    │   START ──► agent (LLM with bind_tools)            │
    │               │                                    │
    │               ├── tool_calls? ──► tools (ToolNode) │
    │               │                        │           │
    │               │◄───────────────────────┘           │
    │               └── no tool_calls ──► END (reply)    │
    └────────────────────────────────────────────────────┘

The LLM decides *when* to call which tool with which arguments; the
ToolNode executes the @tool functions from agents/agent_tools.py and feeds
results back until the model writes its final answer.

Robustness:
- 429 / provider errors → retry with the FALLBACK_MODELS chain.
- Models without tool-calling support → degrade to plain invoke_text
  so the user still gets an answer.
- Tool exceptions inside the loop are returned to the model as error
  messages (handle_tool_errors=True) instead of crashing the graph.
"""

import json
import logging
import re
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agents.base import (
    FALLBACK_MODELS,
    get_llm,
    invoke_text,
    message_text,
    to_lc_messages,
)

logger = logging.getLogger("legal_assist.agentic")

MAX_TOOL_ITERATIONS = 4  # max model→tool round-trips per request


class ToolLoopState(TypedDict, total=False):
    """State for the agent ⇄ tools loop — messages accumulate via add_messages."""

    messages: Annotated[list, add_messages]


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _has_tool_calls(message: Any) -> bool:
    return bool(getattr(message, "tool_calls", None))


def _route_after_agent(state: ToolLoopState) -> str:
    messages = state.get("messages") or []
    if messages and _has_tool_calls(messages[-1]):
        return "tools"
    return END


def _build_tool_graph(llm_with_tools, tools):
    """Compile the agent ⇄ tools loop."""
    tool_node = ToolNode(tools, handle_tool_errors=True)

    def agent_node(state: ToolLoopState) -> dict[str, Any]:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(ToolLoopState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def _tool_trace(messages: list[Any]) -> list[dict[str, Any]]:
    """Extract a UI-friendly log of every tool call made in the loop."""
    trace: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            trace.append({
                "tool": msg.name or "tool",
                "ok": not str(msg.content or "").strip().startswith('{"error"'),
            })
    return trace


def _extract_payload(messages: list[Any], tool_name: str) -> dict[str, Any] | None:
    """Read the JSON payload returned by the LAST call to ``tool_name``.

    Parsed from the ToolMessage content rather than a thread-local, because
    ToolNode may execute tools on worker threads.
    """
    payload: dict[str, Any] | None = None
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == tool_name:
            try:
                data = json.loads(str(msg.content or ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and not data.get("error"):
                payload = data
    return payload


def run_agent_with_tools(
    system: str,
    raw_messages: list[dict[str, str]],
    tools: list[Any],
    config: dict[str, Any] | None,
    *,
    temperature: float = 0.4,
    max_iterations: int = MAX_TOOL_ITERATIONS,
) -> dict[str, Any]:
    """Run one agentic tool-loop turn.

    Returns {"reply", "tool_trace", "agentic", "model", "tool_payloads"}
    - agentic=True  → the reply came from the tool-calling loop
    - agentic=False → degraded to plain generation (no tool support /
      all tool models failed)
    - tool_payloads → {tool_name: parsed JSON of its last successful call}
    """
    cfg = (config or {}).get("configurable", {}) or {}
    api_key = cfg.get("api_key", "")
    model = cfg.get("model", "openai/gpt-oss-120b")

    lc_messages = to_lc_messages(raw_messages, system)

    # Models to try in order: primary first, then fallbacks (on 429 or
    # when the model refuses tool-calling).
    candidates = [model] + [m for m in FALLBACK_MODELS if m != model]
    last_error: Exception | None = None

    for candidate in candidates:
        try:
            llm = get_llm(api_key, candidate, temperature=temperature)
            try:
                llm_with_tools = llm.bind_tools(tools)
            except Exception as bind_exc:  # noqa: BLE001 — model has no tool schema support
                logger.warning("bind_tools failed for %s: %s", candidate, bind_exc)
                continue

            compiled = _build_tool_graph(llm_with_tools, tools)
            result = compiled.invoke(
                {"messages": lc_messages},
                config={"recursion_limit": 2 * max_iterations + 3},
            )
            final_messages = result.get("messages") or []
            # Final answer = last AI message without pending tool calls
            reply = ""
            for msg in reversed(final_messages):
                if isinstance(msg, AIMessage) and not _has_tool_calls(msg):
                    reply = _strip_think(message_text(msg))
                    if reply:
                        break
            if not reply:
                raise RuntimeError("Agent loop finished without a text reply")

            trace = _tool_trace(final_messages)
            payloads = {
                tool.name: _extract_payload(final_messages, tool.name)
                for tool in tools
            }
            logger.info(
                "Agentic run on %s — %d tool call(s): %s",
                candidate, len(trace), [t["tool"] for t in trace] or "-",
            )
            return {
                "reply": reply,
                "tool_trace": trace,
                "tool_payloads": payloads,
                "agentic": True,
                "model": candidate,
            }
        except Exception as exc:  # noqa: BLE001 — try next candidate model
            last_error = exc
            text = str(exc).lower()
            rate_limited = "429" in text or "rate_limit" in text or "rate limit" in text
            tools_rejected = "tool" in text and ("not supported" in text or "invalid" in text)
            logger.warning("Agentic run failed on %s: %s", candidate, exc)
            if not (rate_limited or tools_rejected):
                # Unexpected error — don't burn through all fallbacks
                break
            time.sleep(0.5)

    # ── Graceful degradation: plain generation without tools ─────
    logger.warning("Degrading to tool-free generation (%s)", last_error)
    fallback_llm = get_llm(api_key, model, temperature=temperature)
    reply = invoke_text(fallback_llm, lc_messages, config)
    return {"reply": reply, "tool_trace": [], "tool_payloads": {}, "agentic": False, "model": model}
