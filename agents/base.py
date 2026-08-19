"""Base types, shared state definition, and agent registry."""

import time
from typing import Any, Callable, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq


# ── Shared agent state ──────────────────────────────────────────
class AgentState(TypedDict, total=False):
    messages: list[dict[str, str]]
    analysis: dict[str, Any]
    reply: str
    memory_notes: str
    rag_notes: str
    user_profile: str
    episodic_notes: str
    procedural_notes: str
    active_agent: str
    routed_to: str
    agent_metadata: dict[str, Any]
    user_role: str
    connectors_available: list[str]


# ── Default analysis (fallback) ─────────────────────────────────
DEFAULT_ANALYSIS: dict[str, Any] = {
    "intent": "question",
    "domain": "general",
    "complexity": "simple",
    "jurisdiction": "unspecified",
    "on_topic": True,
    "summary": "Legal question",
    "refined_query": "",
}

# ── Agent registry ──────────────────────────────────────────────
AGENT_REGISTRY: dict[str, dict[str, Any]] = {}


def register_agent(name: str, *, description: str, handles: list[str]) -> None:
    """Register an agent so the orchestrator can discover it."""
    AGENT_REGISTRY[name] = {
        "description": description,
        "handles": handles,  # intent list this agent handles
    }


def get_agent(name: str) -> dict[str, Any] | None:
    return AGENT_REGISTRY.get(name)


def list_agents() -> list[dict[str, Any]]:
    return [{"name": k, **v} for k, v in AGENT_REGISTRY.items()]


# ── LLM helper ──────────────────────────────────────────────────
def get_llm(api_key: str, model: str, temperature: float = 0.4) -> ChatGroq:
    return ChatGroq(
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=2048,
        streaming=True,
    )


# ── Message conversion ──────────────────────────────────────────
def to_lc_messages(raw: list[dict[str, str]], system: str) -> list:
    converted: list = [SystemMessage(content=system)]
    for item in raw:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        if item.get("role") == "assistant":
            converted.append(AIMessage(content=content))
        else:
            converted.append(HumanMessage(content=content))
    return converted


def latest_user_text(messages: list[dict[str, str]]) -> str:
    for item in reversed(messages):
        if item.get("role") == "user" and (item.get("content") or "").strip():
            return item["content"].strip()
    return ""


def message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def unpack_stream_part(part: Any) -> tuple[str | None, Any]:
    if isinstance(part, dict) and part.get("type"):
        return part.get("type"), part.get("data")
    if isinstance(part, tuple) and len(part) == 2:
        return part[0], part[1]
    return None, None


def invoke_text(llm, messages, config: Optional[dict] = None, retries: int = 3) -> str:
    """Invoke an LLM with retry and robust text extraction.

    Retries on exceptions or empty content (Groq occasionally returns
    empty/reasoning-only payloads).  On rate-limit (429) errors it
    automatically switches to a fallback model so the demo keeps
    working even when the primary model's daily quota is exhausted.
    Raises the last error if all attempts fail.
    """
    last_error: Exception | None = None
    current = llm
    for attempt in range(retries + 1):
        try:
            result = current.invoke(messages, config=config) if config else current.invoke(messages)
            text = message_text(result).strip()
            if text:
                return text
            last_error = RuntimeError("LLM returned empty content")
        except Exception as exc:  # noqa: BLE001 — retry on any provider error
            last_error = exc
            if _is_provider_rate_limit(exc) and config:
                cfg = config.get("configurable", {}) or {}
                api_key = cfg.get("api_key", "")
                current_model = getattr(current, "model_name", "") or ""
                for fallback in FALLBACK_MODELS:
                    if fallback != current_model:
                        temp = float(getattr(current, "temperature", 0.4) or 0.4)
                        current = get_llm(api_key, fallback, temperature=temp)
                        break
        if attempt < retries:
            time.sleep(1.0 * (attempt + 1))
    raise last_error or RuntimeError("LLM returned empty content")


# Models tried in order when the primary model is rate-limited (429)
FALLBACK_MODELS = ["openai/gpt-oss-20b", "qwen/qwen3.6-27b", "groq/compound-mini"]


def _is_provider_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate_limit" in text or "rate limit" in text
