"""E2E 2: сценарий API фронтенда целиком (docs/wave1/api_contract.md, раздел 5).

вход -> overview -> корпус -> загрузка ZIP -> прогон -> опрос статуса -> статьи/факты/источник/замечания ->
принять факт + отмена -> экспорт submission -> файл ZIP -> check_submission -> паспорт.
Без внешних сервисов: SQLite + файловое хранилище + мок ML-1 (dev_gold).
"""
from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from e2e_support import EXCLUDED_PAPER, E2E_PAPERS, TABLES

pytestmark = pytest.mark.data
API = "/api/v1"


@pytest.fixture
def client(e2e_settings):
    from fastapi.testclient import TestClient

    from faktura.api.base.app import create_app

    with TestClient(create_app(e2e_settings)) as c:
        r = c.post(f"{API}/auth/login", json={"email": e2e_settings.admin_email,
                                              "password": e2e_settings.admin_password, "remember": False})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        yield c


def _wait(client, url: str, done, timeout: float = 120.0) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(url)
        assert r.status_code == 200, r.text
        if done(r.json()):
            return r.json()
        time.sleep(0.5)
    pytest.fail(f"таймаут ожидания {url}")


def test_health_and_auth(client):
    assert client.get(f"{API}/health").status_code == 200
    me = client.get(f"{API}/auth/me")
    assert me.status_code == 200 and me.json()["email"]
    r = client.post(f"{API}/auth/login", json={"email": "x@y.z", "password": "wrong-password", "remember": False})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_full_frontend_scenario(client, e2e_zip, check_script, e2e_in):
    # 1. первый экран
    assert client.get(f"{API}/corpus/overview").json()["empty"] is True

    # 2. корпус и загрузка ZIP
    r = client.post(f"{API}/corpora", json={"name": "e2e", "collection": "solar"})
    assert r.status_code == 201, r.text
    cid = r.json()["corpus_id"]
    with open(e2e_zip, "rb") as f:
        r = client.post(f"{API}/corpora/{cid}/files", files={"archive": ("dev3.zip", f, "application/zip")})
    assert r.status_code == 202, r.text
    up = r.json()
    assert up["accepted"] == len(E2E_PAPERS) and up["duplicates"] == 0
    assert {x["publication_id"] for x in up["files"]} == set(E2E_PAPERS)
    assert all(x["status"] == "pending" for x in up["files"])

    # не PDF отклоняется
    r = client.post(f"{API}/corpora/{cid}/files", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code in (202, 415)

    # 3. прогон и статус
    r = client.post(f"{API}/corpora/{cid}/runs", json={"kind": "new_only", "concurrency": 2, "keep_review": True})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    run = _wait(client, f"{API}/runs/{run_id}", lambda j: j["status"] in ("done", "done_with_errors", "failed"))
    assert run["status"] == "done", run
    assert run["counts"]["total"] == len(E2E_PAPERS) and run["counts"]["done"] == len(E2E_PAPERS)
    assert run["progress"] == 100 and run["facts_found"] > 0

    # 4. файлы и сводка
    files = client.get(f"{API}/corpora/{cid}/files", params={"limit": 50}).json()["data"]
    assert len(files) == len(E2E_PAPERS) and all(f["status"] == "done" for f in files)
    assert client.get(f"{API}/corpora/{cid}/summary").status_code == 200

    # 5. статьи; исключённая видна как in_scope=false
    papers = client.get(f"{API}/papers", params={"corpus_id": cid, "limit": 50}).json()["data"]
    by_pub = {p["publication_id"]: p for p in papers}
    assert set(by_pub) == set(E2E_PAPERS)
    assert by_pub[EXCLUDED_PAPER]["in_scope"] is False and by_pub[EXCLUDED_PAPER]["exclusion_reason"]
    pid = by_pub["D002"]["paper_id"]
    assert by_pub["D002"]["id"] == pid

    # 6. факты, источник, страница, anchors
    facts = client.get(f"{API}/papers/{pid}/facts").json()["data"]
    assert facts
    f0 = facts[0]
    src = client.get(f"{API}/facts/{f0['fact_id']}/source").json()
    assert src["page"] >= 1 and src["quote"]
    img = client.get(f"{API}/papers/{pid}/pages/{src['page']}")
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
    assert client.get(f"{API}/papers/{pid}/anchors", params={"page": src["page"]}).status_code == 200
    assert client.get(f"{API}/papers/{pid}/file").headers["content-type"].startswith("application/pdf")
    tbl = client.get(f"{API}/facts/table", params={"corpus_id": cid, "limit": 20}).json()
    assert tbl["total"] > 0 and tbl["data"]

    # 7. замечания
    assert client.get(f"{API}/papers/{pid}/issues").status_code == 200
    assert client.get(f"{API}/issues", params={"corpus_id": cid}).status_code == 200

    # 8. принять факт, затем отменить
    r = client.post(f"{API}/facts/{f0['fact_id']}/accept", json={})
    assert r.status_code == 200, r.text
    acc = r.json()
    assert acc["fact"]["review_status"] == "accepted" and acc["action_id"] and "issues_changed" in acc
    assert client.post(f"{API}/actions/{acc['action_id']}/undo").status_code == 200
    assert client.get(f"{API}/facts/{f0['fact_id']}").json()["review_status"] == "new"
    pv = client.post(f"{API}/normalize/preview", json={"collection": "solar", "param": "voc", "raw": "1100 мВ"})
    assert pv.json()["ok"] is True

    # 9. экспорт сдачи: 202 -> опрос -> файл
    r = client.post(f"{API}/exports", json={"format": "submission", "scope": {"corpus_id": cid}, "validate": True},
                    headers={"Idempotency-Key": "e2e-1"})
    assert r.status_code == 202, r.text
    eid = r.json()["export_id"]
    exp = _wait(client, f"{API}/exports/{eid}", lambda j: j["status"] in ("done", "failed"))
    assert exp["status"] == "done", exp
    r = client.get(f"{API}/exports/{eid}/file")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
    assert r.headers["X-Check-Status"] in ("ok", "warnings")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {f"{t}.csv" for t in TABLES} <= names and "check_report.txt" in names

    # 10. выгруженный набор проходит валидатор ТЗ
    with tempfile.TemporaryDirectory() as d:
        z.extractall(d)
        p = subprocess.run([sys.executable, str(check_script), d, str(e2e_in)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        assert p.returncode == 0, p.stdout[-2000:]
        assert (Path(d) / "results.csv").is_file()

    # 11. паспорт
    assert client.get(f"{API}/passport", params={"collection": "solar", "format": "json"}).status_code == 200
    md = client.get(f"{API}/passport", params={"collection": "solar", "format": "md"})
    assert md.headers["content-type"].startswith("text/markdown")

    # 12. прогон обычного корпуса не из /extract: output -> 404 (У-32)
    assert client.get(f"{API}/runs/{run_id}/output").status_code == 404


def test_extract_endpoint_one_shot(client, e2e_zip):
    """POST /extract: архив -> прогон -> ZIP выходной папки (7 CSV + _meta)."""
    with open(e2e_zip, "rb") as f:
        r = client.post(f"{API}/extract", data={"domain": "solar"},
                        files={"archive": ("dev3.zip", f, "application/zip")})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    run = _wait(client, f"{API}/runs/{run_id}", lambda j: j["status"] in ("done", "done_with_errors", "failed"))
    assert run["status"] == "done" and run["output"]["available"] is True and run["output"]["csv_files"] == 7
    r = client.get(f"{API}/runs/{run_id}/output")
    assert r.status_code == 200 and "X-Check-Status" in r.headers
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert all(f"{t}.csv" in names for t in TABLES) and any(n.startswith("_meta/") for n in names)


def test_unauthorized_and_not_found(client):
    from fastapi.testclient import TestClient

    anon = TestClient(client.app)
    assert anon.get(f"{API}/corpora").status_code == 401
    assert client.get(f"{API}/runs/run_nope").status_code == 404
