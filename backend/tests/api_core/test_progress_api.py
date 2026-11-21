"""Прогресс и ETA в /runs (CORE_API.md §4b): поля, конечные статусы, монотонность, страницы, ETA очереди."""
from __future__ import annotations

from datetime import timedelta

from faktura.domain.entities import CorpusFileStatus, RunStatus

from .conftest import API

FILE_FIELDS = {"progress", "eta_seconds", "pages", "started_at"}


def test_final_run_has_100_and_null_eta(client, auth, run_two, services):
    assert {"progress", "eta_seconds", "started_at"} <= set(run_two)  # ответ POST /runs
    got = client.get(f"{API}/runs/{run_two['id']}", headers=auth).json()
    assert got["status"] == "done"
    assert got["progress"] == 100 and got["eta_seconds"] is None and got["started_at"]
    for f in got["files"]:
        assert FILE_FIELDS <= set(f)
        assert f["progress"] == 100 and f["eta_seconds"] is None
        assert isinstance(f["pages"], int) and f["pages"] > 0 and f["started_at"]
    row = client.get(f"{API}/runs", headers=auth).json()["data"][0]
    assert row["progress"] == 100 and row["eta_seconds"] is None
    assert all(FILE_FIELDS <= set(f) for f in row["files"])
    # скорости этапов записаны для области (мок - не из кэша)
    with services.db.uow() as uow:
        assert "Извлечение" in uow.speeds.averages("solar") and uow.speeds.averages("rag") == {}


def test_running_run_progress_eta_and_monotonic(client, auth, run_two, services):
    cid = "crp_" + run_two["id"][4:]
    with services.db.uow() as uow:
        run = uow.runs.list(cid, limit=1)[0]
        uow.runs.update(run.id, status=RunStatus.running)
        a, b = uow.files.list(cid)[:2]
        now = uow.now()
        uow.files.update_status(a.id, CorpusFileStatus.processing, stage="Извлечение",
                                started_at=now - timedelta(seconds=30), stage_started_at=now - timedelta(seconds=30))
        uow.files.update_status(b.id, CorpusFileStatus.queued, stage=None, started_at=None, stage_started_at=None)
    seen = []
    for _ in range(3):
        got = client.get(f"{API}/runs/{run_two['id']}", headers=auth).json()
        assert got["status"] == "running" and got["started_at"]
        by = {f["id"]: f for f in got["files"]}
        seen.append((got["progress"], by["D001"]["progress"]))
    f1, f2 = by["D001"], by["D002"]
    assert f1["status"] == "running" and f1["stage"] == "Извлечение" and 0 < f1["progress"] < 70
    assert f2["status"] == "queued" and f2["progress"] == 0
    assert f1["eta_seconds"] and f1["eta_seconds"] % 5 == 0
    assert f2["eta_seconds"] and f2["eta_seconds"] % 5 == 0  # очередь: с ожиданием
    assert got["eta_seconds"] and got["eta_seconds"] % 5 == 0 and 0 < got["progress"] < 100
    assert all(b2[0] >= b1[0] and b2[1] >= b1[1] for b1, b2 in zip(seen, seen[1:]))


def test_eta_null_when_pages_unknown(client, auth, run_two, services):
    cid = "crp_" + run_two["id"][4:]
    with services.db.uow() as uow:
        run = uow.runs.list(cid, limit=1)[0]
        uow.runs.update(run.id, status=RunStatus.running)
        for f in uow.files.list(cid):
            uow.files.update_status(f.id, CorpusFileStatus.queued, page_count=None)
    got = client.get(f"{API}/runs/{run_two['id']}", headers=auth).json()
    assert got["eta_seconds"] is None and all(f["eta_seconds"] is None and f["pages"] is None for f in got["files"])
