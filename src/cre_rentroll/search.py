"""Web search runs on the capabilities gateway, not in this process."""

from __future__ import annotations


def web_search(query: str, *, offline: bool = False) -> dict:
    raise RuntimeError("capabilities gateway is required (set CAPABILITIES_GATEWAY_URL)")
