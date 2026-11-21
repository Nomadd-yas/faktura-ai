"""Новое от фронта (BACKEND_ONLY §7а): rerun-as, PATCH/DELETE/restart/stop, PDF и статья для out, паспорт."""
from __future__ import annotations

import io
import json
import threading
import zipfile

from .conftest import API, upload


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text
    assert r.json()["error"]["message"]


def mk(client, auth, paths, **form):
    r = upload(client, auth, paths, **form)
    assert r.status_code == 202, r.text
    return r.json()


def _cid(rid: str) -> str:
    return "crp_" + rid[4:]


def _set_run_status(services, rid, status):
    with services.db.uow() as uow:
        run = uow.runs.list(_cid(rid), limit=1)[0]
        uow.runs.update(run.id, status=status, finished_at=None)


# --- PDF и статья для out -----------------------------------------------------------------------

def test_pdf_and_paper_for_out_file(client, auth, pdfs):
    rid = mk(client, auth, [pdfs["D001"], pdfs["D006"]])["id"]
    r = client.get(f"{API}/papers/solar/D006/pdf", params={"run": rid}, headers=auth)
    assert r.status_code == 200 and r.content == pdfs["D006"].read_bytes()
    assert r.headers["content-type"].startswith("application/pdf")
    p = client.get(f"{API}/papers/solar/D006", params={"run": rid}, headers=auth)
    assert p.status_code == 200, p.text
    j = p.json()
    assert j["paper_status"] == "out" and j["meta"]["file_status"] == "out"
    assert j["publication"]["in_scope"] == "false" and j["publication"]["exclusion_reason"]
    for k in ("materials", "devices", "layers", "measurements", "results", "issues"):
        assert j[k] == []
    err(client.get(f"{API}/papers/solar/D999/pdf", params={"run": rid}, headers=auth), 404, "not_found")


def test_pdf_of_error_and_nodata_files(client, auth, pdfs, services):
    """PDF отдаётся для любого принятого файла, в том числе упавшего."""
    from faktura.domain.entities import CorpusFileStatus

    rid = mk(client, auth, [pdfs["D001"]])["id"]
    with services.db.uow() as uow:
        f = uow.files.list(_cid(rid))[0]
        uow.files.update_status(f.id, CorpusFileStatus.failed, error_code="ml_error", error_message="сбой")
    got = client.get(f"{API}/runs/{rid}", headers=auth).json()
    assert got["files"][0]["status"] == "error"
    r = client.get(f"{API}/papers/solar/D001/pdf", params={"run": rid}, headers=auth)
    assert r.status_code == 200 and r.content == pdfs["D001"].read_bytes()


def test_paper_for_all_frontend_statuses(client, auth, pdfs):
    rid = mk(client, auth, [pdfs["D001"], pdfs["D006"]])["id"]
    got = client.get(f"{API}/runs/{rid}", headers=auth).json()
    seen = set()
    for f in got["files"]:
        assert f["status"] in ("done", "out", "nodata", "ocr")
        r = client.get(f"{API}/papers/solar/{f['id']}", params={"run": rid}, headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["paper_status"] == {"done": "has_data", "out": "out", "nodata": "nodata",
                                            "ocr": "has_data"}[f["status"]]
        seen.add(f["status"])
    assert seen == {"done", "out"}


def test_paper_nodata_and_ocr_rag(client, auth, pdfs, services):
    """nodata: 200, paper_status nodata, пустые таблицы; rag ocr: has_data (есть результаты, файл - скан)."""
    from faktura.domain.entities import CorpusFileStatus  # noqa: F401

    rid = mk(client, auth, [pdfs["D001"]], domain="rag")["id"]
    got = client.get(f"{API}/runs/{rid}", headers=auth).json()
    f = got["files"][0]
    r = client.get(f"{API}/papers/rag/{f['id']}", params={"run": rid}, headers=auth)
    assert r.status_code == 200
    assert r.json()["paper_status"] == {"done": "has_data", "ocr": "has_data", "nodata": "nodata",
                                        "out": "out"}[f["status"]]
    assert client.get(f"{API}/papers/rag/{f['id']}/pdf", params={"run": rid}, headers=auth).status_code == 200


def test_suggested_domain_and_pinned_fields(client, auth, pdfs):
    run = mk(client, auth, [pdfs["D001"]])
    got = client.get(f"{API}/runs/{run['id']}", headers=auth).json()
    assert got["files"][0]["suggested_domain"] is None
    assert got["pinned"] is False and run["pinned"] is False


# --- rerun-as -----------------------------------------------------------------------------------

def test_rerun_as_rag(client, auth, pdfs):
    src = mk(client, auth, [pdfs["D001"], pdfs["D006"]], title="Мои статьи")
    r = client.post(f"{API}/runs/{src['id']}/rerun-as", json={"domain": "rag", "file_ids": ["D006"]}, headers=auth)
    assert r.status_code == 202, r.text
    new = r.json()
    assert new["id"] != src["id"] and new["domain"] == "rag" and new["title"] == "Мои статьи · RAG"
    assert r.headers["location"].endswith(new["id"])
    got = client.get(f"{API}/runs/{new['id']}", headers=auth).json()
    assert [f["name"] for f in got["files"]] == ["D006.pdf"] and got["counts"]["files"] == 1
    again = client.get(f"{API}/runs/{src['id']}", headers=auth).json()  # исходный разбор не изменился
    assert again["domain"] == "solar" and again["counts"]["files"] == 2
    p = client.get(f"{API}/papers/rag/D006/pdf", params={"run": new["id"]}, headers=auth)
    assert p.status_code == 200 and p.content == pdfs["D006"].read_bytes()


def test_rerun_as_title_and_solar_suffix(client, auth, pdfs):
    src = mk(client, auth, [pdfs["D001"], pdfs["D002"]], domain="rag", title="RAG-набор")
    ok = client.post(f"{API}/runs/{src['id']}/rerun-as", json={"domain": "solar", "file_ids": ["D001"]}, headers=auth)
    assert ok.status_code == 202 and ok.json()["title"] == "RAG-набор · солнечные"
    ok = client.post(f"{API}/runs/{src['id']}/rerun-as",
                     json={"domain": "solar", "file_ids": ["D001", "D002"], "title": "Свой"}, headers=auth)
    assert ok.status_code == 202 and ok.json()["title"] == "Свой" and ok.json()["counts"]["files"] == 2


def test_rerun_as_errors(client, auth, pdfs):
    src = mk(client, auth, [pdfs["D001"], pdfs["D006"]])
    url = f"{API}/runs/{src['id']}/rerun-as"
    err(client.post(f"{API}/runs/run_nope/rerun-as", json={"domain": "rag", "file_ids": ["D001"]}, headers=auth),
        404, "not_found")
    err(client.post(url, json={"domain": "rag", "file_ids": ["D404"]}, headers=auth), 404, "not_found")
    err(client.post(url, json={"domain": "chem", "file_ids": ["D001"]}, headers=auth), 422, "domain_not_supported")
    err(client.post(url, json={"domain": "rag", "file_ids": []}, headers=auth), 422, "validation_error")
    err(client.post(url, json={"domain": "solar", "file_ids": ["D001"]}, headers=auth), 422, "validation_error")
    err(client.post(url, json={"file_ids": ["D001"]}, headers=auth), 422, "validation_error")
    assert client.post(url, json={"domain": "rag", "file_ids": ["D001"]}).status_code == 401


def test_rerun_as_in_progress(client, auth, pdfs, services):
    from faktura.domain.entities import RunStatus

    src = mk(client, auth, [pdfs["D001"]])
    _set_run_status(services, src["id"], RunStatus.running)
    err(client.post(f"{API}/runs/{src['id']}/rerun-as", json={"domain": "rag", "file_ids": ["D001"]}, headers=auth),
        409, "run_in_progress")


# --- PATCH / DELETE -----------------------------------------------------------------------------

def test_patch_pin_rename_and_order(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"]], title="A")
    b = mk(client, auth, [pdfs["D002"]], title="B")

    def ids():
        return [r["id"] for r in client.get(f"{API}/runs", headers=auth).json()["data"]]

    assert ids() == [b["id"], a["id"]]
    r = client.patch(f"{API}/runs/{a['id']}", json={"pinned": True}, headers=auth)
    assert r.status_code == 200 and r.json()["pinned"] is True and r.json()["title"] == "A"
    assert ids() == [a["id"], b["id"]]
    r = client.patch(f"{API}/runs/{a['id']}", json={"title": "  Новое  "}, headers=auth)
    assert r.json()["title"] == "Новое" and r.json()["pinned"] is True and "files" in r.json()
    assert client.get(f"{API}/runs/{a['id']}", headers=auth).json()["title"] == "Новое"
    assert client.patch(f"{API}/runs/{a['id']}", json={"pinned": False}, headers=auth).json()["pinned"] is False
    assert ids() == [b["id"], a["id"]]


def test_patch_errors(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"]])
    err(client.patch(f"{API}/runs/run_nope", json={"pinned": True}, headers=auth), 404, "not_found")
    err(client.patch(f"{API}/runs/{a['id']}", json={"title": "  "}, headers=auth), 422, "validation_error")
    err(client.patch(f"{API}/runs/{a['id']}", json={}, headers=auth), 422, "validation_error")
    err(client.patch(f"{API}/runs/{a['id']}", json={"pinned": "maybe"}, headers=auth), 400, "validation_error")
    assert client.patch(f"{API}/runs/{a['id']}", json={"pinned": True}).status_code == 401


def test_delete_soft(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"]])
    b = mk(client, auth, [pdfs["D002"]])
    assert client.delete(f"{API}/runs/{a['id']}", headers=auth).status_code == 204
    assert [r["id"] for r in client.get(f"{API}/runs", headers=auth).json()["data"]] == [b["id"]]
    for path in ("", "/summary", "/submission.zip", "/passport.json"):
        err(client.get(f"{API}/runs/{a['id']}{path}", headers=auth), 404, "not_found")
    err(client.delete(f"{API}/runs/{a['id']}", headers=auth), 404, "not_found")
    err(client.delete(f"{API}/runs/run_nope", headers=auth), 404, "not_found")
    err(client.get(f"{API}/papers/solar/D001", params={"run": a["id"]}, headers=auth), 404, "not_found")


def test_delete_running_run_stops_it(client, auth, pdfs, services):
    from faktura.domain.entities import CorpusFileStatus, RunStatus

    a = mk(client, auth, [pdfs["D001"]])
    with services.db.uow() as uow:
        uow.files.update_status(uow.files.list(_cid(a["id"]))[0].id, CorpusFileStatus.processing)
    _set_run_status(services, a["id"], RunStatus.running)
    assert client.delete(f"{API}/runs/{a['id']}", headers=auth).status_code == 204
    with services.db.uow() as uow:
        assert uow.runs.list(_cid(a["id"]), limit=1)[0].status == RunStatus.cancelled


# --- restart / stop -----------------------------------------------------------------------------

def test_restart_all_and_selected(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"], pdfs["D002"]])
    r = client.post(f"{API}/runs/{a['id']}/restart", headers=auth)
    assert r.status_code == 202, r.text
    assert r.json()["id"] == a["id"] and r.json()["counts"]["files"] == 2
    got = client.get(f"{API}/runs/{a['id']}", headers=auth).json()
    assert got["status"] == "done" and got["counts"]["done"] == 2
    r = client.post(f"{API}/runs/{a['id']}/restart", json={"file_ids": ["D002"]}, headers=auth)
    assert r.status_code == 202
    got = client.get(f"{API}/runs/{a['id']}", headers=auth).json()
    assert got["counts"]["files"] == 2 and got["counts"]["done"] == 2
    assert client.post(f"{API}/runs/{a['id']}/restart", json={}, headers=auth).status_code == 202


def test_restart_errors(client, auth, pdfs, services):
    from faktura.domain.entities import RunStatus

    a = mk(client, auth, [pdfs["D001"]])
    err(client.post(f"{API}/runs/run_nope/restart", headers=auth), 404, "not_found")
    err(client.post(f"{API}/runs/{a['id']}/restart", json={"file_ids": ["D404"]}, headers=auth), 404, "not_found")
    err(client.post(f"{API}/runs/{a['id']}/restart", json={"file_ids": []}, headers=auth), 422, "validation_error")
    _set_run_status(services, a["id"], RunStatus.running)
    err(client.post(f"{API}/runs/{a['id']}/restart", headers=auth), 409, "run_in_progress")


def test_stop_running_run(client, auth, pdfs, services):
    """Идущий разбор: незаконченные файлы -> error «остановлено»; готовые остаются; повторный stop безопасен."""
    from faktura.domain.entities import CorpusFileStatus, RunStatus

    a = mk(client, auth, [pdfs["D001"], pdfs["D002"]])
    with services.db.uow() as uow:  # D002 «ещё идёт»
        files = {f.publication_id: f for f in uow.files.list(_cid(a["id"]))}
        uow.files.update_status(files["D002"].id, CorpusFileStatus.processing)
    _set_run_status(services, a["id"], RunStatus.running)
    r = client.post(f"{API}/runs/{a['id']}/stop", headers=auth)
    assert r.status_code == 200, r.text
    by = {f["id"]: f for f in r.json()["files"]}
    assert by["D001"]["status"] == "done"
    assert by["D002"]["status"] == "error" and by["D002"]["reason"] == "остановлено"
    assert r.json()["status"] == "done" and r.json()["counts"]["error"] == 1
    assert client.post(f"{API}/runs/{a['id']}/stop", headers=auth).status_code == 200
    r = client.post(f"{API}/runs/{a['id']}/restart", json={"file_ids": ["D002"]}, headers=auth)  # только остановленный
    assert r.status_code == 202
    got = client.get(f"{API}/runs/{a['id']}", headers=auth).json()
    assert {f["status"] for f in got["files"]} == {"done"}


def test_stop_aborts_inflight_worker(client, auth, pdfs, services):
    """stop взводит abort-флаг выполняющегося файла; статус «остановлено» остаётся."""
    from faktura.domain.entities import CorpusFileStatus, RunStatus
    from faktura.pipeline import RunService
    from faktura.pipeline import runs as R

    a = mk(client, auth, [pdfs["D001"]])
    with services.db.uow() as uow:
        f = uow.files.list(_cid(a["id"]))[0]
        uow.files.update_status(f.id, CorpusFileStatus.processing)
    _set_run_status(services, a["id"], RunStatus.running)
    ev = threading.Event()
    R._ABORTS[f.id] = ev
    with services.db.uow() as uow:
        run_id = uow.runs.list(_cid(a["id"]), limit=1)[0].id
    RunService(services).stop(run_id)
    assert ev.is_set() and f.id not in R._ABORTS
    with services.db.uow() as uow:
        cur = uow.files.get(f.id)
    assert cur.status == CorpusFileStatus.failed and cur.error_message == "остановлено"


def test_worker_skips_stopped_file(client, auth, pdfs, services):
    """Исполнитель не берёт остановленный файл и не затирает его статус."""
    from faktura.domain.entities import CorpusFileStatus, RunStatus
    from faktura.pipeline import RunService

    a = mk(client, auth, [pdfs["D001"]])
    with services.db.uow() as uow:
        f = uow.files.list(_cid(a["id"]))[0]
        run = uow.runs.list(_cid(a["id"]), limit=1)[0]
        uow.files.update_status(f.id, CorpusFileStatus.queued)
        uow.runs.update(run.id, status=RunStatus.queued, finished_at=None)
    rs = RunService(services)
    rs.stop(run.id)
    rs.execute(run.id)  # прогон уже cancelled: ничего не делает
    with services.db.uow() as uow:
        cur = uow.files.get(f.id)
        assert uow.runs.get(run.id).status == RunStatus.cancelled
    assert cur.status == CorpusFileStatus.failed and cur.error_message == "остановлено"


def test_stop_queued_run_and_errors(client, auth, pdfs, services):
    from faktura.domain.entities import CorpusFileStatus, RunStatus

    a = mk(client, auth, [pdfs["D001"]])
    with services.db.uow() as uow:
        uow.files.update_status(uow.files.list(_cid(a["id"]))[0].id, CorpusFileStatus.queued)
    _set_run_status(services, a["id"], RunStatus.queued)
    r = client.post(f"{API}/runs/{a['id']}/stop", headers=auth)
    assert r.status_code == 200 and r.json()["files"][0]["reason"] == "остановлено"
    assert r.json()["status"] == "error"  # все файлы в error
    err(client.post(f"{API}/runs/run_nope/stop", headers=auth), 404, "not_found")
    assert client.post(f"{API}/runs/{a['id']}/stop").status_code == 401


def test_other_users_run_is_404(client, auth, pdfs, services):
    a = mk(client, auth, [pdfs["D001"]])
    with services.db.uow() as uow:
        uow.conn.exec_driver_sql("UPDATE corpora SET created_by = 'usr_other'")
    for method, path, kw in [("patch", "", {"json": {"pinned": True}}), ("delete", "", {}),
                             ("post", "/restart", {}), ("post", "/stop", {}),
                             ("post", "/rerun-as", {"json": {"domain": "rag", "file_ids": ["D001"]}}),
                             ("get", "/passport.json", {}), ("get", "/passport.md", {})]:
        err(getattr(client, method)(f"{API}/runs/{a['id']}{path}", headers=auth, **kw), 404, "not_found")


# --- паспорт ------------------------------------------------------------------------------------

def test_passport_json_equals_zip(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"], pdfs["D006"]])
    z = client.get(f"{API}/runs/{a['id']}/submission.zip", headers=auth)
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    pj = client.get(f"{API}/runs/{a['id']}/passport.json", headers=auth)
    assert pj.status_code == 200 and pj.headers["content-type"].startswith("application/json")
    assert pj.content == zf.read("passport.json")
    pm = client.get(f"{API}/runs/{a['id']}/passport.md", headers=auth)
    assert pm.status_code == 200 and pm.content == zf.read("PASSPORT.md")
    p = pj.json()
    assert p["title"] == "Паспорт данных «Фактура AI»" and p["version"] == "1" and p["format"]
    assert set(p["versions"]) == {"model", "rules", "dictionary"} and p["versions"]["rules"]
    assert [d["id"] for d in p["domains"]] == ["solar"]
    d = p["domains"][0]
    assert [g["id"] for g in d["guides"]] == ["inclusion", "comparability", "extraction"]
    assert [e["id"] for e in d["entities"]] == ["publications", "materials", "devices", "layers", "measurements",
                                               "results", "issues"]
    assert d["rules"] and d["normalization"]
    err(client.get(f"{API}/runs/run_nope/passport.json", headers=auth), 404, "not_found")
    assert client.get(f"{API}/runs/{a['id']}/passport.json").status_code == 401


def test_passport_rag_domain(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"]], domain="rag")
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/runs/{a['id']}/submission.zip", headers=auth).content))
    pj = client.get(f"{API}/runs/{a['id']}/passport.json", headers=auth)
    assert pj.content == z.read("passport.json")
    p = json.loads(pj.content)
    assert [d["id"] for d in p["domains"]] == ["rag"]
    assert [e["id"] for e in p["domains"][0]["entities"]][1] == "configurations"
    assert p["versions"]["rules"] and p["versions"]["dictionary"]
