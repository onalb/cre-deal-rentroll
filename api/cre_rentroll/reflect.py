from __future__ import annotations


def on_tool_error(name: str, error: str) -> str:
    """Tool failure — replan, do not loop."""
    if name == "osm_geocode":
        return f"OSM failed ({error}). Replan: state uncertainty; optional search if geo_confidence is low."
    return f"tool {name} failed: {error}. Continue without looping."
