import json
import re
from typing import Any, Iterator, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph

ANALYZER_PROMPT = """You are a legal query analyser for a legal AI assistant.
Read the latest user message (and short chat context) and return ONLY valid JSON:

{
  "intent": "question|draft|review|procedure|compare|other",
  "domain": "contract|criminal|civil|family|employment|ip|property|tax|constitutional|general",
  "complexity": "simple|medium|complex",
  "jurisdiction": "country or state if mentioned, else unspecified",
  "on_topic": true,
  "summary": "one short sentence",
  "refined_query": "clearer rewrite of the latest user question"
}

on_topic is true only if the query is about law, legal process, rights, or documents.
No markdown, no extra text."""

ANSWER_PROMPT = """You are a legal AI assistant. Give clear, practical answers.
You are not a lawyer and this is not formal legal advice.

A query analyser already classified the latest user message. Use that to focus the answer:
- Match the intent (explain, draft, review, or outline steps).
- Stay in the detected legal domain.
- If jurisdiction is specified, prefer that legal system; otherwise say it may vary.
- If on_topic is false, answer briefly and offer to help with a legal question.
- Keep the tone direct. Mention this is not formal legal advice when the topic is serious."""


class QueryAnalysis(TypedDict):
    intent: str
    domain: str
    complexity: str
    jurisdiction: str
    on_topic: bool
    summary: str
    refined_query: str


class AgentState(TypedDict):
    messages: list[dict[str, str]]
    analysis: Optional[QueryAnalysis]
    reply: str


DEFAULT_ANALYSIS: QueryAnalysis = {
    "intent": "question",
    "domain": "general",
    "complexity": "simple",
    "jurisdiction": "unspecified",
    "on_topic": True,
    "summary": "Legal question",
    "refined_query": "",
}


def get_llm(api_key: str, model: str, temperature: float = 0.4) -> ChatGroq:
    return ChatGroq(
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=1024,
        streaming=True,
    )


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


def parse_analysis(text: str, fallback_query: str) -> QueryAnalysis:
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
        return analysis

    return {
        "intent": str(data.get("intent") or "question"),
        "domain": str(data.get("domain") or "general"),
        "complexity": str(data.get("complexity") or "simple"),
        "jurisdiction": str(data.get("jurisdiction") or "unspecified"),
        "on_topic": bool(data.get("on_topic", True)),
        "summary": str(data.get("summary") or fallback_query)[:240],
        "refined_query": str(data.get("refined_query") or fallback_query),
    }


def latest_user_text(messages: list[dict[str, str]]) -> str:
    for item in reversed(messages):
        if item.get("role") == "user" and (item.get("content") or "").strip():
            return item["content"].strip()
    return ""


def analysis_context(analysis: QueryAnalysis) -> str:
    return (
        "\n\nQuery analysis:\n"
        f"- intent: {analysis['intent']}\n"
        f"- domain: {analysis['domain']}\n"
        f"- complexity: {analysis['complexity']}\n"
        f"- jurisdiction: {analysis['jurisdiction']}\n"
        f"- on_topic: {analysis['on_topic']}\n"
        f"- summary: {analysis['summary']}\n"
        f"- refined_query: {analysis['refined_query']}"
    )


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


def build_graph(api_key: str, model: str):
    analyser = get_llm(api_key, model, temperature=0.1)
    writer = get_llm(api_key, model, temperature=0.4)

    def analyse(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        user_text = latest_user_text(state["messages"])
        result = analyser.invoke(
            to_lc_messages(state["messages"][-6:], ANALYZER_PROMPT),
            config=config,
        )
        return {"analysis": parse_analysis(str(result.content or ""), user_text)}

    def generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
        analysis = state.get("analysis") or DEFAULT_ANALYSIS
        result = writer.invoke(
            to_lc_messages(state["messages"], ANSWER_PROMPT + analysis_context(analysis)),
            config=config,
        )
        return {"reply": str(result.content or "").strip()}

    graph = StateGraph(AgentState)
    graph.add_node("analyse", analyse)
    graph.add_node("generate", generate)
    graph.add_edge(START, "analyse")
    graph.add_edge("analyse", "generate")
    graph.add_edge("generate", END)
    return graph.compile()


def stream_graph(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
) -> Iterator[dict[str, Any]]:
    """Yield app events from LangGraph `stream(version='v2')`.

    Uses `stream_mode=['updates', 'messages']`:
    - updates → analyse node output
    - messages → generate-node LLM tokens
    """
    compiled = build_graph(api_key, model)
    reply_parts: list[str] = []

    for part in compiled.stream(
        {"messages": messages, "analysis": None, "reply": ""},
        stream_mode=["updates", "messages"],
        version="v2",
    ):
        kind, data = unpack_stream_part(part)
        if kind == "updates" and isinstance(data, dict):
            node_out = data.get("analyse") or {}
            if isinstance(node_out, dict) and node_out.get("analysis"):
                yield {"type": "analysis", "analysis": node_out["analysis"], "model": model}
        elif kind == "messages" and isinstance(data, tuple) and len(data) == 2:
            message, metadata = data
            if metadata.get("langgraph_node") != "generate":
                continue
            text = message_text(message)
            if text:
                reply_parts.append(text)
                yield {"type": "token", "content": text}

    if not "".join(reply_parts).strip():
        yield {"type": "error", "detail": "Groq returned an empty reply"}
        return
    yield {"type": "done", "model": model}
