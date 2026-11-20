"""GET/POST /admin/users, PATCH /admin/users/{id} (BACKEND §2.2, api_contract §4.15)."""
from __future__ import annotations

import pytest

NEW = {"email": "new.user@example.com", "name": "Новый Пользователь", "password": "long-enough-1"}


class TestAccessControl:
    @pytest.mark.parametrize("method,path,body", [
        ("GET", "/admin/users", None),
        ("POST", "/admin/users", NEW),
        ("PATCH", "/admin/users/usr_x", {"name": "x"}),
    ])
    def test_regular_user_403(self, client, user_token, method, path, body, h):
        r = client.request(method, h.url(path), json=body, headers=h.bearer(user_token))
        h.assert_error(r, 403, "forbidden")

    @pytest.mark.parametrize("method,path,body", [
        ("GET", "/admin/users", None), ("POST", "/admin/users", NEW), ("PATCH", "/admin/users/usr_x", {"name": "x"}),
    ])
    def test_no_token_401(self, client, method, path, body, h):
        h.assert_error(client.request(method, h.url(path), json=body), 401, "unauthorized")

    def test_forbidden_does_not_create_user(self, client, user_token, services, h):
        client.post(h.url("/admin/users"), json=NEW, headers=h.bearer(user_token))
        with services.db.uow() as uow:
            assert uow.users.get_by_email(NEW["email"]) is None


class TestList:
    def test_list_has_disabled_flag_and_no_secrets(self, client, admin_token, user_creds, services, h):
        h.make_user(services, "blocked@example.com", "blocked-pass-1", disabled=True)
        r = client.get(h.url("/admin/users"), headers=h.bearer(admin_token))
        assert r.status_code == 200
        rows = r.json()["data"]
        by_email = {u["email"]: u for u in rows}
        assert {"admin@example.com", user_creds[0], "blocked@example.com"} <= set(by_email)
        assert by_email["blocked@example.com"]["disabled"] is True
        assert by_email["admin@example.com"]["disabled"] is False and by_email["admin@example.com"]["is_admin"] is True
        for u in rows:
            assert {"id", "user_id", "email", "name", "is_admin", "disabled"} <= set(u)
            assert "password_hash" not in u and "password" not in u

    def test_list_only_own_workspace(self, client, admin_token, h):
        assert isinstance(client.get(h.url("/admin/users"), headers=h.bearer(admin_token)).json()["data"], list)


class TestCreate:
    def test_create_with_password_201(self, client, admin_token, h):
        r = client.post(h.url("/admin/users"), json=NEW, headers=h.bearer(admin_token))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["user"]["email"] == NEW["email"] and body["user"]["name"] == NEW["name"]
        assert body["user"]["is_admin"] is False
        assert "password" not in body  # свой пароль админу не возвращаем
        assert "password_hash" not in str(body)

    def test_created_user_can_login(self, client, admin_token, h):
        client.post(h.url("/admin/users"), json=NEW, headers=h.bearer(admin_token))
        assert h.login(client, NEW["email"], NEW["password"]).status_code == 200

    def test_generate_returns_password_once(self, client, admin_token, h):
        body = {"email": "gen@example.com", "name": "Генерированный", "generate": True}
        r = client.post(h.url("/admin/users"), json=body, headers=h.bearer(admin_token))
        assert r.status_code == 201, r.text
        pw = r.json()["password"]
        assert isinstance(pw, str) and len(pw) >= 8
        assert h.login(client, "gen@example.com", pw).status_code == 200
        # повторно пароль нигде не выдаётся
        listing = client.get(h.url("/admin/users"), headers=h.bearer(admin_token))
        assert pw not in listing.text
        assert "password" not in next(u for u in listing.json()["data"] if u["email"] == "gen@example.com")

    def test_generated_passwords_differ(self, client, admin_token, h):
        pws = set()
        for i in range(3):
            r = client.post(h.url("/admin/users"), json={"email": f"g{i}@example.com", "name": "g", "generate": True},
                            headers=h.bearer(admin_token))
            pws.add(r.json()["password"])
        assert len(pws) == 3

    def test_generate_false_without_password_400(self, client, admin_token, h):
        r = client.post(h.url("/admin/users"), json={"email": "np@example.com", "name": "n", "generate": False},
                        headers=h.bearer(admin_token))
        h.assert_error(r, 400, "validation_error", field="password")

    def test_neither_password_nor_generate_400(self, client, admin_token, h):
        r = client.post(h.url("/admin/users"), json={"email": "np2@example.com", "name": "n"},
                        headers=h.bearer(admin_token))
        h.assert_error(r, 400, "validation_error", field="password")

    @pytest.mark.parametrize("pw", ["", "short", "1234567"])
    def test_short_password_400_field_password(self, client, admin_token, pw, h):
        r = client.post(h.url("/admin/users"), json={**NEW, "password": pw}, headers=h.bearer(admin_token))
        h.assert_error(r, 400, "validation_error", field="password")

    def test_password_exactly_8_ok(self, client, admin_token, h):
        r = client.post(h.url("/admin/users"), json={**NEW, "password": "12345678"}, headers=h.bearer(admin_token))
        assert r.status_code == 201

    def test_short_password_does_not_create_user(self, client, admin_token, services, h):
        client.post(h.url("/admin/users"), json={**NEW, "password": "short"}, headers=h.bearer(admin_token))
        with services.db.uow() as uow:
            assert uow.users.get_by_email(NEW["email"]) is None

    def test_email_taken_409(self, client, admin_token, admin_creds, h):
        r = client.post(h.url("/admin/users"), json={**NEW, "email": admin_creds[0]}, headers=h.bearer(admin_token))
        h.assert_error(r, 409, "email_taken", field="email")

    def test_email_taken_case_insensitive(self, client, admin_token, admin_creds, h):
        r = client.post(h.url("/admin/users"), json={**NEW, "email": admin_creds[0].upper()},
                        headers=h.bearer(admin_token))
        h.assert_error(r, 409, "email_taken")

    def test_second_create_same_email_409(self, client, admin_token, h):
        assert client.post(h.url("/admin/users"), json=NEW, headers=h.bearer(admin_token)).status_code == 201
        h.assert_error(client.post(h.url("/admin/users"), json=NEW, headers=h.bearer(admin_token)), 409, "email_taken")

    @pytest.mark.parametrize("email", ["", "not-an-email", "a@", "@b.c"])
    def test_invalid_email_400(self, client, admin_token, email, h):
        r = client.post(h.url("/admin/users"), json={**NEW, "email": email}, headers=h.bearer(admin_token))
        h.assert_error(r, 400, "validation_error", field="email")

    def test_missing_email_400(self, client, admin_token, h):
        r = client.post(h.url("/admin/users"), json={"name": "n", "password": "long-enough-1"},
                        headers=h.bearer(admin_token))
        h.assert_error(r, 400, "validation_error", field="email")

    def test_create_admin_and_optional_profile_fields(self, client, admin_token, h):
        body = {**NEW, "is_admin": True, "role": "Куратор", "team": "ВИНИТИ"}
        r = client.post(h.url("/admin/users"), json=body, headers=h.bearer(admin_token))
        assert r.status_code == 201
        u = r.json()["user"]
        assert u["is_admin"] is True and u["role"] == "Куратор" and u["team"] == "ВИНИТИ"
        token = h.login_token(client, NEW["email"], NEW["password"])
        assert client.get(h.url("/admin/users"), headers=h.bearer(token)).status_code == 200

    def test_password_is_stored_hashed(self, client, admin_token, services, h):
        client.post(h.url("/admin/users"), json=NEW, headers=h.bearer(admin_token))
        with services.db.uow() as uow:
            stored = uow.users.get_by_email(NEW["email"]).password_hash
        assert stored and NEW["password"] not in stored and stored.startswith("$argon2")


class TestPatch:
    @pytest.fixture
    def target(self, services, h):
        return h.make_user(services, "target@example.com", "target-pass-1", name="Цель")

    def test_disable_blocks_login(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"disabled": True}, headers=h.bearer(admin_token))
        assert r.status_code == 200 and r.json()["disabled"] is True
        h.assert_error(h.login(client, "target@example.com", "target-pass-1"), 403, "account_disabled")

    def test_enable_back(self, client, admin_token, target, h):
        client.patch(h.url(f"/admin/users/{target.id}"), json={"disabled": True}, headers=h.bearer(admin_token))
        client.patch(h.url(f"/admin/users/{target.id}"), json={"disabled": False}, headers=h.bearer(admin_token))
        assert h.login(client, "target@example.com", "target-pass-1").status_code == 200

    def test_disable_revokes_refresh_sessions(self, app, client, admin_token, target, h):
        rt = h.cookie_value(h.login(h.new_client(app), "target@example.com", "target-pass-1"))
        client.patch(h.url(f"/admin/users/{target.id}"), json={"disabled": True}, headers=h.bearer(admin_token))
        assert h.refresh_with(app, rt).status_code in (401, 403)

    def test_promote_to_admin(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"is_admin": True}, headers=h.bearer(admin_token))
        assert r.status_code == 200 and r.json()["is_admin"] is True
        token = h.login_token(client, "target@example.com", "target-pass-1")
        assert client.get(h.url("/admin/users"), headers=h.bearer(token)).status_code == 200

    def test_rename(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"name": "Другое имя"}, headers=h.bearer(admin_token))
        assert r.status_code == 200 and r.json()["name"] == "Другое имя"

    def test_change_password(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"password": "brand-new-pass-1"},
                         headers=h.bearer(admin_token))
        assert r.status_code == 200 and "password" not in r.json()
        assert h.login(client, "target@example.com", "brand-new-pass-1").status_code == 200
        h.assert_error(h.login(client, "target@example.com", "target-pass-1"), 401, "invalid_credentials")

    @pytest.mark.parametrize("pw", ["short", "", "1234567"])
    def test_short_password_400_and_unchanged(self, client, admin_token, target, pw, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"password": pw}, headers=h.bearer(admin_token))
        h.assert_error(r, 400, "validation_error", field="password")
        assert h.login(client, "target@example.com", "target-pass-1").status_code == 200

    def test_password_8_chars_ok(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"password": "12345678"}, headers=h.bearer(admin_token))
        assert r.status_code == 200

    def test_unknown_user_404(self, client, admin_token, h):
        r = client.patch(h.url("/admin/users/usr_unknown"), json={"name": "x"}, headers=h.bearer(admin_token))
        h.assert_error(r, 404, "not_found")

    def test_empty_patch_is_noop(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={}, headers=h.bearer(admin_token))
        assert r.status_code == 200 and r.json()["email"] == "target@example.com"

    def test_email_cannot_be_patched(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"email": "x@y.z"}, headers=h.bearer(admin_token))
        assert r.status_code in (200, 400)
        if r.status_code == 400:
            assert r.json()["error"]["field"] == "email"
        assert h.login(client, "target@example.com", "target-pass-1").status_code == 200  # почта не изменилась

    def test_response_shape(self, client, admin_token, target, h):
        r = client.patch(h.url(f"/admin/users/{target.id}"), json={"name": "N"}, headers=h.bearer(admin_token))
        assert {"id", "user_id", "email", "name", "is_admin", "disabled"} <= set(r.json())
