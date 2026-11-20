"""GET /health (api_contract §4.1, spec/04 §4.21)."""
from __future__ import annotations


class TestHealth:
    def test_ok_without_token(self, client, h):
        r = client.get(h.url("/health"))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["db"] == "ok" and body["storage"] == "ok" and body["ml"] == "ok"
        assert body["ml_model_version"] == "extract-0.3"

    def test_ignores_invalid_authorization_header(self, client, h):
        assert client.get(h.url("/health"), headers={"Authorization": "Bearer garbage"}).status_code == 200

    def test_ml_down_is_degraded_but_status_ok(self, client, extractor, h):
        extractor.status = "down"
        r = client.get(h.url("/health"))
        assert r.status_code == 200
        assert r.json()["status"] == "ok" and r.json()["ml"] == "degraded"

    def test_ml_degraded_reported_as_degraded(self, client, extractor, h):
        extractor.status = "degraded"
        body = client.get(h.url("/health")).json()
        assert body["ml"] == "degraded" and body["status"] == "ok"

    def test_ml_health_raising_is_degraded_not_500(self, client, extractor, h):
        extractor.raise_exc = ConnectionError("ml unreachable")
        r = client.get(h.url("/health"))
        assert r.status_code == 200
        assert r.json()["ml"] == "degraded" and r.json()["status"] == "ok"

    def test_ml_without_model_version(self, client, extractor, h):
        extractor.model_version = None
        r = client.get(h.url("/health"))
        assert r.status_code == 200
        assert r.json().get("ml_model_version") is None

    def test_db_failure_503_with_error_fields(self, client, services, h):
        services.db = h.BrokenDb()
        r = client.get(h.url("/health"))
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "error" and body["db"] == "error"
        assert "Retry-After" in r.headers

    def test_db_failure_body_does_not_leak_internals(self, client, services, h):
        services.db = h.BrokenDb()
        text = client.get(h.url("/health")).text
        assert "db is down" not in text and "Traceback" not in text

    def test_storage_failure_503(self, client, services, h):
        services.store = h.BrokenStore()
        r = client.get(h.url("/health"))
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "error" and body["storage"] == "error" and body["db"] == "ok"

    def test_db_and_storage_both_down(self, client, services, h):
        services.db, services.store = h.BrokenDb(), h.BrokenStore()
        body = client.get(h.url("/health")).json()
        assert body["db"] == "error" and body["storage"] == "error" and body["status"] == "error"

    def test_ml_down_and_db_down_still_503(self, client, services, extractor, h):
        extractor.status = "down"
        services.db = h.BrokenDb()
        r = client.get(h.url("/health"))
        assert r.status_code == 503 and r.json()["ml"] == "degraded"

    def test_health_has_exact_documented_keys(self, client, h):
        assert {"status", "db", "storage", "ml", "ml_model_version"} <= set(client.get(h.url("/health")).json())

    def test_recovers_after_failure(self, client, services, h):
        good = services.db
        services.db = h.BrokenDb()
        assert client.get(h.url("/health")).status_code == 503
        services.db = good
        assert client.get(h.url("/health")).status_code == 200
