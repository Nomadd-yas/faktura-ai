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


def test_add_files_archive_and_rejects(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(pdfs["D002"], "D002.pdf")
        z.writestr("readme.txt", "x")
    r = _add(client, auth, run, archive=buf.getvalue())
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["counts"]["files"] == 2
    assert [x["code"] for x in body["rejected"]] == ["unsupported_file"]


def test_add_files_errors(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    assert client.post(f"{API}/runs/{run}/files", headers=auth).status_code == 400
    r = _add(client, auth, run, [pdfs["D001"]])  # только дубликат
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_files"
    assert r.json()["error"]["details"]["rejected"][0]["code"] == "duplicate"
    assert _add(client, auth, "run_nope", [pdfs["D002"]]).status_code == 404


def test_add_files_to_running_run(client, auth, pdfs, services):
    """Идущий разбор: файл дописывается в тот же прогон (без нового)."""
    from faktura.domain.entities import RunStatus

    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    cid = "crp_" + run[4:]
    with services.db.uow() as uow:
        rid = uow.runs.list(cid, limit=1)[0].id
        uow.runs.update(rid, status=RunStatus.running)
    r = _add(client, auth, run, [pdfs["D002"]])
    assert r.status_code == 202, r.text
    with services.db.uow() as uow:
        runs = uow.runs.list(cid, limit=5)
        assert len(runs) == 1 and len(runs[0].file_ids) == 2
    assert {f["id"]: f["status"] for f in r.json()["files"]}["D002"] == "queued"


# ---- счётчики --------------------------------------------------------------------------------------
def _first_issue(client, auth, run):
    paper = _get(client, auth, "/papers/solar/D001", run=run)
    iss = next(i for i in paper["issues"] if i.get("record_ids"))
    return paper, iss


def test_open_issue_counters(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"], pdfs["D002"]]).json()["id"]
    base = _get(client, auth, f"/runs/{run}")
    f1 = next(f for f in base["files"] if f["id"] == "D001")
    assert f1["open_issue_count"] == f1["issues"] and base["issues_open"] == base["issues"]
    assert _get(client, auth, f"/runs/{run}/summary")["issues_open"] == base["issues"]
    short = _get(client, auth, "/runs", files="true")["data"][0]["files"][0]
    assert "open_issue_count" in short
    _, iss = _first_issue(client, auth, run)
    r = client.patch(f"{API}/runs/{run}/papers/solar/D001/issues/{iss['issue_id']}", json={"state": "ok"},
                     headers=auth)
    assert r.status_code == 200, r.text
    now = _get(client, auth, f"/runs/{run}")
    f1 = next(f for f in now["files"] if f["id"] == "D001")
    assert f1["open_issue_count"] == f1["issues"] - 1
    assert now["issues_open"] == base["issues_open"] - 1
    client.patch(f"{API}/runs/{run}/papers/solar/D001/issues/{iss['issue_id']}", json={"state": "open"},
                 headers=auth)
    assert _get(client, auth, f"/runs/{run}")["issues_open"] == base["issues_open"]


def test_open_counter_after_autofix(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    _, iss = _first_issue(client, auth, run)
    total = _get(client, auth, f"/runs/{run}")["issues_open"]
    for rid in [x for x in iss["record_ids"].split(";") if x]:
        _review(client, auth, run, "D001", rid, status="rejected")
    after = _get(client, auth, f"/runs/{run}")
    assert after["issues_open"] < total
    assert after["files"][0]["open_issue_count"] == after["issues_open"]


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


def test_paper_includes_extra_recheck_issues(client, auth, pdfs, services):
    from faktura.storage import review_tables as RT

    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    paper = _get(client, auth, "/papers/solar/D001", run=run)
    cid = "crp_" + run[4:]
    rid = paper["results"][0]["result_id"]
    data = {"publication_id": "D001", "issue_id": "VABC", "record_ids": rid, "check_name": "range",
            "description": "после правки"}
    with services.db.uow() as uow:
        uow.conn.execute(RT.run_extra_issues.insert().values(corpus_id=cid, domain="solar",
                                                             publication_id="D001", issue_id="VABC", data=data))
    p = _get(client, auth, "/papers/solar/D001", run=run)
    assert any(i["issue_id"] == "VABC" for i in p["issues"])
    assert "VABC" in p["meta"]["results"][rid]["issue_ids"]
    f = _get(client, auth, f"/runs/{run}")["files"][0]
    assert f["open_issue_count"] == f["issues"] + 1


def test_lists_have_review_and_state(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"]]).json()["id"]
    paper, iss = _first_issue(client, auth, run)
    rid = paper["results"][0]["result_id"]
    _review(client, auth, run, "D001", rid, status="rejected")
    rows = _get(client, auth, "/results", run=run)["data"]
    by = {x["row"]["result_id"]: x for x in rows}
    assert by[rid]["review"]["status"] == "rejected"
    other = next(x for k, x in by.items() if k != rid)
    assert other["review"] == {"status": "new", "corrected_value": None, "at": None, "who": None}
    client.patch(f"{API}/runs/{run}/papers/solar/D001/issues/{iss['issue_id']}", json={"state": "ok"},
                 headers=auth)
    items = _get(client, auth, "/issues", run=run)["data"]
    st = {x["row"]["issue_id"]: x["state"] for x in items}
    assert st[iss["issue_id"]] == "ok" and set(st.values()) <= {"open", "fixed", "ok"}


def test_rag_paper_and_lists_have_v2_fields(client, auth, pdfs):
    r = upload(client, auth, [pdfs["D001"]], domain="rag")
    assert r.status_code == 202, r.text
    run = r.json()["id"]
    p = _get(client, auth, "/papers/rag/D001", run=run)
    assert p["reviews"] == {} and p["issue_states"] == {}
    data = _get(client, auth, "/results", run=run, limit=5)["data"]
    assert all("review" in x for x in data)
    for x in _get(client, auth, "/issues", run=run)["data"]:
        assert x["state"] == "open"


# ---- «лучшее значение» -----------------------------------------------------------------------------
def _eligible(row) -> bool:
    return (row["result_origin"] == "primary" and row["extraction_status"] == "extracted"
            and not row["duplicate_of"])


def test_best_values(client, auth, pdfs):
    run = upload(client, auth, [pdfs["D001"], pdfs["D002"]]).json()["id"]
    best = _get(client, auth, f"/runs/{run}/summary")["best_values"]
    assert "PCE" in best
    b = best["PCE"]
    assert set(b) == {"pid", "result_id", "value", "unit", "aggregation", "corrected"}
    rows = [x["row"] for x in _get(client, auth, "/results", run=run, metric="PCE", limit=1000)["data"]]
    cand = [r for r in rows if _eligible(r) and r["normalized_value"]]
    pool = [r for r in cand if r["aggregation"] == "champion"] or cand
    assert b["value"] == max(float(r["normalized_value"]) for r in pool)
    _review(client, auth, run, b["pid"], b["result_id"], status="accepted", corrected_value="99")
    nb = _get(client, auth, f"/runs/{run}/summary")["best_values"]["PCE"]
    assert nb["result_id"] == b["result_id"] and nb["value"] == 99 and nb["corrected"] is True
    _review(client, auth, run, b["pid"], b["result_id"], status="rejected")
    nb = _get(client, auth, f"/runs/{run}/summary")["best_values"].get("PCE")
    assert nb is None or nb["result_id"] != b["result_id"]


def test_best_value_rules_unit(monkeypatch):
    import faktura.review.runs_v2 as V

    def row(rid, m, v, agg, **kw):
        return {"publication_id": "P", "result_id": rid, "metric_name": m, "normalized_value": v,
                "normalized_unit": "%", "aggregation": agg, "result_origin": "primary",
                "extraction_status": "extracted", "duplicate_of": "", "note": "", **kw}

    class Sub:
        def results_of_metrics(self, corpus_id, metrics, domain="solar"):
            return {"p1": [row("R1", "PCE", "20", "mean"), row("R2", "PCE", "18", "champion"),
                           row("R3", "PCE", "25", "champion", result_origin="cited"),
                           row("R4", "Rs", "5", "mean"), row("R5", "Rs", "2", "mean"),
                           row("R6", "Rs", "1", "mean", duplicate_of="R5"),
                           row("R7", "HI", "0.3", "mean"), row("R8", "HI", "0.1", "mean")]}

    monkeypatch.setattr(V, "reviews_of", lambda *a, **k: {})
    b = V.best_values(Sub(), None, "c", "solar", ["PCE", "Rs", "HI", "FF"], {"p1": "D1"})
    assert b["PCE"]["result_id"] == "R2"
    assert b["Rs"]["result_id"] == "R5" and b["HI"]["result_id"] == "R8"
    assert "FF" not in b and b["PCE"]["pid"] == "D1"
