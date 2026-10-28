import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))
_GATEWAY_ROOT = Path(r"C:\Users\burak\capabilities-gateway")
if _GATEWAY_ROOT.is_dir() and str(_GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_ROOT))

_PLAN_TOOLS = frozenset(
    {"create_plan", "update_plan", "update_plan_status", "reflection"}
)


@pytest.fixture(autouse=True)
def _mock_scope_agent_token(monkeypatch):
    """Tests inject IN_SCOPE by default. OOS tests override the token. No live OpenAI.

    Checklist tools run the real gateway engine in-process. Other capability
    tools still do not open a network connection unless a test replaces
    call_deal_tool.
    """
    monkeypatch.delenv("CAPABILITIES_GATEWAY_URL", raising=False)
    monkeypatch.setenv("CAPABILITIES_GATEWAY_URL", "http://127.0.0.1:8010")
    monkeypatch.setattr(
        "cre_rentroll.scope._scope_agent_token",
        lambda message, history=None: "IN_SCOPE",
    )
    import cre_rentroll.capabilities_client as client

    def routed(name, arguments, timeout=180.0):
        if name in _PLAN_TOOLS:
            from capabilities_gateway.checklist import apply_checklist

            return json.dumps(apply_checklist("deal", name, arguments or {}))
        raise RuntimeError(client.GATEWAY_REQUIRED)

    monkeypatch.setattr(client, "call_deal_tool", routed)
