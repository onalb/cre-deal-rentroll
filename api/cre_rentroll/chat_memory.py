"""This page visit. A refresh uses a new id, so the old chat, rent roll, and deal facts are gone.

None of those are written to Dynamo.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

_LOCK = Lock()
_CHATS: dict[str, list[dict[str, Any]]] = {}
_ROLLS: dict[str, dict[str, Any]] = {}
_FACTS: dict[str, dict[str, Any]] = {}
_LIMIT = 80


def load(session_id: str) -> list[dict[str, Any]]:
    key = (session_id or "").strip()
    if not key:
        return []
    with _LOCK:
        return list(_CHATS.get(key) or [])


def save(session_id: str, messages: list[dict[str, Any]]) -> None:
    key = (session_id or "").strip()
    if not key:
        return
    with _LOCK:
        _CHATS[key] = list(messages)[-_LIMIT:]


def load_roll(session_id: str):
    from cre_rentroll.schemas import CanonicalRentRoll

    key = (session_id or "").strip()
    if not key:
        return None
    with _LOCK:
        raw = _ROLLS.get(key)
    if not isinstance(raw, dict):
        return None
    return CanonicalRentRoll.model_validate(raw)


def save_roll(session_id: str, roll) -> None:
    key = (session_id or "").strip()
    if not key:
        return
    with _LOCK:
        _ROLLS[key] = roll.model_dump(mode="json")


def load_facts(session_id: str) -> dict[str, Any]:
    key = (session_id or "").strip()
    if not key:
        return {}
    with _LOCK:
        return dict(_FACTS.get(key) or {})


def save_facts(session_id: str, facts: dict[str, Any]) -> None:
    key = (session_id or "").strip()
    if not key:
        return
    with _LOCK:
        _FACTS[key] = dict(facts)
