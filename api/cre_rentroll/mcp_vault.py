"""MCP deal-vault — query/compute against the session rent-roll grid.

Do not register create_plan / update_plan / update_plan_status / reflection / score_gcr here.
Those need session turn_ctx; a stdio child would drop ContextVars.
Live Agent uses this FastMCP object in-process (mcp_runtime), not a stdio spawn.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cre_rentroll import tools

mcp = FastMCP("deal-vault")


@mcp.tool()
def query_units(occupancy: str | None = None, vacant_only: bool = False) -> str:
    return tools.query_units(occupancy=occupancy, vacant_only=vacant_only)


@mcp.tool()
def compute_income(vacant_fill_rent: float | None = None) -> str:
    return tools.compute_income(vacant_fill_rent=vacant_fill_rent)


@mcp.tool()
def compute_noi(operating_expenses: float | None = None) -> str:
    return tools.compute_noi(operating_expenses=operating_expenses)


@mcp.tool()
def compute_cap_rate(ask_price: float, noi: float | None = None) -> str:
    return tools.compute_cap_rate(ask_price=ask_price, noi=noi)


@mcp.tool()
def ask_vacancy_agent(floor_plan: str | None = None) -> str:
    return tools.ask_vacancy_agent(floor_plan=floor_plan)


@mcp.tool()
def ask_prediction_agent(horizon_months: int = 12, question: str = "") -> str:
    return tools.ask_prediction_agent(horizon_months=horizon_months, question=question)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
