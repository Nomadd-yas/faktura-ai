"""GET/POST /admin/users, PATCH /admin/users/{id} (BACKEND §2.2, api_contract §4.15)."""
from __future__ import annotations

import pytest

NEW = {"email": "new.user@example.com", "name": "Новый Пользователь", "password": "long-enough-1"}


class TestAccessControl:
    @pytest.mark.parametrize("method,path,body", [
        ("GET", "/admin/users", None),
    ])
    def test_regular_user_403(self, client, user_token, method, path, body, h):
        r = client.request(method, h.url(path), json=body, headers=h.bearer(user_token))
        h.assert_error(r, 403, "forbidden")






class TestCreate:
    def test_create_with_password_201(self, client, admin_token, h):
        r = client.post(h.url("/admin/users"), json=NEW, headers=h.bearer(admin_token))
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["user"]["email"] == NEW["email"] and body["user"]["name"] == NEW["name"]
        assert body["user"]["is_admin"] is False
        assert "password" not in body  # свой пароль админу не возвращаем
        assert "password_hash" not in str(body)

















