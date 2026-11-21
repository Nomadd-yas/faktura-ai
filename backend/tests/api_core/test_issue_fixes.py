"""Исправления у замечаний: движок предложений и эндпоинты fix / fix/apply (v2)."""
from __future__ import annotations

from faktura.review import fixes as F

from .conftest import API, upload


def _r(rid, metric, v, unit="", agg=None, meas="M1"):
    return {"result_id": rid, "metric_name": metric, "normalized_value": str(v), "normalized_unit": unit,
            "aggregation": agg, "measurement_id": meas}


def test_suggest_rules():
    # jv_consistency: PCE 19.0 против Voc·Jsc·FF/Pin = 1.1·22·78/100 = 18.9 - согласуется; здесь FF сдвинут запятой
    rows = [_r("p", "PCE", 18.9, "%"), _r("v", "Voc", 1.1, "V"), _r("j", "Jsc", 22, "mA/cm2"), _r("f", "FF", 780, "%")]
    s = F.suggest("solar", {"check_name": "jv_consistency", "record_ids": "p;v;j;f"}, rows)
    top = s["suggestions"][0]
    assert top["rid"] == "f" and abs(top["suggested"] - 78.1) < 0.2 and top["confidence"] == "high"
    assert s["alternatives"] and all(a["items"][0]["method"] == "rule" for a in s["alternatives"])
    # FF как доля, мВ вместо В
    u = F.suggest("solar", {"check_name": "unit_inconsistency", "record_ids": "a;b"},
                  [_r("a", "FF", 0.71, "%"), _r("b", "Voc", 1050, "V")])
    assert {i["rid"]: i["suggested"] for i in u["suggestions"]} == {"a": 71.0, "b": 1.05}
    # champion < mean -> обмен значений; derived -> reject; ambiguity -> resolution ok; неизвестная -> пусто
    c = F.suggest("solar", {"check_name": "champion_vs_mean", "record_ids": "c;m"},
                  [_r("c", "PCE", 18, "%", "champion"), _r("m", "PCE", 20, "%", "mean")])
    assert {i["rid"]: i["suggested"] for i in c["suggestions"]} == {"c": 20.0, "m": 18.0}
    d = F.suggest("solar", {"check_name": "derived_values", "record_ids": "x"}, [_r("x", "PCE", 2, "%")])
    assert d["suggestions"][0]["action"] == "reject"
    a = F.suggest("solar", {"check_name": "ambiguity", "record_ids": "x"}, [_r("x", "PCE", 2, "%")])
    assert not a["suggestions"] and a["resolution"]["issue_state"] == "ok"
    assert F.suggest("solar", {"check_name": "nope", "record_ids": "x"}, []) == {
        "suggestions": [], "alternatives": [], "note": None, "resolution": None}
    m = F.suggest("rag", {"check_name": "metric_scale", "record_ids": "r"}, [_r("r", "Recall", 85)])
    assert m["suggestions"][0]["suggested"] == 0.85


def test_fix_endpoints_apply_and_undo(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    base = f"{API}/runs/{run}/papers/solar/D001"
    rev = client.get(f"{base}/review", headers=auth).json()
    assert all("fix_available" in i for i in rev["issues"])
    paper = client.get(f"{API}/papers/solar/D001", params={"run": run}, headers=auth).json()
    iss = next(i for i in paper["issues"] if i.get("record_ids"))
    iid, rid = iss["issue_id"], iss["record_ids"].split(";")[0]
    f = client.get(f"{base}/issues/{iid}/fix", headers=auth)
    assert f.status_code == 200, f.text
    assert f.json()["issue_id"] == iid and "suggestions" in f.json() and "alternatives" in f.json()
    assert client.get(f"{base}/issues/NOPE/fix", headers=auth).status_code == 404
    bad = client.post(f"{base}/issues/{iid}/fix/apply", json={"items": [{"rid": "ZZZ", "suggested": 1}]},
                      headers=auth)
    assert bad.status_code == 400 and bad.json()["error"]["field"] == "items"
    ok = client.post(f"{base}/issues/{iid}/fix/apply", json={"items": [{"rid": rid, "suggested": 12.5}]},
                     headers=auth)
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["reviews"][rid]["corrected_value"] == "12.5"
    assert next(i for i in body["issues"] if i["issue_id"] == iid)["state"] == "fixed"
    u = client.post(f"{API}/actions/{body['action_id']}/undo", headers=auth)
    assert u.status_code == 200, u.text
    after = client.get(f"{base}/review", headers=auth).json()
    assert rid not in after["reviews"]
    assert next(i for i in after["issues"] if i["issue_id"] == iid)["state"] == "open"
    allf = client.get(f"{API}/runs/{run}/fixes", params={"domain": "solar", "pid": "D001"}, headers=auth)
    assert allf.status_code == 200 and isinstance(allf.json()["data"], list)
