import json

from cre_rentroll.agentcore_app import InvocationBody, parse_payload
from cre_rentroll.agentcore_client import session_id, should_invoke_remote
from cre_rentroll.agentcore_platform import begin_turn, enabled, gateway_call
from cre_rentroll.memory import write_trace


def test_parse_payload_nested_and_flat():
    flat = parse_payload(InvocationBody(username="burak", prompt="occupancy?"))
    assert flat["username"] == "burak"
    assert flat["prompt"] == "occupancy?"
    nested = parse_payload(
        InvocationBody(input={"username": "burak", "prompt": "units", "ack_prefix": "parsed 164 units"})
    )
    assert nested["prompt"] == "units"
    assert nested["ack_prefix"] == "parsed 164 units"


def test_session_id_is_long_enough():
    sid = session_id("ab")
    assert len(sid) >= 33
    assert sid.startswith("cre-rr-")


def test_should_invoke_remote(monkeypatch):
    monkeypatch.delenv("AGENTCORE_RUNTIME_ARN", raising=False)
    monkeypatch.delenv("AGENTCORE_INSIDE", raising=False)
    assert should_invoke_remote() is False
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x")
    assert should_invoke_remote() is True
    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    assert should_invoke_remote() is False


def test_platform_disabled_without_runtime_flag(monkeypatch):
    monkeypatch.delenv("AGENTCORE_INSIDE", raising=False)
    assert enabled() is False
    assert begin_turn("burak") == {}
    assert gateway_call("query_units", {"occupancy": "Occupied"}) is None


def test_agent_query_units_posts_capabilities_not_agentcore(monkeypatch):
    """The agent calls tools.query_units. AgentCore gateway_call is not in that path."""
    from cre_rentroll.agents import agent_tool_impls
    from cre_rentroll.turn_ctx import reset_turn

    called = {"gateway": 0, "deal": None}

    def fake_gateway(name, args):
        called["gateway"] += 1
        return '{"ok": true, "via": "agentcore"}'

    def fake_deal(name, arguments, timeout=180.0):
        called["deal"] = (name, arguments)
        return json.dumps({"ok": True, "unit_count": 3})

    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    monkeypatch.setenv("AGENTCORE_GATEWAY_URL", "https://example.invalid/mcp")
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    monkeypatch.setattr("cre_rentroll.agentcore_platform.gateway_call", fake_gateway)
    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake_deal)
    reset_turn("ada")
    out = json.loads(agent_tool_impls()["query_units"](occupancy="Occupied"))
    assert out["ok"] is True
    assert out["unit_count"] == 3
    assert called["gateway"] == 0
    assert called["deal"][0] == "query_units"
    assert called["deal"][1]["username"] == "ada"
    assert called["deal"][1]["occupancy"] == "Occupied"


def test_save_session_keeps_facts_and_traces(monkeypatch):
    table: dict[tuple[str, str], dict] = {}

    def fake_get(username, sk):
        return table.get((username, sk))

    def fake_put(username, sk, attrs):
        table[(username, sk)] = {"pk": username, "sk": sk, **attrs}

    monkeypatch.setattr("cre_rentroll.dynamo.get_item", fake_get)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", fake_put)
    from cre_rentroll import vault

    vault.save_session("ada", [], facts={"unit_count": 4}, traces=[{"run_id": "t1"}])
    vault.save_session("ada", [{"role": "user", "content": "occupancy"}])
    loaded = vault.load_session("ada")
    assert loaded["messages"][0]["content"] == "occupancy"
    assert loaded["facts"]["unit_count"] == 4
    vault.clear_messages("ada")
    cleared = vault.load_session("ada")
    assert cleared["messages"] == []
    assert cleared["facts"]["unit_count"] == 4
    assert loaded["traces"][0]["run_id"] == "t1"
    assert "FACT#" not in table
    assert not any(sk.startswith("TRACE#") for _, sk in table)


def test_vault_lives_on_the_page_session(monkeypatch):
    puts: list[tuple] = []

    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *_a, **_k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **_k: puts.append(a))
    monkeypatch.setattr("cre_rentroll.dynamo.delete_item", lambda *_a, **_k: None)
    from cre_rentroll import turn_ctx, vault
    from cre_rentroll.main import session
    from cre_rentroll.memory import generate_from_roll, retrieve_facts
    from cre_rentroll.schemas import CanonicalRentRoll, UnitRow

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Chambers",
        units=[UnitRow(unit_id="1", in_place_rent=100, vacant=False)],
    )
    turn_ctx.set_chat_session("visit-a")
    try:
        vault.save_roll("ada", roll)
        generate_from_roll("ada", roll)
        loaded = vault.load_roll("ada")
        assert loaded is not None and loaded.property_name == "Chambers"
        assert retrieve_facts("ada")["property_name"] == "Chambers"
        assert puts == []
        turn_ctx.set_chat_session("visit-b")
        assert vault.load_roll("ada") is None
        assert retrieve_facts("ada") == {}
    finally:
        turn_ctx.set_chat_session(None)
    body = session("ada")
    assert body["vault"] is None
    assert body["facts"] == {}
    assert body["messages"] == []


def test_write_trace_preview_optional(monkeypatch):
    session = {"messages": [{"role": "user", "content": "q"}], "facts": {"unit_count": 3}, "traces": []}

    def fake_load(_username):
        return dict(session)

    def fake_save(_username, messages, **extra):
        session["messages"] = messages
        session.update(extra)

    monkeypatch.setattr("cre_rentroll.vault.load_session", fake_load)
    monkeypatch.setattr("cre_rentroll.vault.save_session", fake_save)
    write_trace("burak", run_id="abc", question="q", tools_called=["query_units"], scores={}, stop_reason="ok")
    assert session["traces"][0]["run_id"] == "abc"
    assert session["traces"][0]["answer_preview"] == ""
    assert session["facts"]["unit_count"] == 3
    assert session["messages"][0]["content"] == "q"


def test_agentcore_box_prefixes_are_stable():
    from cre_rentroll.agentcore_platform import BOX_STEPS
    from cre_rentroll.run_events import _cloudwatch_line

    assert "agentcore_runtime" in BOX_STEPS
    assert "agentcore_payments" in BOX_STEPS
    for step in BOX_STEPS:
        line = _cloudwatch_line(step, "skipped example")
        assert line.startswith(f"RUN {step} ")
        assert "RUN agentcore_" in line


def test_notes_emit_only_inside_runtime(monkeypatch):
    from cre_rentroll.agentcore_platform import (
        note_disk_skills,
        note_fastmcp_fallback,
        note_harness_react,
        note_runtime,
    )
    from cre_rentroll.run_events import bind, reset

    monkeypatch.delenv("AGENTCORE_INSIDE", raising=False)
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        note_runtime()
        note_harness_react()
        note_disk_skills(("underwriting",), True)
        note_fastmcp_fallback("query_units")
    finally:
        reset(token)
    assert seen == []

    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    seen.clear()
    token = bind(seen.append)
    try:
        note_runtime()
        note_harness_react()
        note_disk_skills(("underwriting",), True)
        note_fastmcp_fallback("query_units")
    finally:
        reset(token)
    steps = [e["step"] for e in seen]
    assert steps == [
        "agentcore_runtime",
        "agentcore_harness",
        "agentcore_skills",
        "agentcore_tools_fastmcp",
    ]
    assert not any("payments" in (e.get("message") or "") and "ok" == e["step"] for e in seen)


def test_begin_turn_emits_payments_skipped(monkeypatch):
    from cre_rentroll.agentcore_platform import begin_turn
    from cre_rentroll.run_events import bind, reset

    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    monkeypatch.setattr("cre_rentroll.agentcore_platform._safe", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.agentcore_platform._pulse_builtin", lambda: None)
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        begin_turn("burak")
    finally:
        reset(token)
    pay = [e for e in seen if e["step"] == "agentcore_payments"]
    assert pay
    assert str(pay[0]["message"]).startswith("skipped")
    assert not any(e["step"] == "agentcore_payments" and not str(e["message"]).startswith("skipped") for e in seen)


def test_bind_workload_token_from_runtime_header(monkeypatch):
    from cre_rentroll.agentcore_platform import bind_workload_token, _identity
    from cre_rentroll.run_events import bind, reset

    monkeypatch.delenv("AGENTCORE_WORKLOAD_TOKEN", raising=False)
    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    monkeypatch.setenv("AGENTCORE_WORKLOAD_NAME", "cre_deal_rentroll-Nh63z6GXmK")

    assert bind_workload_token({}) == ""
    tok = bind_workload_token({"WorkloadAccessToken": "AgV4testtoken"})
    assert tok.startswith("AgV4")
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        rec = _identity("burak")
    finally:
        reset(token)
    assert rec and rec.get("source") == "runtime_header"
    ident = [e for e in seen if e["step"] == "agentcore_identity"]
    assert ident and str(ident[0]["message"]).startswith("success")
    assert "Runtime-injected" in ident[0]["message"]
    assert not any(str(e.get("message")).startswith("failed") for e in ident)


def test_identity_does_not_mint_service_linked_token(monkeypatch):
    from cre_rentroll.agentcore_platform import _identity
    from cre_rentroll.run_events import bind, reset

    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    monkeypatch.setenv("AGENTCORE_WORKLOAD_NAME", "cre_deal_rentroll-Nh63z6GXmK")
    monkeypatch.delenv("AGENTCORE_WORKLOAD_TOKEN", raising=False)
    monkeypatch.delenv("AGENTCORE_IDENTITY_NAME", raising=False)

    def boom(*_a, **_k):
        raise AssertionError("must not call bedrock-agentcore for identity mint")

    monkeypatch.setattr("cre_rentroll.agentcore_platform._client", boom)
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        assert _identity("burak") is None
    finally:
        reset(token)
    ident = [e for e in seen if e["step"] == "agentcore_identity"]
    assert ident and str(ident[0]["message"]).startswith("failed")


def test_identity_mints_user_created_workload_token(monkeypatch):
    from cre_rentroll.agentcore_platform import _identity
    from cre_rentroll.run_events import bind, reset

    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    monkeypatch.setenv("AGENTCORE_WORKLOAD_NAME", "cre_deal_rentroll-Nh63z6GXmK")
    monkeypatch.setenv("AGENTCORE_IDENTITY_NAME", "cre_deal_rentroll_wli")
    monkeypatch.delenv("AGENTCORE_WORKLOAD_TOKEN", raising=False)

    class Fake:
        def get_workload_access_token_for_user_id(self, **kwargs):
            assert kwargs["workloadName"] == "cre_deal_rentroll_wli"
            assert kwargs["userId"] == "burak"
            return {"workloadAccessToken": "AgV4minted"}

    monkeypatch.setattr("cre_rentroll.agentcore_platform._client", lambda *_a, **_k: Fake())
    seen: list[dict] = []
    token = bind(seen.append)
    try:
        rec = _identity("burak")
    finally:
        reset(token)
    assert rec and rec.get("source") == "mint_user_id"
    ident = [e for e in seen if e["step"] == "agentcore_identity"]
    assert ident and str(ident[0]["message"]).startswith("success")


def test_resolve_gateway_tool_accepts_target_prefix():
    from cre_rentroll.agentcore_platform import _resolve_gateway_tool

    listed = ["cretools___cre_ping", "cretools___query_units"]
    assert _resolve_gateway_tool("cre_ping", listed) == "cretools___cre_ping"
    assert _resolve_gateway_tool("query_units", listed) == "cretools___query_units"


def test_cfn_keeps_runtime_arn_on_apprunner():
    from pathlib import Path

    text = Path("deploy/cloudformation.yaml").read_text(encoding="utf-8")
    assert "AGENTCORE_RUNTIME_ARN" in text
    assert "AgentCoreRuntimeArn" in text
    assert "runtime-endpoint/*" in text
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "https://umvtmdweif.us-east-1.awsapprunner.com" in readme
    assert "not the AgentCore Runtime host" in readme


def test_invoke_react_calls_harness_note(monkeypatch):
    from cre_rentroll.agents import DEAL
    from cre_rentroll.harness import _invoke_react

    called: list[int] = []
    monkeypatch.setattr("cre_rentroll.agentcore_platform.note_harness_react", lambda: called.append(1))
    monkeypatch.setattr("cre_rentroll.harness.run_agent", lambda *a, **k: "draft")
    monkeypatch.setattr("cre_rentroll.turn_ctx.llm_call_kinds", lambda: [])
    monkeypatch.setattr("cre_rentroll.turn_ctx.add_llm_if_unchanged", lambda *a, **k: None)
    _invoke_react(DEAL, "prompt", "facts")
    assert called == [1]


def test_memory_snippets_flatten_ltm_and_stm():
    from cre_rentroll.agentcore_platform import _memory_snippets

    recs = [{"content": {"text": "164 units 13 vacant"}}]
    events = [
        {
            "payload": [
                {"conversational": {"content": {"text": "User: occupancy?"}, "role": "USER"}},
            ]
        }
    ]
    bits = _memory_snippets(recs, events)
    assert any("164 units" in b for b in bits)
    assert any("occupancy" in b.lower() for b in bits)


def test_emit_if_reused_memory(monkeypatch):
    from cre_rentroll.harness import _emit_if_reused_memory
    from cre_rentroll.run_events import bind, reset

    seen: list[dict] = []
    token = bind(seen.append)
    try:
        _emit_if_reused_memory(
            "13 vacant of 164, occupancy 92%",
            [{"name": "osm_geocode"}],
            "- vacant_count: 13",
            [{"role": "assistant", "content": "164 units"}],
        )
        _emit_if_reused_memory(
            "13 vacant",
            [{"name": "query_units"}],
            "- vacant_count: 13",
            [],
        )
    finally:
        reset(token)
    reuse = [e for e in seen if e["step"] == "memory_reuse"]
    assert len(reuse) == 1
    assert "skipped query_units" in reuse[0]["message"]


def test_emit_llm_memory_context():
    from cre_rentroll.harness import _emit_llm_memory_context
    from cre_rentroll.run_events import bind, reset

    seen: list[dict] = []
    token = bind(seen.append)
    try:
        _emit_llm_memory_context(
            "Known deal facts (Dynamo LTM, auto-injected):\n- vacant_count: 13",
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "164 units"}],
        )
    finally:
        reset(token)
    rows = [e for e in seen if e["step"] == "memory_context"]
    assert rows
    assert "prior chat turns" in rows[0]["message"]
    assert "vacant_count" in rows[0]["message"]


class _ChunkBody:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def iter_chunks(self, chunk_size=256):
        yield from self._chunks

    def read(self):
        return b"".join(self._chunks)


def test_iter_sse_events_yields_each_log_chunk():
    from cre_rentroll.agentcore_client import iter_sse_events

    body = _ChunkBody(
        [
            b'data: {"log": {"step": "inventory", "message": "full tool menu"}}\n\n',
            b'data: {"log": {"step": "react", "message": "ReAct turn 1"}}\n\n',
            b'data: {"outcome": {"kind": "answer", "text": "164", "stop_reason": "eval_ok"}, "host": "agentcore"}\n\n',
        ]
    )
    events = list(iter_sse_events(body))
    assert events[0]["log"]["step"] == "inventory"
    assert events[1]["log"]["step"] == "react"
    assert events[2]["outcome"]["kind"] == "answer"


def test_invoke_replays_sse_logs_before_outcome(monkeypatch):
    from cre_rentroll.agentcore_client import invoke_run_task
    from cre_rentroll.run_events import bind, reset

    seen: list[dict] = []
    order: list[str] = []

    class FakeClient:
        def invoke_agent_runtime(self, **kwargs):
            assert kwargs["accept"] == "text/event-stream"
            from cre_rentroll import __version__

            ver = "".join(ch for ch in __version__ if ch.isalnum())
            assert kwargs["runtimeSessionId"] == session_id("burak")
            assert kwargs["runtimeSessionId"] != "page-visit"
            assert ver in kwargs["runtimeSessionId"]
            return {
                "contentType": "text/event-stream",
                "statusCode": 200,
                "response": _ChunkBody(
                    [
                        b'data: {"log": {"step": "inventory", "message": "full tool menu"}}\n\n',
                        b'data: {"log": {"step": "react", "message": "ReAct turn 1"}}\n\n',
                        b'data: {"outcome": {"kind": "answer", "text": "164 units", "stop_reason": "eval_ok"}, "host": "agentcore"}\n\n',
                    ]
                ),
            }

    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x")
    monkeypatch.delenv("AGENTCORE_INSIDE", raising=False)
    monkeypatch.setattr("cre_rentroll.agentcore_client.boto3.client", lambda *a, **k: FakeClient())

    def capturing_sink(ev):
        seen.append(ev)
        order.append(ev["step"])

    token = bind(capturing_sink)
    try:
        outcome = invoke_run_task("burak", "occupancy?", session_id="page-visit")
    finally:
        reset(token)
    assert outcome.kind == "answer"
    assert order == ["inventory", "react"]
    assert [row["step"] for row in seen] == ["inventory", "react"]


def test_invocations_sse_emits_log_before_outcome(monkeypatch):
    from fastapi.testclient import TestClient

    from cre_rentroll.agentcore_app import app
    from cre_rentroll.run_events import emit
    from cre_rentroll.schemas import ChatOutcome

    def fake_task(user, text, has_attachment=False, ack_prefix="", session_id=""):
        emit("inventory", "full tool menu (book)")
        emit("react", "ReAct turn 1")
        return ChatOutcome(kind="answer", text="164 units", stop_reason="eval_ok")

    monkeypatch.setenv("AGENTCORE_INSIDE", "1")
    monkeypatch.setattr("cre_rentroll.agentcore_platform.bind_workload_token", lambda *_a, **_k: "")
    monkeypatch.setattr("cre_rentroll.agentcore_platform.note_runtime", lambda: emit("agentcore_runtime", "success ping"))
    monkeypatch.setattr("cre_rentroll.harness.run_task", fake_task)

    client = TestClient(app)
    res = client.post(
        "/invocations",
        json={"username": "burak", "prompt": "occupancy?"},
        headers={"Accept": "text/event-stream"},
    )
    assert "text/event-stream" in res.headers.get("content-type", "")
    text = res.content.decode()
    assert "full tool menu" in text
    assert text.index("inventory") < text.index('"kind": "answer"')
    json_res = client.post(
        "/invocations",
        json={"username": "burak", "prompt": "occupancy?"},
        headers={"Accept": "application/json"},
    )
    body = json_res.json()
    assert body["host"] == "agentcore"
    assert body["outcome"]["kind"] == "answer"
    assert body["logs"][0]["step"] == "agentcore_runtime"


def test_remote_turn_stays_on_the_caller_transcript(monkeypatch):
    from cre_rentroll.chat_memory import load, save
    from cre_rentroll.harness import run_task
    from cre_rentroll.schemas import ChatOutcome

    sid = "page-history-keep"
    save(sid, [{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "252 units"}])

    def fake_invoke(username, message, **kwargs):
        assert kwargs["session_id"] == sid
        return ChatOutcome(kind="answer", text="still 252", stop_reason="eval_ok")

    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x")
    monkeypatch.delenv("AGENTCORE_INSIDE", raising=False)
    monkeypatch.setattr("cre_rentroll.agentcore_client.invoke_run_task", fake_invoke)

    outcome = run_task("ada", "and occupancy?", ack_prefix="Saved roll.", session_id=sid)
    hist = load(sid)
    assert outcome.text == "still 252"
    assert [row["content"] for row in hist] == [
        "earlier",
        "252 units",
        "and occupancy?",
        "Saved roll.\n\nstill 252",
    ]


def test_runtime_seeds_the_page_transcript():
    from cre_rentroll.agentcore_app import _seed_visit
    from cre_rentroll.chat_memory import load

    _seed_visit(
        {
            "session_id": "runtime-seed",
            "messages": [
                {"role": "user", "content": "unit count?"},
                {"role": "assistant", "content": "3"},
            ],
        }
    )
    assert load("runtime-seed")[-1]["content"] == "3"
