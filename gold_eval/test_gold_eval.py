from types import SimpleNamespace

from gold_eval import (
    case_prompts,
    load_cases,
    place_hit,
    score_accuracy,
    score_stats,
    score_tool_calls,
    summarize,
)
from gold_eval.store import load_result, save_result


def _roll(**kwargs):
    base = dict(
        unit_count=10,
        occupancy_pct=0.9,
        in_place_monthly=1000.0,
        vacant_count=1,
        mtm_count=2,
        suggested_address="100 Congress Ave, Austin, TX 78701",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_gold_file_has_q1_and_multi_1():
    ids = [case["id"] for case in load_cases()]
    assert ids == ["Q1", "Multi-1"]


def test_case_prompts_are_the_questions_sent():
    cases = load_cases()
    prompts = case_prompts()
    assert [row["id"] for row in prompts] == [case["id"] for case in cases]
    by_id = {row["id"]: row["query"] for row in prompts}
    assert by_id["Q1"] == cases[0]["query"]
    assert "map" in by_id["Multi-1"]
    assert "vacant" in by_id["Multi-1"]


def test_q1_tool_budget_ignores_score_gcr():
    q1 = load_cases()[0]
    ok = score_tool_calls(q1, ["query_units", "compute_income", "score_gcr"])
    assert ok["ok"] is True
    assert ok["used"] == 2
    assert ok["budget"] == 2
    repeat = score_tool_calls(q1, ["query_units", "query_units", "compute_income"])
    assert repeat["ok"] is False
    assert repeat["used"] == 3
    assert repeat["extra"] == ["query_units"]
    missed = score_tool_calls(q1, ["query_units"])
    assert missed["missing"] == ["compute_income"]
    banned = score_tool_calls(q1, ["query_units", "compute_income", "osm_geocode"])
    assert banned["forbidden_used"] == ["osm_geocode"]
    assert banned["extra"] == ["osm_geocode"]


def test_multi_1_budget_is_one_geocode_and_one_query():
    case = next(row for row in load_cases() if row["id"] == "Multi-1")
    ok = score_tool_calls(case, ["osm_geocode", "query_units"])
    assert ok["ok"] is True
    assert ok["budget"] == 2
    extra = score_tool_calls(case, ["osm_geocode", "osm_geocode", "query_units"])
    assert extra["ok"] is False
    assert extra["used"] == 3
    assert extra["extra"] == ["osm_geocode"]
    search = score_tool_calls(case, ["query_units", "osm_geocode", "web_search"])
    assert search["forbidden_used"] == ["web_search"]


def test_q1_accuracy_accepts_percent_and_requires_every_fact():
    q1 = load_cases()[0]
    roll = _roll()
    hit = score_accuracy(
        q1,
        "10 units, occupancy 90%, in-place monthly $1,000, 1 vacant, 2 month-to-month.",
        roll,
    )
    assert hit["pass"] is True
    assert hit["gold_answer"] == (
        "unit count 10 · occupancy 90.00% · in-place monthly 1000 · "
        "vacant count 1 · month-to-month 2"
    )
    fraction = score_accuracy(
        q1,
        "10 units, occupancy 0.9, in-place monthly 1000, 1 vacant, 2 MTM.",
        roll,
    )
    assert fraction["pass"] is True
    missed = score_accuracy(
        q1,
        "10 units, occupancy 90%, in-place monthly $1,000, 1 vacant.",
        roll,
    )
    assert missed["pass"] is False
    labels = {fact["label"]: fact["hit"] for fact in missed["facts"]}
    assert labels["month-to-month"] is False
    assert labels["unit count"] is True


def test_q1_skips_month_to_month_when_the_roll_has_none():
    q1 = load_cases()[0]
    scored = score_accuracy(
        q1,
        "10 units, occupancy 90%, in-place monthly $1,000, 1 vacant.",
        _roll(mtm_count=0),
    )
    assert scored["pass"] is True
    assert "month-to-month" not in {fact["label"] for fact in scored["facts"]}


def test_multi_1_accuracy_needs_the_place():
    case = next(row for row in load_cases() if row["id"] == "Multi-1")
    roll = _roll()
    numbers = "1 vacant and occupancy 90%."
    assert score_accuracy(case, numbers, roll)["pass"] is False
    city = score_accuracy(case, numbers + " The property is in Austin.", roll)
    assert city["pass"] is True
    assert city["gold_answer"] == (
        "vacant count 1 · occupancy 90.00% · "
        "place 100 Congress Ave, Austin, TX 78701"
    )
    street = score_accuracy(case, numbers + " It is at 100 Congress Ave.", roll)
    assert street["pass"] is True
    assert place_hit("Nowhere in particular.", roll.suggested_address) is False


def test_summarize_counts_accuracy_and_tool_calls():
    summary = summarize(
        ["Q1"],
        {
            "Q1": [
                {"G": 5, "C": 5, "R": 5, "accuracy": True, "tools_ok": True},
                {"G": 4, "C": 5, "R": 5, "accuracy": False, "tools_ok": False},
            ]
        },
    )
    row = summary[0]
    assert row["runs"] == 2
    assert row["accuracy_passed"] == 1
    assert row["tool_calls_passed"] == 1
    assert row["G"]["n"] == 2


def test_saved_result_is_per_user_and_replaced(monkeypatch):
    saved: dict[tuple[str, str], dict] = {}

    def put(username, sk, attrs):
        saved[(username, sk)] = dict(attrs)

    def get(username, sk):
        return saved.get((username, sk))

    monkeypatch.setattr("cre_rentroll.dynamo.put_item", put)
    monkeypatch.setattr("cre_rentroll.dynamo.get_item", get)
    save_result(
        "ada",
        runs=1,
        status="Finished",
        summary=[{"id": "Q1"}],
        log=[{"case": "Q1", "run": 1, "summary": {"id": "Q1"}}],
    )
    save_result(
        "ada",
        runs=3,
        status="Finished",
        summary=[{"id": "Q1"}, {"id": "Multi-1"}],
        log=[{"case": "Multi-1", "run": 1, "G": 5}],
    )
    save_result(
        "bob",
        runs=1,
        status="Finished",
        summary=[{"id": "Q1"}],
        log=[{"case": "Q1", "run": 1}],
    )
    ada = load_result("ada")
    assert ada is not None
    assert ada["runs"] == 3
    assert [row["id"] for row in ada["summary"]] == ["Q1", "Multi-1"]
    assert ada["log"] == [{"case": "Multi-1", "run": 1, "G": 5}]
    assert load_result("bob")["log"][0]["case"] == "Q1"
    assert load_result("cara") is None


def test_score_stats_one_run_has_zero_stdev():
    stats = score_stats([4])
    assert stats["n"] == 1
    assert stats["mean"] == 4
    assert stats["stdev"] == 0
    empty = score_stats([None, None])
    assert empty["n"] == 0
    assert empty["mean"] is None
