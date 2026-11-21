"""WP4: stage/progress файла и статус разбора в GET /runs/{id} (spec §4.1, §4.2)."""
from __future__ import annotations

from faktura.domain.entities import CorpusFileStatus, RunStatus

from .conftest import API, upload


def _corpus_and_run(services, run_id):
    with services.db.uow() as uow:
        cid = "crp_" + run_id.removeprefix("run_")
        return cid, uow.runs.list(cid, limit=1)[0]


def test_running_file_returns_persisted_stage_and_progress(client, auth, services, pdfs):
    body = upload(client, auth, [pdfs["D001"], pdfs["D002"]]).json()
    cid, run = _corpus_and_run(services, body["id"])
    with services.db.uow() as uow:
        f1, f2 = uow.files.list(cid)[:2]
        uow.files.update_status(f1.id, CorpusFileStatus.processing, stage="Проверка", progress=85)
        uow.files.update_status(f2.id, CorpusFileStatus.queued, stage=None, progress=0)
        uow.runs.update(run.id, status=RunStatus.running)
    r = client.get(f"{API}/runs/{body['id']}", headers=auth).json()
    by = {f["name"]: f for f in r["files"]}
    running = [f for f in r["files"] if f["status"] == "running"][0]
    assert running["stage"] == "Проверка" and running["progress"] == 85
    queued = [f for f in r["files"] if f["status"] == "queued"][0]
    assert queued["stage"] is None and queued["progress"] == 0
    assert r["status"] == "running" and len(by) == 2


def test_run_status_error_only_if_all_failed(client, auth, services, pdfs):
    body = upload(client, auth, [pdfs["D001"], pdfs["D002"]]).json()
    cid, run = _corpus_and_run(services, body["id"])
    with services.db.uow() as uow:
        files = uow.files.list(cid)
        uow.files.update_status(files[0].id, CorpusFileStatus.failed, error_message="Сбой", progress=100)
        uow.runs.update(run.id, status=RunStatus.done_with_errors)
    r = client.get(f"{API}/runs/{body['id']}", headers=auth).json()
    assert r["status"] == "done"  # часть файлов упала, остальные в конечном статусе
    with services.db.uow() as uow:
        for f in uow.files.list(cid):
            uow.files.update_status(f.id, CorpusFileStatus.failed, error_message="Сбой", progress=100)
    r = client.get(f"{API}/runs/{body['id']}", headers=auth).json()
    assert r["status"] == "error" and r["counts"]["error"] == 2
