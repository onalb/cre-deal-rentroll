from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from cre_rentroll.schemas import CanonicalRentRoll

SLOT_KEYS = (
    "unit_count",
    "in_place_monthly",
    "occupancy_pct",
    "vacant_count",
    "as_of",
    "geo_confidence",
    "address_hint",
    "property_name",
)


class MemoryStore:
    """Strands-shaped MemoryStore: search / add over Dynamo FACT# slots."""

    def __init__(self, username: str):
        self.username = username

    def search(self, query: str = "") -> list[dict[str, Any]]:
        from cre_rentroll import dynamo

        items = dynamo.query_prefix(self.username, "FACT#")
        q = query.lower().strip()
        out = []
        for it in items:
            slot = str(it.get("sk", "")).removeprefix("FACT#")
            if not q or q in slot or q in str(it.get("value", "")).lower():
                out.append({"slot": slot, "value": it.get("value"), "sk": it.get("sk")})
        return out

    def add(self, slot: str, value: Any, *, overwrite: bool = False) -> None:
        from cre_rentroll import dynamo

        sk = f"FACT#{slot}"
        existing = dynamo.get_item(self.username, sk)
        if existing and not overwrite:
            return
        dynamo.put_item(self.username, sk, {"slot": slot, "value": value})


def retrieve_facts(username: str) -> dict[str, Any]:
    return {row["slot"]: row["value"] for row in MemoryStore(username).search()}


def facts_prompt(username: str) -> str:
    facts = retrieve_facts(username)
    if not facts:
        return "No LTM FACT# slots yet for this user."
    lines = [f"- {k}: {v}" for k, v in sorted(facts.items())]
    return "Known deal facts (Dynamo LTM, auto-injected):\n" + "\n".join(lines)


def generate_from_roll(username: str, roll: CanonicalRentRoll) -> None:
    store = MemoryStore(username)
    mapping = {
        "unit_count": roll.unit_count,
        "in_place_monthly": round(roll.in_place_monthly, 2),
        "occupancy_pct": round(roll.occupancy_pct, 4),
        "vacant_count": roll.vacant_count,
        "as_of": roll.as_of,
        "geo_confidence": roll.geo_confidence,
        "address_hint": roll.suggested_address,
        "property_name": roll.property_name,
    }
    for slot, value in mapping.items():
        if value is None or value == "":
            continue
        store.add(slot, value, overwrite=True)


def write_trace(
    username: str,
    *,
    run_id: str,
    question: str,
    tools_called: list[str],
    scores: dict[str, Any],
    stop_reason: str,
    preview: str,
) -> None:
    from cre_rentroll import dynamo

    ts = datetime.now(timezone.utc).isoformat()
    qh = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    dynamo.put_item(
        username,
        f"TRACE#{ts}",
        {
            "run_id": run_id,
            "question_hash": qh,
            "tools_called": tools_called,
            "vault_calc_ok": scores.get("vault_calc_ok"),
            "R": scores.get("R"),
            "G": scores.get("G"),
            "request_C": scores.get("request_C"),
            "retry": scores.get("retry", False),
            "stop_reason": stop_reason,
            "answer_preview": (preview or "")[:400],
        },
    )


class InMemoryStore:
    """Test double — no Dynamo."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def search(self, query: str = "") -> list[dict[str, Any]]:
        q = query.lower()
        return [{"slot": k, "value": v} for k, v in self._data.items() if not q or q in k.lower()]

    def add(self, slot: str, value: Any, *, overwrite: bool = False) -> None:
        if slot in self._data and not overwrite:
            return
        self._data[slot] = value
