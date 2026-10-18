from cre_rentroll.ingest import as_of_from_message
from cre_rentroll.present import answer_prose, is_operator_leak, user_visible
from cre_rentroll.scope import SEARCH_DOWN_TEXT


def test_hides_eval_retry_and_eval_banner():
    raw = (
        "Abstract this.\n\n[eval retry: fix vault/calc grounding and cover the request]"
    )
    assert "[eval retry" not in user_visible(raw)
    geo = """Mapped to 1 Main St (1.0, 2.0).

— Warning: live eval G=5 request-C=3 R=5 (score 3 band)."""
    prose = answer_prose(geo)
    assert "Warning" not in prose
    assert "Mapped to 1 Main St" in prose


def test_answer_prose_does_not_pick_fields_from_the_question():
    text = '{"property_name":"Hill","unit_count":252,"vacant_count":29}'
    q = "please give me only the vacant number of units"
    assert answer_prose(text, question=q) == text


def test_operator_leak_detects_need_replan_hint():
    leak = (
        "Need replan: call update_plan (MCP) with the NEW remaining work. "
        "Completed/incomplete rows stay on the list."
    )
    assert is_operator_leak(leak)
    assert not is_operator_leak(SEARCH_DOWN_TEXT)
    assert not is_operator_leak("Recent multifamily cap rates in Miami averaged 5.8%.")


def test_as_of_from_question_not_today():
    q = (
        "Abstract this rent roll: unit count, occupancy, in-place monthly income, "
        "vacant count, MTM if present as of 01/01/2021. Cite computed totals."
    )
    assert as_of_from_message(q) == "2021-01-01"
    q2 = (
        "Abstract this rent roll: unit count, occupancy, in-place monthly income, "
        "vacant count, MTM if present. Cite computed totals. Do not geocode or search."
    )
    assert as_of_from_message(q2) is None
