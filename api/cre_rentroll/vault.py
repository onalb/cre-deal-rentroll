from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cre_rentroll import dynamo
from cre_rentroll.schemas import CanonicalRentRoll

SK = "VAULT#default"
SESSION_SK = "SESSION#default"
MEMO_SK_PREFIX = "MEMO#"


def save_roll(username: str, roll: CanonicalRentRoll) -> None:
    dynamo.put_item(username, SK, {"roll": roll.model_dump()})


def load_roll(username: str) -> CanonicalRentRoll | None:
    item = dynamo.get_item(username, SK)
    if not item or "roll" not in item:
        return None
    return CanonicalRentRoll.model_validate(item["roll"])


def load_session(username: str) -> dict[str, Any]:
    item = dynamo.get_item(username, SESSION_SK) or {}
    return {
        "messages": item.get("messages") or [],
        "pending_hitl": item.get("pending_hitl"),
        "eval_retry_used": bool(item.get("eval_retry_used", False)),
    }


def save_session(username: str, messages: list[dict[str, Any]], **extra: Any) -> None:
    payload: dict[str, Any] = {"messages": messages[-80:], **extra}
    dynamo.put_item(username, SESSION_SK, payload)


def vault_spans(roll: CanonicalRentRoll) -> list[str]:
    return roll.vault_spans()


def save_memo(username: str, title: str, body: str, ts: str | None = None) -> dict[str, Any]:
    """Persist an approved HITL memo. SK is MEMO#{ts} on USER#{username}."""
    ts = ts or datetime.now(timezone.utc).isoformat()
    item = {"title": title, "body": body, "ts": ts}
    dynamo.put_item(username, f"{MEMO_SK_PREFIX}{ts}", item)
    return item


def list_memos(username: str) -> list[dict[str, Any]]:
    rows = dynamo.query_prefix(username, MEMO_SK_PREFIX)
    out: list[dict[str, Any]] = []
    for it in rows:
        ts = it.get("ts") or str(it.get("sk") or "").removeprefix(MEMO_SK_PREFIX)
        out.append({"title": it.get("title") or "", "body": it.get("body") or "", "ts": ts})
    return out


def memos_prompt(username: str) -> str:
    memos = list_memos(username)
    if not memos:
        return (
            "Published memos (Dynamo MEMO# on this user): none. "
            "No memo is published until HITL Approve. Do not treat chat history "
            "as a vault memo."
        )
    blocks = []
    for m in memos:
        blocks.append(f"- MEMO#{m['ts']} title={m['title']}\n{m['body']}")
    return (
        "Published memos (Dynamo MEMO# on this user) — answer recall asks only "
        "from these rows, not from prior chat:\n" + "\n".join(blocks)
    )
