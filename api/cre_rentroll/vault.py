from __future__ import annotations

from typing import Any

from cre_rentroll import dynamo
from cre_rentroll.schemas import CanonicalRentRoll

SK = "VAULT#default"
SESSION_SK = "SESSION#default"


def save_roll(username: str, roll: CanonicalRentRoll) -> None:
    dynamo.put_item(username, SK, {"roll": roll.model_dump()})


def load_roll(username: str) -> CanonicalRentRoll | None:
    item = dynamo.get_item(username, SK)
    if not item or "roll" not in item:
        return None
    return CanonicalRentRoll.model_validate(item["roll"])


def load_session(username: str) -> dict[str, Any]:
    item = dynamo.get_item(username, SESSION_SK) or {}
    facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
    traces = item.get("traces") if isinstance(item.get("traces"), list) else []
    return {
        "messages": item.get("messages") or [],
        "eval_retry_used": bool(item.get("eval_retry_used", False)),
        "facts": facts,
        "traces": traces,
    }


def save_session(username: str, messages: list[dict[str, Any]], **extra: Any) -> None:
    """Replace the chat turns. Facts and traces stay unless ``extra`` sets them."""
    current = dynamo.get_item(username, SESSION_SK) or {}
    facts = current.get("facts") if isinstance(current.get("facts"), dict) else {}
    traces = current.get("traces") if isinstance(current.get("traces"), list) else []
    payload: dict[str, Any] = {
        "messages": messages[-80:],
        "facts": facts,
        "traces": traces,
        "eval_retry_used": bool(current.get("eval_retry_used", False)),
    }
    payload.update(extra)
    dynamo.put_item(username, SESSION_SK, payload)


def vault_spans(roll: CanonicalRentRoll) -> list[str]:
    return roll.vault_spans()
