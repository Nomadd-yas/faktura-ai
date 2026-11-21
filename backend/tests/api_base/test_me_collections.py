"""GET/PATCH /me, GET /collections (api_contract §4.3, spec/04 §4.2-4.3)."""
from __future__ import annotations

import pytest

SOLAR_EXTRA_PARAMS = ["rs", "rsh", "hi", "jsc_eqe", "stabilized_pce", "t80", "retained_pce", "area"]
SOLAR_BASE_PARAMS = ["pce", "voc", "jsc", "ff", "bandgap", "thickness", "conditions"]


class TestMe:
    def test_get_me(self, client, user_token, user_creds, h):
        r = client.get(h.url("/me"), headers=h.bearer(user_token))
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == user_creds[0] and body["is_admin"] is False
        assert body["id"] == body["user_id"]
        assert {"name", "role", "team", "color", "focus"} <= set(body)
        assert "password_hash" not in body















