from __future__ import annotations

from typing import Any

from cre_rentroll import dynamo
from cre_rentroll.schemas import CanonicalRentRoll

SK = "VAULT#default"
SESSION_SK = "SESSION#default"


def save_roll(username: str, roll: CanonicalRentRoll) -> None:
    """Keep the parsed rent roll on this page visit. Dynamo is not the vault."""
    del username
    from cre_rentroll import turn_ctx
    from cre_rentroll.chat_memory import save_roll as save_mem

    save_mem(turn_ctx.chat_session(), roll)


def load_roll(username: str) -> CanonicalRentRoll | None:
    del username
    from cre_rentroll import turn_ctx
    from cre_rentroll.chat_memory import load_roll as load_mem

    return load_mem(turn_ctx.chat_session())


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


def clear_messages(username: str) -> None:
    """Drop the chat transcript. The rent roll and deal facts stay."""
    save_session(username, [])


def clear_visit(username: str) -> None:
    """Page load. The previous visit's chat, rent roll, and deal facts are gone."""
    save_session(username, [], facts={})
    dynamo.delete_item(username, SK)


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
