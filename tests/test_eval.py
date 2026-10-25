import json

from cre_rentroll.eval_service import (
    JUDGE_SYS,
    _judge_facts,
    ask_is_conceptual,
    ask_is_locate,
    ask_is_market_area,
    ask_is_market_comps,
    ask_skips_vault_payload,
    cited_locate_map_draft,
    ci_aggregate,
    format_eval_log,
    gate,
    has_cre_payload,
    live_judge,
    need_replan_hint,
    prediction_clarify_draft,
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
    from cre_rentroll.scope import OOS_TEXT

    assert not has_cre_payload(OOS_TEXT)
    assert has_cre_payload('{"unit_count": 3, "occupancy_pct": 0.92}')

    assert has_cre_payload("Metro news is unavailable because search failed.")
    assert vault_calc_ok("", roll, []) is None
    assert vault_calc_ok("   ", roll, []) is None


def test_definition_draft_with_citations_is_not_missing_cre_payload(monkeypatch):
    q = "What's a rent roll?"
    draft = (
        "A rent roll is a schedule of tenants, rents, and lease terms for a "
        "property, used to underwrite in-place income. [1] FACT#1"
    )

    def boom_scope(message, history=None):
        raise AssertionError("score_gcr conceptual skip must not re-call scope classify")

    monkeypatch.setattr("cre_rentroll.scope._scope_agent_token", boom_scope)
    monkeypatch.setattr("cre_rentroll.scope.in_scope", lambda *a, **k: False)
    monkeypatch.setattr("cre_rentroll.scope.classify_scope", lambda *a, **k: False)
    assert ask_is_conceptual(q)
    assert ask_is_conceptual("What\u2019s a rent roll?")
    assert ask_is_conceptual("What is occupancy?")
    assert ask_is_conceptual("What is NOI?")
    assert has_cre_payload(draft, q)
    _mock_openai(monkeypatch, '{"R":5,"G":5,"request_C":5}')
    traces = [
        {
            "name": "web_search",
            "result": {"hits": [{"title": "Rent roll", "url": "https://example.com"}]},
        }
    ]
    scores = live_judge(q, draft, None, traces)
    assert scores.judge == "llm"
    assert scores.reason != "draft missing CRE payload"
    assert scores.G == 5 and scores.request_C == 5
    assert scores.vault_calc_ok is True
    assert gate(scores, retry_used=False) == "show"
    assert "draft missing CRE payload" not in format_eval_log(scores, "show")


def test_occupancy_number_ask_requires_vault_payload(monkeypatch):
    q = "What is occupancy on this rent roll?"
    definition = "Occupancy is the share of units that are leased rather than vacant."
    assert not ask_is_conceptual(q)
    assert not has_cre_payload(definition, q)
    called = {"n": 0}

    class _Boom:
        def __init__(self, **kwargs):
            called["n"] += 1
            raise AssertionError("should not judge a numeric occupancy draft with no vault figures")

    import openai as openai_mod

    monkeypatch.setattr("cre_rentroll.settings.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr(openai_mod, "OpenAI", _Boom)
    scores = live_judge(q, definition, None, [])
    assert called["n"] == 0
    assert scores.judge == "empty"
    assert scores.reason == "draft missing CRE payload"
    assert scores.G is None and scores.vault_calc_ok is None
    assert gate(scores, retry_used=False) == "error"
    assert has_cre_payload("Occupancy is 92.07%.", q)


def test_comps_listing_draft_is_on_topic_not_missing_payload(monkeypatch):
    q = "What's the average property value in that area?"
    draft = (
        "Recent listings in that metro average about $185,000. "
        "See https://www.zillow.com/homedetails/example and "
        "https://www.redfin.com/example. [1]"
    )
    assert ask_is_market_comps(q)
    assert not ask_is_conceptual(q)
    assert has_cre_payload(draft, q)
    hint = need_replan_hint(q)
    assert "rent-roll definition" in hint.lower()
    assert "do not" in hint.lower()
    assert "those work tools" not in hint
    _mock_openai(
        monkeypatch,
        '{"R":5,"G":5,"request_C":5,"reason":"cited comps and listing links"}',
    )
    traces = [
        {
            "name": "web_search",
            "result": {
                "hits": [
                    {
                        "title": "Area listings",
                        "url": "https://www.zillow.com/homedetails/example",
                    }
                ]
            },
        }
    ]
    scores = live_judge(q, draft, None, traces)
    assert scores.judge == "llm"
    assert scores.reason != "draft missing CRE payload"
    assert scores.G == 5 and scores.request_C == 5 and scores.R == 5
    assert gate(scores, retry_used=False) == "show"
    assert "off-topic" not in (scores.reason or "").lower()
    assert "draft missing CRE payload" not in format_eval_log(scores, "show")


def test_area_demographics_draft_is_on_topic_not_missing_payload(monkeypatch):
    draft = (
        "Census estimates about 58,400 residents in that zip. "
        "See https://www.census.gov/example [1]"
    )
    traces = [
        {
            "name": "web_search",
            "result": {
                "hits": [
                    {
                        "title": "Area profile",
                        "url": "https://www.census.gov/example",
                    }
                ]
            },
        }
    ]
    asks = (
        "What's the population in that area?",
        "populatin in that area",
        "zip 77082",
        "Houston",
    )
    for q in asks:
        assert ask_is_market_area(q)
        assert ask_skips_vault_payload(q)
        assert not ask_is_conceptual(q)
        assert has_cre_payload(draft, q)
    assert not ask_is_market_area("How are you today")
    assert not ask_skips_vault_payload("How are you today")
    q = asks[0]
    hint = need_replan_hint(q)
    assert "occupancy" in hint.lower()
    assert "rent-roll definition" in hint.lower()
    assert "those work tools" not in hint
    _mock_openai(
        monkeypatch,
        '{"R":5,"G":5,"request_C":5,"reason":"cited area/market figure from search"}',
    )
    scores = live_judge(q, draft, None, traces)
    assert scores.judge == "llm"
    assert scores.reason != "draft missing CRE payload"
    assert scores.G == 5 and scores.request_C == 5 and scores.R == 5
    assert gate(scores, retry_used=False) == "show"
    assert "off-topic" not in (scores.reason or "").lower()
    assert "not a rent-roll question" not in (scores.reason or "").lower()
    assert "draft missing CRE payload" not in format_eval_log(scores, "show")


def test_failed_eval_does_not_return_comps_draft(monkeypatch):
    from cre_rentroll.harness import run_task
    from cre_rentroll.run_events import bind, reset
    from cre_rentroll.tools import score_gcr

    comps = (
        "Recent listings in that metro average about $185,000. "
        "See https://www.zillow.com/homedetails/example"
    )

    def fake_agent(*_a, **_k):
        score_gcr(comps)
        return comps

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: None)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(
            R=1,
            G=1,
            request_C=1,
            vault_calc_ok=None,
            judge="llm",
            reason="off-topic as it does not address the CRE rent-roll question",
        ),
    )
    token = bind(lambda *_a, **_k: None)
    try:
        outcome = run_task(
            "tester",
            "What's the average property value in that area?",
            skip_scope=True,
        )
    finally:
        reset(token)
    assert outcome.stop_reason == "eval_error"
    assert outcome.kind == "error"
    assert "185,000" not in (outcome.text or "")
    assert "zillow" not in (outcome.text or "").lower()
    assert "could not finish that answer this turn" in (outcome.text or "").lower()


def test_locate_correction_is_on_topic_payload_not_market_area():
    draft = (
        "The property geocodes to Example Apartments, Example City, TX, "
        "United States at lat 29.7195 lon -95.6084."
    )
    traces = [
        {
            "name": "osm_geocode",
            "result": {
                "ok": True,
                "display_name": "Example Apartments, Example City, TX, United States",
                "lat": 29.7195,
                "lon": -95.6084,
            },
        }
    ]
    asks = (
        "It is not in Mexico it is in the USA",
        "Search the USA map",
        "Where is this property? Can you put it on a map?",
    )
    for q in asks:
        assert ask_is_locate(q)
        assert not ask_is_market_area(q)
        assert not ask_is_conceptual(q)
        assert ask_skips_vault_payload(q)
        assert has_cre_payload(draft, q)
        assert cited_locate_map_draft(q, draft, traces)
    hint = need_replan_hint(asks[0])
    assert "off-topic" in hint.lower()
    assert "osm_geocode" in hint.lower()
    assert "occupancy" in hint.lower()
    assert not ask_is_locate("What's the average property value in that area?")
    assert not cited_locate_map_draft(asks[0], "Hello there friend.", traces)


def test_cited_locate_offtopic_eval_shows_map_draft(monkeypatch):
    from cre_rentroll.harness import run_task
    from cre_rentroll.run_events import bind, reset
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import record

    draft = (
        "The property geocodes to Example Apartments, Example City, TX, "
        "United States at lat 29.7195 lon -95.6084."
    )
    geo = {
        "ok": True,
        "display_name": "Example Apartments, Example City, TX, United States",
        "lat": 29.7195,
        "lon": -95.6084,
    }

    def fake_agent(*_a, **_k):
        record("osm_geocode", {"query": "property USA"}, geo)
        score_gcr(draft)
        return draft

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "FACT#property_name=La Cordoba")
    monkeypatch.setattr(
        "cre_rentroll.vault.load_roll",
        lambda u: CanonicalRentRoll(
            layout="one_row",
            property_name="La Cordoba",
            units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
        ),
    )
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(
            R=1,
            G=1,
            request_C=1,
            vault_calc_ok=None,
            judge="llm",
            reason="off-topic as it does not address the CRE rent-roll question",
        ),
    )
    token = bind(lambda *_a, **_k: None)
    try:
        outcome = run_task(
            "tester",
            "Search the USA map",
            skip_scope=True,
        )
    finally:
        reset(token)
    assert outcome.kind == "answer"
    assert "could not finish" not in (outcome.text or "").lower()
    assert "29.7195" in (outcome.text or "")
    assert "Example Apartments" in (outcome.text or "")


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


def test_vault_calc_cap_rate_percent_vs_fraction_and_rounding():
    class CapRoll(CanonicalRentRoll):
        @property
        def unit_count(self) -> int:
            return 252

        @property
        def occupied_count(self) -> int:
            return 247

        @property
        def vacant_count(self) -> int:
            return 5

        @property
        def mtm_count(self) -> int:
            return 5

        @property
        def occupancy_pct(self) -> float:
            return 0.9802

        @property
        def in_place_monthly(self) -> float:
            return 290851.0

    roll = CapRoll(
        layout="one_row",
        units=[UnitRow(unit_id="1", in_place_rent=290851.0, vacant=False, mtm=True)],
    )
    traces = [
        {
            "name": "compute_cap_rate",
            "result": {
                "ask_price": 12_000_000,
                "basis": 3_490_212.0,
                "implied_cap": 0.290826,
                "implied_on_income": True,
            },
        }
    ]
    draft = (
        "The property, located at Barbers Hill Elementary School North, Perry Avenue, "
        "Mont Belvieu, Chambers County, Texas, has 252 units, of which 5 are "
        "month-to-month leases. The implied cap rate at a $12 million asking price "
        "is approximately 29.08%."
    )
    assert vault_calc_ok(draft, roll, traces) is True
    assert vault_calc_invented(draft, roll, traces) == []
    assert vault_calc_ok("Implied cap 0.2908", roll, traces) is True
    assert vault_calc_ok("Implied cap 29.1%.", roll, traces) is True
    assert vault_calc_ok("Implied cap 29%.", roll, traces) is True
    assert vault_calc_ok("Implied cap 8%.", roll, traces) is None
    assert "8%" in vault_calc_invented("Implied cap 8%.", roll, traces)
    eight = [
        {
            "name": "compute_cap_rate",
            "result": {"ask_price": 12_000_000, "basis": 960_000.0, "implied_cap": 0.08},
        }
    ]
    assert vault_calc_ok("Implied cap 8.0%.", roll, eight) is True
    assert vault_calc_ok("Implied cap 0.080", roll, eight) is True
    assert vault_calc_ok("Implied cap 29%.", roll, eight) is None


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


def test_score_gcr_cited_definition_skips_missing_payload(monkeypatch):
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import reset_turn, set_eval_query

    q = "What's a rent roll?"
    draft = (
        "A rent roll is a property's schedule of tenants, rents, and lease terms, "
        "used to underwrite in-place income. [1]"
    )
    monkeypatch.setattr("cre_rentroll.scope.in_scope", lambda *a, **k: False)
    monkeypatch.setattr(
        "cre_rentroll.scope._scope_agent_token",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no scope hop in score_gcr")),
    )
    _mock_openai(monkeypatch, '{"R":5,"G":5,"request_C":5,"reason":"cited definition"}')
    reset_turn("tester")
    set_eval_query(q)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: None)
    out = json.loads(score_gcr(draft))
    assert out["judge"] == "llm"
    assert out["decision"] == "ok"
    assert out.get("reason") != "draft missing CRE payload"
    assert "compute_income" not in (out.get("hint") or "").lower()


def test_definition_need_replan_does_not_add_vault_calculators(monkeypatch):
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import reset_turn, set_eval_query

    q = "What's a rent roll?"
    hint = need_replan_hint(q)
    assert "vault calculators" in hint.lower()
    assert "web_search" in hint.lower()
    assert "compute_noi" in hint
    assert "those work tools" not in hint
    income_hint = need_replan_hint(
        "What is in-place monthly income, and what would annualized NOI be if opex is 35% of EGI?"
    )
    assert "those work tools" in income_hint
    assert "vault calculators" not in income_hint.lower()

    why = "user wanted a definition not location/occupancy/cap"
    reset_turn("tester")
    set_eval_query(q)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: None)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(
            R=5, G=1, request_C=5, vault_calc_ok=None, judge="llm", reason=why
        ),
    )
    out = json.loads(
        score_gcr("Occupancy is 92% and implied cap is 8% at a $12M ask.")
    )
    assert out["decision"] == "need_replan"
    assert "vault calculators" in (out.get("hint") or "").lower()
    assert "web_search" in (out.get("hint") or "").lower()
    assert "those work tools" not in (out.get("hint") or "")
    assert why in (out.get("reason") or "")


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
    assert "0.2908" in JUDGE_SYS
    assert "29.08%" in JUDGE_SYS
    assert "0.080" in JUDGE_SYS
    assert "8.0%" in JUDGE_SYS
    assert "8% vs 29%" in JUDGE_SYS
    assert "vacant-only" in JUDGE_SYS.lower() or "vacant only" in JUDGE_SYS.lower()
    assert "100% occupancy" in JUDGE_SYS.lower()
    assert "same as with vacancy" in JUDGE_SYS.lower()
    assert "user-stated scenario rents" in JUDGE_SYS.lower()
    assert "vacant-fill-only" in JUDGE_SYS.lower() or "vacant fill" in JUDGE_SYS.lower()
    assert "DeepEval" in JUDGE_SYS
    assert '"reason"' in JUDGE_SYS
    assert "interview" in JUDGE_SYS.lower()
    assert "gold.json" in JUDGE_SYS
    assert "identity" in JUDGE_SYS.lower()
    assert "rent-roll-only refuse" in JUDGE_SYS.lower()
    assert "conceptual" in JUDGE_SYS.lower()
    assert "what is a rent roll" in JUDGE_SYS.lower()
    assert "average property value" in JUDGE_SYS.lower()
    assert "listing" in JUDGE_SYS.lower()
    assert "off-topic" in JUDGE_SYS.lower()
    assert "this-roll" in JUDGE_SYS.lower() or "this-roll payload" in JUDGE_SYS.lower()
    assert "area/market" in JUDGE_SYS.lower()
    assert "demographics" in JUDGE_SYS.lower()
    assert "hazards" in JUDGE_SYS.lower()
    assert "not a rent-roll question" in JUDGE_SYS.lower()
    assert "search hits" in JUDGE_SYS.lower()
    assert "locate" in JUDGE_SYS.lower()
    assert "map" in JUDGE_SYS.lower()
    assert "country" in JUDGE_SYS.lower()
    assert "osm_geocode" in JUDGE_SYS.lower()
    assert "population" not in JUDGE_SYS.lower()
    assert "flood" not in JUDGE_SYS.lower()
    assert "houston" not in JUDGE_SYS.lower()
    assert "mexico" not in JUDGE_SYS.lower()
    assert "united states" not in JUDGE_SYS.lower()
    assert "what's your name" not in JUDGE_SYS.lower()
    assert "who are you" not in JUDGE_SYS.lower()
    assert "ask_prediction_agent" in JUDGE_SYS
    assert "clarifying question" in JUDGE_SYS.lower()
    assert "which group/plan/variant" in JUDGE_SYS.lower()
    assert "clarify-from-predictor" in JUDGE_SYS.lower()
    assert "query_units occupancy" in JUDGE_SYS.lower()
    assert "must include occupancy % from the vault" in JUDGE_SYS.lower()
    assert "la cordoba" not in JUDGE_SYS.lower()
    assert "80019" not in JUDGE_SYS


def test_predictor_clarify_draft_is_complete_not_missing_occupancy(monkeypatch):
    q = "What occupancy do you predict?"
    draft = (
        "Which floor plan should I score? Pick one:\n"
        "- Plan Alpha\n"
        "- Plan Beta (renovated)"
    )
    traces = [
        {
            "name": "ask_prediction_agent",
            "result": {"ok": True, "answer": draft, "source": "vacancy-prediction-agent"},
        }
    ]
    roll = CanonicalRentRoll(
        layout="one_row",
        property_name="Hill",
        units=[UnitRow(unit_id="1", in_place_rent=1500, vacant=False)],
    )
    captured = _mock_openai(
        monkeypatch,
        '{"R":5,"G":5,"request_C":5,"reason":"quoted predictor clarifying question"}',
    )
    assert prediction_clarify_draft(draft, traces)
    assert has_cre_payload(draft, q, traces)
    assert not ask_is_conceptual(q)
    hint = need_replan_hint(q, prediction_clarify=True)
    assert "query_units occupancy" in hint.lower()
    assert "those work tools" not in hint
    facts = _judge_facts(roll, traces)
    assert facts["predictor_clarify"] is True
    assert "occupancy_pct" not in (facts["roll_abstract"] or {})
    assert "occupancy_pct_display" not in (facts["roll_abstract"] or {})
    assert "in_place_monthly" not in (facts["roll_abstract"] or {})
    scores = live_judge(q, draft, roll, traces)
    assert scores.judge == "llm"
    assert scores.reason != "draft missing CRE payload"
    assert scores.G == 5 and scores.request_C == 5 and scores.R == 5
    assert scores.vault_calc_ok is True
    assert gate(scores, retry_used=False) == "show"
    user = captured["kwargs"]["messages"][1]["content"]
    assert "clarifying question" in user.lower()
    assert "query_units occupancy" in user.lower()
    assert "occupancy_pct_display" not in user


def test_score_gcr_predictor_clarify_low_c_is_ok(monkeypatch):
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import record, reset_turn, set_eval_query

    q = "What occupancy do you predict?"
    draft = (
        "Which floor plan should I score? Pick one:\n"
        "- Plan Alpha\n"
        "- Plan Beta (renovated)"
    )
    reset_turn("tester")
    set_eval_query(q)
    record(
        "ask_prediction_agent",
        {"question": q},
        {"ok": True, "answer": draft, "source": "vacancy-prediction-agent"},
    )
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: None)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(
            R=3,
            G=3,
            request_C=2,
            vault_calc_ok=True,
            judge="llm",
            reason="does not predict occupancy",
        ),
    )
    out = json.loads(score_gcr(draft))
    assert out["decision"] == "ok"
    assert out["request_C"] >= 4
    assert out["G"] >= 4
    assert out["vault_calc_ok"] is True
    assert out.get("hint") == "ok to show this sentence"


def test_harness_predictor_clarify_is_not_eval_error(monkeypatch):
    from cre_rentroll.harness import run_task
    from cre_rentroll.run_events import bind, reset
    from cre_rentroll.tools import score_gcr
    from cre_rentroll.turn_ctx import record

    draft = (
        "Which floor plan should I score? Pick one:\n"
        "- Plan Alpha\n"
        "- Plan Beta (renovated)"
    )

    def fake_agent(*_a, **_k):
        record(
            "ask_prediction_agent",
            {"question": "What occupancy do you predict?"},
            {"ok": True, "answer": draft, "source": "vacancy-prediction-agent"},
        )
        score_gcr(draft)
        return draft

    monkeypatch.setattr("cre_rentroll.harness.openai_api_key", lambda: "sk-test")
    monkeypatch.setattr("cre_rentroll.harness.run_agent", fake_agent)
    monkeypatch.setattr("cre_rentroll.harness.facts_prompt", lambda u: "")
    monkeypatch.setattr("cre_rentroll.vault.load_roll", lambda u: None)
    monkeypatch.setattr("cre_rentroll.vault.load_session", lambda u: {"messages": []})
    monkeypatch.setattr("cre_rentroll.vault.save_session", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.dynamo.put_item", lambda *a, **k: None)
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: None)
    monkeypatch.setattr("cre_rentroll.harness.write_trace", lambda *a, **k: None)
    monkeypatch.setattr(
        "cre_rentroll.eval_service.live_judge",
        lambda *a, **k: EvalScores(
            R=3,
            G=3,
            request_C=2,
            vault_calc_ok=True,
            judge="llm",
            reason="does not predict occupancy",
        ),
    )
    token = bind(lambda *_a, **_k: None)
    try:
        outcome = run_task("tester", "What occupancy do you predict?", skip_scope=True)
    finally:
        reset(token)
    assert outcome.stop_reason != "eval_error"
    assert outcome.kind != "error"
    assert "which floor plan" in (outcome.text or "").lower()
    assert "plan alpha" in (outcome.text or "").lower()


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
    cap_facts = _judge_facts(
        roll,
        [{"name": "compute_cap_rate", "result": {"ask_price": 12_000_000, "implied_cap": 0.290826}}],
    )
    assert cap_facts["tools"][0]["result"]["implied_cap_pct"] == 29.08


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
