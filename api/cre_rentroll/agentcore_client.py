"""App Runner → AgentCore Runtime. No-op unless AGENTCORE_RUNTIME_ARN is set.

Prefer text/event-stream so RUN lines fan into App Runner SSE as the runtime
flushes them. Fall back to one JSON body when the runtime does not stream.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from cre_rentroll.run_events import emit, replay
from cre_rentroll.schemas import ChatOutcome


def runtime_arn() -> str:
    return (os.environ.get("AGENTCORE_RUNTIME_ARN") or "").strip()


def inside_runtime() -> bool:
    return os.environ.get("AGENTCORE_INSIDE", "").strip() in {"1", "true", "yes"}


def should_invoke_remote() -> bool:
    return bool(runtime_arn()) and not inside_runtime()


def session_id(username: str) -> str:
    slug = re.sub(r"[^a-z0-9-]", "-", (username or "anon").strip().lower())[:24]
    digest = hashlib.sha256(slug.encode()).hexdigest()
    sid = f"cre-rr-{slug}-{digest}"
    return sid[:64] if len(sid) >= 33 else (sid + "0" * 33)[:33]


def _parse_sse_block(block: str) -> dict[str, Any] | None:
    data_lines: list[str] = []
    for line in block.split("\n"):
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not data_lines:
        return None
    try:
        data = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def iter_sse_events(body: Any) -> Iterator[dict[str, Any]]:
    """Yield JSON objects from an SSE (or raw JSON) streaming body."""
    buf = ""

    def feed(piece: str) -> Iterator[dict[str, Any]]:
        nonlocal buf
        buf += piece
        if "\n\n" not in buf and buf.lstrip().startswith("{") and buf.rstrip().endswith("}"):
            try:
                data = json.loads(buf)
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict):
                buf = ""
                yield data
                return
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            parsed = _parse_sse_block(block)
            if parsed is not None:
                yield parsed

    chunks: Iterator[Any]
    if hasattr(body, "iter_chunks"):
        chunks = body.iter_chunks(chunk_size=256)
    elif hasattr(body, "iter_lines"):
        def _lines() -> Iterator[bytes]:
            for line in body.iter_lines(chunk_size=256):
                if isinstance(line, bytes):
                    yield line + b"\n"
                elif line:
                    yield (str(line) + "\n").encode()

        chunks = _lines()
    else:
        raw = body.read() if hasattr(body, "read") else body
        chunks = iter([raw])

    for chunk in chunks:
        if not chunk:
            continue
        if isinstance(chunk, bytes):
            piece = chunk.decode("utf-8", errors="replace")
        else:
            piece = str(chunk)
        yield from feed(piece)
    if buf.strip():
        parsed = _parse_sse_block(buf) if buf.lstrip().startswith("data:") else None
        if parsed is not None:
            yield parsed
            return
        try:
            data = json.loads(buf)
        except json.JSONDecodeError:
            return
        if isinstance(data, dict):
            yield data


def apply_runtime_event(data: dict[str, Any]) -> ChatOutcome | None:
    """Fan log events into the App Runner SSE sink; return an outcome if present."""
    if isinstance(data.get("log"), dict):
        replay([data["log"]])
    elif data.get("logs"):
        replay(data.get("logs") or [])
    err = data.get("error")
    blob = data
    nested = data.get("output")
    if isinstance(nested, dict) and ("outcome" in nested or "kind" in nested):
        blob = nested
    outcome = blob.get("outcome") if isinstance(blob.get("outcome"), dict) else None
    if outcome is None and isinstance(blob.get("kind"), str) and "text" in blob:
        outcome = blob
    if outcome is not None:
        return ChatOutcome.model_validate(outcome)
    if err and outcome is None and not data.get("log") and not data.get("logs"):
        emit("error", f"AgentCore stream: {err}")
        return ChatOutcome(
            kind="error",
            text="The AgentCore runtime could not run that turn. Check CloudWatch for AgentCore logs, then send again.",
            stop_reason="agentcore_invoke_failed",
        )
    return None


def invoke_run_task(
    username: str,
    message: str,
    *,
    has_attachment: bool = False,
    chip_id: str = "",
) -> ChatOutcome:
    arn = runtime_arn()
    client = boto3.client(
        "bedrock-agentcore",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        config=Config(read_timeout=120, retries={"max_attempts": 3, "mode": "standard"}),
    )
    payload = json.dumps(
        {
            "username": username,
            "prompt": message,
            "has_attachment": has_attachment,
            "chip_id": chip_id,
        }
    ).encode()
    try:
        resp = client.invoke_agent_runtime(
            agentRuntimeArn=arn,
            runtimeSessionId=session_id(username),
            runtimeUserId=(username or "anon")[:128],
            payload=payload,
            qualifier="DEFAULT",
            contentType="application/json",
            accept="text/event-stream",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", type(exc).__name__)
        detail = exc.response.get("Error", {}).get("Message", str(exc))
        emit("error", f"AgentCore {code}: {detail}")
        return ChatOutcome(
            kind="error",
            text="The AgentCore runtime could not run that turn. Check CloudWatch for AgentCore logs, then send again.",
            stop_reason="agentcore_invoke_failed",
        )
    status = resp.get("statusCode")
    if status not in (None, 200):
        emit("error", f"AgentCore HTTP {status}")
        return ChatOutcome(
            kind="error",
            text="The AgentCore runtime could not run that turn. Check CloudWatch for AgentCore logs, then send again.",
            stop_reason="agentcore_invoke_failed",
        )
    body = resp.get("response")
    ctype = str(resp.get("contentType") or "").lower()
    outcome: ChatOutcome | None = None
    if "event-stream" in ctype:
        for event in iter_sse_events(body):
            got = apply_runtime_event(event)
            if got is not None:
                outcome = got
    else:
        raw = body.read() if hasattr(body, "read") else body
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data: dict[str, Any] = json.loads(raw or "{}")
        if isinstance(data.get("output"), dict) and "outcome" in data["output"]:
            data = data["output"]
        outcome = apply_runtime_event(data)
        if outcome is None and isinstance(data.get("outcome") or data, dict):
            try:
                outcome = ChatOutcome.model_validate(data.get("outcome") or data)
            except Exception:
                outcome = None
    if outcome is None:
        emit("error", "AgentCore returned no outcome")
        return ChatOutcome(
            kind="error",
            text="The AgentCore runtime could not run that turn. Check CloudWatch for AgentCore logs, then send again.",
            stop_reason="agentcore_invoke_failed",
        )
    return outcome
