"""Словарь нормализации (CORE_API §4h): GET /dictionary, свои синонимы, их действие на разбор, версия в паспорте."""
from __future__ import annotations

from urllib.parse import quote

import pytest

from .conftest import API, upload

RAW = "Коэффициент полезного действия устройства"


def ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code
    return r.json()["error"]


def post_param(client, auth, text, param="pce", collection="solar"):
    return client.post(f"{API}/dictionary/params/{collection}/{param}/synonyms", json={"text": text}, headers=auth)


def custom_of(d, key):
    return [s["text"] for s in d["params"][key] if s["custom"]]


@pytest.fixture
def other_auth(client, services):
    from faktura.domain.security import hash_password

    with services.db.uow() as uow:
        uow.workspaces.ensure("ws_other", "Другая")
        uow.users.create(workspace_id="ws_other", email="o@example.com", name="Другой",
                         password_hash=hash_password("other-password-1"), is_admin=False, disabled=False)
    r = client.post(f"{API}/auth/login", json={"email": "o@example.com", "password": "other-password-1"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def renamed_metric(monkeypatch):
    """Мок-экстрактор отдаёт PCE под «сырым» названием, которого нет в словаре."""
    from faktura.ml.adapters import MockGoldExtractor

    orig = MockGoldExtractor.extract

    def extract(self, req):
        out = orig(self, req)
        for r in out.bundle.results:
            if r.metric_name == "PCE":
                r.metric_name = RAW
        return out

    monkeypatch.setattr(MockGoldExtractor, "extract", extract)


def metrics_of(client, auth, run_id) -> set[str]:
    rows, cursor = [], None
    while True:
        r = client.get(f"{API}/results", params={"run": run_id, "limit": 200, **({"cursor": cursor} if cursor else {})},
                       headers=auth)
        d = ok(r)
        rows += d["data"]
        cursor = d.get("next_cursor")
        if not cursor:
            return {x["row"]["metric_name"] for x in rows}


# --- GET /dictionary ----------------------------------------------------------------------------





# --- добавление и удаление ----------------------------------------------------------------------

def test_add_and_remove_param_synonym(client, auth):
    base = ok(client.get(f"{API}/dictionary", headers=auth))["version"]
    r = post_param(client, auth, "  Эффективность  ячейки ")
    body = ok(r, 201)
    assert r.headers["X-Action-Id"] == body["action_id"] and "X-Faktura-Stub" not in r.headers
    assert body["synonym"]["text"] == "Эффективность ячейки" and body["synonym"]["custom"] is True
    assert body["synonym"]["param"] == "pce" and body["renormalize"] == {"papers": 0, "job_id": None}
    d = ok(client.get(f"{API}/dictionary", headers=auth))
    assert custom_of(d, "pce") == ["Эффективность ячейки"]
    assert d["version"] != base and d["version"].startswith(base + "+u")
    r = client.delete(f"{API}/dictionary/params/solar/pce/synonyms/{quote('эффективность ячейки')}", headers=auth)
    assert ok(r)["removed"]["text"] == "Эффективность ячейки"
    d = ok(client.get(f"{API}/dictionary", headers=auth))
    assert custom_of(d, "pce") == [] and d["version"] == base














# --- действие на разбор -------------------------------------------------------------------------





