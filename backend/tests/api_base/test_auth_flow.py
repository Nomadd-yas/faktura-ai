"""/auth/login|refresh|logout|register, GET /auth/me (BACKEND §2.1, api_contract §4.2). TestClient + SQLite."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.usefixtures("clock")


# --- login -------------------------------------------------------------------------------------

class TestLogin:
    def test_ok_returns_token_user_and_cookie(self, client, admin_creds, h):
        r = h.login(client, *admin_creds)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token_type"] == "Bearer"
        assert body["expires_in"] == 900
        assert body["access_token"].count(".") == 2
        assert "refresh_token" not in body  # по умолчанию refresh только в cookie
        user = body["user"]
        assert user["email"] == admin_creds[0]
        assert user["is_admin"] is True
        assert user["id"] == user["user_id"]
        assert "password_hash" not in user and "password" not in user

    def test_cookie_attributes_remember_true(self, client, admin_creds, h):
        r = h.login(client, *admin_creds, remember=True)
        c = h.parse_set_cookie(r)
        assert c is not None and c.value
        assert int(c["max-age"]) == h.THIRTY_DAYS
        assert c["path"] == "/api/v1/auth"
        assert c["httponly"]
        assert c["samesite"].lower() == "strict"
        assert not c["secure"]  # cookie_secure=False в тестовых настройках

    def test_cookie_secure_when_enabled(self, settings, services, admin_creds, h):
        from fastapi.testclient import TestClient

        from faktura.api.base import create_app

        app = create_app(settings.model_copy(update={"cookie_secure": True}), services)
        r = h.login(TestClient(app, base_url="https://testserver"), *admin_creds)
        assert r.status_code == 200
        assert h.parse_set_cookie(r)["secure"]

    def test_remember_false_cookie_is_12_hours(self, client, admin_creds, h):
        r = h.login(client, *admin_creds, remember=False)
        assert int(h.parse_set_cookie(r)["max-age"]) == h.TWELVE_HOURS















# --- refresh -----------------------------------------------------------------------------------



# --- logout ------------------------------------------------------------------------------------



# --- GET /auth/me и проверка токена ------------------------------------------------------------



# --- register ----------------------------------------------------------------------------------


class TestRefresh:
    def test_refresh_ok_returns_new_access_and_rotates_cookie(self, client, admin_creds, h):
        r1 = h.login(client, *admin_creds)
        rt1 = h.cookie_value(r1)
        r2 = client.post(h.url("/auth/refresh"))
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["token_type"] == "Bearer" and body["expires_in"] == 900 and body["access_token"]
        rt2 = h.cookie_value(r2)
        assert rt2 and rt2 != rt1
        c = h.parse_set_cookie(r2)
        assert c["path"] == "/api/v1/auth" and c["httponly"]


    def test_reuse_of_old_refresh_is_401_and_kills_whole_chain(self, app, client, admin_creds, h):
        rt1 = h.cookie_value(h.login(client, *admin_creds))
        r2 = h.refresh_with(app, rt1)
        assert r2.status_code == 200
        rt2 = h.cookie_value(r2)
        # злоумышленник/гонка предъявляет уже использованный rt1
        h.assert_error(h.refresh_with(app, rt1), 401, "refresh_invalid")
        # цепочка погашена: свежий rt2 тоже больше не работает
        h.assert_error(h.refresh_with(app, rt2), 401, "refresh_invalid")
