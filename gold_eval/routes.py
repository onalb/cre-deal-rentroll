"""HTTP routes for the gold eval runner."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from gold_eval.runner import MAX_RUNS, case_prompts, iter_gold

router = APIRouter()


class EvalBody(BaseModel):
    runs: int = 3


def _user(x_username: str | None) -> str:
    if not x_username or not x_username.strip():
        raise HTTPException(401, "X-Username required")
    return x_username.strip()


def _sse_pack(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.get("/api/eval/cases")
def eval_cases(x_username: str | None = Header(default=None)) -> list[dict[str, str]]:
    _user(x_username)
    return case_prompts()


@router.post("/api/eval/run")
async def eval_run(body: EvalBody, x_username: str | None = Header(default=None)):
    user = _user(x_username)
    if body.runs < 1 or body.runs > MAX_RUNS:
        raise HTTPException(400, f"runs must be from 1 to {MAX_RUNS}")
    q: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def work() -> None:
        try:
            for event in iter_gold(user, body.runs):
                q.put(event)
        except Exception as exc:
            q.put({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    async def gen():
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            yield _sse_pack(str(item.get("event") or "status"), item)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
