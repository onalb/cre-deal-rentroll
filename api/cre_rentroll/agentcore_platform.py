"""Live calls into AgentCore product boxes. Fail-soft when IDs/APIs are missing."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse

from cre_rentroll.run_events import emit

REGION = os.environ.get("AWS_REGION", "us-east-1")
GATEWAY_TOOL_NAMES = frozenset(
    {
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
        "osm_geocode",
        "web_search",
    }
)
# emit() prefixes → CloudWatch "RUN agentcore_* …"
BOX_STEPS = (
    "agentcore_runtime",
    "agentcore_harness",
    "agentcore_identity",
    "agentcore_memory",
    "agentcore_gateway",
    "agentcore_tools_fastmcp",
    "agentcore_policy",
    "agentcore_builtin",
    "agentcore_skills",
    "agentcore_eval",
    "agentcore_observability",
    "agentcore_registry",
    "agentcore_payments",
)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def enabled() -> bool:
    return os.environ.get("AGENTCORE_INSIDE", "").strip() in {"1", "true", "yes"}


# Runtime injects this on /invocations. Service-linked workload identities cannot mint it.
_WORKLOAD_HEADER_KEYS = (
    "WorkloadAccessToken",
    "workloadaccesstoken",
    "x-amzn-bedrock-agentcore-runtime-workload-accesstoken",
)


def _header_pairs(headers: Mapping[str, str] | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if headers is None:
        return pairs
    raw = getattr(headers, "raw", None) or getattr(headers, "scope", None)
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                k, v = item[0], item[1]
                if isinstance(k, (bytes, bytearray)):
                    k = k.decode("latin-1", "replace")
                if isinstance(v, (bytes, bytearray)):
                    v = v.decode("latin-1", "replace")
                pairs.append((str(k), str(v)))
    getter = getattr(headers, "items", None)
    if callable(getter):
        try:
            for key, val in headers.items():
                pairs.append((str(key), str(val)))
        except Exception:
            pass
    return pairs


def bind_workload_token(headers: Mapping[str, str] | Any | None) -> str:
    """Capture the Runtime-linked WorkloadAccessToken. Never call GetWorkloadAccessToken*."""
    token = ""
    names: list[str] = []
    if headers is not None and hasattr(headers, "scope") and hasattr(headers, "headers"):
        try:
            for k, v in headers.scope.get("headers") or []:
                if isinstance(k, (bytes, bytearray)):
                    k = k.decode("latin-1", "replace")
                if isinstance(v, (bytes, bytearray)):
                    v = v.decode("latin-1", "replace")
                names.append(str(k).lower())
                lk = str(k).lower()
                if lk in {
                    "workloadaccesstoken",
                    "x-amzn-bedrock-agentcore-runtime-workload-accesstoken",
                } and v:
                    token = str(v).strip()
                elif lk == "authorization" and v and not str(v).startswith("AWS4-HMAC-SHA256"):
                    token = str(v).removeprefix("Bearer ").strip()
        except Exception:
            pass
        headers = headers.headers
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            for key in _WORKLOAD_HEADER_KEYS:
                val = getter(key)
                if val:
                    token = str(val).strip()
                    break
            auth = getter("Authorization") or getter("authorization")
            if not token and auth and not str(auth).startswith("AWS4-HMAC-SHA256"):
                token = str(auth).removeprefix("Bearer ").strip()
        for key, val in _header_pairs(headers):
            names.append(key.lower())
            lk = key.lower()
            if token:
                continue
            if lk in {
                "workloadaccesstoken",
                "x-amzn-bedrock-agentcore-runtime-workload-accesstoken",
            } and val:
                token = str(val).strip()
            elif lk == "authorization" and val and not str(val).startswith("AWS4-HMAC-SHA256"):
                token = str(val).removeprefix("Bearer ").strip()
    uniq = ",".join(sorted(set(names)))[:400]
    os.environ["AGENTCORE_INVOCATION_HEADER_KEYS"] = uniq
    if token:
        os.environ["AGENTCORE_WORKLOAD_TOKEN"] = token
    return token


def _client(service: str):
    import boto3
    from botocore.config import Config

    return boto3.client(
        service,
        region_name=_env("AWS_REGION") or REGION,
        config=Config(read_timeout=20, connect_timeout=5, retries={"max_attempts": 2, "mode": "standard"}),
    )


def _emit_box(box: str, status: str, detail: str = "") -> None:
    """One CloudWatch line: RUN agentcore_<box> skipped|failed|success …"""
    if status in {"skipped", "failed"}:
        bit = " ".join(f"{status} {detail}".split())
    else:
        bit = " ".join(f"success {detail or status}".split())
    emit(f"agentcore_{box}", bit[:1400])


def _exc_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:350]


def _is_access_denied(exc: BaseException) -> bool:
    text = _exc_text(exc)
    return "AccessDenied" in text or "UnrecognizedClient" in text


def _safe(box: str, fn, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        if _is_access_denied(exc):
            _emit_box(box, "skipped", _exc_text(exc))
        else:
            _emit_box(box, "failed", _exc_text(exc))
        return None


def note_runtime() -> None:
    if not enabled():
        return
    _emit_box("runtime", "ok", "POST /invocations on AgentCore Runtime")


def note_harness_react() -> None:
    if not enabled():
        return
    _emit_box("harness", "ok", "Strands ReAct loop running")


def note_disk_skills(names: tuple[str, ...], found: bool) -> None:
    if not enabled():
        return
    listed = ", ".join(names) if names else "(none)"
    if found:
        _emit_box("skills", "ok", f"disk SKILL.md {listed} (registry not used)")
    else:
        _emit_box("skills", "skipped", f"no SKILL.md on disk for {listed}")


def note_fastmcp_fallback(name: str) -> None:
    if not enabled():
        return
    _emit_box("tools_fastmcp", "ok", f"in-process FastMCP after Gateway miss ({name})")


def begin_turn(username: str) -> dict[str, Any]:
    """Touch every wired AgentCore product on the invoke path. Never raises."""
    used: dict[str, Any] = {}
    if not enabled():
        return used
    _emit_box("payments", "skipped", "not provisioned")
    used["identity"] = _safe("identity", _identity, username)

    jobs = {
        "harness": lambda: _safe("harness", _harness_bind),
        "memory": lambda: _safe("memory", _memory_retrieve, username),
        "gateway": lambda: _safe("gateway", _gateway_ping, username),
        "builtin": lambda: _pulse_builtin(),
        "registry": lambda: _safe("registry", _registry_search),
        "policy": lambda: _safe("policy", _policy_status),
    }
    # Sequential so ContextVar RUN sink and stdout stay on the invoke thread.
    for key, fn in jobs.items():
        used[key] = fn()
    otel = _env("AGENT_OBSERVABILITY_ENABLED").lower() in {"1", "true", "yes"}
    used["observability"] = {"otel": otel}
    if otel:
        distro = _env("OTEL_PYTHON_DISTRO") or "unset"
        _emit_box("observability", "ok", f"OTEL enabled distro={distro} (stdout RUN only; no extra exporter call)")
    else:
        _emit_box("observability", "skipped", "AGENT_OBSERVABILITY_ENABLED off")
    return used


def end_turn(
    username: str,
    *,
    user_text: str,
    assistant_text: str,
    traces: list[dict[str, Any]],
    scores: dict[str, Any] | None,
) -> None:
    if not enabled():
        return
    _safe("memory", _memory_write_turn, username, user_text, assistant_text, traces)
    _safe("eval", _evaluate_turn, user_text, assistant_text, traces, scores)


def facts_from_memory(username: str) -> list[str]:
    if not enabled() or not _env("AGENTCORE_MEMORY_ID"):
        return []
    recs = _safe("memory", lambda: _memory_retrieve(username, announce=False)) or []
    lines: list[str] = []
    for rec in recs:
        content = rec.get("content") or {}
        text = content.get("text") if isinstance(content, dict) else None
        if text:
            lines.append(str(text)[:400])
    return lines


def write_fact_record(username: str, slot: str, value: Any) -> None:
    if not enabled() or not _env("AGENTCORE_MEMORY_ID"):
        return
    _safe("memory", _memory_put_fact, username, slot, value)


def gateway_call(name: str, arguments: dict[str, Any]) -> str | None:
    if not enabled() or name not in GATEWAY_TOOL_NAMES or not _env("AGENTCORE_GATEWAY_URL"):
        return None
    payload = dict(arguments)
    from cre_rentroll import turn_ctx

    payload.setdefault("username", turn_ctx.username() or "anon")
    result = _safe("gateway", _mcp_tools_call, name, payload)
    if result is None:
        return None
    if isinstance(result, str):
        return result
    return json.dumps(result, default=str)


def load_registry_skills(names: tuple[str, ...]) -> str:
    if not enabled() or not _env("AGENTCORE_REGISTRY_ID"):
        return ""
    chunks: list[str] = []
    for name in names:
        recs = _safe("registry", _search_skill, name) or []
        for rec in recs:
            desc = rec.get("descriptors") or rec.get("description") or ""
            text = ""
            if isinstance(desc, dict):
                skill = ((desc.get("agentSkills") or {}).get("skillMd") or {}).get("inlineContent")
                text = skill or json.dumps(desc)[:4000]
            elif isinstance(desc, str):
                text = desc
            if text:
                chunks.append(f"### skill:{name}\n{text}")
                break
    joined = "\n\n".join(chunks)
    if joined.strip():
        _emit_box("skills", "ok", f"registry records {', '.join(names)}")
    return joined


def _identity(username: str) -> dict[str, Any] | None:
    """Prefer Runtime-injected WAT. IAM invoke does not inject it, so mint a user-created identity."""
    runtime_name = _env("AGENTCORE_WORKLOAD_NAME") or "cre_deal_rentroll-Nh63z6GXmK"
    ident_name = _env("AGENTCORE_IDENTITY_NAME") or runtime_name
    token = _env("AGENTCORE_WORKLOAD_TOKEN")
    if token:
        _emit_box("identity", "ok", f"Runtime-injected WorkloadAccessToken name={runtime_name}")
        return {"workload": runtime_name, "token": True, "source": "runtime_header"}
    if ident_name == runtime_name or ident_name.endswith(runtime_name):
        keys = _env("AGENTCORE_INVOCATION_HEADER_KEYS") or "none"
        _emit_box(
            "identity",
            "failed",
            f"no WorkloadAccessToken header keys={keys} (service-linked; set AGENTCORE_IDENTITY_NAME)",
        )
        return None
    client = _client("bedrock-agentcore")
    resp = client.get_workload_access_token_for_user_id(
        workloadName=ident_name,
        userId=(username or "anon")[:128],
    )
    minted = (resp.get("workloadAccessToken") or resp.get("token") or "").strip()
    if not minted:
        raise RuntimeError("GetWorkloadAccessTokenForUserId returned empty token")
    os.environ["AGENTCORE_WORKLOAD_TOKEN"] = minted
    _emit_box("identity", "ok", f"minted WorkloadAccessToken identity={ident_name} user={(username or 'anon')[:32]}")
    return {"workload": ident_name, "token": True, "source": "mint_user_id"}


def _harness_bind() -> dict[str, Any] | None:
    hid = _env("AGENTCORE_HARNESS_ID")
    if not hid:
        _emit_box("harness", "skipped", "no AGENTCORE_HARNESS_ID")
        return None
    client = _client("bedrock-agentcore-control")
    resp = client.get_harness(harnessId=hid)
    tools = resp.get("tools") or resp.get("allowedTools") or []
    n = len(tools) if isinstance(tools, list) else 0
    status = resp.get("status") or "unknown"
    _emit_box("harness", "ok", f"GetHarness {hid} status={status} tools={n}")
    return {"harnessId": hid, "status": status, "toolCount": n}


def _memory_retrieve(username: str, announce: bool = True) -> list[dict[str, Any]]:
    mid = _env("AGENTCORE_MEMORY_ID")
    if not mid:
        if announce:
            _emit_box("memory", "skipped", "no AGENTCORE_MEMORY_ID")
        return []
    client = _client("bedrock-agentcore")
    actor = (username or "anon")[:128]
    ns = f"/cre/{actor}/facts"
    recs: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    ltm_err = ""
    try:
        resp = client.retrieve_memory_records(
            memoryId=mid,
            namespace=ns,
            searchCriteria={"searchQuery": "rent roll facts occupancy income", "topK": 8},
        )
        recs = list(resp.get("memoryRecordSummaries") or resp.get("memoryRecords") or [])
    except Exception as exc:
        ltm_err = _exc_text(exc)
        try:
            resp = client.list_memory_records(memoryId=mid, namespace=ns, maxResults=8)
            recs = list(resp.get("memoryRecordSummaries") or resp.get("memoryRecords") or [])
            ltm_err = ""
        except Exception as exc2:
            ltm_err = _exc_text(exc2)
    sid = f"cre-{actor}"[:50]
    try:
        ev = client.list_events(memoryId=mid, actorId=actor, sessionId=sid, maxResults=8)
        events = list(ev.get("events") or ev.get("memoryEvents") or [])
    except Exception:
        events = []
    snippets = _memory_snippets(recs, events)
    if announce:
        extra = f" ltm_err={ltm_err}" if ltm_err and not recs else ""
        _emit_box(
            "memory",
            "ok",
            f"retrieved LTM={len(recs)} STM_events={len(events)} ns={ns}{extra}",
        )
        if snippets:
            _emit_box(
                "memory",
                "ok",
                "payload to model (auto-retrieve, not an LLM tool): " + " || ".join(snippets),
            )
        else:
            _emit_box("memory", "ok", "payload empty — counts only; prior chat may still be in session_prompt")
    return recs or events


def _memory_snippets(recs: list[dict[str, Any]], events: list[dict[str, Any]], *, limit: int = 6) -> list[str]:
    out: list[str] = []
    for rec in recs:
        text = _blob_text(rec)
        if text:
            out.append("LTM:" + text[:220])
        if len(out) >= limit:
            return out
    for ev in events:
        text = _blob_text(ev)
        if text:
            out.append("STM:" + text[:220])
        if len(out) >= limit:
            break
    return out


def _blob_text(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return " ".join(obj.split()).strip()
    if isinstance(obj, dict):
        for key in ("text", "value", "memoryContent"):
            got = _blob_text(obj.get(key))
            if got:
                return got
        content = obj.get("content")
        if content is not None:
            got = _blob_text(content)
            if got:
                return got
        conv = obj.get("conversational")
        if conv is not None:
            got = _blob_text(conv)
            if got:
                return got
        payload = obj.get("payload")
        if isinstance(payload, list):
            parts = [_blob_text(p) for p in payload]
            return " | ".join(p for p in parts if p)
    if isinstance(obj, list):
        return " | ".join(p for p in (_blob_text(x) for x in obj) if p)
    return ""


def _memory_put_fact(username: str, slot: str, value: Any) -> None:
    mid = _env("AGENTCORE_MEMORY_ID")
    client = _client("bedrock-agentcore")
    client.batch_create_memory_records(
        memoryId=mid,
        records=[
            {
                "requestIdentifier": str(uuid.uuid4()),
                "namespaces": [f"/cre/{username}/facts"],
                "content": {"text": f"{slot}={value}"},
                "timestamp": datetime.now(timezone.utc),
            }
        ],
    )


def _memory_write_turn(
    username: str, user_text: str, assistant_text: str, traces: list[dict[str, Any]]
) -> None:
    mid = _env("AGENTCORE_MEMORY_ID")
    if not mid:
        _emit_box("memory", "skipped", "write skipped no AGENTCORE_MEMORY_ID")
        return
    client = _client("bedrock-agentcore")
    actor = (username or "anon")[:128]
    sid = f"cre-{actor}"[:50]
    tools = ",".join(str(t.get("name") or "") for t in traces if t.get("name"))
    user_blob = (user_text or "")[:1500]
    asst_blob = (assistant_text or "")[:1500]
    if tools:
        asst_blob = (asst_blob + f"\n[tools:{tools}]")[:1500]
    payload = [
        {"conversational": {"content": {"text": user_blob}, "role": "USER"}},
        {"conversational": {"content": {"text": asst_blob}, "role": "ASSISTANT"}},
    ]
    client.create_event(
        memoryId=mid,
        actorId=actor,
        sessionId=sid,
        eventTimestamp=datetime.now(timezone.utc),
        payload=payload,
    )
    ltm = 0
    try:
        client.batch_create_memory_records(
            memoryId=mid,
            records=[
                {
                    "requestIdentifier": str(uuid.uuid4()),
                    "namespaces": [f"/cre/{actor}/facts"],
                    "content": {"text": f"turn user={user_blob[:400]} assistant={asst_blob[:400]}"},
                    "timestamp": datetime.now(timezone.utc),
                }
            ],
        )
        ltm = 1
    except Exception:
        ltm = 0
    _emit_box("memory", "ok", f"wrote conversational STM event session={sid} LTM={ltm}")


def _mcp_headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-03-26",
    }
    token = _env("AGENTCORE_WORKLOAD_TOKEN")
    if token:
        headers["WorkloadAccessToken"] = token
    return headers


def _sign_and_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    import boto3
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session = boto3.Session(region_name=_env("AWS_REGION") or REGION)
    creds = session.get_credentials()
    if creds is None:
        raise RuntimeError("no AWS credentials for Gateway")
    data = json.dumps(body).encode("utf-8")
    headers = _mcp_headers()
    request = AWSRequest(method="POST", url=url, data=data, headers=headers)
    frozen = creds.get_frozen_credentials()
    SigV4Auth(frozen, "bedrock-agentcore", session.region_name or REGION).add_auth(request)
    prepared = request.prepare()
    import httpx

    resp = httpx.post(url, content=prepared.body, headers=dict(prepared.headers), timeout=25.0)
    if resp.status_code >= 400:
        raise RuntimeError(f"Gateway HTTP {resp.status_code}: {resp.text[:300]}")
    text = (resp.text or "").strip()
    if not text:
        return {}
    if text.startswith("event:") or "data:" in text.splitlines()[0]:
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
    if not text:
        return {}
    return json.loads(text)


def _gateway_url() -> str:
    url = _env("AGENTCORE_GATEWAY_URL")
    if url and not url.rstrip("/").endswith("mcp"):
        parsed = urlparse(url)
        if parsed.path in {"", "/"}:
            url = url.rstrip("/") + "/mcp"
    return url


_listed_gateway_tools: list[str] | None = None
_mcp_initialized = False


def _gateway_tool_names(tools: list[Any]) -> list[str]:
    names: list[str] = []
    for item in tools:
        if isinstance(item, dict):
            n = str(item.get("name") or "")
        else:
            n = str(item)
        if n:
            names.append(n)
    return names


def _resolve_gateway_tool(name: str, listed: list[str]) -> str:
    if name in listed:
        return name
    for n in listed:
        if n.endswith(f"___{name}") or n.endswith(f"_{name}") or n.split("/")[-1] == name:
            return n
    return name


def _ensure_mcp_session() -> None:
    global _mcp_initialized
    if _mcp_initialized:
        return
    url = _gateway_url()
    try:
        _sign_and_post(
            url,
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cre-deal-rentroll", "version": "0.1.0"},
                },
            },
        )
        _sign_and_post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    except Exception:
        pass
    _mcp_initialized = True


def _mcp_tools_list() -> list[Any]:
    url = _gateway_url()
    _ensure_mcp_session()
    data = _sign_and_post(
        url,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    result = data.get("result") or data
    tools = result.get("tools") or data.get("tools") or []
    if isinstance(tools, dict):
        tools = list(tools.values())
    return list(tools)


def _mcp_tools_call(name: str, arguments: dict[str, Any]) -> Any:
    url = _gateway_url()
    _ensure_mcp_session()
    global _listed_gateway_tools
    listed = _listed_gateway_tools
    if listed is None:
        listed = _gateway_tool_names(_mcp_tools_list())
        _listed_gateway_tools = listed
    remote = _resolve_gateway_tool(name, listed or [f"cretools___{name}", name])
    data = _sign_and_post(
        url,
        {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": remote, "arguments": arguments},
        },
    )
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:300])
    result = data.get("result") or {}
    content = result.get("content")
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict) and first.get("text"):
            return first["text"]
    return result.get("structuredContent") or result


def _gateway_ping(username: str) -> dict[str, Any] | None:
    if not _env("AGENTCORE_GATEWAY_URL"):
        _emit_box("gateway", "skipped", "no AGENTCORE_GATEWAY_URL")
        return None
    global _listed_gateway_tools
    tools = _mcp_tools_list()
    listed = _gateway_tool_names(tools)
    _listed_gateway_tools = listed
    ping = _mcp_tools_call("cre_ping", {"username": username})
    n = len(listed)
    sample = ",".join(listed[:8])
    _emit_box("gateway", "ok", f"MCP tools/list+cre_ping tools={n} names={sample}")
    return {"tools": n, "ping": ping, "names": listed}


def _code_interpreter_pulse() -> dict[str, Any] | None:
    ident = _env("AGENTCORE_CODE_INTERPRETER_ID")
    if not ident:
        return {"status": "skipped", "detail": "no AGENTCORE_CODE_INTERPRETER_ID"}
    client = _client("bedrock-agentcore")
    started = client.start_code_interpreter_session(
        codeInterpreterIdentifier=ident,
        name=f"cre-{int(time.time())}"[:48],
        sessionTimeoutSeconds=60,
    )
    sid = started.get("sessionId") or started.get("codeInterpreterSessionId")
    if not sid:
        return {"status": "failed", "detail": "code interpreter start returned no session id"}
    try:
        client.stop_code_interpreter_session(
            codeInterpreterIdentifier=ident,
            sessionId=sid,
        )
    except Exception:
        try:
            client.stop_code_interpreter_session(sessionId=sid)
        except Exception:
            pass
    return {"status": "ok", "detail": "code interpreter session pulsed", "session": True}


def _browser_pulse() -> dict[str, Any] | None:
    ident = _env("AGENTCORE_BROWSER_ID")
    if not ident:
        return {"status": "skipped", "detail": "no AGENTCORE_BROWSER_ID"}
    client = _client("bedrock-agentcore")
    started = client.start_browser_session(
        browserIdentifier=ident,
        name=f"cre-{int(time.time())}"[:48],
        sessionTimeoutSeconds=60,
    )
    sid = started.get("sessionId") or started.get("browserSessionId")
    if not sid:
        return {"status": "failed", "detail": "browser start returned no session id"}
    try:
        client.stop_browser_session(browserIdentifier=ident, sessionId=sid)
    except Exception:
        try:
            client.stop_browser_session(sessionId=sid)
        except Exception:
            pass
    return {"status": "ok", "detail": "browser session pulsed", "session": True}


def _pulse_builtin() -> dict[str, Any]:
    parts: list[str] = []
    any_ok = False
    any_fail = False
    for label, fn in (("code_interpreter", _code_interpreter_pulse), ("browser", _browser_pulse)):
        try:
            rec = fn() or {"status": "skipped", "detail": "no result"}
        except Exception as exc:
            any_fail = True
            parts.append(f"{label} failed {_exc_text(exc)}")
            continue
        status = str(rec.get("status") or "skipped")
        detail = str(rec.get("detail") or "")
        if status == "ok":
            any_ok = True
            parts.append(detail or f"{label} pulsed")
        elif status == "failed":
            any_fail = True
            parts.append(f"{label} failed {detail}".strip())
        else:
            parts.append(f"{label} skipped {detail}".strip())
    joined = "; ".join(parts)
    if any_ok:
        _emit_box("builtin", "ok", joined)
    elif any_fail:
        _emit_box("builtin", "failed", joined)
    else:
        _emit_box("builtin", "skipped", joined)
    return {"ok": any_ok, "detail": joined}


def _registry_search() -> dict[str, Any] | None:
    rid = _env("AGENTCORE_REGISTRY_ID")
    if not rid:
        _emit_box("registry", "skipped", "AccessDenied / no registry id; skills on disk")
        return None
    client = _client("bedrock-agentcore")
    resp = client.search_registry_records(
        searchQuery="rent-roll-abstract citation-policy underwriting",
        maxResults=5,
        registryIds=[rid],
    )
    recs = resp.get("records") or resp.get("registryRecords") or []
    _emit_box("registry", "ok", f"search_registry_records hits={len(recs)}")
    return {"hits": len(recs)}


def _search_skill(name: str) -> list[dict[str, Any]]:
    rid = _env("AGENTCORE_REGISTRY_ID")
    client = _client("bedrock-agentcore")
    kwargs: dict[str, Any] = {"searchQuery": name, "maxResults": 3}
    if rid:
        kwargs["registryIds"] = [rid]
    resp = client.search_registry_records(**kwargs)
    return list(resp.get("records") or resp.get("registryRecords") or [])


def _policy_status() -> dict[str, Any] | None:
    eid = _env("AGENTCORE_POLICY_ENGINE_ID")
    pid = _env("AGENTCORE_POLICY_ID")
    if not eid:
        _emit_box("policy", "skipped", "no AGENTCORE_POLICY_ENGINE_ID")
        return None
    client = _client("bedrock-agentcore-control")
    engine = client.get_policy_engine(policyEngineId=eid)
    policy = None
    if pid:
        try:
            policy = client.get_policy(policyEngineId=eid, policyId=pid)
        except Exception:
            policy = client.get_policy(policyId=pid)
    est = engine.get("status") or engine.get("name") or eid
    pst = (policy or {}).get("status") or pid or "unset"
    _emit_box("policy", "ok", f"GetPolicyEngine {eid} status={est} policy={pst} mode=LOG_ONLY")
    return {
        "engine": est,
        "policy": pst,
    }


def _evaluate_turn(
    user_text: str,
    assistant_text: str,
    traces: list[dict[str, Any]],
    scores: dict[str, Any] | None,
) -> dict[str, Any] | None:
    judge = None if not scores else scores.get("judge")
    if not scores or judge in {None, "", "not_run"}:
        _emit_box("eval", "skipped", "score_gcr not run")
        return None
    evaluator = _env("AGENTCORE_EVALUATOR_ID")
    if not evaluator:
        _emit_box("eval", "skipped", "no AGENTCORE_EVALUATOR_ID")
        return None
    client = _client("bedrock-agentcore")
    span_id = uuid.uuid4().hex[:16]
    trace_id = uuid.uuid4().hex[:32]
    session_id = f"cre-eval-{int(time.time())}"
    start_ns = str(time.time_ns())
    end_ns = str(time.time_ns() + 1_000_000)
    now = datetime.now(timezone.utc).isoformat()
    tool_names = [str(t.get("name") or "") for t in traces if t.get("name")]
    attrs: dict[str, Any] = {
        "input.value": (user_text or "")[:500],
        "output.value": (assistant_text or "")[:500],
        "gen_ai.tool.names": ",".join(tool_names),
        "session.id": session_id,
    }
    if scores:
        if scores.get("G") is not None:
            attrs["cre.g"] = scores.get("G")
        if scores.get("request_C") is not None:
            attrs["cre.request_c"] = scores.get("request_C")
        if scores.get("R") is not None:
            attrs["cre.r"] = scores.get("R")
    span = {
        "name": "execute_event_loop_cycle",
        "kind": "SPAN_KIND_INTERNAL",
        "context": {"trace_id": trace_id, "span_id": span_id, "trace_state": "[]"},
        "parent_id": "",
        "start_time": now,
        "end_time": now,
        "startTimeUnixNano": start_ns,
        "endTimeUnixNano": end_ns,
        "status": {"status_code": "UNSET", "code": "STATUS_CODE_OK"},
        "attributes": attrs,
        "events": [],
        "links": [],
        "resource": {"attributes": {"service.name": "strands-agents"}},
        "instrumentation_scope": {"name": "strands.telemetry.tracer", "version": "1.0.0"},
        "instrumentationScope": {"name": "strands.telemetry.tracer", "version": "1.0.0"},
        "scope": {"name": "strands.telemetry.tracer"},
    }
    # Builtin.Faithfulness is TRACE-level: do not pass evaluationTarget.spanIds.
    resp = client.evaluate(
        evaluatorId=evaluator,
        evaluationInput={"sessionSpans": [span]},
    )
    if not resp:
        raise RuntimeError(f"evaluate {evaluator} empty response")
    err = resp.get("error") or resp.get("message")
    if err:
        raise RuntimeError(str(err)[:300])
    _emit_box("eval", "ok", f"evaluate {evaluator} after score_gcr")
    return {"evaluator": evaluator, "result": True}
