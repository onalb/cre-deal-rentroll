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


TRACE_KEEP = 25


class MemoryStore:
    """Strands-shaped MemoryStore: search / add over this session's fact slots."""

    def __init__(self, username: str):
        self.username = username

    def search(self, query: str = "") -> list[dict[str, Any]]:
        from cre_rentroll import turn_ctx
        from cre_rentroll.chat_memory import load_facts

        facts = load_facts(turn_ctx.chat_session())
        q = query.lower().strip()
        out = []
        for slot, value in facts.items():
            if not q or q in str(slot).lower() or q in str(value).lower():
                out.append({"slot": slot, "value": value})
        return out

    def add(self, slot: str, value: Any, *, overwrite: bool = False) -> None:
        from cre_rentroll import turn_ctx
        from cre_rentroll.chat_memory import load_facts, save_facts

        facts = load_facts(turn_ctx.chat_session())
        if slot in facts and not overwrite:
            return
        facts[slot] = value
        save_facts(turn_ctx.chat_session(), facts)


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


def write_trace(
    username: str,
    *,
    run_id: str,
    question: str,
    tools_called: list[str],
    scores: dict[str, Any],
    stop_reason: str,
    preview: str = "",
) -> None:
    from cre_rentroll import vault

    ts = datetime.now(timezone.utc).isoformat()
    qh = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    sess = vault.load_session(username)
    traces = list(sess.get("traces") or [])
    traces.append(
        {
            "at": ts,
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
        }
    )
    vault.save_session(
        username,
        list(sess.get("messages") or []),
        traces=traces[-TRACE_KEEP:],
    )


def list_traces(username: str) -> list[dict[str, Any]]:
    from cre_rentroll import vault

    traces = list(vault.load_session(username).get("traces") or [])
    return traces[-TRACE_KEEP:]


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
