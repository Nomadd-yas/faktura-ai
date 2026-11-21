"""v2 (остальное): POST /runs/{id}/files, счётчики открытых замечаний, v2-поля статьи и списков, «лучшее значение»."""
from __future__ import annotations

import io
import zipfile

from .conftest import API, upload

_RV = "{api}/runs/{run}/papers/solar/{pid}/results/{rid}/review"


def _get(client, auth, path, **params):
    r = client.get(f"{API}{path}", params=params, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _review(client, auth, run, pid, rid, **body):
    r = client.patch(_RV.format(api=API, run=run, pid=pid, rid=rid), json=body, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def _add(client, auth, run, paths=(), archive=None):
    files = [("file", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    if archive is not None:
        files.append(("archive", ("more.zip", archive, "application/zip")))
    return client.post(f"{API}/runs/{run}/files", files=files, headers=auth)


# ---- POST /runs/{id}/files ------------------------------------------------------------------------
def test_add_files_to_finished_run(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    r = _add(client, auth, run, [pdfs["D002"], pdfs["D001"]])
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["id"] == run and body["counts"]["files"] == 2
    assert [x["code"] for x in body["rejected"]] == ["duplicate"]
    full = _get(client, auth, f"/runs/{run}")
    assert full["status"] == "done" and {f["id"] for f in full["files"]} == {"D001", "D002"}
    assert all(f["status"] in ("done", "nodata", "out") for f in full["files"])








# ---- счётчики --------------------------------------------------------------------------------------
def _first_issue(client, auth, run):
    paper = _get(client, auth, "/papers/solar/D001", run=run)
    iss = next(i for i in paper["issues"] if i.get("record_ids"))
    return paper, iss






# ---- статья и списки -------------------------------------------------------------------------------
def test_paper_v2_fields(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    paper, iss = _first_issue(client, auth, run)
    assert paper["reviews"] == {} and paper["issue_states"] == {}
    rid = paper["results"][0]["result_id"]
    _review(client, auth, run, "D001", rid, status="accepted", corrected_value="12.5")
    for r in [x for x in iss["record_ids"].split(";") if x]:
        _review(client, auth, run, "D001", r, status="rejected")
    p = _get(client, auth, "/papers/solar/D001", run=run)
    assert p["reviews"][rid]["status"] in ("accepted", "rejected")
    assert set(p["reviews"][rid]) == {"status", "corrected_value", "at", "who"}
    assert p["issue_states"][iss["issue_id"]] == {"state": "fixed", "auto": True}
    assert "state" not in p["issues"][0] and "status" not in p["results"][0]
    assert p["results"][0]["normalized_value"] == paper["results"][0]["normalized_value"]








# ---- «лучшее значение» -----------------------------------------------------------------------------
def _eligible(row) -> bool:
    return (row["result_origin"] == "primary" and row["extraction_status"] == "extracted"
            and not row["duplicate_of"])




