"""Geocode runs on the capabilities gateway, not in this process."""

from __future__ import annotations


def geocode(query: str, *, offline: bool = False, user_agent: str = "cre-deal-rentroll/0.1") -> dict:
    raise RuntimeError("capabilities gateway is required (set CAPABILITIES_GATEWAY_URL)")


def write_fixtures() -> None:
    raise RuntimeError("capabilities gateway is required (set CAPABILITIES_GATEWAY_URL)")
