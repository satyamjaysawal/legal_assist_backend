"""Email Agent — professional legal email composition.

Handles requests to write, compose, or format emails for legal purposes:
client communication, notices, demand letters via email, follow-ups, etc.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from agents.base import (
    AgentState,
    latest_user_text,
    register_agent,
    to_lc_messages,
)

_llm_cache: dict[str, Any] = {}

EMAIL_SYSTEM = """You are the Email Composition agent of a legal AI system.
You specialise in drafting professional, legally-appropriate emails.

You handle:
- Client communication emails (updates, instructions, responses)
- Legal notice emails (cease-and-desist, demand letters, formal notices)
- Follow-up and reminder emails for legal matters
- Email responses to legal queries from clients or counterparties
- Professional introductions and referral requests
- Meeting request / scheduling emails for legal consultations

Guidelines:
- Draft emails in a professional, clear, and concise tone.
- Use appropriate legal email conventions (formal salutation, clear subject line suggestion, professional sign-off).
- Structure the email as:
  **Subject:** [Suggested subject line]
  
  [Salutation],
  
  [Body — clear, professional, well-structured paragraphs]
  
  [Closing — appropriate sign-off]
  
  [Signature placeholder]
- Include a suggested subject line at the top.
- Use [PLACEHOLDER] markers for details the user hasn't provided.
- If the email involves legal content, add a disclaimer at the bottom.
- For demand/notice emails, ensure all legal elements are present (dates, reference numbers, deadlines).
- End with: "This email was drafted with AI assistance — please review before sending."

Important: The output should be clean email text that can be copied directly into an email client."""


def _get_llm(api_key: str, model: str):
    cache_key = f"email:{model}"
    if cache_key not in _llm_cache:
        from agents.base import get_llm
        _llm_cache[cache_key] = get_llm(api_key, model, temperature=0.3)
    return _llm_cache[cache_key]


def email_generate(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Compose a professional legal email."""
    api_key = config.get("configurable", {}).get("api_key", "")
    model = config.get("configurable", {}).get("model", "openai/gpt-oss-120b")

    analysis = state.get("analysis") or {}
    system = EMAIL_SYSTEM

    if analysis:
        system += (
            f"\n\nEmail request analysis:\n"
            f"- intent: {analysis.get('intent', 'email')}\n"
            f"- domain: {analysis.get('domain', 'general')}\n"
            f"- jurisdiction: {analysis.get('jurisdiction', 'unspecified')}\n"
            f"- summary: {analysis.get('summary', '')}"
        )

    # Inject user profile
    profile = (state.get("user_profile") or "").strip()
    if profile:
        system += "\n\nUser profile (use for signature/sender):\n" + profile

    # Inject episodic memory
    episodic = (state.get("episodic_notes") or "").strip()
    if episodic:
        system += "\n\nPast conversations:\n" + episodic

    # Inject procedural memory
    procedural = (state.get("procedural_notes") or "").strip()
    if procedural:
        system += "\n\nUser preferences (tone/format):\n" + procedural

    notes = (state.get("memory_notes") or "").strip()
    if notes:
        system += "\n\nUser context:\n" + notes

    rag = (state.get("rag_notes") or "").strip()
    if rag:
        system += (
            "\n\n=== REFERENCE DOCUMENTS (use for tone/context) ===\n" + rag
        )

    llm = _get_llm(api_key, model)
    result = llm.invoke(
        to_lc_messages(state.get("messages") or [], system),
        config=config,
    )
    reply = str(result.content or "").strip()

    return {
        "reply": reply,
        "active_agent": "email",
        "agent_metadata": {
            "email": {
                "model": model,
                "format": "email",
                "disclaimer": "AI-drafted email — review before sending.",
            }
        },
    }


register_agent(
    "email",
    description="Professional legal email composition — client emails, notices, follow-ups",
    handles=["email"],
)
