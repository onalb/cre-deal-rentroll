from __future__ import annotations

import os
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
    """Strands-shaped MemoryStore: search / add over this session's fact slots."""

    def __init__(self, username: str):
        self.username = username

    def search(self, query: str = "") -> list[dict[str, Any]]:
        from cre_rentroll import turn_ctx
        from cre_rentroll.chat_memory import load_facts

        facts = load_facts(turn_ctx.chat_session(), username=self.username)
        q = query.lower().strip()
        out = []
        for slot, value in facts.items():
            if not q or q in str(slot).lower() or q in str(value).lower():
                out.append({"slot": slot, "value": value})
        return out

    def add(self, slot: str, value: Any, *, overwrite: bool = False) -> None:
        from cre_rentroll import turn_ctx
        from cre_rentroll.chat_memory import load_facts, save_facts

        facts = load_facts(turn_ctx.chat_session(), username=self.username)
        if slot in facts and not overwrite:
            return
        facts[slot] = value
        save_facts(turn_ctx.chat_session(), facts, username=self.username)


def retrieve_facts(username: str) -> dict[str, Any]:
    return {row["slot"]: row["value"] for row in MemoryStore(username).search()}


def facts_prompt(username: str) -> str:
    facts = retrieve_facts(username)
    lines = [f"- {k}: {v}" for k, v in sorted(facts.items())]
    ac_lines: list[str] = []
    try:
        from cre_rentroll.agentcore_platform import facts_from_memory

        ac_lines = facts_from_memory(username)
    except Exception:
        ac_lines = []
    parts: list[str] = []
    if lines:
        parts.append("Known deal facts (this session, auto-injected):\n" + "\n".join(lines))
    if ac_lines:
        parts.append("AgentCore Memory records:\n" + "\n".join(f"- {x}" for x in ac_lines[:8]))
    if not parts:
        return "No session fact slots yet for this user."
    return "\n".join(parts)


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
        try:
            from cre_rentroll.agentcore_platform import write_fact_record

            write_fact_record(username, slot, value)
        except Exception:
            pass


def write_cycle_log(
    username: str,
    *,
    turn: int,
    tools: list[str],
    stop: str = "",
    line: str = "",
    run_id: str = "",
) -> None:
    """One Dynamo row for one think-act cycle. Not the chat, and not SESSION#default."""
    user = (username or "").strip()
    if not user or os.environ.get("CRE_CYCLE_LOG", "1") == "0":
        return
    from cre_rentroll import dynamo

    rid = (run_id or "run").strip()[:32]
    n = int(turn)
    ts = datetime.now(timezone.utc).isoformat()
    dynamo.put_item(
        user,
        f"CYCLE#{rid}#{n:02d}",
        {
            "at": ts,
            "run_id": rid,
            "turn": n,
            "tools": [str(name) for name in tools if name],
            "stop": (stop or "")[:40],
            "line": " ".join((line or "").split())[:500],
        },
    )


def list_traces(username: str) -> list[dict[str, Any]]:
    """Think-act cycle rows for this user, one item per cycle."""
    from cre_rentroll import dynamo

    rows = dynamo.query_prefix(username, "CYCLE#")
    rows.sort(key=lambda row: str(row.get("sk") or ""))
    return rows


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
