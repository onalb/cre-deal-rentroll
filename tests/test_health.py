"""Deal /api/health reports this API and the capabilities gateway."""

from cre_rentroll import __version__
from cre_rentroll.capabilities_client import gateway_version
from cre_rentroll.main import health


def test_health_reports_api_and_gateway(monkeypatch):
    monkeypatch.setattr("cre_rentroll.main.gateway_version", lambda: "9.9.9")
    body = health()
    assert body["ok"] is True
    assert body["version"] == __version__
    assert body["capabilities_gateway_version"] == "9.9.9"


def test_gateway_version_reads_health(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "version": "9.9.9"}

    monkeypatch.setattr("cre_rentroll.capabilities_client.httpx.get", lambda *args, **kwargs: Response())
    assert gateway_version() == "9.9.9"


def test_gateway_version_blank_when_down(monkeypatch):
    def down(*args, **kwargs):
        raise OSError("down")

    monkeypatch.setattr("cre_rentroll.capabilities_client.httpx.get", down)
    assert gateway_version() == ""
