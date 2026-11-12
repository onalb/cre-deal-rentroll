"""AgentCore Gateway Lambda target.

This is the same tool body the local capabilities gateway runs. The deal
app's ``cre_rentroll.tools`` functions call this gateway, so they must not
be the Lambda body: that call loops and AgentCore reports
"An internal error occurred. Please retry later."
"""

from __future__ import annotations

from typing import Any


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    try:
        from capabilities_gateway.gateway_target import lambda_handler as target

        result = target(event, context)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return result if isinstance(result, dict) else {"result": result}
