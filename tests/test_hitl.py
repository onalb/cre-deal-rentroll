import json
from pathlib import Path

from cre_rentroll import hitl, vault
from cre_rentroll.agents import BOOK, _openai_specs, system_prompt
from cre_rentroll.harness import run_task, session_prompt
from cre_rentroll.memory import facts_prompt
from cre_rentroll.run_events import bind, reset
from cre_rentroll.schemas import CanonicalRentRoll, UnitRow
from cre_rentroll.tools import create_plan, publish_memo


def _bind_ddb(monkeypatch):
    store: dict[tuple[str, str], dict] = {}

    def put(username, sk, attrs):
        store[(username.strip().lower(), sk)] = dict(attrs)

    def get(username, sk):
        return store.get((username.strip().lower(), sk))

    def query(username, prefix):
        user = username.strip().lower()
        return [
            {"sk": sk, **attrs}
            for (u, sk), attrs in sorted(store.items())
            if u == user and sk.startswith(prefix)
        ]

    monkeypatch.setattr("cre_rentroll.dynamo.put_item", put)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", get)
    monkeypatch.setattr("cre_rentroll.dynamo.query_prefix", query)
    return store


def _gateway_saves_approved_memo(monkeypatch) -> None:
    """Approve goes through /mcp/deal. The gateway writes MEMO# on the shared table."""
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")

    def fake(name, arguments, timeout=180.0):
        assert name == "publish_memo"
        assert arguments.get("hitl_allow") is True
        saved = vault.save_memo(arguments["username"], arguments["title"], arguments["body"])
        return json.dumps(
            {
                "status": "published",
                "title": arguments["title"],
                "bytes": len(arguments.get("body") or ""),
                "ts": saved.get("ts"),
            }
        )

    monkeypatch.setattr("cre_rentroll.capabilities_client.call_deal_tool", fake)


def _pending() -> dict:
    return {
        "tool": "publish_memo",
        "title": "Hill and La Cordoba",
        "body": (
            "Hill: 92% occupancy, in-place $80,019/mo. "
            "La Cordoba: 164 units, mapped to Houston. "
            "Both deals discussed this session."
        ),
    }


def test_publish_memo_prompts_require_session_synthesis():
    sys = system_prompt(BOOK, "VAULT HAS a rent roll for this session.\n").lower()
    doc = " ".join((publish_memo.__doc__ or "").lower().split())
    create_doc = " ".join((create_plan.__doc__ or "").lower().split())
    body_desc = next(
        s["function"]["parameters"]["properties"]["body"]["description"]
        for s in _openai_specs(BOOK.tool_names)
        if s["function"]["name"] == "publish_memo"
    ).lower()
    follow = session_prompt(
        "Create a memo covering everything we discussed",
        [
            {"role": "user", "content": "Abstract Hill occupancy and income."},
            {"role": "assistant", "content": "Hill is 92% occupied, $80,019 in-place."},
            {"role": "user", "content": "Now look at La Cordoba. How many units?"},
            {"role": "assistant", "content": "La Cordoba has 164 units."},
        ],
    ).lower()
    for blob in (sys, doc, body_desc, follow, create_doc):
        assert "this session" in blob
        assert "python does not write" in blob
        assert "fact#" in blob
    for blob in (sys, doc, follow, create_doc):
        assert "publish_memo" in blob
        assert "first (and typically only) work tool is publish_memo" in blob
        assert "do not dump the memo as chat before the tool" in blob
        assert "do not extra create_plan" in blob
        assert "functions.publish_memo" in blob
        assert "let's proceed" in blob
        assert "immediately" in blob
        assert "memo#" in blob
        assert "chat" in blob
        assert "hitl" in blob
    assert "la cordoba has 164 units" in follow
    assert "hill is 92%" in follow
    assert "create a memo covering everything we discussed" in follow


def test_hitl_approve_surfaces_pending_title_and_body(monkeypatch):
    pending = _pending()
    saved: list[dict] = []

    monkeypatch.setattr(
        "cre_rentroll.hitl.vault.load_session",
        lambda _u: {"messages": [{"role": "user", "content": "publish it"}], "pending_hitl": pending},
    )

    def _save(_u, messages, **extra):
        saved.append({"pending_hitl": extra.get("pending_hitl"), "n": len(messages)})

    monkeypatch.setattr("cre_rentroll.hitl.vault.save_session", _save)
    published: list[tuple[str, str]] = []

    def _publish(title, body):
        published.append((title, body))
        return json.dumps({"status": "published", "title": title, "bytes": len(body)})

    monkeypatch.setattr("cre_rentroll.hitl.publish_memo", _publish)
    result = hitl.resolve("analyst", True, [{"role": "user", "content": "publish it"}])
    assert result["status"] == "published"
    assert "The memo was saved." not in result["text"]
    assert "Hill and La Cordoba" in result["text"]
    assert "Hill: 92% occupancy" in result["text"]
    assert "La Cordoba: 164 units" in result["text"]
    assert "Both deals discussed this session." in result["text"]
    assert published == [(pending["title"], pending["body"])]
    assert saved[-1]["pending_hitl"] is None


def test_hitl_deny_stays_unpublished(monkeypatch):
    pending = _pending()
    store = _bind_ddb(monkeypatch)
    monkeypatch.setattr(
        "cre_rentroll.hitl.vault.load_session",
        lambda _u: {"messages": [], "pending_hitl": pending},
    )
    monkeypatch.setattr("cre_rentroll.hitl.vault.save_session", lambda *_a, **_k: None)
    result = hitl.resolve("analyst", False, [])
    assert result["status"] == "denied"
    assert result["text"] == "The memo was not published."
    assert pending["body"] not in result["text"]
    assert not any(sk.startswith("MEMO#") for (_u, sk) in store)
    assert vault.list_memos("analyst") == []


def test_hitl_approve_persists_and_retrieves_memo(monkeypatch):
    pending = _pending()
    store = _bind_ddb(monkeypatch)
    _gateway_saves_approved_memo(monkeypatch)
    monkeypatch.setattr(
        "cre_rentroll.hitl.vault.load_session",
        lambda _u: {"messages": [], "pending_hitl": pending},
    )
    monkeypatch.setattr("cre_rentroll.hitl.vault.save_session", lambda *_a, **_k: None)
    monkeypatch.setattr("cre_rentroll.agentcore_platform.facts_from_memory", lambda _u: [])
    result = hitl.resolve("analyst", True, [])
    assert result["status"] == "published"
    rows = [(sk, attrs) for (u, sk), attrs in store.items() if u == "analyst" and sk.startswith("MEMO#")]
    assert len(rows) == 1
    sk, attrs = rows[0]
    assert sk == f"MEMO#{attrs['ts']}"
    assert attrs["title"] == pending["title"]
    assert attrs["body"] == pending["body"]
    listed = vault.list_memos("analyst")
    assert listed == [{"title": pending["title"], "body": pending["body"], "ts": attrs["ts"]}]
    facts = facts_prompt("analyst")
    assert pending["title"] in facts
    assert pending["body"] in facts
    assert f"MEMO#{attrs['ts']}" in facts
    assert "answer recall asks only from these rows" in facts.lower()
    assert "not from prior chat" in facts.lower()


def test_facts_prompt_empty_memos_says_none_published(monkeypatch):
    _bind_ddb(monkeypatch)
    monkeypatch.setattr(
        "cre_rentroll.agentcore_platform.facts_from_memory",
        lambda _u: ["ac-memory-line"],
    )
    facts = facts_prompt("analyst")
    assert "MEMO#" in facts
    assert "none" in facts.lower()
    assert "hitl" in facts.lower()
    assert "chat history" in facts.lower()
    assert "ac-memory-line" in facts


def test_hitl_api_approve_appends_memo_to_history(monkeypatch):
    from fastapi.testclient import TestClient

    from cre_rentroll.main import app

    pending = _pending()
    store = {
        "messages": [
            {"role": "user", "content": "Create a memo of this whole chat"},
            {
                "role": "assistant",
                "content": "Saving a memo needs your approval. Use Approve or Deny on the right.",
                "kind": "hitl_pending",
            },
        ],
        "pending_hitl": pending,
    }

    def load_session(_u):
        return dict(store)

    def save_session(_u, messages, **extra):
        store["messages"] = list(messages)
        if "pending_hitl" in extra:
            store["pending_hitl"] = extra["pending_hitl"]

    monkeypatch.setattr("cre_rentroll.main.vault.load_session", load_session)
    monkeypatch.setattr("cre_rentroll.main.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.hitl.vault.load_session", load_session)
    monkeypatch.setattr("cre_rentroll.hitl.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.dynamo.ensure_table", lambda: None)
    _bind_ddb(monkeypatch)
    _gateway_saves_approved_memo(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/hitl", json={"allow": True}, headers={"X-Username": "analyst"})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "published"
    last = data["history"][-1]
    assert last["role"] == "assistant"
    assert last["kind"] == "answer"
    assert "Hill and La Cordoba" in last["content"]
    assert "La Cordoba: 164 units" in last["content"]
    assert "The memo was saved." not in last["content"]
    assert store["pending_hitl"] is None
    assert data["memos"]
    assert data["memos"][0]["title"] == pending["title"]
    assert data["memos"][0]["body"] == pending["body"]
    assert data["memos"][0]["ts"]


def test_harness_hitl_pause_keeps_llm_session_body(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill-only-vault",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    pending_saved: list = []
    saved_msgs: list = []

    def fake_agent(*_a, **_k):
        create_plan("1. [in_progress] publish_memo")
        return publish_memo(
            "Hill and La Cordoba",
            "Hill occupancy from earlier. La Cordoba 164 units from earlier.",
        )

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "FACT#property_name=Hill-only-vault")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})

    def save_session(_u, msgs, **extra):
        saved_msgs.append(list(msgs or []))
        if extra.get("pending_hitl"):
            pending_saved.append(extra["pending_hitl"])

    monkeypatch.setattr("cre_rentroll.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        outcome = run_task("tester", "Publish a memo of this whole chat", skip_scope=True)
    finally:
        reset(token)

    assert outcome.kind == "hitl_pending"
    body = (outcome.pending_hitl or {}).get("body") or ""
    assert "La Cordoba 164" in body
    assert "Hill occupancy" in body
    assert pending_saved
    assert "La Cordoba 164" in pending_saved[-1]["body"]
    assert saved_msgs
    last_asst = saved_msgs[-1][-1]
    assert last_asst.get("kind") == "hitl_pending"
    assert last_asst.get("role") == "assistant"


def test_leaked_functions_publish_memo_json_arms_hitl(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill-only-vault",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    token = bind(lambda *_a, **_k: None)
    pending_saved: list = []
    saved_msgs: list = []
    leak = (
        "Let's proceed\n"
        '{"recipient_name":"functions.publish_memo","parameters":{'
        '"title":"Hill and La Cordoba",'
        '"body":"Hill occupancy from earlier. La Cordoba 164 units from earlier."}}'
    )

    def fake_agent(*_a, **_k):
        create_plan("1. [in_progress] publish_memo")
        return leak

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "FACT#property_name=Hill-only-vault")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})

    def save_session(_u, msgs, **extra):
        saved_msgs.append(list(msgs or []))
        if extra.get("pending_hitl"):
            pending_saved.append(extra["pending_hitl"])

    monkeypatch.setattr("cre_rentroll.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        outcome = run_task(
            "tester",
            "Create a memo synthesizing this session.",
            skip_scope=True,
        )
    finally:
        reset(token)

    assert outcome.kind == "hitl_pending"
    assert outcome.pending_hitl
    assert outcome.pending_hitl["title"] == "Hill and La Cordoba"
    assert "La Cordoba 164" in outcome.pending_hitl["body"]
    assert "recipient_name" not in (outcome.text or "")
    assert "functions.publish_memo" not in (outcome.text or "")
    assert "Let's proceed" not in (outcome.text or "")
    assert pending_saved
    assert pending_saved[-1]["title"] == "Hill and La Cordoba"
    last_asst = saved_msgs[-1][-1]
    assert last_asst.get("kind") == "hitl_pending"
    assert "functions.publish_memo" not in (last_asst.get("content") or "")


def test_proceed_after_memo_ask_invokes_publish_memo(monkeypatch):
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill-only-vault",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    token = bind(lambda *_a, **_k: None)
    pending_saved: list = []
    history = [
        {"role": "user", "content": "Create a memo synthesizing this session."},
        {
            "role": "assistant",
            "content": (
                "Creating the Memo\n"
                "1. [in_progress] publish_memo\n"
                "Let's proceed\n"
                '{"recipient_name":"functions.publish_memo","parameters":{'
                '"title":"Hill session memo",'
                '"body":"Hill occupancy 92%. La Cordoba 164 units from this session."}}'
            ),
        },
    ]

    def fake_agent(*_a, **_k):
        create_plan("1. [in_progress] publish_memo")
        return "Let's proceed\n1. [in_progress] publish_memo"

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "FACT#property_name=Hill-only-vault")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: roll)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": history})

    def save_session(_u, msgs, **extra):
        if extra.get("pending_hitl"):
            pending_saved.append(extra["pending_hitl"])

    monkeypatch.setattr("cre_rentroll.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", save_session)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    try:
        outcome = run_task("tester", "yes", skip_scope=True)
    finally:
        reset(token)

    assert outcome.kind == "hitl_pending"
    assert outcome.pending_hitl
    assert outcome.pending_hitl["title"] == "Hill session memo"
    assert "La Cordoba 164" in outcome.pending_hitl["body"]
    assert "recipient_name" not in (outcome.text or "")
    assert "1. [in_progress]" not in (outcome.text or "")
    assert pending_saved


def test_ui_opens_vault_on_hitl_pending_kind():
    src = Path(__file__).resolve().parents[1] / "ui" / "src" / "App.jsx"
    text = src.read_text(encoding="utf-8")
    assert 'data.kind === "hitl_pending"' in text
    assert "setVaultOpen(true)" in text
    assert "isHitlPendingPayload" in text
