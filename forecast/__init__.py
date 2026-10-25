"""Predict the rent similar units in the same building are paying today.

Each rent roll is fit on its own occupied units. The line uses square feet,
bedrooms, a renovation flag, and residential versus retail. The prediction
is that building's peer rent, in dollars per month.
"""

from __future__ import annotations

from forecast.linear import LinearModel, fit_gradient_descent
from forecast.rolls import FEATURE_NAMES, feature_row


def fit_peer_rent(units: list[dict]) -> LinearModel:
    """Fit in-place rent on the occupied units of one roll."""
    rows: list[list[float]] = []
    rents: list[float] = []
    for unit in units:
        rent = unit.get("in_place", unit.get("in_place_rent"))
        sqft = unit.get("sqft")
        if rent is None or sqft is None:
            continue
        if float(rent) < 100 or float(sqft) < 50:
            continue
        rows.append(feature_row(unit))
        rents.append(float(rent))
    if len(rents) < 5:
        raise ValueError("need at least 5 occupied units with rent and square feet")
    return fit_gradient_descent(rows, rents, FEATURE_NAMES)


def peer_rent(model: LinearModel, unit: dict) -> float:
    return model.predict_row(feature_row(unit))


def dollars_per_sqft(model: LinearModel) -> float:
    index = model.feature_names.index("sqft")
    return model.weights[index] / model.stds[index]
