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

    def test_wrong_password_401_invalid_credentials(self, client, admin_creds, h):
        r = h.login(client, admin_creds[0], "wrong-password")
        h.assert_error(r, 401, "invalid_credentials")
        assert h.parse_set_cookie(r) is None
        assert "access_token" not in r.text

    def test_unknown_email_same_error_as_wrong_password(self, client, admin_creds, h):
        a = h.login(client, admin_creds[0], "wrong-password")
        b = h.login(client, "nobody@example.com", "wrong-password")
        assert b.status_code == a.status_code == 401
        assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"
        assert a.json()["error"]["message"] == b.json()["error"]["message"]  # не раскрываем существование почты

    def test_disabled_account_403(self, client, services, h):
        h.make_user(services, "off@example.com", "off-password-1", disabled=True)
        h.assert_error(h.login(client, "off@example.com", "off-password-1"), 403, "account_disabled")

    def test_disabled_with_wrong_password_does_not_leak_state(self, client, services, h):
        h.make_user(services, "off2@example.com", "off-password-1", disabled=True)
        h.assert_error(h.login(client, "off2@example.com", "bad-password-1"), 401, "invalid_credentials")

    @pytest.mark.parametrize("body,field", [
        ({}, "email"),
        ({"password": "x"}, "email"),
        ({"email": "a@b.c"}, "password"),
        ({"email": "", "password": "x"}, "email"),
        ({"email": "a@b.c", "password": ""}, "password"),
    ])
    def test_validation_errors_400(self, client, body, field, h):
        r = client.post(h.url("/auth/login"), json=body)
        h.assert_error(r, 400, "validation_error", field=field)

    def test_malformed_json_400(self, client, h):
        r = client.post(h.url("/auth/login"), content=b"{not json", headers={"Content-Type": "application/json"})
        h.assert_error(r, 400, "validation_error")

    def test_email_is_case_insensitive(self, client, admin_creds, h):
        r = h.login(client, admin_creds[0].upper(), admin_creds[1])
        assert r.status_code == 200

    def test_sixth_attempt_429_too_many_attempts(self, client, admin_creds, h):
        for _ in range(5):
            h.assert_error(h.login(client, admin_creds[0], "wrong-password"), 401, "invalid_credentials")
        r = h.login(client, admin_creds[0], admin_creds[1])  # даже верный пароль на 6-й попытке
        err = h.assert_error(r, 429, "too_many_attempts")
        assert isinstance(err["details"]["retry_after"], int) and err["details"]["retry_after"] > 0
        assert int(r.headers["Retry-After"]) == err["details"]["retry_after"]

    def test_limit_is_per_email(self, client, admin_creds, user_creds, h):
        for _ in range(5):
            h.login(client, admin_creds[0], "wrong-password")
        assert h.login(client, *user_creds).status_code == 200

    def test_successful_login_resets_counter(self, client, admin_creds, h):
        for _ in range(4):
            h.login(client, admin_creds[0], "wrong-password")
        assert h.login(client, *admin_creds).status_code == 200
        for _ in range(4):
            h.assert_error(h.login(client, admin_creds[0], "wrong-password"), 401, "invalid_credentials")

    def test_block_expires_after_15_minutes(self, client, admin_creds, clock, h):
        for _ in range(5):
            h.login(client, admin_creds[0], "wrong-password")
        h.assert_error(h.login(client, *admin_creds), 429, "too_many_attempts")
        clock.advance(minutes=15, seconds=1)
        assert h.login(client, *admin_creds).status_code == 200

    def test_ip_limit_20_attempts_across_emails(self, client, h):
        for i in range(20):
            h.assert_error(h.login(client, f"user{i}@example.com", "wrong-password"), 401, "invalid_credentials")
        h.assert_error(h.login(client, "user99@example.com", "wrong-password"), 429, "too_many_attempts")

    def test_refresh_token_in_body_when_enabled(self, settings, services, admin_creds, h):
        from fastapi.testclient import TestClient

        from faktura.api.base import create_app

        app = create_app(settings.model_copy(update={"refresh_in_body": True}), services)
        c = TestClient(app)
        body = h.login(c, *admin_creds).json()
        assert body["refresh_token"]
        r = c.post(h.url("/auth/refresh"), json={"refresh_token": body["refresh_token"]})
        assert r.status_code == 200
        assert r.json()["refresh_token"] != body["refresh_token"]


# --- refresh -----------------------------------------------------------------------------------

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

    def test_new_access_token_works(self, client, admin_creds, h):
        h.login(client, *admin_creds)
        token = client.post(h.url("/auth/refresh")).json()["access_token"]
        assert client.get(h.url("/auth/me"), headers=h.bearer(token)).status_code == 200

    def test_chain_of_rotations(self, client, admin_creds, h):
        h.login(client, *admin_creds)
        for _ in range(3):
            assert client.post(h.url("/auth/refresh")).status_code == 200

    def test_reuse_of_old_refresh_is_401_and_kills_whole_chain(self, app, client, admin_creds, h):
        rt1 = h.cookie_value(h.login(client, *admin_creds))
        r2 = h.refresh_with(app, rt1)
        assert r2.status_code == 200
        rt2 = h.cookie_value(r2)
        # злоумышленник/гонка предъявляет уже использованный rt1
        h.assert_error(h.refresh_with(app, rt1), 401, "refresh_invalid")
        # цепочка погашена: свежий rt2 тоже больше не работает
        h.assert_error(h.refresh_with(app, rt2), 401, "refresh_invalid")

    def test_reuse_does_not_touch_other_sessions(self, app, admin_creds, h):
        a = h.new_client(app)
        b = h.new_client(app)
        rt_a = h.cookie_value(h.login(a, *admin_creds))
        rt_b = h.cookie_value(h.login(b, *admin_creds))
        h.refresh_with(app, rt_a)
        h.assert_error(h.refresh_with(app, rt_a), 401, "refresh_invalid")
        assert h.refresh_with(app, rt_b).status_code == 200

    def test_no_cookie_no_body_401(self, client, h):
        h.assert_error(client.post(h.url("/auth/refresh")), 401, "refresh_invalid")

    @pytest.mark.parametrize("token", ["garbage", "rt_" + "a" * 40, "", "x" * 2000])
    def test_garbage_token_401(self, app, token, h):
        h.assert_error(h.refresh_with(app, token), 401, "refresh_invalid")

    def test_refresh_expires_after_30_days_when_remember(self, app, client, admin_creds, clock, h):
        rt = h.cookie_value(h.login(client, *admin_creds, remember=True))
        clock.advance(days=29)
        r = h.refresh_with(app, rt)
        assert r.status_code == 200
        rt2 = h.cookie_value(r)
        clock.advance(days=31)
        h.assert_error(h.refresh_with(app, rt2), 401, "refresh_invalid")

    def test_remember_false_refresh_valid_at_11h_invalid_at_13h(self, app, admin_creds, clock, h):
        c1, c2 = h.new_client(app), h.new_client(app)
        rt_11 = h.cookie_value(h.login(c1, *admin_creds, remember=False))
        rt_13 = h.cookie_value(h.login(c2, *admin_creds, remember=False))
        clock.advance(hours=11)
        assert h.refresh_with(app, rt_11).status_code == 200
        clock.advance(hours=2)  # от выдачи прошло 13 ч
        h.assert_error(h.refresh_with(app, rt_13), 401, "refresh_invalid")

    def test_rotated_cookie_keeps_remember_false_ttl(self, app, client, admin_creds, h):
        h.login(client, *admin_creds, remember=False)
        r = client.post(h.url("/auth/refresh"))
        assert 0 < int(h.parse_set_cookie(r)["max-age"]) <= h.TWELVE_HOURS

    def test_rotated_cookie_remember_true_ttl(self, client, admin_creds, h):
        h.login(client, *admin_creds, remember=True)
        r = client.post(h.url("/auth/refresh"))
        assert 0 < int(h.parse_set_cookie(r)["max-age"]) <= h.THIRTY_DAYS

    def test_refresh_after_user_disabled_is_rejected(self, app, client, services, h):
        user = h.make_user(services, "soon-off@example.com", "pass-word-1")
        rt = h.cookie_value(h.login(client, "soon-off@example.com", "pass-word-1"))
        with services.db.uow() as uow:
            uow.users.update(user.id, disabled=True)
        r = h.refresh_with(app, rt)
        assert r.status_code in (401, 403)
        assert r.json()["error"]["code"] in ("refresh_invalid", "account_disabled")

    def test_refresh_endpoint_is_public(self, client, h):
        """Без Authorization отвечает не unauthorized, а refresh_invalid."""
        err = client.post(h.url("/auth/refresh")).json()["error"]
        assert err["code"] != "unauthorized"


# --- logout ------------------------------------------------------------------------------------

class TestLogout:
    def test_logout_204_clears_cookie_and_revokes_refresh(self, app, client, admin_creds, h):
        r = h.login(client, *admin_creds)
        rt, token = h.cookie_value(r), r.json()["access_token"]
        out = client.post(h.url("/auth/logout"), json={}, headers=h.bearer(token))
        assert out.status_code == 204
        assert out.content == b""
        c = h.parse_set_cookie(out)
        assert c is not None and (c.value == "" or int(c["max-age"]) == 0)
        h.assert_error(h.refresh_with(app, rt), 401, "refresh_invalid")

    def test_logout_only_current_session_by_default(self, app, admin_creds, h):
        a, b = h.new_client(app), h.new_client(app)
        ra, rb = h.login(a, *admin_creds), h.login(b, *admin_creds)
        a.post(h.url("/auth/logout"), json={}, headers=h.bearer(ra.json()["access_token"]))
        assert h.refresh_with(app, h.cookie_value(rb)).status_code == 200

    def test_logout_all_revokes_every_session_of_user(self, app, admin_creds, h):
        a, b = h.new_client(app), h.new_client(app)
        ra, rb = h.login(a, *admin_creds), h.login(b, *admin_creds)
        out = a.post(h.url("/auth/logout"), json={"all": True}, headers=h.bearer(ra.json()["access_token"]))
        assert out.status_code == 204
        h.assert_error(h.refresh_with(app, h.cookie_value(rb)), 401, "refresh_invalid")

    def test_logout_all_does_not_touch_other_users(self, app, admin_creds, user_creds, h):
        a, b = h.new_client(app), h.new_client(app)
        ra, rb = h.login(a, *admin_creds), h.login(b, *user_creds)
        a.post(h.url("/auth/logout"), json={"all": True}, headers=h.bearer(ra.json()["access_token"]))
        assert h.refresh_with(app, h.cookie_value(rb)).status_code == 200

    def test_logout_without_session_is_idempotent_204(self, client, h):
        assert client.post(h.url("/auth/logout"), json={}).status_code == 204

    def test_logout_twice(self, client, admin_creds, h):
        r = h.login(client, *admin_creds)
        hd = h.bearer(r.json()["access_token"])
        assert client.post(h.url("/auth/logout"), json={}, headers=hd).status_code == 204
        assert client.post(h.url("/auth/logout"), json={}, headers=hd).status_code == 204


# --- GET /auth/me и проверка токена ------------------------------------------------------------

class TestAuthMe:
    def test_me_returns_user(self, client, admin_token, admin_creds, h):
        r = client.get(h.url("/auth/me"), headers=h.bearer(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == admin_creds[0]
        assert {"id", "user_id", "email", "name", "is_admin"} <= set(body)

    def test_no_token_401_unauthorized(self, client, h):
        err = h.assert_error(client.get(h.url("/auth/me")), 401, "unauthorized")
        assert err["request_id"] is None or isinstance(err["request_id"], str)

    @pytest.mark.parametrize("header", ["Bearer", "Bearer abc.def.ghi", "Basic dXNlcjpwYXNz", "bearer", "Token xyz", " "])
    def test_bad_authorization_header_401(self, client, header, h):
        h.assert_error(client.get(h.url("/auth/me"), headers={"Authorization": header}), 401, "unauthorized")

    def test_expired_access_is_token_expired(self, client, admin_token, clock, h):
        clock.advance(seconds=901)
        h.assert_error(client.get(h.url("/auth/me"), headers=h.bearer(admin_token)), 401, "token_expired")

    def test_access_valid_before_expiry(self, client, admin_token, clock, h):
        clock.advance(seconds=899)
        assert client.get(h.url("/auth/me"), headers=h.bearer(admin_token)).status_code == 200

    def test_expired_access_then_refresh_then_ok(self, client, admin_creds, clock, h):
        h.login(client, *admin_creds)
        clock.advance(minutes=16)
        token = client.post(h.url("/auth/refresh")).json()["access_token"]
        assert client.get(h.url("/auth/me"), headers=h.bearer(token)).status_code == 200

    def test_token_of_disabled_user_is_rejected(self, client, services, h):
        user = h.make_user(services, "tmp@example.com", "pass-word-1")
        token = h.login_token(client, "tmp@example.com", "pass-word-1")
        with services.db.uow() as uow:
            uow.users.update(user.id, disabled=True)
        r = client.get(h.url("/auth/me"), headers=h.bearer(token))
        assert r.status_code in (401, 403)

    def test_token_of_deleted_user_is_unauthorized(self, client, h):
        """Токен подписан верно, но пользователя нет в БД."""
        import jwt as pyjwt

        from faktura.api.base import auth

        secret = "test-secret-test-secret-test-secret"
        now = auth.utcnow()
        forged = pyjwt.encode({"sub": "usr_ghost", "workspace_id": "ws", "is_admin": True,
                               "iat": int(now.timestamp()), "exp": int(now.timestamp()) + 900}, secret, algorithm="HS256")
        r = client.get(h.url("/auth/me"), headers=h.bearer(forged))
        assert r.status_code == 401


# --- register ----------------------------------------------------------------------------------

def test_register_route_removed(client, h):
    r = client.post(h.url("/auth/register"), json={"email": "new@example.com", "password": "new-password-1"})
    assert r.status_code in (404, 405)
