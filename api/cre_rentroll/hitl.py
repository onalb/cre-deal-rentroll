from __future__ import annotations

from typing import Any

from cre_rentroll import turn_ctx, vault
from cre_rentroll.tools import publish_memo


def request_or_run(title: str, body: str, username: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """HITL: save session, then either pause for UI approval or publish."""
    pending = {"tool": "publish_memo", "title": title, "body": body}
    vault.save_session(username, messages, pending_hitl=pending, eval_retry_used=False)
    if turn_ctx.hitl_allowed():
        result = publish_memo(title, body)
        vault.save_session(username, messages, pending_hitl=None)
        return {"status": "published", "result": result}
    return {"status": "pending_hitl", "pending_hitl": pending}


def resolve(username: str, allow: bool, messages: list[dict[str, Any]]) -> dict[str, Any]:
    sess = vault.load_session(username)
    pending = sess.get("pending_hitl")
    if not pending:
        return {"status": "idle", "text": "Nothing is waiting for approval."}
    if not allow:
        vault.save_session(username, messages, pending_hitl=None)
        return {"status": "denied", "text": "The memo was not published."}
    turn_ctx.reset_turn(username, hitl_allow=True)
    result = publish_memo(pending.get("title") or "memo", pending.get("body") or "")
    vault.save_session(username, messages, pending_hitl=None)
    return {"status": "published", "text": "The memo was saved.", "result": result}
