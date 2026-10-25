import json

from cre_rentroll.eval_service import vault_calc_invented
from cre_rentroll.mcp_runtime import VAULT_MCP, mcp_server_for
from cre_rentroll.tools import TOOL_IMPLS, ask_vacancy_agent
from cre_rentroll.vacancy_agent import VacancyAgent


def _units():
    rows = [{"plan": "1x1", "vacant": False}] * 40
    rows += [{"plan": "1x1", "vacant": True}] * 2
    rows += [{"plan": "1x1 Reno", "vacant": False}] * 12
    rows += [{"plan": "1x1 Reno", "vacant": True}] * 6
    return rows


def test_vacancy_agent_scores_the_emptier_plan_higher():
    out = VacancyAgent().run(_units())
    by = {row["plan"]: row["probability"] for row in out["plans"]}
    assert out["agent"] == "vacancy"
    assert out["steps"] == ["fit_vacancy_model"]
    assert by["1x1 Reno"] > by["1x1"]
    assert vault_calc_invented(out["answer"], None, [{"name": "ask_vacancy_agent", "result": out}]) == []


def test_vacancy_agent_can_score_one_plan():
    out = VacancyAgent().run(_units(), floor_plan="1x1 Reno")
    assert [row["plan"] for row in out["plans"]] == ["1x1 Reno"]


def test_deal_agent_tool_calls_the_vacancy_agent(monkeypatch):
    class Roll:
        as_of = "2021-05-25"
        units = []

        def __iter__(self):
            return iter(())

    unit_rows = []
    for raw in _units():
        unit_rows.append(_Unit(raw["plan"], raw["vacant"]))
    roll = Roll()
    roll.units = unit_rows
    monkeypatch.setattr("cre_rentroll.tools._roll", lambda: roll)
    monkeypatch.setattr("cre_rentroll.tools.turn_ctx.asked_as_of", lambda: None)
    recorded = {}
    monkeypatch.setattr(
        "cre_rentroll.tools.turn_ctx.record",
        lambda name, args, result: recorded.update(name=name, result=result),
    )
    payload = json.loads(ask_vacancy_agent("1x1 Reno"))
    assert recorded["name"] == "ask_vacancy_agent"
    assert payload["agent"] == "vacancy"
    assert payload["plans"][0]["plan"] == "1x1 Reno"
    assert TOOL_IMPLS["ask_vacancy_agent"] is ask_vacancy_agent
    assert mcp_server_for("ask_vacancy_agent") == VAULT_MCP


class _Unit:
    def __init__(self, plan: str, vacant: bool) -> None:
        self.unit_type = plan
        self.occupancy = "Vacant" if vacant else "Occupied"
        self._vacant = vacant

    def vacant_on(self, _as_of: str) -> bool:
        return self._vacant
