from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from cre_rentroll.settings import openai_api_key
from cre_rentroll import dynamo, harness, hitl, run_events, turn_ctx, vault
from cre_rentroll.memory import retrieve_facts


@asynccontextmanager
async def lifespan(_app: FastAPI):
    dynamo.ensure_table()
    yield


def _cors_origins() -> list[str]:
    raw = (os.environ.get("CORS_ORIGINS") or "http://localhost:8080,http://127.0.0.1:8080").strip()
    return [o.strip() for o in raw.split(",") if o.strip()]


app = FastAPI(title="CRE Deal Rentroll", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginBody(BaseModel):
    username: str


class HitlBody(BaseModel):
    allow: bool


def _user(x_username: str | None) -> str:
    if not x_username or not x_username.strip():
        raise HTTPException(401, "X-Username required")
    return x_username.strip()


@app.get("/api/health")
def health() -> dict[str, Any]:
    key = bool(openai_api_key())
    arn = (os.environ.get("AGENTCORE_RUNTIME_ARN") or "").strip()
    inside = os.environ.get("AGENTCORE_INSIDE", "").strip() in {"1", "true", "yes"}
    return {
        "ok": True,
        "openai_key_present": key,
        "ddb_endpoint": os.environ.get("DDB_ENDPOINT") or "",
        "table": os.environ.get("DDB_TABLE", "ml_interview"),
        "agent_host": "agentcore" if arn and not inside else "in_process",
        "agentcore_configured": bool(arn),
        "agentcore_boxes": {
            "runtime": bool(arn),
            "memory": bool(os.environ.get("AGENTCORE_MEMORY_ID")),
            "gateway": bool(os.environ.get("AGENTCORE_GATEWAY_URL")),
            "harness": bool(os.environ.get("AGENTCORE_HARNESS_ID")),
            "identity": bool(os.environ.get("AGENTCORE_WORKLOAD_NAME")),
            "evaluations": bool(os.environ.get("AGENTCORE_EVALUATOR_ID")),
            "registry": bool(os.environ.get("AGENTCORE_REGISTRY_ID")),
            "policy": bool(os.environ.get("AGENTCORE_POLICY_ENGINE_ID")),
            "code_interpreter": bool(os.environ.get("AGENTCORE_CODE_INTERPRETER_ID")),
            "browser": bool(os.environ.get("AGENTCORE_BROWSER_ID")),
            "observability": os.environ.get("AGENT_OBSERVABILITY_ENABLED", "").lower() in {"1", "true", "yes"},
        },
    }


@app.post("/api/login")
def login(body: LoginBody) -> dict[str, Any]:
    try:
        profile = dynamo.login(body.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    sess = vault.load_session(profile["username"])
    return {**profile, "pending_hitl": sess.get("pending_hitl")}


@app.get("/api/session")
def session(x_username: str | None = Header(default=None)) -> dict[str, Any]:
    user = _user(x_username)
    sess = vault.load_session(user)
    roll = vault.load_roll(user)
    return {
        "username": user,
        "messages": sess.get("messages"),
        "pending_hitl": sess.get("pending_hitl"),
        "facts": retrieve_facts(user),
        "memos": vault.list_memos(user),
        "vault": None
        if roll is None
        else {
            "property_name": roll.property_name,
            "unit_count": roll.unit_count,
            "as_of": roll.as_of,
            "geo_confidence": roll.geo_confidence,
            "layout": roll.layout,
        },
    }


@app.get("/api/traces")
def traces(x_username: str | None = Header(default=None)) -> dict[str, Any]:
    user = _user(x_username)
    items = dynamo.query_prefix(user, "TRACE#")
    return {"traces": items[-25:]}


def _decorate_outcomes(user: str, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    last = dict(outcomes[-1])
    sess = vault.load_session(user)
    last["history"] = sess.get("messages")
    last["all"] = list(outcomes)
    last["facts"] = retrieve_facts(user)
    last["memos"] = vault.list_memos(user)
    roll = vault.load_roll(user)
    last["vault"] = (
        None
        if roll is None
        else {
            "property_name": roll.property_name,
            "unit_count": roll.unit_count,
            "geo_confidence": roll.geo_confidence,
        }
    )
    last["pending_hitl"] = last.get("pending_hitl") or sess.get("pending_hitl")
    return last


def _run_chat_sync(
    user: str,
    text: str,
    fail: bool,
    attached: bool,
    filename: str | None,
    raw: bytes | None,
) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    if filename and raw is not None:
        if len(raw) > 8_000_000:
            raise HTTPException(413, "file too large")
        # File-only ingest still prints RUN to CloudWatch; compact UI waits for a question Submit.
        if text:
            outcomes.append(harness.ingest_attachment(user, filename, raw).model_dump())
        else:
            with run_events.cloudwatch_only():
                outcomes.append(harness.ingest_attachment(user, filename, raw).model_dump())
    if text:
        outcomes.append(
            harness.run_task(user, text, search_fail=fail, has_attachment=attached).model_dump()
        )
    if not outcomes:
        raise HTTPException(400, "attach a rent roll or type a message")
    return _decorate_outcomes(user, outcomes)


def _sse_pack(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@app.post("/api/chat")
async def chat(
    request: Request,
    message: str = Form(default=""),
    search_fail: bool = Form(default=False),
    file: UploadFile | None = File(default=None),
    x_username: str | None = Header(default=None),
):
    user = _user(x_username)
    raw = None
    filename = None
    if file is not None and file.filename:
        filename = file.filename
        raw = await file.read()
        if len(raw) > 8_000_000:
            raise HTTPException(413, "file too large")
    text = (message or "").strip()
    fail = bool(search_fail)
    attached = bool(filename)
    want_sse = "text/event-stream" in (request.headers.get("accept") or "")

    collected: list[dict[str, Any]] = []

    def sink(ev: dict[str, Any]) -> None:
        collected.append(ev)
        if want_sse:
            q.put(("log", ev))

    if not want_sse:
        token = run_events.bind(sink)
        try:
            last = _run_chat_sync(user, text, fail, attached, filename, raw)
        finally:
            run_events.reset(token)
        last["logs"] = collected
        if last.get("stop_reason") == "openai_rate_limited":
            return JSONResponse(status_code=429, content=last)
        return last

    q: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()

    def work() -> None:
        token = run_events.bind(sink)
        try:
            last = _run_chat_sync(user, text, fail, attached, filename, raw)
            last["logs"] = collected
            q.put(("done", last))
        except HTTPException as exc:
            q.put(("error", {"message": str(exc.detail), "status": exc.status_code}))
        except Exception as exc:
            q.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            run_events.reset(token)
            q.put(None)

    threading.Thread(target=work, daemon=True).start()

    async def gen():
        while True:
            item = await asyncio.to_thread(q.get)
            if item is None:
                break
            event, payload = item
            yield _sse_pack(event, payload)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/hitl")
def hitl_resolve(body: HitlBody, x_username: str | None = Header(default=None)) -> dict[str, Any]:
    user = _user(x_username)
    sess = vault.load_session(user)
    msgs = list(sess.get("messages") or [])
    turn_ctx.reset_turn(user, hitl_allow=body.allow)
    result = hitl.resolve(user, body.allow, msgs)
    kind = "hitl_denied" if result["status"] == "denied" else "answer"
    msgs.append({"role": "assistant", "content": result["text"], "kind": kind})
    vault.save_session(user, msgs, pending_hitl=None)
    return {**result, "kind": kind, "history": msgs, "memos": vault.list_memos(user)}
