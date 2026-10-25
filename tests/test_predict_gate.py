from cre_rentroll.agents import BOOK
from cre_rentroll.chips import allow_predict, chip_prompt, filter_book_tools


def test_predict_chip_allows_inventory():
    assert allow_predict("predict", "what is occupancy today") is True
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
    none = filter_book_tools(BOOK.tool_names, "", "forecast expirations and MTM")
    assert "ask_prediction_agent" in none


def test_chip_prompt_steers_not_forces():
    text = chip_prompt("predict")
    assert "SHOULD" in text
    assert "ask_prediction_agent" in text
    assert "force" in text.lower() or "Do not force" in text or "skip" in text.lower()
