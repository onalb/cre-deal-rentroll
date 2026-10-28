from pathlib import Path

from cre_rentroll.agents import BOOK
from cre_rentroll.chips import allow_predict, chip_prompt, filter_book_tools
from cre_rentroll.predict_followup import reset_predict_state


def test_predict_chip_allows_inventory():
    assert allow_predict("predict", "what is occupancy today") is True
    assert allow_predict("predict_occupancy_only", "what is occupancy today") is True
    assert allow_predict("abstract", "what occupancy do you predict") is False
    assert allow_predict("locate", "forecast occupancy") is False
    assert allow_predict("weather", "weekend weather") is False
    assert allow_predict("", "What's occupancy today?") is False
    assert allow_predict("", "Over the next 12 months, what occupancy do you predict?") is True


def test_filter_hides_predict_for_current_chip():
    names = filter_book_tools(BOOK.tool_names, "abstract", "occupancy today")
    assert "ask_prediction_agent" not in names
    assert "query_units" in names
    shown = filter_book_tools(BOOK.tool_names, "predict", "occupancy today")
    assert "ask_prediction_agent" in shown
    only = filter_book_tools(BOOK.tool_names, "predict_occupancy_only", "What occupancy do you predict?")
    assert "ask_prediction_agent" in only
    none = filter_book_tools(BOOK.tool_names, "", "forecast expirations and MTM")
    assert "ask_prediction_agent" in none


def test_chip_prompt_steers_not_forces():
    text = chip_prompt("predict")
    assert "SHOULD" in text
    assert "ask_prediction_agent" in text
    assert "force" in text.lower() or "Do not force" in text or "skip" in text.lower()
    only = chip_prompt("predict_occupancy_only")
    assert "SHOULD" in only
    assert "ask_prediction_agent" in only
    assert "occupancy only" in only.lower()
    assert "expirations" in only.lower()
    assert only != text
    assert "not trained" in text.lower()
    assert "vault property" not in text.lower()
    assert "vault property" not in only.lower()
    assert "do not invent a building" in text.lower()
    gateway = Path(__file__).resolve().parents[2] / "capabilities-gateway" / "skills"
    rr = (gateway / "rr-predict" / "SKILL.md").read_text(encoding="utf-8")
    cite = (gateway / "citation-policy" / "SKILL.md").read_text(encoding="utf-8")
    assert "complete answer for this user turn" in rr.lower()
    assert "stop react" in rr.lower()
    assert "did not write" in rr.lower()
    assert "query_units" in rr.lower()
    assert "clarifying question" in cite.lower()
    assert "occupancy_pct" in cite
    for banned in ("La Cordoba", "Bay View", "Chambers Hill", "1x1", "2x2", "80019"):
        assert banned not in rr
        assert banned not in cite
        assert banned not in text
        assert banned not in only
    assert "92.07" not in rr
    assert "92.07" not in cite
    assert "92.07" not in text
    assert "92.07" not in only


def test_followup_keeps_predict_tool_without_chip(monkeypatch):
    reset_predict_state()
    assert allow_predict("", "Plan A", username="tester") is False
    monkeypatch.setattr(
        "cre_rentroll.predict_followup.session_awaiting_followup",
        lambda user: user == "tester",
    )
    assert allow_predict("", "Plan A", username="tester") is True
    names = filter_book_tools(BOOK.tool_names, "", "Plan A", username="tester")
    assert "ask_prediction_agent" in names
    hint = chip_prompt("", "Plan A", username="tester")
    assert "ask_prediction_agent" in hint
    assert "follow-up" in hint.lower()
