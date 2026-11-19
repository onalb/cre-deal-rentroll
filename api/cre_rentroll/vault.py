from __future__ import annotations

from cre_rentroll import dynamo
from cre_rentroll.schemas import CanonicalRentRoll

SK = "VAULT#default"


def save_roll(username: str, roll: CanonicalRentRoll) -> None:
    """Keep the parsed rent roll on this page visit. Dynamo is not the vault."""
    from cre_rentroll import turn_ctx
    from cre_rentroll.chat_memory import save_roll as save_mem

    save_mem(turn_ctx.chat_session(), roll, username=username)


def load_roll(username: str) -> CanonicalRentRoll | None:
    from cre_rentroll import turn_ctx
    from cre_rentroll.chat_memory import load_roll as load_mem

    return load_mem(turn_ctx.chat_session(), username=username)


def clear_visit(username: str) -> None:
    """Page load. Drop a leftover Dynamo vault row. The chat is not stored there."""
    dynamo.delete_item(username, SK)


def vault_spans(roll: CanonicalRentRoll) -> list[str]:
    return roll.vault_spans()
