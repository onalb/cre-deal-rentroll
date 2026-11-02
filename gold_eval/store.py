"""One saved gold-eval result per login user. A new finish replaces it."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

SK = "GOLD#latest"
_MAX_JSON = 300_000


def _public_log(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [{k: v for k, v in row.items() if k != "summary"} for row in log]
    raw = json.dumps(rows, default=str)
    if len(raw) <= _MAX_JSON:
        return rows
    slim: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        copy.pop("trace", None)
        slim.append(copy)
    return slim


def save_result(
    username: str,
    *,
    runs: int,
    status: str,
    summary: list[dict[str, Any]],
    log: list[dict[str, Any]],
) -> None:
    from cre_rentroll import dynamo

    dynamo.put_item(
        username,
        SK,
        {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "runs": int(runs),
            "status": status or "",
            "summary": summary or [],
            "log": _public_log(log),
        },
    )


def load_result(username: str) -> dict[str, Any] | None:
    from cre_rentroll import dynamo

    item = dynamo.get_item(username, SK)
    if not item:
        return None
    return {
        "saved_at": item.get("saved_at") or "",
        "runs": item.get("runs") or 0,
        "status": item.get("status") or "",
        "summary": item.get("summary") or [],
        "log": item.get("log") or [],
    }
