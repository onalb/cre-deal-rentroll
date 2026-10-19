import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))


@pytest.fixture(autouse=True)
def _mock_scope_agent_token(monkeypatch):
    """Tests inject IN_SCOPE by default. OOS tests override the token. No live OpenAI."""
    monkeypatch.setattr(
        "cre_rentroll.scope._scope_agent_token",
        lambda message, history=None: "IN_SCOPE",
    )
