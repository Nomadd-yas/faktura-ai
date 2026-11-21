"""v2 (BACKEND_ONLY §9): правки значений, состояния замечаний, журнал и отмена, влияние на выгрузку."""
from __future__ import annotations

import csv
import io
import zipfile

from .conftest import API, upload


def _run(client, auth, pdfs) -> str:
    r = upload(client, auth, [pdfs["D001"]])
    assert r.status_code == 202, r.text
    return r.json()["id"]


def _paper(client, auth, run):
    r = client.get(f"{API}/papers/solar/D001", params={"run": run}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _zip_table(client, auth, run, table):
    r = client.get(f"{API}/runs/{run}/submission.zip", headers=auth)
    assert r.status_code == 200, r.text
    z = zipfile.ZipFile(io.BytesIO(r.content))
    name = next(n for n in z.namelist() if n.endswith(f"{table}.csv"))
    return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))


def _review(client, auth, run, rid, **body):
    return client.patch(f"{API}/runs/{run}/papers/solar/D001/results/{rid}/review", json=body, headers=auth)


def test_correct_value_goes_to_export_and_undo(client, auth, pdfs):
    run = _run(client, auth, pdfs)
    rid = _paper(client, auth, run)["results"][0]["result_id"]
    r = _review(client, auth, run, rid, status="accepted", corrected_value="12.5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review"]["corrected_value"] == "12.5" and body["review"]["status"] == "accepted"
    assert r.headers["X-Action-Id"] == body["action_id"]
    assert "opened" in body["issues_changed"] and "closed" in body["issues_changed"]
    row = next(x for x in _zip_table(client, auth, run, "results") if x["result_id"] == rid)
    assert row["normalized_value"] == "12.5" and row["metric_value"] == "12.5"
    assert "Исправлено вручную: было" in row["note"]
    # отмена последнего действия разбора возвращает значение из статьи
    u = client.post(f"{API}/history/undo", params={"run": run}, headers=auth)
    assert u.status_code == 200, u.text
    row = next(x for x in _zip_table(client, auth, run, "results") if x["result_id"] == rid)
    assert "Исправлено вручную" not in row["note"]
    h = client.get(f"{API}/history", params={"run": run}, headers=auth).json()["data"]
    assert h and h[0]["undone"] is True


def test_reject_autofixes_issue_and_blocks_reopen(client, auth, pdfs):
    run = _run(client, auth, pdfs)
    paper = _paper(client, auth, run)
    iss = next(i for i in paper["issues"] if i.get("record_ids"))
    rids = [x for x in iss["record_ids"].split(";") if x]
    for rid in rids:
        r = _review(client, auth, run, rid, status="rejected")
        assert r.status_code == 200, r.text
    st = client.get(f"{API}/runs/{run}/papers/solar/D001/review", headers=auth).json()
    mine = next(i for i in st["issues"] if i["issue_id"] == iss["issue_id"])
    assert mine["state"] == "fixed" and mine["auto"] is True
    r = client.patch(f"{API}/runs/{run}/papers/solar/D001/issues/{iss['issue_id']}", json={"state": "open"},
                     headers=auth)
    assert r.status_code == 409 and r.json()["error"]["code"] == "issue_auto_fixed"
    # отклонённые строки не выгружаются и пропадают из record_ids
    res = {x["result_id"] for x in _zip_table(client, auth, run, "results")}
    assert not set(rids) & res
    for i in _zip_table(client, auth, run, "issues"):
        assert not set(rids) & set(i["record_ids"].split(";"))


def test_issue_ok_not_exported_and_stats(client, auth, pdfs):
    run = _run(client, auth, pdfs)
    iss = _paper(client, auth, run)["issues"][0]
    before = client.get(f"{API}/issues/stats", params={"run": run}, headers=auth).json()["data"]
    total = sum(x["count"] for x in before)
    r = client.patch(f"{API}/runs/{run}/papers/solar/D001/issues/{iss['issue_id']}", json={"state": "ok"},
                     headers=auth)
    assert r.status_code == 200, r.text
    assert iss["issue_id"] not in {i["issue_id"] for i in _zip_table(client, auth, run, "issues")}
    after = client.get(f"{API}/issues/stats", params={"run": run}, headers=auth).json()["data"]
    assert sum(x["count"] for x in after) == total - 1


def test_batch_reset_and_errors(client, auth, pdfs):
    run = _run(client, auth, pdfs)
    rows = _paper(client, auth, run)["results"][:3]
    items = [{"domain": "solar", "pid": "D001", "rid": x["result_id"], "status": "rejected"} for x in rows]
    r = client.post(f"{API}/runs/{run}/reviews/batch", json={"items": items}, headers=auth)
    assert r.status_code == 200 and r.json()["count"] == 3
    assert len(_zip_table(client, auth, run, "results")) == len(_paper(client, auth, run)["results"]) - 3
    r = client.delete(f"{API}/runs/{run}/reviews", headers=auth)
    assert r.status_code == 200 and r.json()["count"] == 3
    assert len(_zip_table(client, auth, run, "results")) == len(_paper(client, auth, run)["results"])
    assert _review(client, auth, run, "R999", status="accepted").status_code == 404
    assert _review(client, auth, run, rows[0]["result_id"], status="accepted",
                   corrected_value="abc").status_code == 400
    assert client.post(f"{API}/runs/{run}/reviews/batch", json={"items": []}, headers=auth).status_code == 400


def test_duplicate_promotion_and_redirect():
    from faktura.review.runs_v2 import _effective_results
    rows = [{"result_id": "R1", "duplicate_of": "", "normalized_value": "1", "note": ""},
            {"result_id": "R2", "duplicate_of": "R1", "normalized_value": "1", "note": ""},
            {"result_id": "R3", "duplicate_of": "R1", "normalized_value": "1", "note": ""}]
    out = _effective_results(rows, {"R1": {"status": "rejected", "corrected_value": None}})
    assert [(r["result_id"], r["duplicate_of"]) for r in out] == [("R2", ""), ("R3", "R2")]
