import json

from cre_rentroll.eval_service import (
    JUDGE_SYS,
    _judge_facts,
    ci_aggregate,
    format_eval_log,
    gate,
    has_cre_payload,
    live_judge,
    vault_calc_invented,
    vault_calc_log_label,
    vault_calc_ok,
)
from cre_rentroll.agents import BOOK
from cre_rentroll.harness import inventories_from_intent, inventory_for
from cre_rentroll.memory import InMemoryStore
from cre_rentroll.schemas import CanonicalRentRoll, EvalScores, UnitRow
from cre_rentroll.turn_ctx import llm_call_count


def test_how_many_units_is_cre():
    assert inventory_for("How many units?") == [BOOK]
    assert inventories_from_intent("abstract") == [BOOK]
    assert inventories_from_intent("geo") == [BOOK]
    assert inventories_from_intent("underwrite") == [BOOK]
    assert "osm_geocode" in BOOK.tool_names
    assert "query_units" in BOOK.tool_names
    assert "publish_memo" in BOOK.tool_names


def test_q1_do_not_geocode_is_prompt_not_inventory_lock():
    from cre_rentroll.agents import system_prompt

    q = (
        "Abstract this rent roll: unit count, occupancy, in-place monthly income, "
        "vacant count, MTM if present as of 01/01/2021. Cite computed totals. "
        "Do not geocode or search."
    )
    assert inventory_for(q) == [BOOK]
    assert "osm_geocode" in BOOK.tool_names
    sys = system_prompt(BOOK, "").lower()
    assert "do not geocode" in sys
    assert "pure rent-roll" in sys or "q1" in sys
    assert "mtm_count" in sys
    assert "units on month-to-month leases" in sys
    assert "existence/yes-no" in sys
    assert "occupancy status" in sys
    assert "one unfiltered query_units" in sys
    assert "not four" in sys
    assert "do not call query_units again for those counts" in sys
    assert "occupancy=mtm" not in sys
    assert "any month-to-month on this rent roll" not in sys


def test_empty_draft_has_no_cre_payload_and_vault_not_ok():
    roll = CanonicalRentRoll(
        layout="one_row",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    assert not has_cre_payload("")
    assert not has_cre_payload("   ")
    assert has_cre_payload('{"unit_count": 3, "occupancy_pct": 0.92}')
    from cre_rentroll.scope import SEARCH_DOWN_TEXT

    assert has_cre_payload(SEARCH_DOWN_TEXT)
    assert vault_calc_ok("", roll, []) is None
    assert vault_calc_ok("   ", roll, []) is None


def test_gate_empty_draft_is_error_no_retry():
    empty = EvalScores(judge="empty")
    assert empty.G is None and empty.R is None and empty.request_C is None
    assert empty.vault_calc_ok is None
    assert gate(empty, retry_used=False) == "error"
    assert gate(empty, retry_used=True) == "error"
    skipped = EvalScores(judge="not_run")
    assert gate(skipped, retry_used=False) == "error"
    assert vault_calc_log_label(None) == "N/A"
    assert format_eval_log(skipped, "error") == (
        "G=N/A C=N/A R=N/A vault_calc=N/A judge=not_run → error"
    )


def test_live_judge_empty_skips_openai(monkeypatch):
    called = {"n": 0}

    class _Boom:
        def __init__(self, **kwargs):
            called["n"] += 1
            raise AssertionError("should not judge empty draft")

    import openai as openai_mod

    monkeypatch.setattr("cre_rentroll.settings.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr(openai_mod, "OpenAI", _Boom)
    scores = live_judge("What is occupancy?", "", None, [])
    assert called["n"] == 0
    assert scores.judge == "empty"
    assert scores.G is None and scores.R is None and scores.request_C is None
    assert scores.vault_calc_ok is None
    assert gate(scores, retry_used=False) == "error"


def test_vault_calc_flags_invented_number():
    roll = CanonicalRentRoll(
        layout="one_row",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    traces = [{"name": "compute_income", "result": {"in_place_monthly": 1500}}]
    assert vault_calc_ok("Income is 1500", roll, traces) is True
    assert vault_calc_ok("Income is 12400000", roll, traces) is None
    assert vault_calc_invented("Income is 12400000", roll, traces) == ["12400000"]
    assert vault_calc_log_label(None, ["12400000"], draft="Income is 12400000") == "N/A"


def _headline_roll() -> CanonicalRentRoll:
    class Hill(CanonicalRentRoll):
        @property
        def unit_count(self) -> int:
            return 252

        @property
        def occupied_count(self) -> int:
            return 223

        @property
        def vacant_count(self) -> int:
            return 29

        @property
        def mtm_count(self) -> int:
            return 5

        @property
        def occupancy_pct(self) -> float:
            return 0.8849

        @property
        def in_place_monthly(self) -> float:
            return 290851.0

    return Hill(
        layout="one_row",
        units=[
            UnitRow(unit_id="218", in_place_rent=218, vacant=False, mtm=False),
            UnitRow(unit_id="247", in_place_rent=247, vacant=False, mtm=True),
        ],
    )


def test_vault_calc_derived_not_in_allowlist_is_na_not_ok():
    roll = _headline_roll()
    traces = [
        {
            "name": "query_units",
            "result": {
                "unit_count": 252,
                "occupied_count": 223,
                "vacant_count": 29,
                "mtm_count": 5,
                "occupancy_pct": 0.8849,
                "units": [{"unit_id": "218", "in_place_rent": 218}] * 10,
            },
        }
    ]
    derived_218 = "There are 218 occupied units that are not on month-to-month leases."
    derived_247 = "252 units minus 5 vacant leaves 247."
    headline = "There are 223 occupied units, 252 units, and 5 MTM."
    assert vault_calc_ok(derived_218, roll, traces) is None
    assert vault_calc_ok(derived_247, roll, traces) is None
    assert "218" in vault_calc_invented(derived_218, roll, traces)
    assert "247" in vault_calc_invented(derived_247, roll, traces)
    assert vault_calc_ok(headline, roll, traces) is True
    assert vault_calc_invented(headline, roll, traces) == []
    assert vault_calc_log_label(None) == "N/A"
    assert vault_calc_log_label(True) == "ok"
    scores = EvalScores(R=5, G=5, request_C=5, vault_calc_ok=None, judge="llm")
    assert gate(scores, retry_used=False) == "show"
    assert gate(scores, retry_used=True) == "show"
    line = format_eval_log(scores, "show")
    assert "vault_calc=N/A" in line
    assert "mismatch" not in line
    assert "→ error" not in line


def test_vault_calc_occupancy_percent_and_rounding():
    class OccRoll(CanonicalRentRoll):
        @property
        def occupancy_pct(self) -> float:
            return 0.9207

    roll = OccRoll(
        layout="one_row",
        units=[UnitRow(unit_id="1", in_place_rent=80019.0, vacant=False)],
    )
    answer = "Occupancy is 92.07% with in-place monthly 80019.0."
    assert vault_calc_ok(answer, roll, [])
    assert vault_calc_ok("Occupancy 0.9207", roll, [])
    assert vault_calc_ok("Occupancy 0.921", roll, [])
    assert vault_calc_ok("Occupancy is 92.1%.", roll, [])
    assert vault_calc_invented(answer, roll, []) == []
    assert vault_calc_invented("Occupancy 0.9207", roll, []) == []
    assert vault_calc_ok("NOI is 12400000", roll, []) is None
    bogus = vault_calc_invented("NOI is 12400000", roll, [])
    assert bogus == ["12400000"]
    assert vault_calc_log_label(None, bogus, draft="NOI is 12400000") == "N/A"


def test_vault_calc_skips_calendar_dates_and_logs_excerpt():
    class HillRoll(CanonicalRentRoll):
        @property
        def unit_count(self) -> int:
            return 164

        @property
        def vacant_count(self) -> int:
            return 13

        @property
        def occupied_count(self) -> int:
            return 151

        @property
        def occupancy_pct(self) -> float:
            return 0.9207

        @property
        def in_place_monthly(self) -> float:
            return 80019.0

        @property
        def mtm_count(self) -> int:
            return 0

    roll = HillRoll(
        layout="one_row",
        units=[UnitRow(unit_id="1", in_place_rent=80019.0, vacant=False)],
    )
    dated = (
        "As of April 20, 2021 occupancy is 92.07% with in-place monthly 80019."
    )
    assert vault_calc_invented(dated, roll, []) == []
    assert vault_calc_ok(dated, roll, [])
    iso = "As of 2021-04-20 occupancy is 92.07%."
    assert "20" not in vault_calc_invented(iso, roll, [])
    assert vault_calc_ok(iso, roll, [])
    label = vault_calc_log_label(
        None,
        ["99"],
        draft="NOI is 99 this year.",
        roll=roll,
    )
    assert label == "N/A"


def test_score_gcr_need_replan_log_includes_reason(monkeypatch):
    from cre_rentroll.run_events import bind, reset
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import reset_turn, set_eval_query

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    why = "occupancy 92.1% is not a vault figure"
    seen: list[dict] = []
    token = bind(seen.append)
    reset_turn("tester")
    set_eval_query("What is occupancy?")
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(
            R=5, G=1, request_C=5, vault_calc_ok=None, judge="llm", reason=why
        ),
    )
    try:
        out = json.loads(score_gcr("Occupancy is 92.1%."))
    finally:
        reset(token)
    assert out["decision"] == "need_replan"
    assert out["vault_calc"] == "N/A"
    assert why in (out.get("reason") or "")
    tool_msgs = [row["message"] for row in seen if row.get("step") == "tool"]
    assert any("need_replan" in m and why in m and "vault_calc=N/A" in m for m in tool_msgs)
    eval_line = format_eval_log(
        EvalScores(
            R=5,
            G=1,
            request_C=5,
            vault_calc_ok=None,
            vault_calc=out["vault_calc"],
            judge="llm",
            reason=why,
        ),
        "need_replan",
    )
    assert why in eval_line
    assert "vault_calc=N/A" in eval_line
    assert "mismatch" not in eval_line


def test_score_gcr_logs_invented_draft_numbers(monkeypatch):
    from cre_rentroll.run_events import bind, reset
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import reset_turn, set_eval_query

    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    seen: list[dict] = []
    token = bind(seen.append)
    reset_turn("tester")
    set_eval_query("What is occupancy?")
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(R=5, G=5, request_C=5, vault_calc_ok=None, judge="llm"),
    )
    try:
        out_pct = json.loads(score_gcr("Occupancy is 92.1%."))
        out_bogus = json.loads(score_gcr("NOI is 12400000"))
    finally:
        reset(token)
    assert out_pct["vault_calc"] == "N/A"
    assert out_pct["vault_calc_ok"] is None
    assert out_pct["decision"] == "ok"
    assert out_bogus["vault_calc"] == "N/A"
    assert out_bogus["decision"] == "ok"
    msgs = [row["message"] for row in seen if row.get("step") == "tool"]
    assert any("vault_calc=N/A" in m and "→ ok" in m for m in msgs)
    assert not any("mismatch" in m for m in msgs)


def test_gate_shared_retry():
    bad = EvalScores(R=5, G=1, request_C=1, vault_calc_ok=True)
    assert gate(bad, retry_used=False) == "retry"
    assert gate(bad, retry_used=True) == "error"
    mismatch = EvalScores(R=5, G=5, request_C=5, vault_calc_ok=False)
    assert gate(mismatch, retry_used=False) == "retry"
    na = EvalScores(R=5, G=5, request_C=5, vault_calc_ok=None, judge="llm")
    assert gate(na, retry_used=False) == "show"
    assert gate(na, retry_used=True) == "show"
    failed = EvalScores(vault_calc_ok=True, judge="error")
    assert gate(failed, retry_used=False) == "error"
    assert gate(failed, retry_used=True) == "error"


def test_ci_aggregate_mean_stdev():
    stats = ci_aggregate([{"G": 5, "C": 4, "R": 5}, {"G": 4, "C": 4, "R": 5}, {"G": 5, "C": 5, "R": 4}])
    assert stats["n"] == 3
    assert 4 <= stats["G"]["mean"] <= 5


def test_memory_write_if_empty():
    s = InMemoryStore()
    s.add("unit_count", 10)
    s.add("unit_count", 99)
    assert s.search("unit")[0]["value"] == 10
    s.add("unit_count", 12, overwrite=True)
    assert s.search("unit")[0]["value"] == 12


def test_judge_prompt_percent_vs_fraction_and_vacant_only():
    assert "0.9207" in JUDGE_SYS
    assert "92.07%" in JUDGE_SYS
    assert "vacant-only" in JUDGE_SYS.lower() or "vacant only" in JUDGE_SYS.lower()
    assert "100% occupancy" in JUDGE_SYS.lower()
    assert "same as with vacancy" in JUDGE_SYS.lower()
    assert "user-stated scenario rents" in JUDGE_SYS.lower()
    assert "vacant-fill-only" in JUDGE_SYS.lower() or "vacant fill" in JUDGE_SYS.lower()
    assert "DeepEval" in JUDGE_SYS
    assert '"reason"' in JUDGE_SYS
    assert "interview" in JUDGE_SYS.lower()
    assert "gold.json" in JUDGE_SYS


def test_judge_facts_include_percent_display(monkeypatch):
    monkeypatch.setattr("cre_rentroll.memory.retrieve_facts", lambda u: {})

    class OccRoll(CanonicalRentRoll):
        @property
        def occupancy_pct(self) -> float:
            return 0.9207

    roll = OccRoll(
        layout="one_row",
        units=[UnitRow(unit_id="1", in_place_rent=80019.0, vacant=False)],
    )
    traces = [{"name": "query_units", "result": {"unit_count": 1, "occupancy_pct": 0.9207, "units": [{"unit_id": "1"}]}}]
    facts = _judge_facts(roll, traces)
    assert facts["roll_abstract"]["occupancy_pct_display"] == "92.07%"
    assert facts["tools"][0]["result"].get("units") is None


def _mock_openai(monkeypatch, payload: str | Exception):
    monkeypatch.setattr("cre_rentroll.settings.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.settings.openai_client_args", lambda: {"api_key": "sk-test", "max_retries": 0, "timeout": 12.0})
    monkeypatch.setattr("cre_rentroll.settings.openai_classify_model", lambda: "gpt-4o-mini")
    monkeypatch.setattr("cre_rentroll.memory.retrieve_facts", lambda u: {})
    captured: dict = {}

    class _Msg:
        content = payload if isinstance(payload, str) else "{}"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kwargs):
            captured["kwargs"] = kwargs
            if isinstance(payload, Exception):
                raise payload
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    import openai as openai_mod

    monkeypatch.setattr(openai_mod, "OpenAI", _Client)
    return captured


def test_live_judge_reads_reason_notes_justification(monkeypatch):
    _mock_openai(
        monkeypatch,
        json.dumps(
            {
                "R": 5,
                "G": 1,
                "request_C": 5,
                "notes": "G failed",
                "justification": "invented occupancy",
                "invented": ["92.1%"],
            }
        ),
    )
    scores = live_judge("What is occupancy?", "Occupancy is 92.1%.", None, [])
    assert scores.judge == "llm"
    assert scores.G == 1
    assert "G failed" in (scores.reason or "")
    assert "invented occupancy" in (scores.reason or "")
    assert "92.1%" in (scores.reason or "")
    line = format_eval_log(scores, "need_replan")
    assert "failed G=1" in line
    assert "G failed" in line
    assert "What is occupancy?" not in line


def test_live_judge_reason_strips_interview_query(monkeypatch):
    q = "What is occupancy at Hillcrest Gardens?"
    _mock_openai(
        monkeypatch,
        json.dumps({"R": 5, "G": 1, "request_C": 5, "reason": f"{q} invented 92.1%"}),
    )
    scores = live_judge(q, "Occupancy is 92.1%.", None, [])
    assert q not in (scores.reason or "")
    assert "invented 92.1%" in (scores.reason or "")


def test_live_judge_uses_mocked_llm(monkeypatch):
    captured = _mock_openai(monkeypatch, '{"R":5,"G":5,"request_C":4}')
    before = llm_call_count()
    scores = live_judge("What is occupancy?", "Occupancy is 92.07%.", None, [])
    assert llm_call_count() == before + 1
    assert scores.judge == "llm"
    assert scores.R == 5 and scores.G == 5 and scores.request_C == 4
    assert scores.vault_calc_ok is None
    assert gate(scores, retry_used=True) == "show"
    kw = captured["kwargs"]
    assert kw["response_format"] == {"type": "json_object"}
    assert "tools" not in kw
    sys_msg = kw["messages"][0]["content"]
    assert "0.9207" in sys_msg and "92.07%" in sys_msg
    user_msg = kw["messages"][1]["content"]
    assert "QUERY:" in user_msg and "ANSWER:" in user_msg and "FACTS:" in user_msg


def test_live_judge_error_on_timeout(monkeypatch):
    _mock_openai(monkeypatch, TimeoutError("judge timeout"))
    scores = live_judge(
        "Abstract this rent roll. Do not geocode or search.",
        "198 units occupancy 90% in-place monthly income 1200 rent vacant 10",
        None,
        [],
    )
    assert scores.judge == "error"
    assert scores.R is None and scores.G is None and scores.request_C is None
    assert "TimeoutError" in (scores.reason or "")
    assert "judge timeout" in (scores.reason or "")
    assert gate(scores, retry_used=False) == "error"
    err_line = format_eval_log(scores, "error")
    assert "judge=error" in err_line
    assert "judge timeout" in err_line


def test_live_judge_error_on_bad_json(monkeypatch):
    _mock_openai(monkeypatch, "not-json")
    scores = live_judge("What is occupancy?", "Occupancy is 92.07%.", None, [])
    assert scores.judge == "error"
    assert scores.G is None


def test_live_judge_error_on_missing_scores(monkeypatch):
    _mock_openai(monkeypatch, "{}")
    scores = live_judge("What is occupancy?", "Occupancy is 92.07%.", None, [])
    assert scores.judge == "error"
    assert scores.R is None


def test_live_judge_error_without_api_key(monkeypatch):
    called = {"n": 0}

    class _Boom:
        def __init__(self, **kwargs):
            called["n"] += 1
            raise AssertionError("should not construct OpenAI")

    import openai as openai_mod

    monkeypatch.setattr("cre_rentroll.settings.openai_api_key", lambda: "")
    monkeypatch.setattr(openai_mod, "OpenAI", _Boom)
    scores = live_judge("What is occupancy?", "Occupancy is 92.07%.", None, [])
    assert called["n"] == 0
    assert scores.judge == "error"
    assert scores.G is None
    assert gate(scores, retry_used=False) == "error"
