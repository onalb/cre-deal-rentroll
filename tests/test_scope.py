import cre_rentroll.scope as scope_mod
from cre_rentroll.harness import run_task
from cre_rentroll.run_events import bind, reset
from cre_rentroll.scope import OOS_TEXT


def test_live_scope_module_has_no_classify():
    assert not hasattr(scope_mod, "classify_scope")
    assert not hasattr(scope_mod, "apply_scope_gate")
    assert not hasattr(scope_mod, "ScopeDecision")


def _stub_vault(monkeypatch):
    monkeypatch.setattr("cre_rentroll.harness.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.harness.vault.load_roll", lambda u: None)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")


def test_run_task_greeting_agent_oos(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    called = {"n": 0}

    monkeypatch.setattr(
        "cre_rentroll.harness.run_agent",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or OOS_TEXT,
    )
    seen = []
    token = bind(seen.append)
    try:
        out = run_task("analyst", "Hello")
    finally:
        reset(token)
    assert called["n"] == 1
    assert out.text == OOS_TEXT  # stub LLM copied the prompt sentence; Python does not swap it in
    assert not any(row["step"] == "scope" for row in seen)
    assert not any("in-loop (ReAct prompt)" in row["message"] for row in seen)
    summary = next(row["message"] for row in seen if row["step"] == "summary")
    assert "scope," not in summary
    assert "agent" in summary


def test_run_task_need_file_vault_gate_no_extra_llm(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    from cre_rentroll.tools import query_units

    captured: dict[str, str] = {}

    def fake_agent(*_a, **_k):
        captured["t"] = query_units()
        return captured["t"]

    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    seen = []
    token = bind(seen.append)
    try:
        out = run_task("analyst", "Abstract this rent roll")
    finally:
        reset(token)
    assert out.kind == "warning"
    assert out.stop_reason == "need_attachment"
    assert out.text == captured["t"]
    kinds = [row["step"] for row in seen]
    assert kinds.count("scope") == 1
    scope_ev = next(row for row in seen if row["step"] == "scope")
    assert "scope (agent)" in scope_ev["message"]
    assert "need rent-roll" in scope_ev["message"]
    assert "(LLM)" not in scope_ev["message"]
    summary = next(row["message"] for row in seen if row["step"] == "summary")
    assert "scope," not in summary


def test_run_task_oos_keeps_llm_wording(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    draft = (
        "I only answer questions about an uploaded rent roll — units, occupancy, "
        "income, location, or underwriting. This app will not forecast weekend weather."
    )
    monkeypatch.setattr("cre_rentroll.harness.run_agent", lambda *a, **k: draft)
    out = run_task("analyst", "What's the weather this weekend?")
    assert out.text == draft
    assert out.text != OOS_TEXT


def test_run_task_does_not_call_classify_scope(monkeypatch):
    _stub_vault(monkeypatch)
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", lambda *a, **k: OOS_TEXT)
    out = run_task("analyst", "What's the weather?")
    assert out.text == OOS_TEXT
    assert out.eval is not None


def test_run_task_in_scope_does_not_emit_scope(monkeypatch):
    from cre_rentroll.schemas import CanonicalRentRoll, UnitRow

    real = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    monkeypatch.setattr("cre_rentroll.harness.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.harness.vault.load_roll", lambda u: real)
    monkeypatch.setattr("cre_rentroll.harness.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        "cre_rentroll.harness.run_agent",
        lambda *a, **k: "Occupancy is 100% with in-place monthly 1500.",
    )
    seen = []
    token = bind(seen.append)
    try:
        run_task("analyst", "What is occupancy and in-place income?")
    finally:
        reset(token)
    assert not any(row["step"] == "scope" for row in seen)
    assert not any("in-loop (ReAct prompt)" in row["message"] for row in seen)
