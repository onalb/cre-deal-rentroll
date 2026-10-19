from __future__ import annotations

import re
from typing import Any

OOS_TEXT = (
    "This agent answers questions about the property, the deal, or the area it "
    "sits in — not personal chat."
)

_REFUSE_MARK = "answers questions about the property, the deal, or the area it sits in"


def is_refuse_draft(text: str) -> bool:
    """True when the model drafted the generic property/deal/area refuse."""
    return _REFUSE_MARK in (text or "").lower()

NEED_FILE_TEXT = (
    "Please attach a rent-roll CSV or Excel file first. "
    "Once it is saved, I can answer follow-up questions about it."
)

# Tool-less first hop. Model replies IN_SCOPE or OOS; Python does not lexicon-match CRE.
_SCOPE_SYS = (
    "You decide whether the LAST user message is in scope for a CRE rent-roll agent. "
    "Reply with exactly one token: IN_SCOPE or OOS. No tools. No other words.\n"
    "Empty chat history, empty memory, no uploaded rent roll, and no prior turns "
    "do not make an in-scope question OOS.\n"
    "IN_SCOPE: questions about this property, the rent roll, the deal, or the "
    "area/market it sits in (area/market facts too). If FACT#/vault/memory lack "
    "the answer, it is still IN_SCOPE — the later hop should plan web_search. "
    "A zip or city in the message is enough for search; 'in that area' uses "
    "prior geocode/address. Short follow-ups stay "
    "IN_SCOPE when prior turns supply the property. Short corrections about "
    "where the attached deal sits (wrong country or place on the last pin, "
    "search the map again, they already attached the roll) are IN_SCOPE locate "
    "for this property — not personal chat.\n"
    "Create / publish a memo, and proceed / yes / ok after a memo ask or memo "
    "plan, are IN_SCOPE (continue that memo) — not idle chat.\n"
    "Deal artifacts already in vault/session (saved memos, vault notes, a "
    "published memo) are IN_SCOPE for this property/deal — not idle chat.\n"
    "OOS only when the user is talking to the agent as a person or idle chatting "
    "(how are you, what's your name, hello, sports, recipes). "
    "Idle weekend weather is OOS; facts about the property's area or market stay IN_SCOPE."
)
_TOKEN_SPLIT = re.compile(r"[\s,.:;!?]+")


def parse_scope_token(raw: str) -> bool:
    """Honor the agent's token only. Unknown / empty → OOS (fail closed)."""
    text = (raw or "").strip().upper()
    if not text:
        return False
    token = _TOKEN_SPLIT.split(text, maxsplit=1)[0]
    if token in {"OOS", "OUT", "OUT_OF_SCOPE", "IRRELEVANT"}:
        return False
    return token in {"IN_SCOPE", "INSCOPE"}


def _scope_user(message: str, history: list[dict[str, Any]] | None) -> str:
    bits: list[str] = []
    for row in (history or [])[-4:]:
        role = str(row.get("role") or "").strip().lower()
        content = " ".join(str(row.get("content") or "").split())
        if not content or role not in {"user", "assistant"}:
            continue
        bits.append(f"{role}: {content[:240]}")
    last = (message or "").strip()
    prior = "\n".join(bits)
    if prior:
        return (
            f"Session (brief):\n{prior}\n\nLast user message:\n{last}\n\n"
            "Reply IN_SCOPE or OOS."
        )
    return f"Last user message:\n{last}\n\nReply IN_SCOPE or OOS."


def _scope_agent_token(message: str, history: list[dict[str, Any]] | None = None) -> str:
    """Same OpenAI agent, no tool specs — cannot MCP. Tests patch this."""
    from openai import OpenAI

    from cre_rentroll.settings import openai_classify_model, openai_client_args

    client = OpenAI(**openai_client_args())
    resp = client.chat.completions.create(
        model=openai_classify_model(),
        messages=[
            {"role": "system", "content": _SCOPE_SYS},
            {"role": "user", "content": _scope_user(message, history)},
        ],
        temperature=0,
        max_tokens=8,
    )
    try:
        from cre_rentroll.turn_ctx import add_llm_from_response

        add_llm_from_response("scope", resp)
    except Exception:
        pass
    return (resp.choices[0].message.content or "").strip()


def classify_scope(
    message: str, history: list[dict[str, Any]] | None = None
) -> bool:
    """True when the tool-less agent said IN_SCOPE. Python does not classify CRE."""
    text = (message or "").strip()
    if not text:
        return False
    try:
        raw = _scope_agent_token(text, history)
    except Exception as exc:
        err = str(exc).lower()
        if "429" in err or "rate limit" in err or "rate_limit" in err:
            raise
        return False
    return parse_scope_token(raw)


def in_scope(message: str, history: list[dict[str, Any]] | None = None) -> bool:
    """Back-compat alias: agent's IN_SCOPE/OOS token, not a keyword list."""
    return classify_scope(message, history)
