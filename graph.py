import json
import re
from typing import Any, Iterator, Optional, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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

    analysis: QueryAnalysis = {
        "intent": str(data.get("intent") or "question"),
        "domain": str(data.get("domain") or "general"),
        "complexity": str(data.get("complexity") or "simple"),
        "jurisdiction": str(data.get("jurisdiction") or "unspecified"),
        "on_topic": bool(data.get("on_topic", True)),
        "summary": str(data.get("summary") or fallback_query)[:240],
        "refined_query": str(data.get("refined_query") or fallback_query),
    }
    return analysis


def latest_user_text(messages: list[dict[str, str]]) -> str:
    for item in reversed(messages):
        if item.get("role") == "user" and (item.get("content") or "").strip():
            return item["content"].strip()
    return ""


def analyse_query(messages: list[dict[str, str]], api_key: str, model: str) -> QueryAnalysis:
    user_text = latest_user_text(messages)
    llm = get_llm(api_key, model, temperature=0.1)
    result = llm.invoke(to_lc_messages(messages[-6:], ANALYZER_PROMPT))
    return parse_analysis(str(result.content or ""), user_text)


def stream_answer(
    messages: list[dict[str, str]],
    analysis: QueryAnalysis,
    api_key: str,
    model: str,
) -> Iterator[str]:
    context = (
        f"\n\nQuery analysis:\n"
        f"- intent: {analysis['intent']}\n"
        f"- domain: {analysis['domain']}\n"
        f"- complexity: {analysis['complexity']}\n"
        f"- jurisdiction: {analysis['jurisdiction']}\n"
        f"- on_topic: {analysis['on_topic']}\n"
        f"- summary: {analysis['summary']}\n"
        f"- refined_query: {analysis['refined_query']}"
    )
    llm = get_llm(api_key, model, temperature=0.4)
    for chunk in llm.stream(to_lc_messages(messages, ANSWER_PROMPT + context)):
        text = chunk.content
        if text:
            yield text


def _analyse_node(state: AgentState, api_key: str, model: str) -> dict[str, Any]:
    return {"analysis": analyse_query(state["messages"], api_key, model)}


def _generate_node(state: AgentState, api_key: str, model: str) -> dict[str, Any]:
    analysis = state.get("analysis") or DEFAULT_ANALYSIS
    parts: list[str] = []
    for token in stream_answer(state["messages"], analysis, api_key, model):
        parts.append(token)
    return {"reply": "".join(parts).strip()}


def build_graph(api_key: str, model: str):
    graph = StateGraph(AgentState)
    graph.add_node("analyse", lambda state: _analyse_node(state, api_key, model))
    graph.add_node("generate", lambda state: _generate_node(state, api_key, model))
    graph.add_edge(START, "analyse")
    graph.add_edge("analyse", "generate")
    graph.add_edge("generate", END)
    return graph.compile()
