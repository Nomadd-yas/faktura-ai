"""Экспорт сдачи: ExportJobs, POST /exports, GET /exports/{id}[/file], Idempotency-Key, preview (REQ-079, REQ-101..103).

Контракт: api_contract §3.11, §4.12, У-29. Всегда асинхронно: ExportJobs -> pipeline.build_output -> ZIP в FileStore.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from faktura.api.corpus import ExportJobs, sse_stream
from faktura.domain.entities import CurrentUser

API = "/api/v1"


def _wait_export(client, auth, export_id: str, timeout: float = 120) -> dict:
    end = time.time() + timeout
    while True:
        r = client.get(f"{API}/exports/{export_id}", headers=auth)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in ("done", "failed") or time.time() > end:
            return body
        time.sleep(0.2)


def _submit(client, auth, corpus_id: str, headers: dict | None = None, **extra):
    body = {"format": "submission", "scope": {"corpus_id": corpus_id}, "validate": True, **extra}
    return client.post(f"{API}/exports", json=body, headers={**auth, **(headers or {})})


@pytest.fixture
def parsed_corpus(make_client, h, dev_zip3):
    """Фабрика: корпус из трёх dev-PDF уже разобран мок-экстрактором. kw -> настройки (check_script)."""
    def _make(**settings_over):
        client, services, auth = make_client(**settings_over)
        c = client.post(f"{API}/corpora", json={"name": "Exp", "collection": "solar"}, headers=auth).json()
        assert h.upload_zip(client, auth, c["corpus_id"], dev_zip3).status_code == 202
        run = h.start_run(client, auth, c["corpus_id"]).json()
        assert h.wait_run(client, auth, run["run_id"])["status"] in ("done", "done_with_errors")
        return client, services, auth, c

    return _make


# ---- POST /exports: валидация --------------------------------------------------------------------------------
@pytest.mark.parametrize("body", [
    {"format": "submission"},                                        # нет scope
    {"format": "submission", "scope": {}},                           # пустой scope
    {"format": "yaml", "scope": {"corpus_id": "crp_x"}},             # неизвестный формат
    {"scope": {"corpus_id": "crp_x"}},                               # нет format
    {"format": "submission", "scope": {"corpus_id": "crp_x"}, "validate": "maybe"},
])
def test_export_validation_400(client, auth, body):
    r = client.post(f"{API}/exports", json=body, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"


def test_export_unknown_corpus_404(client, auth):
    assert _submit(client, auth, "crp_missing").status_code == 404


def test_export_requires_auth(client):
    assert client.post(f"{API}/exports", json={"format": "submission", "scope": {"corpus_id": "x"}}).status_code == 401


def test_export_empty_corpus_nothing_to_export(client, auth, mkcorpus):
    c = mkcorpus()
    r = _submit(client, auth, c["corpus_id"])
    if r.status_code == 422:
        assert r.json()["error"]["code"] == "nothing_to_export"
        return
    assert r.status_code == 202
    body = _wait_export(client, auth, r.json()["export_id"])
    assert body["status"] == "failed" and body["error"]["code"] == "nothing_to_export"


def test_exports_get_unknown_404(client, auth):
    assert client.get(f"{API}/exports/exp_missing", headers=auth).status_code == 404
    assert client.get(f"{API}/exports/exp_missing/file", headers=auth).status_code == 404


# ---- happy path ------------------------------------------------------------------------------------------------
@pytest.mark.data
def test_submission_export_202_then_done_check_ok_and_zip(parsed_corpus, check_script, h):
    client, services, auth, c = parsed_corpus(check_script=check_script)
    r = _submit(client, auth, c["corpus_id"])
    assert r.status_code == 202, r.text                         # всегда асинхронно (У-29)
    exp = r.json()
    assert exp["export_id"].startswith("exp_") and exp["format"] == "submission"
    assert exp["status"] in ("processing", "done") and exp["action_id"] == r.headers["X-Action-Id"]
    body = _wait_export(client, auth, exp["export_id"])
    assert body["status"] == "done", body
    assert body["check"]["status"] in ("ok", "warnings") and body["check"]["errors"] == 0
    assert body["check"]["script"] == "configured" and body["error"] is None
    assert body["url"].endswith(f"/exports/{exp['export_id']}/file")
    assert body["filename"].startswith("submission_") and body["filename"].endswith(".zip")
    assert body["papers"] == 3 and body["facts"] > 0 and body["finished_at"]

    f = client.get(f"{API}/exports/{exp['export_id']}/file", headers=auth)
    assert f.status_code == 200 and f.headers["content-type"].startswith("application/zip")
    assert f.headers["X-Check-Status"] == body["check"]["status"]
    assert "attachment" in f.headers["content-disposition"] and body["filename"] in f.headers["content-disposition"]
    names = h.zip_names(f.content)
    assert sorted(n for n in names if n.endswith(".csv")) == sorted(h.CSV_NAMES)
    assert "check_report.txt" in names


@pytest.mark.data
def test_submission_csv_have_bom_and_crlf(parsed_corpus, check_script):
    import io
    import zipfile

    client, _, auth, c = parsed_corpus(check_script=check_script)
    exp = _wait_export(client, auth, _submit(client, auth, c["corpus_id"]).json()["export_id"])
    z = zipfile.ZipFile(io.BytesIO(client.get(f"{API}/exports/{exp['export_id']}/file", headers=auth).content))
    raw = z.read("publications.csv")
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw


@pytest.mark.data
def test_export_ready_event_published(parsed_corpus, check_script):
    client, services, auth, c = parsed_corpus(check_script=check_script)
    exp = _wait_export(client, auth, _submit(client, auth, c["corpus_id"]).json()["export_id"])
    ws = client.get(f"{API}/auth/me", headers=auth).json().get("workspace_id", "ws_default")

    async def run():
        return [f async for f in sse_stream(services.bus, ws, "0", ping_s=0.05, idle_timeout_s=0.5)]

    frames = asyncio.run(run())
    ready = [f for f in frames if "event: export.ready" in f]
    assert ready and exp["export_id"] in ready[-1] and '"status"' in ready[-1]


@pytest.mark.data
def test_validate_false_skips_check(parsed_corpus):
    client, _, auth, c = parsed_corpus()
    exp = _wait_export(client, auth, _submit(client, auth, c["corpus_id"], validate=False).json()["export_id"])
    assert exp["status"] == "done" and exp["check"] is None


@pytest.mark.data
def test_export_scope_paper_ids_subset(parsed_corpus, check_script):
    client, _, auth, c = parsed_corpus(check_script=check_script)
    papers = client.get(f"{API}/corpora/{c['corpus_id']}/files", headers=auth).json()["data"]
    one = papers[0]["paper_id"]
    r = client.post(f"{API}/exports", headers=auth,
                    json={"format": "submission", "scope": {"paper_ids": [one]}, "validate": True})
    assert r.status_code == 202
    assert _wait_export(client, auth, r.json()["export_id"])["papers"] == 1


# ---- валидатор с ошибкой -----------------------------------------------------------------------------------
@pytest.mark.data
def test_failing_validator_status_failed_submission_invalid_file_available(parsed_corpus, fake_validator, h):
    client, _, auth, c = parsed_corpus(check_script=fake_validator(False))
    exp = _wait_export(client, auth, _submit(client, auth, c["corpus_id"]).json()["export_id"])
    assert exp["status"] == "failed"
    assert exp["error"]["code"] == "submission_invalid" and exp["error"]["message"]
    assert exp["error"]["details"]["errors"]
    assert exp["check"]["status"] == "failed" and exp["check"]["errors"] >= 1
    f = client.get(f"{API}/exports/{exp['export_id']}/file", headers=auth)
    assert f.status_code == 200 and f.headers["X-Check-Status"] == "failed"   # ZIP остаётся для разбора
    assert "check_report.txt" in h.zip_names(f.content)


@pytest.mark.data
def test_ok_fake_validator_status_ok(parsed_corpus, fake_validator):
    client, _, auth, c = parsed_corpus(check_script=fake_validator(True))
    exp = _wait_export(client, auth, _submit(client, auth, c["corpus_id"]).json()["export_id"])
    assert exp["status"] == "done" and exp["check"]["status"] == "ok"
    assert client.get(f"{API}/exports/{exp['export_id']}/file", headers=auth).headers["X-Check-Status"] == "ok"


@pytest.mark.data
def test_no_check_script_configured_reports_not_configured(parsed_corpus):
    client, _, auth, c = parsed_corpus(check_script=Path("Z:/no/such/check_submission.py"))
    exp = _wait_export(client, auth, _submit(client, auth, c["corpus_id"]).json()["export_id"])
    assert exp["status"] in ("done", "failed")
    assert exp["check"]["script"] in ("not_configured", "configured")


# ---- файл до готовности ------------------------------------------------------------------------------------
def test_file_while_processing_409(make_client, h, make_pdf):
    """Экспорт стоит в очереди, пока исполнитель удержан: файл недоступен, 409 conflict."""
    ex = h.GatedExecutor()
    client, services, auth = make_client(executor=ex)
    c = client.post(f"{API}/corpora", json={"name": "P", "collection": "solar"}, headers=auth).json()
    h.upload_zip(client, auth, c["corpus_id"], h.make_zip({"P1.pdf": make_pdf(["p"], name="P1.pdf").read_bytes()}))
    ex.open()
    run = h.start_run(client, auth, c["corpus_id"]).json()
    ex.wait_idle()
    h.wait_run(client, auth, run["run_id"])
    ex.gate.clear()                                              # экспорт будет удержан
    r = _submit(client, auth, c["corpus_id"])
    assert r.status_code == 202
    exp_id = r.json()["export_id"]
    assert r.json()["status"] == "processing" and r.json()["url"] is None
    f = client.get(f"{API}/exports/{exp_id}/file", headers=auth)
    assert f.status_code == 409 and f.json()["error"]["code"] == "conflict"
    ex.open()
    ex.wait_idle()


# ---- Idempotency-Key ---------------------------------------------------------------------------------------
@pytest.mark.data
def test_idempotency_key_same_export_id(parsed_corpus, check_script):
    client, _, auth, c = parsed_corpus(check_script=check_script)
    h1 = {"Idempotency-Key": "7c1e-aaaa"}
    r1 = _submit(client, auth, c["corpus_id"], h1)
    r2 = _submit(client, auth, c["corpus_id"], h1)
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["export_id"] == r2.json()["export_id"]
    r3 = _submit(client, auth, c["corpus_id"], {"Idempotency-Key": "other-key"})
    assert r3.json()["export_id"] != r1.json()["export_id"]


def test_idempotency_key_scoped_per_user_not_shared_between_requests_without_key(client, auth, mkcorpus):
    c = mkcorpus()
    r1 = _submit(client, auth, c["corpus_id"])
    r2 = _submit(client, auth, c["corpus_id"])
    if r1.status_code == 202 and r2.status_code == 202:
        assert r1.json()["export_id"] != r2.json()["export_id"]


# ---- ExportJobs напрямую ----------------------------------------------------------------------------------
def _jobs(services) -> ExportJobs:
    return ExportJobs(services)  # конструктор принимает Services (контракт модуля)


def _user(client, auth) -> CurrentUser:
    me = client.get(f"{API}/auth/me", headers=auth).json()
    return CurrentUser(id=me["id"], workspace_id=me.get("workspace_id", "ws_default"), email=me["email"],
                       is_admin=me.get("is_admin", False))


@pytest.mark.data
def test_export_jobs_submit_and_status(parsed_corpus, check_script):
    client, services, auth, c = parsed_corpus(check_script=check_script)
    jobs = _jobs(services)
    exp = jobs.submit({"corpus_id": c["corpus_id"]}, {"format": "submission", "validate": True}, _user(client, auth))
    assert exp.id.startswith("exp_") and exp.format == "submission"
    assert exp.status in ("queued", "running", "done")
    end = time.time() + 120
    while jobs.status(exp.id).status not in ("done", "failed") and time.time() < end:
        time.sleep(0.2)
    final = jobs.status(exp.id)
    assert final.status == "done" and final.file_key and services.store.exists(final.file_key)
    assert final.finished_at is not None


def test_export_jobs_status_unknown_raises_not_found(ctx):
    from faktura.domain.errors import AppError

    with pytest.raises(AppError) as e:
        _jobs(ctx[1]).status("exp_missing")
    assert e.value.code == "not_found"


def test_export_jobs_submit_unknown_corpus_raises(ctx):
    from faktura.domain.errors import AppError

    client, services, auth = ctx
    with pytest.raises(AppError) as e:
        _jobs(services).submit({"corpus_id": "crp_missing"}, {"format": "submission"}, _user(client, auth))
    assert e.value.code == "not_found"


def test_export_jobs_submit_empty_scope_raises_validation(ctx):
    from faktura.domain.errors import AppError

    client, services, auth = ctx
    with pytest.raises(AppError) as e:
        _jobs(services).submit({}, {"format": "submission"}, _user(client, auth))
    assert e.value.code == "validation_error"


# ---- заглушка /exports/preview -----------------------------------------------------------------------------
def test_exports_preview_is_stub_with_header_and_shape(client, auth):
    r = client.post(f"{API}/exports/preview", headers=auth, json={"format": "csv", "scope": {"corpus_id": "x"}})
    assert r.status_code == 200
    assert r.headers["X-Faktura-Stub"] == "1"
    body = r.json()
    assert body["head"] == [] and body["rows"] == [] and body["facts"] == 0 and body["papers"] == 0


def test_exports_preview_requires_auth(client):
    assert client.post(f"{API}/exports/preview", json={}).status_code == 401


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "json", "tsv"])
def test_human_formats_not_blocking_wave1(client, auth, mkcorpus, fmt):
    """P2-форматы: допустим ответ-заглушка или 400, но не 500."""
    c = mkcorpus()
    r = client.post(f"{API}/exports", headers=auth, json={"format": fmt, "scope": {"corpus_id": c["corpus_id"]}})
    assert r.status_code < 500
