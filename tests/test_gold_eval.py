from cre_rentroll.gold_eval import (
    case_prompts,
    case_query,
    expected_tools,
    load_cases,
    score_stats,
    tools_match,
)


def test_gold_file_has_the_six_cases():
    ids = [case["id"] for case in load_cases()]
    assert ids == ["Q1", "Q2", "Q3", "weather", "search-down", "avg-sqft"]


def test_case_prompts_are_the_questions_sent():
    cases = load_cases()
    prompts = case_prompts()
    assert [row["id"] for row in prompts] == [case["id"] for case in cases]
    by_id = {row["id"]: row["query"] for row in prompts}
    q3 = next(case["query"] for case in cases if case["id"] == "Q3")
    assert by_id["search-down"] == q3
    assert "forced to fail" not in by_id["search-down"]
    assert by_id["weather"] == next(case["query"] for case in cases if case["id"] == "weather")
    assert by_id["Q1"] == cases[0]["query"]


def test_search_down_sends_the_q3_question():
    cases = load_cases()
    search = next(case for case in cases if case["id"] == "search-down")
    q3 = next(case for case in cases if case["id"] == "Q3")
    query, fail = case_query(search, cases)
    assert fail is True
    assert query == q3["query"]
    assert "forced to fail" not in query


def test_q1_tool_match():
    cases = load_cases()
    q1 = cases[0]
    ok = tools_match(q1, cases, ["query_units", "compute_income", "score_gcr"])
    assert ok["ok"] is True
    missed = tools_match(q1, cases, ["query_units"])
    assert missed["missing"] == ["compute_income"]
    banned = tools_match(q1, cases, ["query_units", "compute_income", "osm_geocode"])
    assert banned["forbidden_used"] == ["osm_geocode"]


def test_weather_fails_when_a_work_tool_runs():
    cases = load_cases()
    weather = next(case for case in cases if case["id"] == "weather")
    required, forbidden = expected_tools(weather, cases)
    assert required == set()
    assert "web_search" in forbidden
    assert tools_match(weather, cases, [])["ok"] is True
    assert tools_match(weather, cases, ["web_search"])["ok"] is False


def test_search_down_expects_q3_tools():
    cases = load_cases()
    search = next(case for case in cases if case["id"] == "search-down")
    matched = tools_match(search, cases, ["web_search"])
    assert matched["ok"] is True
    assert tools_match(search, cases, ["web_search", "osm_geocode"])["ok"] is False


def test_score_stats_one_run_has_zero_stdev():
    stats = score_stats([4])
    assert stats["n"] == 1
    assert stats["mean"] == 4
    assert stats["stdev"] == 0
    empty = score_stats([None, None])
    assert empty["n"] == 0
    assert empty["mean"] is None
