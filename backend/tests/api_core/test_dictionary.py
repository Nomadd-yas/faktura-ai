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

def test_get_dictionary_solar_shape(client, auth):
    d = ok(client.get(f"{API}/dictionary", headers=auth))
    assert set(d) == {"collection", "version", "params", "units", "materials", "conditions"}
    assert d["collection"] == "solar" and d["version"].startswith("solar-")
    pce = d["params"]["pce"]
    assert {"text": "PCE", "custom": False} in pce and {"text": "КПД", "custom": False} in pce
    assert all(s["custom"] is False for s in pce)
    assert "conditions" in d["params"] and "bandgap" in d["params"]
    assert any(u["param"] == "voc" and u["unit"] == "mV" and u["factor"] == 0.001 for u in d["units"])
    assert all(u["param"] != "conditions" for u in d["units"])
    mapbi = next(m for m in d["materials"] if m["canon"] == "CH3NH3PbI3")
    assert mapbi["id"] == "ch3nh3pbi3" and mapbi["collection"] == "solar"
    assert {"text": "mapbi3", "custom": False} in mapbi["synonyms"]
    am = next(c for c in d["conditions"] if c["canon"] == "AM1.5G")
    assert {"text": "AM 1.5G", "custom": False} in am["synonyms"]


def test_get_dictionary_rag_and_validation(client, auth):
    d = ok(client.get(f"{API}/dictionary", params={"collection": "rag"}, headers=auth))
    assert d["collection"] == "rag" and set(d["params"]) >= {"r5", "mrr", "chunk"}
    assert d["materials"] == [] and d["conditions"] == []
    err(client.get(f"{API}/dictionary", params={"collection": "zzz"}, headers=auth), 400, "validation_error")
    assert client.get(f"{API}/dictionary").status_code == 401


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


def test_param_synonym_validation(client, auth):
    err(post_param(client, auth, ""), 400, "validation_error")
    err(post_param(client, auth, "x" * 201), 400, "validation_error")
    err(client.post(f"{API}/dictionary/params/solar/pce/synonyms", json={"text": 5}, headers=auth), 400,
        "validation_error")
    err(post_param(client, auth, "abc", param="nope"), 404, "not_found")
    err(post_param(client, auth, "abc", collection="zzz"), 400, "validation_error")
    assert client.post(f"{API}/dictionary/params/solar/pce/synonyms", json={"text": "a"}).status_code == 401


def test_duplicates_are_409(client, auth):
    err(post_param(client, auth, "pce"), 409, "synonym_exists")  # встроенное, другой регистр
    err(post_param(client, auth, "напряжение холостого хода", param="pce"), 409, "synonym_exists")  # у другого параметра
    ok(post_param(client, auth, "Мой КПД"), 201)
    err(post_param(client, auth, "мой кпд"), 409, "synonym_exists")
    err(post_param(client, auth, "мой кпд", param="voc"), 409, "synonym_exists")  # в коллекции написание одно


def test_builtin_cannot_be_removed(client, auth):
    e = err(client.delete(f"{API}/dictionary/params/solar/pce/synonyms/{quote('КПД')}", headers=auth), 409,
            "synonym_builtin")
    assert "Встроен" in e["message"]
    err(client.delete(f"{API}/dictionary/params/solar/pce/synonyms/nothing", headers=auth), 404, "not_found")
    err(client.delete(f"{API}/dictionary/params/solar/nope/synonyms/x", headers=auth), 404, "not_found")


def test_entry_synonyms(client, auth):
    r = client.post(f"{API}/dictionary/entries/ch3nh3pbi3/synonyms", json={"text": "МАPI-перовскит"}, headers=auth)
    body = ok(r, 201)
    assert body["synonym"]["entry_id"] == "ch3nh3pbi3" and body["synonym"]["custom"] is True
    d = ok(client.get(f"{API}/dictionary", headers=auth))
    m = next(x for x in d["materials"] if x["id"] == "ch3nh3pbi3")
    assert [s["text"] for s in m["synonyms"] if s["custom"]] == ["МАPI-перовскит"]
    r = client.post(f"{API}/dictionary/entries/am15g/synonyms", json={"text": "стандартное солнце"}, headers=auth)
    ok(r, 201)
    d = ok(client.get(f"{API}/dictionary", headers=auth))
    assert [s["text"] for s in next(c for c in d["conditions"] if c["id"] == "am15g")["synonyms"] if s["custom"]] \
        == ["стандартное солнце"]
    err(client.post(f"{API}/dictionary/entries/ch3nh3pbi3/synonyms", json={"text": "MAPBI3"}, headers=auth), 409,
        "synonym_exists")
    err(client.post(f"{API}/dictionary/entries/zzz/synonyms", json={"text": "a"}, headers=auth), 404, "not_found")
    err(client.delete(f"{API}/dictionary/entries/ch3nh3pbi3/synonyms/mapbi3", headers=auth), 409, "synonym_builtin")
    err(client.delete(f"{API}/dictionary/entries/ch3nh3pbi3/synonyms/{quote('нет такого')}", headers=auth), 404,
        "not_found")
    ok(client.delete(f"{API}/dictionary/entries/ch3nh3pbi3/synonyms/{quote('МАPI-перовскит')}", headers=auth))
    d = ok(client.get(f"{API}/dictionary", headers=auth))
    assert not [s for s in next(x for x in d["materials"] if x["id"] == "ch3nh3pbi3")["synonyms"] if s["custom"]]


def test_journal_records_actions(client, auth, services):
    ok(post_param(client, auth, "Мой КПД"), 201)
    client.delete(f"{API}/dictionary/params/solar/pce/synonyms/{quote('мой кпд')}", headers=auth)
    with services.db.uow() as uow:
        acts = [a.action for a in uow.actions.list(uow.users.get_by_email(services.settings.admin_email).workspace_id)]
    assert "dictionary.synonym_add" in acts and "dictionary.synonym_remove" in acts


def test_workspace_isolation(client, auth, other_auth):
    ok(post_param(client, auth, "Мой КПД"), 201)
    d = ok(client.get(f"{API}/dictionary", headers=other_auth))
    assert custom_of(d, "pce") == []
    ok(post_param(client, other_auth, "Мой КПД"), 201)  # то же написание в другой области - можно
    err(client.delete(f"{API}/dictionary/params/solar/voc/synonyms/{quote('Мой КПД')}", headers=other_auth), 404,
        "not_found")  # написание привязано к параметру pce
    ok(client.delete(f"{API}/dictionary/params/solar/pce/synonyms/{quote('Мой КПД')}", headers=other_auth))
    assert custom_of(ok(client.get(f"{API}/dictionary", headers=auth)), "pce") == ["Мой КПД"]


# --- действие на разбор -------------------------------------------------------------------------

def test_user_synonym_applies_to_new_run_and_restart(client, auth, pdfs, renamed_metric):
    r = upload(client, auth, [pdfs["D001"]])
    assert r.status_code == 202, r.text
    run0 = r.json()["id"]
    assert RAW in metrics_of(client, auth, run0)

    ok(post_param(client, auth, RAW), 201)
    run1 = upload(client, auth, [pdfs["D001"]]).json()["id"]
    got = metrics_of(client, auth, run1)
    assert RAW not in got and "PCE" in got
    assert RAW in metrics_of(client, auth, run0)  # старый разбор не пересчитывается сам

    ok(client.post(f"{API}/runs/{run0}/restart", headers=auth), 202)
    got = metrics_of(client, auth, run0)
    assert RAW not in got and "PCE" in got


def test_other_workspace_synonym_does_not_apply(client, auth, other_auth, pdfs, renamed_metric):
    ok(post_param(client, other_auth, RAW), 201)
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    assert RAW in metrics_of(client, auth, run)


def test_passport_dictionary_version_changes(client, auth, pdfs):
    def passport(run_id):
        r = client.get(f"{API}/runs/{run_id}/passport.json", headers=auth)
        assert r.status_code == 200
        return r.json()["versions"]["dictionary"], r.content

    run0 = upload(client, auth, [pdfs["D001"]]).json()["id"]
    v0, _ = passport(run0)
    assert v0 and "+u" not in v0
    ok(post_param(client, auth, "Мой КПД"), 201)
    run1 = upload(client, auth, [pdfs["D001"]]).json()["id"]
    v1, raw = passport(run1)
    assert v1.startswith(v0 + "+u") and len(v1) == len(v0) + 10
    import io
    import zipfile

    z = client.get(f"{API}/runs/{run1}/submission.zip", headers=auth)
    assert zipfile.ZipFile(io.BytesIO(z.content)).read("passport.json") == raw  # паспорт и zip - байт в байт
    assert passport(run0)[0] == v0  # прежний разбор: словарь, которым он нормализован
    ok(client.delete(f"{API}/dictionary/params/solar/pce/synonyms/{quote('Мой КПД')}", headers=auth))
    run2 = upload(client, auth, [pdfs["D001"]]).json()["id"]
    assert passport(run2)[0] == v0
