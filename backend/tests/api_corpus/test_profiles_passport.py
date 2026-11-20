"""Профили (версии, rerun_hint) и паспорт данных (REQ-079, REQ-113, REQ-115).

Контракт: api_contract §3.5, §4.6; spec/13 §13.3; spec/04 §4.17.
"""
from __future__ import annotations

import pytest

API = "/api/v1"
BUILTIN = "prf_solar_tz_v1"
EVAL_METRICS = ["PCE", "Voc", "Jsc", "FF", "Rs", "Rsh", "HI", "Jsc_EQE", "stabilized_PCE", "T80", "retained_PCE"]


def _create(client, auth, name="Мой профиль", **extra):
    return client.post(f"{API}/profiles", headers=auth,
                       json={"name": name, "collection": "solar", "base_profile_id": BUILTIN, **extra})


# ---- GET /profiles -----------------------------------------------------------------------------------------------
def test_list_profiles_contains_builtin(client, auth):
    r = client.get(f"{API}/profiles?collection=solar", headers=auth)
    assert r.status_code == 200
    data = r.json()["data"]
    p = next(x for x in data if x["profile_id"] == BUILTIN)
    assert p["builtin"] is True and p["collection"] == "solar" and p["version"] >= 1
    assert p["fields"] and p["rules"] and set(p["flags"]) >= {"export_absent", "ml_issue_min_confidence",
                                                               "backend_rules"}


def test_list_profiles_other_collection_empty_or_filtered(client, auth):
    data = client.get(f"{API}/profiles?collection=rag", headers=auth).json()["data"]
    assert all(p["collection"] == "rag" for p in data)


def test_get_profile_fields_cover_eval_metrics(client, auth):
    r = client.get(f"{API}/profiles/{BUILTIN}", headers=auth)
    assert r.status_code == 200
    keys = {f["metric_name"] for f in r.json()["fields"] if f.get("metric_name")}
    assert set(EVAL_METRICS) <= keys
    f = next(x for x in r.json()["fields"] if x.get("metric_name") == "PCE")
    assert {"key", "entity", "tz_name", "label", "type", "unit", "required", "synonyms", "enabled"} <= set(f)


def test_get_profile_alias_and_404(client, auth):
    assert client.get(f"{API}/profiles/prf_missing", headers=auth).status_code == 404


# ---- POST /profiles ----------------------------------------------------------------------------------------------
def test_create_profile_201_copy_of_base(client, auth):
    r = _create(client, auth)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["profile_id"].startswith("prf_") and p["profile_id"] != BUILTIN
    assert p["builtin"] is False and p["version"] == 1 and p["name"] == "Мой профиль"
    base = client.get(f"{API}/profiles/{BUILTIN}", headers=auth).json()
    assert len(p["fields"]) == len(base["fields"])
    assert p["action_id"] == r.headers["X-Action-Id"]


@pytest.mark.parametrize("body", [
    {"collection": "solar", "base_profile_id": BUILTIN},                        # нет name
    {"name": "", "collection": "solar", "base_profile_id": BUILTIN},
    {"name": "X", "collection": "solar"},                                       # нет base_profile_id
    {"name": "X", "collection": "solar", "base_profile_id": "prf_missing"},
])
def test_create_profile_validation(client, auth, body):
    r = client.post(f"{API}/profiles", json=body, headers=auth)
    assert r.status_code in (400, 404)
    assert r.json()["error"]["code"] in ("validation_error", "not_found")


def test_created_profile_usable_for_corpus(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    r = client.post(f"{API}/corpora", json={"name": "WithProfile", "collection": "solar", "profile_id": pid},
                    headers=auth)
    assert r.status_code == 201 and r.json()["profile_id"] == pid


# ---- PATCH /profiles/{id} ----------------------------------------------------------------------------------
def test_patch_builtin_403_forbidden(client, auth):
    r = client.patch(f"{API}/profiles/{BUILTIN}", json={"name": "hack"}, headers=auth)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


def test_patch_unknown_404(client, auth):
    assert client.patch(f"{API}/profiles/prf_missing", json={"name": "x"}, headers=auth).status_code == 404


def test_patch_rename_bumps_version_with_no_rerun(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    r = client.patch(f"{API}/profiles/{pid}", json={"name": "Переименован"}, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"]["version"] == 2 and body["profile"]["name"] == "Переименован"
    assert body["rerun_hint"] is None
    assert body["action_id"] == r.headers["X-Action-Id"]
    assert client.get(f"{API}/profiles/{pid}", headers=auth).json()["version"] == 2


def test_patch_rule_threshold_only_rerun_recheck(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    r = client.patch(f"{API}/profiles/{pid}", headers=auth,
                     json={"rules": [{"check_name": "jv_consistency", "enabled": True,
                                      "params": {"threshold_pct": 5}}]})
    assert r.status_code == 200
    assert r.json()["profile"]["version"] == 2 and r.json()["rerun_hint"] == "recheck"


def test_patch_units_only_rerun_renormalize(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    prof = client.get(f"{API}/profiles/{pid}", headers=auth).json()
    fields = [dict(f) for f in prof["fields"]]
    pce = next(f for f in fields if f.get("metric_name") == "PCE")
    pce["units"] = {**(pce.get("units") or {}), "permille": 0.1}
    r = client.patch(f"{API}/profiles/{pid}", json={"fields": fields}, headers=auth)
    assert r.status_code == 200 and r.json()["rerun_hint"] == "renormalize"


def test_patch_new_field_rerun_full(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    prof = client.get(f"{API}/profiles/{pid}", headers=auth).json()
    fields = [dict(f) for f in prof["fields"]]
    fields.append({"key": "new_metric", "entity": "result", "tz_name": "metric_value", "metric_name": "T80",
                   "label": "Новый", "type": "number", "unit": "h", "required": False, "synonyms": [],
                   "enabled": True})
    r = client.patch(f"{API}/profiles/{pid}", json={"fields": fields}, headers=auth)
    assert r.status_code == 200 and r.json()["rerun_hint"] == "full"
    assert r.json()["profile"]["version"] == 2


def test_patch_two_times_versions_monotonic(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    client.patch(f"{API}/profiles/{pid}", json={"name": "v2"}, headers=auth)
    r = client.patch(f"{API}/profiles/{pid}", json={"name": "v3"}, headers=auth)
    assert r.json()["profile"]["version"] == 3


@pytest.mark.parametrize("body", [{"name": ""}, {"flags": {"ml_issue_min_confidence": 7}},
                                  {"rules": [{"check_name": "no_such_check", "enabled": True, "params": {}}]}])
def test_patch_validation_400(client, auth, body):
    pid = _create(client, auth).json()["profile_id"]
    r = client.patch(f"{API}/profiles/{pid}", json=body, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"


def test_patch_flags_do_not_require_rerun(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    r = client.patch(f"{API}/profiles/{pid}", json={"flags": {"export_absent": True}}, headers=auth)
    assert r.status_code == 200 and r.json()["profile"]["flags"]["export_absent"] is True


# ---- GET /passport ---------------------------------------------------------------------------------------------
def test_passport_json_form(client, auth):
    r = client.get(f"{API}/passport?collection=solar", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    p = r.json()
    assert p["collection"] == "solar" and p["version"] and p["scope"]
    names = {t["name"] for t in p["tables"]}
    assert names == {"publications", "materials", "devices", "layers", "measurements", "results", "issues"}
    results = next(t for t in p["tables"] if t["name"] == "results")
    assert results["key"] == "result_id"
    mn = next(f for f in results["fields"] if f["name"] == "metric_name")
    assert set(EVAL_METRICS) <= set(mn["values"])                  # все 11 метрик ТЗ (У-19)
    assert {"quantity", "unit"} <= set(p["units"][0])
    assert p["statuses"]["extraction_status"] == ["extracted", "present_not_extractable", "absent"]
    assert p["rules"] and {"id", "title", "level", "computed_by"} <= set(p["rules"][0])
    assert p["comparability"]


def test_passport_json_default_format_equals_explicit(client, auth):
    a = client.get(f"{API}/passport?collection=solar", headers=auth).json()
    b = client.get(f"{API}/passport?collection=solar&format=json", headers=auth).json()
    assert a == b


def test_passport_md(client, auth):
    r = client.get(f"{API}/passport?collection=solar&format=md", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown") and "utf-8" in r.headers["content-type"].lower()
    text = r.text
    assert text.lstrip().startswith("#") and "results" in text and "metric_name" in text
    for m in EVAL_METRICS:
        assert m in text


def test_passport_by_profile_id(client, auth):
    pid = _create(client, auth).json()["profile_id"]
    r = client.get(f"{API}/passport?collection=solar&profile_id={pid}", headers=auth)
    assert r.status_code == 200 and r.json()["collection"] == "solar"


def test_passport_unknown_profile_404(client, auth):
    assert client.get(f"{API}/passport?profile_id=prf_missing", headers=auth).status_code == 404


def test_passport_xlsx_is_p2_stub_not_500(client, auth):
    r = client.get(f"{API}/passport?collection=solar&format=xlsx", headers=auth)
    assert r.status_code in (200, 400) and r.status_code < 500


def test_passport_bad_format_400(client, auth):
    r = client.get(f"{API}/passport?format=docx", headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"


def test_passport_requires_auth(client):
    assert client.get(f"{API}/passport").status_code == 401


def test_profiles_require_auth(client):
    assert client.get(f"{API}/profiles").status_code == 401
    assert client.post(f"{API}/profiles", json={}).status_code == 401
