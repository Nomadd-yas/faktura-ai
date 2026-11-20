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

    def test_get_me_requires_auth(self, client, h):
        h.assert_error(client.get(h.url("/me")), 401, "unauthorized")

    def test_patch_name_persists(self, client, user_token, h):
        r = client.patch(h.url("/me"), json={"name": "Новое имя"}, headers=h.bearer(user_token))
        assert r.status_code == 200 and r.json()["name"] == "Новое имя"
        assert client.get(h.url("/me"), headers=h.bearer(user_token)).json()["name"] == "Новое имя"

    def test_patch_profile_fields(self, client, user_token, h):
        body = {"name": "Котова Е.", "role": "Материаловед", "team": "Команда хакатона", "color": "graphite",
                "focus": "Перовскитные и тонкоплёночные солнечные элементы"}
        r = client.patch(h.url("/me"), json=body, headers=h.bearer(user_token))
        assert r.status_code == 200, r.text
        got = client.get(h.url("/me"), headers=h.bearer(user_token)).json()
        assert {k: got[k] for k in body} == body

    def test_patch_partial_keeps_other_fields(self, client, user_token, h):
        client.patch(h.url("/me"), json={"role": "Аналитик"}, headers=h.bearer(user_token))
        client.patch(h.url("/me"), json={"team": "Команда"}, headers=h.bearer(user_token))
        got = client.get(h.url("/me"), headers=h.bearer(user_token)).json()
        assert got["role"] == "Аналитик" and got["team"] == "Команда"

    def test_patch_empty_body_is_noop(self, client, user_token, h):
        before = client.get(h.url("/me"), headers=h.bearer(user_token)).json()
        r = client.patch(h.url("/me"), json={}, headers=h.bearer(user_token))
        assert r.status_code == 200 and r.json() == before

    def test_name_80_chars_ok(self, client, user_token, h):
        assert client.patch(h.url("/me"), json={"name": "я" * 80}, headers=h.bearer(user_token)).status_code == 200

    @pytest.mark.parametrize("name", ["", "я" * 81, "   "])
    def test_name_invalid_400(self, client, user_token, name, h):
        r = client.patch(h.url("/me"), json={"name": name}, headers=h.bearer(user_token))
        h.assert_error(r, 400, "validation_error", field="name")

    def test_email_cannot_be_changed(self, client, user_token, h):
        r = client.patch(h.url("/me"), json={"email": "other@example.com"}, headers=h.bearer(user_token))
        h.assert_error(r, 400, "validation_error", field="email")

    def test_invalid_color_400(self, client, user_token, h):
        r = client.patch(h.url("/me"), json={"color": "not-a-color"}, headers=h.bearer(user_token))
        h.assert_error(r, 400, "validation_error", field="color")

    def test_cannot_self_promote_to_admin(self, client, user_token, h):
        r = client.patch(h.url("/me"), json={"is_admin": True}, headers=h.bearer(user_token))
        assert r.status_code in (200, 400)
        assert client.get(h.url("/me"), headers=h.bearer(user_token)).json()["is_admin"] is False
        assert client.get(h.url("/admin/users"), headers=h.bearer(user_token)).status_code == 403

    def test_patch_requires_auth(self, client, h):
        h.assert_error(client.patch(h.url("/me"), json={"name": "x"}), 401, "unauthorized")

    def test_patch_does_not_change_other_users(self, client, user_token, admin_token, admin_creds, h):
        before = client.get(h.url("/me"), headers=h.bearer(admin_token)).json()
        client.patch(h.url("/me"), json={"name": "Только я"}, headers=h.bearer(user_token))
        assert client.get(h.url("/me"), headers=h.bearer(admin_token)).json() == before

    def test_auth_me_equals_me(self, client, user_token, h):
        a = client.get(h.url("/me"), headers=h.bearer(user_token)).json()
        b = client.get(h.url("/auth/me"), headers=h.bearer(user_token)).json()
        assert a == b


class TestCollections:
    def _data(self, client, token, h) -> list[dict]:
        r = client.get(h.url("/collections"), headers=h.bearer(token))
        assert r.status_code == 200
        return r.json()["data"]

    def test_requires_auth(self, client, h):
        h.assert_error(client.get(h.url("/collections")), 401, "unauthorized")

    def test_solar_and_rag_present(self, client, user_token, h):
        ids = [c["id"] for c in self._data(client, user_token, h)]
        assert "solar" in ids and "rag" in ids

    def test_solar_header_fields(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        assert solar["name"] == "Солнечные элементы"
        for k in ("hint", "icon", "row_word", "params", "table", "best", "quality", "plot", "topics", "scope_note"):
            assert k in solar, k
        assert {"one", "gen", "cap", "all", "allCap", "byRows"} <= set(solar["row_word"])

    def test_solar_params_complete_with_u6_extras(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        keys = [p["key"] for p in solar["params"]]
        for k in SOLAR_BASE_PARAMS + SOLAR_EXTRA_PARAMS:
            assert k in keys, k
        assert len(keys) == len(set(keys))

    def test_param_shape(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        for p in solar["params"]:
            assert {"key", "label", "unit", "units", "digits", "text", "direction", "target"} <= set(p), p
            assert isinstance(p["units"], dict) and isinstance(p["text"], bool)
            assert p["direction"] in ("higher", "lower", "none")
            assert p["key"] == p["key"].lower()

    def test_pce_param_details(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        pce = next(p for p in solar["params"] if p["key"] == "pce")
        assert pce["unit"] == "%" and pce["direction"] == "higher"
        assert pce["target"] == {"entity": "result", "metric_name": "PCE"}

    def test_units_are_ascii(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        for p in solar["params"]:
            assert p["unit"].isascii(), p
            assert all(u.isascii() for u in p["units"]), p

    def test_conditions_is_text_param(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        cond = next(p for p in solar["params"] if p["key"] == "conditions")
        assert cond["text"] is True and cond["units"] == {}

    def test_table_best_plot_refer_to_known_params(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        keys = {p["key"] for p in solar["params"]}
        assert solar["table"] == ["voc", "jsc", "ff", "pce"]
        assert solar["best"] == "pce"
        assert set(solar["quality"]) <= keys
        assert solar["plot"] == {"x": "voc", "y": "pce"}

    def test_topics(self, client, user_token, h):
        solar = next(c for c in self._data(client, user_token, h) if c["id"] == "solar")
        ids = {t["id"] for t in solar["topics"]}
        assert {"perovskite", "cigs", "cdte", "tandem", "leadfree"} <= ids

    def test_rag_is_placeholder_only(self, client, user_token, h):
        rag = next(c for c in self._data(client, user_token, h) if c["id"] == "rag")
        assert {"name", "params"} <= set(rag)

    def test_collections_response_is_stable(self, client, user_token, h):
        a = client.get(h.url("/collections"), headers=h.bearer(user_token)).json()
        b = client.get(h.url("/collections"), headers=h.bearer(user_token)).json()
        assert a == b
