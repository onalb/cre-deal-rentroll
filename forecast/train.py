"""Fit one peer-rent line per property and write forecast/model.json.

Target is the occupied unit's current contractual rent. Features are square
feet, bedrooms, renovation, and retail. Each property is fit on its own.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from forecast.linear import fit_gradient_descent, ordinary_least_squares, r2, rmse
from forecast.rolls import FEATURE_NAMES, load_training_rows

DEFAULT_DIR = Path(r"C:\Users\burak\Desktop\ML Interview")
MODEL_PATH = Path(__file__).resolve().parent / "model.json"


def load_rows(folder: Path | None = None) -> list[dict]:
    folder = folder or DEFAULT_DIR
    return load_training_rows(
        folder / "La Cordoba Rent Roll 04-20-21 (2) 164.xlsx",
        folder / "RentRoll-Bay View Gardens Apartments-20210930-040414 - 250.xlsx",
        folder / "RentRoll-Chambers Hill Elementary School-20231024-180834 - 252.xlsx",
    )


def _score(rows: list[dict], model, train_mean: float) -> dict:
    pred = [model.predict_row(r["x"]) for r in rows]
    actual = [float(r["y"]) for r in rows]
    return {
        "n": len(rows),
        "rmse": round(rmse(pred, actual), 2),
        "r2": round(r2(pred, actual), 3),
        "mean_rent_baseline_rmse": round(rmse([train_mean] * len(rows), actual), 2),
    }


def _holdout(rows: list[dict], fraction: float = 0.2, seed: int = 0) -> dict:
    rng = random.Random(seed)
    group = rows[:]
    rng.shuffle(group)
    cut = max(1, int(round(len(group) * fraction)))
    test, train = group[:cut], group[cut:]
    y = [float(r["y"]) for r in train]
    model = fit_gradient_descent([r["x"] for r in train], y, FEATURE_NAMES)
    return _score(test, model, sum(y) / len(y))


def _fit_property(name: str, rows: list[dict]) -> dict:
    x = [r["x"] for r in rows]
    y = [float(r["y"]) for r in rows]
    model = fit_gradient_descent(x, y, FEATURE_NAMES)
    ols_bias, ols_w = ordinary_least_squares(x, y, model.means, model.stds, model.l2)
    weight_gap = max(abs(model.bias - ols_bias), *(abs(a - b) for a, b in zip(model.weights, ols_w)))
    per_sqft = model.weights[FEATURE_NAMES.index("sqft")] / model.stds[FEATURE_NAMES.index("sqft")]
    return {
        "n": len(rows),
        "mean_rent": round(sum(y) / len(y), 2),
        "dollars_per_sqft": round(per_sqft, 3),
        "bias": model.bias,
        "weights": model.weights,
        "means": model.means,
        "stds": model.stds,
        "epochs_run": model.epochs_run,
        "train_rmse": round(model.train_rmse, 2),
        "gradient_descent_vs_normal_equations_max_abs_weight_diff": weight_gap,
        "holdout_20pct": _holdout(rows),
    }


def main() -> None:
    rows = load_rows()
    names = sorted({r["property"] for r in rows})
    by_property = {name: _fit_property(name, [r for r in rows if r["property"] == name]) for name in names}
    payload = {
        "target": "in_place_rent",
        "features": FEATURE_NAMES,
        "fit": "one line per property, on that property's occupied units",
        "by_property": by_property,
    }
    MODEL_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"wrote {MODEL_PATH}")


if __name__ == "__main__":
    main()
