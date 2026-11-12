"""The live gateway Lambda must compute the roll. It must not call MCP again."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_HANDLER = Path(__file__).resolve().parents[1] / "deploy" / "lambda_gateway" / "handler.py"


def _handler():
    spec = importlib.util.spec_from_file_location("cre_gw_handler", _HANDLER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.lambda_handler


def test_query_units_uses_the_stamped_roll():
    roll = {
        "property_name": "Bay View",
        "as_of": "2021-09-30",
        "layout": "one_row",
        "units": [
            {"unit_id": "1", "occupancy": "Occupied", "in_place_rent": 1000, "sqft": 400, "mtm": True},
            {"unit_id": "2", "occupancy": "Occupied", "in_place_rent": 2000, "sqft": 600},
            {"unit_id": "3", "occupancy": "Vacant", "in_place_rent": 0, "sqft": 500},
        ],
    }
    out = _handler()(
        {
            "name": "query_units",
            "arguments": {"username": "ada", "route": "deal", "roll": roll},
        },
        None,
    )
    assert out["unit_count"] == 3
    assert out["vacant_count"] == 1
    assert out["occupancy_pct"] == 0.6667
    assert out["mtm_count"] == 1
    assert out["avg_sqft"] == 500
    assert out.get("ok") is not False
