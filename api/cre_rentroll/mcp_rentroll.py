"""In-process deal-rentroll MCP — scratchpad, search, memo. Same interpreter (not stdio).

create_plan / update_plan / update_plan_status / reflection / score_gcr stay off deal-vault: they need
turn_ctx checklist state. Live Agent talks to this FastMCP object in-process.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cre_rentroll import tools

mcp = FastMCP("deal-rentroll")


@mcp.tool()
def create_plan(tasks: str) -> str:
    return tools.create_plan(tasks)


@mcp.tool()
def update_plan(tasks: str) -> str:
    return tools.update_plan(tasks)


@mcp.tool()
def update_plan_status(tasks: str) -> str:
    return tools.update_plan_status(tasks)


@mcp.tool()
def reflection(analysis: str, need_replan: bool = False) -> str:
    return tools.reflection(analysis, need_replan=need_replan)


@mcp.tool()
def score_gcr(draft: str) -> str:
    return tools.score_gcr(draft)


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    return tools.web_search(query, max_results=max_results)


@mcp.tool()
def publish_memo(title: str, body: str) -> str:
    return tools.publish_memo(title, body)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
