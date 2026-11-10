"""Stand-in menu for prompt tests.

The deal app stores no tool list. system_prompt renders the in-scope
instructions only when the inventory has names, which at runtime come
from the gateway tools/list.
"""

from cre_rentroll.agents import DEAL, Inventory

WITH_TOOLS = Inventory(
    DEAL.name,
    (
        "create_plan",
        "update_plan",
        "update_plan_status",
        "reflection",
        "score_gcr",
        "query_units",
        "compute_income",
        "compute_noi",
        "compute_cap_rate",
        "ask_prediction_agent",
        "osm_geocode",
        "web_search",
    ),
    DEAL.skills,
)
