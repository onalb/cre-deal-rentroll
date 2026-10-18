"""Demo questions. Each is asked separately. Inventory is the same; tools should differ."""

QUESTIONS = {
    "q1": {
        "id": "q1_roll_abstract",
        "prompt": (
            "Abstract this rent roll: unit count, occupancy, in-place monthly income, "
            "vacant count, and MTM share. Cite unit span_ids or calc spans. "
            "Do not geocode and do not search the web."
        ),
        "required": ["vault_or_calc", "occupancy", "income"],
        "forbidden": ["osm", "web_search"],
    },
    "q2": {
        "id": "q2_location",
        "prompt": (
            "Find this property on the map. Use the ingested address if confidence is high; "
            "otherwise web-search for an address that matches the unit count, then geocode "
            "with OpenStreetMap. Return lat/lng and display_name. Do not compute cap rates."
        ),
        "required": ["osm"],
        "forbidden": ["compute_implied_cap"],
    },
    "q3": {
        "id": "q3_cap_vs_market",
        "prompt": (
            "Using in-place annual rent (monthly * 12), compute implied cap at a $12.4M ask. "
            "Web-search recent multifamily cap rates for this property's metro and compare. "
            "Cite calc span and article URLs. Do not geocode."
        ),
        "required": ["calc", "web_search"],
        "forbidden": ["osm"],
    },
    "weather": {
        "id": "q_weather",
        "prompt": "What's the weather in Miami this weekend?",
        "required": [],
        "forbidden": ["osm", "web_search", "vault_or_calc"],
        "scope": "irrelevant",
    },
}


def render(qid: str, property_name: str) -> str:
    q = QUESTIONS[qid]["prompt"]
    return f"Deal: {property_name}. {q}"
