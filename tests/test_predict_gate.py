from pathlib import Path

from cre_rentroll.agents import BOOK
from cre_rentroll.chips import allow_predict, filter_book_tools
from cre_rentroll.predict_followup import reset_predict_state


def test_predict_menu_follows_the_question():
    assert allow_predict("what is occupancy today") is False
    assert allow_predict("What's occupancy today?") is False
    assert allow_predict("What occupancy do you predict?") is True
    assert allow_predict("Over the next 12 months, what occupancy do you predict?") is True
    assert allow_predict("forecast occupancy") is True
    assert allow_predict("weekend weather") is False


def test_filter_hides_predict_unless_the_question_is_forward():
    names = filter_book_tools(BOOK.tool_names, "occupancy today")
    assert "ask_prediction_agent" not in names
    assert "query_units" in names
    shown = filter_book_tools(BOOK.tool_names, "What occupancy do you predict?")
    assert "ask_prediction_agent" in shown
    none = filter_book_tools(BOOK.tool_names, "forecast expirations and MTM")
    assert "ask_prediction_agent" in none


def test_predict_skill_does_not_name_chips():
    gateway = Path(__file__).resolve().parents[2] / "capabilities-gateway" / "skills"
    rr = (gateway / "rr-predict" / "SKILL.md").read_text(encoding="utf-8")
    cite = (gateway / "citation-policy" / "SKILL.md").read_text(encoding="utf-8")
    assert "chip" not in rr.lower()
    assert "complete answer for this user turn" in rr.lower()
    assert "stop react" in rr.lower()
    assert "did not write" in rr.lower()
    assert "query_units" in rr.lower()
    assert "clarifying question" in cite.lower()
    assert "occupancy_pct" in cite
    for banned in ("La Cordoba", "Bay View", "Chambers Hill", "1x1", "2x2", "80019"):
        assert banned not in rr
        assert banned not in cite
    assert "92.07" not in rr
    assert "92.07" not in cite


def test_followup_keeps_predict_tool(monkeypatch):
    reset_predict_state()
    assert allow_predict("Plan A", username="tester") is False
    monkeypatch.setattr(
        "cre_rentroll.predict_followup.session_awaiting_followup",
        lambda user: user == "tester",
    )
    assert allow_predict("Plan A", username="tester") is True
    names = filter_book_tools(BOOK.tool_names, "Plan A", username="tester")
    assert "ask_prediction_agent" in names
