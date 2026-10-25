"""Vacancy agent. The deal rent-roll agent calls this. It does not talk to the user.

One tool, fit_vacancy_model: logistic regression of vacant vs occupied by floor
plan, fit by gradient descent on this building only. The reply is a probability
per plan.
"""

from __future__ import annotations

import math
from typing import Any

SYSTEM_PROMPT = (
    "You are the Vacancy agent. The deal rent-roll agent calls you with units "
    "from one building. Predict whether a floor plan is vacant. Do not estimate "
    "rent, NOI, or cap rate. Your only action is fit_vacancy_model. Then report "
    "each plan's probability."
)


class VacancyAgent:
    name = "vacancy"

    def run(self, units: list[dict[str, Any]], floor_plan: str | None = None) -> dict[str, Any]:
        """Agent loop: call the model tool, then answer from that result only."""
        fit = self.fit_vacancy_model(units)
        plans = list(fit["plans"])
        asked = (floor_plan or "").strip()
        if asked:
            matched = [p for p in plans if str(p["plan"]).lower() == asked.lower()]
            plans = matched or plans
        return {
            "agent": self.name,
            "ok": True,
            "steps": ["fit_vacancy_model"],
            "floor_plan": asked or None,
            "plans": plans,
            "answer": _answer(plans),
        }

    def fit_vacancy_model(self, units: list[dict[str, Any]]) -> dict[str, Any]:
        """Logistic regression. Target is 1 when the unit is vacant."""
        rows: list[tuple[str, int]] = []
        for unit in units:
            plan = str(unit.get("plan") or unit.get("unit_type") or "").strip() or "unknown"
            vacant = unit.get("vacant")
            if vacant is None:
                continue
            rows.append((plan, 1 if vacant else 0))
        if not rows:
            return {"plans": [], "note": "no units"}
        names = sorted({plan for plan, _ in rows})
        index = {name: i for i, name in enumerate(names)}
        weights = _fit([index[plan] for plan, _ in rows], [y for _, y in rows], len(names))
        counts: dict[str, list[int]] = {name: [0, 0] for name in names}
        for plan, y in rows:
            counts[plan][0] += 1
            counts[plan][1] += y
        plans = []
        for name in names:
            n, vacant = counts[name]
            probability = _sigmoid(weights[0] + weights[index[name] + 1])
            plans.append(
                {
                    "plan": name,
                    "units": n,
                    "vacant": vacant,
                    "probability": round(probability, 4),
                }
            )
        plans.sort(key=lambda row: (-row["probability"], row["plan"]))
        return {"plans": plans}


def _fit(plan_index: list[int], y: list[int], n_plans: int, *, lr: float = 0.4, epochs: int = 800, l2: float = 0.15) -> list[float]:
    """Batch gradient descent. Weight 0 is the bias. Other weights are floor plans.

    Loss is mean log loss plus L2 on the plan weights. The penalty pulls a
    small plan toward the building rate.
    """
    weights = [0.0] * (n_plans + 1)
    n = len(y)
    for _ in range(epochs):
        grad = [0.0] * (n_plans + 1)
        for j, target in zip(plan_index, y):
            prob = _sigmoid(weights[0] + weights[j + 1])
            err = prob - target
            grad[0] += err
            grad[j + 1] += err
        weights[0] -= lr * grad[0] / n
        for j in range(n_plans):
            weights[j + 1] -= lr * (grad[j + 1] / n + l2 * weights[j + 1])
    return weights


def _sigmoid(z: float) -> float:
    z = max(-20.0, min(20.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def _answer(plans: list[dict[str, Any]]) -> str:
    if not plans:
        return "This building has no floor plans to score."
    parts = []
    for row in plans:
        pct = round(float(row["probability"]) * 100)
        parts.append(
            f"{row['plan']} is vacant with probability {row['probability']} "
            f"({pct} percent, {row['vacant']} vacant of {row['units']})"
        )
    return ". ".join(parts) + "."
