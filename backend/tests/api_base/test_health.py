"""GET /health (api_contract §4.1, spec/04 §4.21)."""
from __future__ import annotations


class TestHealth:
    def test_ok_without_token(self, client, h):
        r = client.get(h.url("/health"))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["db"] == "ok" and body["storage"] == "ok" and body["ml"] == "ok"
        assert body["ml_model_version"] == "extract-0.3"












