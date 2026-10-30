"""The in-gateway floor-plan fit tool is gone; one prediction tool remains."""

import inspect

from cre_rentroll.agents import BOOK
from cre_rentroll.capabilities_client import CAPABILITY_TOOLS
from cre_rentroll.tools import TOOL_IMPLS, WORK_TOOL_NAMES, ask_prediction_agent


def test_ask_vacancy_agent_is_off_the_menu_everywhere():
    assert "ask_vacancy_agent" not in BOOK.tool_names
    assert "ask_vacancy_agent" not in TOOL_IMPLS
    assert "ask_vacancy_agent" not in WORK_TOOL_NAMES
    assert "ask_vacancy_agent" not in CAPABILITY_TOOLS
    assert "ask_vacancy" not in BOOK.tool_names
    assert "ask_prediction_agent" in BOOK.tool_names
    assert "ask_prediction_agent" in TOOL_IMPLS


def test_the_prediction_tool_does_not_fit_or_load_a_roll_locally():
    source = inspect.getsource(ask_prediction_agent)
    assert "VacancyAgent" not in source
    assert "_sigmoid" not in source
    assert "load_roll" not in source
    assert "plan_table" not in source  # attached gateway-side, never by the model
    for name in TOOL_IMPLS:
        assert name != "ask_vacancy_agent"