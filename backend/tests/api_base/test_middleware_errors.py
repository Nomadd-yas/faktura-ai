"""Middleware X-Request-Id, единый формат ошибок, CORS, лимит 600/мин (api_contract §1.3, §1.5, §1.7)."""
from __future__ import annotations

import re

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from faktura.api import API_PREFIX
from faktura.domain.errors import AppError

ORIGIN = "http://localhost:5200"
EXPOSE = {"x-action-id", "x-request-id", "content-disposition", "x-check-status", "x-faktura-stub"}
ALLOW = {"authorization", "content-type", "x-request-id", "last-event-id", "idempotency-key"}


def _csv(value: str) -> set[str]:
    return {p.strip().lower() for p in value.split(",") if p.strip()}


# --- X-Request-Id ------------------------------------------------------------------------------

class TestRequestId:
    def test_generated_when_absent(self, client, h):
        r = client.get(h.url("/health"))
        rid = r.headers.get("X-Request-Id")
        assert rid and len(rid) >= 8

    def test_unique_per_request(self, client, h):
        ids = {client.get(h.url("/health")).headers["X-Request-Id"] for _ in range(5)}
        assert len(ids) == 5

    def test_client_value_is_echoed(self, client, h):
        r = client.get(h.url("/health"), headers={"X-Request-Id": "req-abc-123"})
        assert r.headers["X-Request-Id"] == "req-abc-123"

    @pytest.mark.parametrize("method,path,status", [
        ("GET", "/health", 200),
        ("GET", "/auth/me", 401),
        ("GET", "/no/such/route", 404),
        ("POST", "/auth/login", 400),
    ])
    def test_present_on_every_status(self, client, h, method, path, status):
        r = client.request(method, h.url(path), json={} if method == "POST" else None)
        assert r.status_code == status
        assert r.headers.get("X-Request-Id")

    def test_present_on_429_and_503(self, client, services, h, admin_creds):
        for _ in range(5):
            h.login(client, admin_creds[0], "wrong-password")
        r = h.login(client, *admin_creds)
        assert r.status_code == 429 and r.headers.get("X-Request-Id")
        services.db = h.BrokenDb()
        r = client.get(h.url("/health"))
        assert r.status_code == 503 and r.headers.get("X-Request-Id")

    def test_absurdly_long_or_unsafe_client_value_is_replaced_or_truncated(self, client, h):
        r = client.get(h.url("/health"), headers={"X-Request-Id": "x" * 1000})
        rid = r.headers["X-Request-Id"]
        assert 0 < len(rid) <= 200


# --- единый формат ошибок ----------------------------------------------------------------------

class TestErrorFormat:
    def test_unknown_route_404_not_found(self, client, h):
        h.assert_error(client.get(h.url("/definitely/not/here")), 404, "not_found")

    def test_wrong_method_has_error_shape(self, client, h):
        r = client.delete(h.url("/health"))
        assert r.status_code == 405
        assert "error" in r.json() and isinstance(r.json()["error"]["code"], str)

    def test_pydantic_validation_is_400_not_422(self, client, h):
        r = client.post(h.url("/auth/login"), json={"email": 123, "password": ["x"]})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_error"
        assert r.json()["error"]["field"] in ("email", "password")

    def test_validation_message_is_human_readable_string(self, client, h):
        err = client.post(h.url("/auth/login"), json={}).json()["error"]
        assert isinstance(err["message"], str) and len(err["message"]) > 3
        assert "pydantic" not in err["message"].lower() and "loc" not in err["message"]

    def test_apperror_maps_status_field_details(self, app, h):
        def route():
            raise AppError("validation_error", "Поле «name» не может быть пустым", field="name",
                           details={"max": 80})

        app.add_api_route(h.url("/_t/apperror"), route, methods=["GET"])
        err = h.assert_error(TestClient(app).get(h.url("/_t/apperror")), 400, "validation_error", field="name")
        assert err["message"] == "Поле «name» не может быть пустым"
        assert err["details"] == {"max": 80}
        assert err["request_id"] is None

    def test_apperror_with_custom_http_status(self, app, h):
        def route():
            raise AppError("gone", http_status=410)

        app.add_api_route(h.url("/_t/gone"), route, methods=["GET"])
        h.assert_error(TestClient(app).get(h.url("/_t/gone")), 410, "gone")

    def test_unhandled_exception_is_500_internal_error_with_request_id(self, app, h):
        def route():
            raise RuntimeError("secret-internal-detail /etc/passwd")

        app.add_api_route(h.url("/_t/crash"), route, methods=["GET"])
        r = TestClient(app, raise_server_exceptions=False).get(h.url("/_t/crash"), headers={"X-Request-Id": "rid-500"})
        err = h.assert_error(r, 500, "internal_error")
        assert err["request_id"] == "rid-500" == r.headers["X-Request-Id"]
        assert "secret-internal-detail" not in r.text and "Traceback" not in r.text

    def test_500_request_id_matches_generated_header(self, app, h):
        def route():
            raise RuntimeError("x")

        app.add_api_route(h.url("/_t/crash2"), route, methods=["GET"])
        r = TestClient(app, raise_server_exceptions=False).get(h.url("/_t/crash2"))
        assert r.json()["error"]["request_id"] == r.headers["X-Request-Id"]

    def test_error_content_type_is_json_utf8_russian_message(self, client, h):
        r = client.get(h.url("/auth/me"))
        assert "application/json" in r.headers["content-type"]
        assert re.search(r"[а-яА-Я]", r.json()["error"]["message"])  # сообщения по-русски

    def test_error_with_non_ascii_not_escaped_garbled(self, app, h):
        def route():
            raise AppError("conflict", "Прогон уже идёт")

        app.add_api_route(h.url("/_t/ru"), route, methods=["GET"])
        r = TestClient(app).get(h.url("/_t/ru"))
        assert "Прогон уже идёт" in r.content.decode("utf-8") or r.json()["error"]["message"] == "Прогон уже идёт"


# --- CORS --------------------------------------------------------------------------------------

class TestCors:
    def test_preflight_allowed_origin(self, client, h):
        r = client.options(h.url("/auth/login"), headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,idempotency-key,x-request-id,last-event-id",
        })
        assert r.status_code in (200, 204)
        assert r.headers["access-control-allow-origin"] == ORIGIN
        assert r.headers["access-control-allow-credentials"] == "true"
        assert ALLOW <= _csv(r.headers["access-control-allow-headers"])
        assert {"get", "post", "patch", "delete"} <= _csv(r.headers["access-control-allow-methods"])

    def test_simple_request_carries_allow_and_expose(self, client, h):
        r = client.get(h.url("/health"), headers={"Origin": ORIGIN})
        assert r.headers["access-control-allow-origin"] == ORIGIN
        assert r.headers["access-control-allow-credentials"] == "true"
        assert EXPOSE <= _csv(r.headers["access-control-expose-headers"])

    def test_error_response_also_has_cors(self, client, h):
        r = client.get(h.url("/auth/me"), headers={"Origin": ORIGIN})
        assert r.status_code == 401
        assert r.headers["access-control-allow-origin"] == ORIGIN

    def test_foreign_origin_gets_no_allow_origin(self, client, h):
        r = client.get(h.url("/health"), headers={"Origin": "http://evil.example.com"})
        assert "access-control-allow-origin" not in r.headers
        assert "access-control-allow-credentials" not in r.headers

    def test_foreign_origin_preflight_denied(self, client, h):
        r = client.options(h.url("/auth/login"), headers={
            "Origin": "http://evil.example.com", "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in r.headers
        assert r.status_code in (400, 403)

    def test_wildcard_is_never_used_with_credentials(self, client, h):
        r = client.get(h.url("/health"), headers={"Origin": ORIGIN})
        assert r.headers["access-control-allow-origin"] != "*"

    def test_no_origin_header_no_cors_headers(self, client, h):
        assert "access-control-allow-origin" not in client.get(h.url("/health")).headers

    def test_whitelist_comes_from_settings(self, settings, services, h):
        from faktura.api.base import create_app

        app = create_app(settings.model_copy(update={"cors_origins": ["https://app.example.com"]}), services)
        c = TestClient(app)
        ok = c.get(h.url("/health"), headers={"Origin": "https://app.example.com"})
        assert ok.headers["access-control-allow-origin"] == "https://app.example.com"
        no = c.get(h.url("/health"), headers={"Origin": ORIGIN})
        assert "access-control-allow-origin" not in no.headers

    def test_multiple_origins(self, settings, services, h):
        from faktura.api.base import create_app

        origins = ["http://localhost:5200", "http://127.0.0.1:5200"]
        c = TestClient(create_app(settings.model_copy(update={"cors_origins": origins}), services))
        for o in origins:
            assert c.get(h.url("/health"), headers={"Origin": o}).headers["access-control-allow-origin"] == o


# --- лимит запросов ----------------------------------------------------------------------------

class TestRateLimit:
    def test_601st_request_in_a_minute_is_429(self, client, user_token, clock, h):
        hd = h.bearer(user_token)
        for i in range(600):
            r = client.get(h.url("/collections"), headers=hd)
            assert r.status_code == 200, (i, r.text)
        r = client.get(h.url("/collections"), headers=hd)
        err = h.assert_error(r, 429, "too_many_requests")
        retry = int(r.headers["Retry-After"])
        assert 1 <= retry <= 60
        assert err["details"]["retry_after"] == retry

    def test_limit_window_slides(self, client, user_token, clock, h):
        hd = h.bearer(user_token)
        for _ in range(600):
            client.get(h.url("/collections"), headers=hd)
        assert client.get(h.url("/collections"), headers=hd).status_code == 429
        clock.advance(seconds=61)
        assert client.get(h.url("/collections"), headers=hd).status_code == 200

    def test_limit_is_per_user(self, client, user_token, admin_token, clock, h):
        for _ in range(600):
            client.get(h.url("/collections"), headers=h.bearer(user_token))
        assert client.get(h.url("/collections"), headers=h.bearer(user_token)).status_code == 429
        assert client.get(h.url("/collections"), headers=h.bearer(admin_token)).status_code == 200

    def test_health_is_not_counted_against_user_limit(self, client, user_token, clock, h):
        for _ in range(650):
            assert client.get(h.url("/health")).status_code == 200
        assert client.get(h.url("/collections"), headers=h.bearer(user_token)).status_code == 200
