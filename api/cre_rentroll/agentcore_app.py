"""AgentCore Runtime HTTP contract: POST /invocations, GET /ping.

Same Strands harness as FastAPI. Set AGENTCORE_INSIDE=1 so this process never
invokes itself. App Runner (without that flag) calls invoke_agent_runtime.

JSON Accept still returns one body at the end. text/event-stream fans each
RUN event as SSE so InvokeAgentRuntime can flush logs before the outcome.
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from cre_rentroll import __version__, harness, run_events
from cre_rentroll.schemas import ChatOutcome

app = FastAPI(title="CRE Deal Rentroll AgentCore", version=__version__)


class InvocationBody(BaseModel):
    prompt: str | None = None
    username: str | None = None
    has_attachment: bool = False
    ack_prefix: str = ""
    session_id: str = ""
    roll: dict[str, Any] | None = None
    facts: dict[str, Any] | None = None
    messages: list[dict[str, Any]] | None = None
    input: dict[str, Any] | None = None


def parse_payload(body: dict[str, Any] | InvocationBody) -> dict[str, Any]:
    raw = body.model_dump() if isinstance(body, InvocationBody) else dict(body)
    nested = raw.get("input") if isinstance(raw.get("input"), dict) else {}
    username = (raw.get("username") or nested.get("username") or "").strip()
    prompt = (raw.get("prompt") or nested.get("prompt") or "").strip()
    if not username:
        raise HTTPException(400, "username required")
    if not prompt:
        raise HTTPException(400, "prompt required")
    return {
        "username": username,
        "prompt": prompt,
        "has_attachment": bool(raw.get("has_attachment") or nested.get("has_attachment")),
        "ack_prefix": str(raw.get("ack_prefix") or nested.get("ack_prefix") or ""),
        "session_id": str(raw.get("session_id") or nested.get("session_id") or ""),
        "roll": raw.get("roll") if isinstance(raw.get("roll"), dict) else nested.get("roll"),
        "facts": raw.get("facts") if isinstance(raw.get("facts"), dict) else nested.get("facts"),
        "messages": raw.get("messages") if isinstance(raw.get("messages"), list) else nested.get("messages"),
    }


def _sse_data(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, default=str)}\n\n".encode()


def _seed_visit(args: dict[str, Any]) -> None:
    """Copy the page-session vault into this process before the turn."""
    from cre_rentroll import turn_ctx
    from cre_rentroll.chat_memory import save as save_chat
    from cre_rentroll.chat_memory import save_facts, save_roll
    from cre_rentroll.schemas import CanonicalRentRoll

    sid = str(args.get("session_id") or "")
    turn_ctx.set_chat_session(sid)
    raw = args.get("roll")
    if isinstance(raw, dict):
        save_roll(sid, CanonicalRentRoll.model_validate(raw))
    facts = args.get("facts")
    if isinstance(facts, dict) and facts:
        save_facts(sid, facts)
    messages = args.get("messages")
    if isinstance(messages, list):
        save_chat(sid, [row for row in messages if isinstance(row, dict)])


def _run_task(args: dict[str, Any], headers: Any) -> ChatOutcome:
    from cre_rentroll.agentcore_platform import bind_workload_token, note_runtime

    bind_workload_token(headers)
    note_runtime()
    _seed_visit(args)
    return harness.run_task(
        args["username"],
        args["prompt"],
        has_attachment=args["has_attachment"],
        ack_prefix=args.get("ack_prefix") or "",
        session_id=args.get("session_id") or "",
    )


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "Healthy"}


@app.post("/invocations")
def invocations(body: InvocationBody, request: Request) -> Any:
    args = parse_payload(body)
    accept = (request.headers.get("accept") or "").lower()
    want_sse = "text/event-stream" in accept
    header_map = {k: v for k, v in request.headers.items()}

    if not want_sse:
        logs: list[dict[str, Any]] = []
        token = run_events.bind(logs.append)
        try:
            outcome = _run_task(args, header_map)
        finally:
            run_events.reset(token)
        return {"outcome": outcome.model_dump(), "logs": logs, "host": "agentcore"}

    q: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def sink(ev: dict[str, Any]) -> None:
        q.put(("log", ev))

    def work() -> None:
        token = run_events.bind(sink)
        try:
            outcome: ChatOutcome = _run_task(args, header_map)
            q.put(("done", {"outcome": outcome.model_dump(), "host": "agentcore"}))
        except HTTPException as exc:
            q.put(("error", {"error": str(exc.detail), "status": exc.status_code}))
        except Exception as exc:
            q.put(("error", {"error": f"{type(exc).__name__}: {exc}"}))
        finally:
            run_events.reset(token)
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            kind, payload = item
            if kind == "log":
                yield _sse_data({"log": payload})
            else:
                yield _sse_data(payload)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
