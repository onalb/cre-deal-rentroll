"""MCP OSM — Nominatim geocode.

stdio `main()` is for a standalone host. The live Book agent uses this FastMCP
object in-process (see mcp_runtime) so turn_ctx survives.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cre_rentroll import tools

mcp = FastMCP("osm")


@mcp.tool()
def osm_geocode(query: str) -> str:
    return tools.osm_geocode(query)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
