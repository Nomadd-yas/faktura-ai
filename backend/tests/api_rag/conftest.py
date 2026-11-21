"""Фикстуры api_rag: приложение с мок-экстрактором rag (data/caseA_drive/dev_gold), синхронный исполнитель."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API = "/api/v1"
RAG_DIR = Path(__file__).resolve().parents[3] / "data" / "caseA_drive"
SMALL = ("D020", "D004", "D007")
TABLES = ("publications", "configurations", "datasets", "tasks", "experiments", "results", "issues")


@pytest.fixture(scope="session")
def rag_dir() -> Path:
    if not (RAG_DIR / "dev_gold" / "publications.csv").is_file():
        pytest.skip("нет данных кейса rag (data/caseA_drive)")
    return RAG_DIR


@pytest.fixture(scope="session")
def gold(rag_dir):
    """{таблица: {publication_id: [строки]}} эталона."""
    out: dict[str, dict[str, list[dict]]] = {}
    csv.field_size_limit(1 << 28)
    for t in TABLES:
        with (rag_dir / "dev_gold" / f"{t}.csv").open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                out.setdefault(t, {}).setdefault(r["publication_id"], []).append(r)
    return out


@pytest.fixture
def services(settings, rag_dir):
    from faktura.pipeline import InlineExecutor, InMemoryEventBus, build_services
    from faktura.storage import seed_admin

    s = settings.model_copy(update={"rag_case_dir": rag_dir, "rag_mock_gold_dir": rag_dir / "dev_gold",
                                    "rag_check_script": rag_dir / "tools" / "check_submission.py",
                                    "extractor": "mock"})
    sv = build_services(s, executor=InlineExecutor(), bus=InMemoryEventBus())
    sv.db.create_all()
    seed_admin(sv.db, s)
    return sv


@pytest.fixture
def client(services) -> TestClient:
    from faktura.api.base import create_app

    return TestClient(create_app(services.settings, services))


@pytest.fixture
def auth(client, settings) -> dict[str, str]:
    r = client.post(f"{API}/auth/login", json={"email": settings.admin_email, "password": settings.admin_password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def pdfs(tmp_path: Path, rag_dir: Path) -> dict[str, Path]:
    d = tmp_path / "pdfs"
    d.mkdir()
    out = {}
    for pid in SMALL:
        shutil.copy(rag_dir / "dev" / f"{pid}.pdf", d / f"{pid}.pdf")
        out[pid] = d / f"{pid}.pdf"
    return out


def upload(client, auth, paths, **form):
    files = [("file", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    return client.post(f"{API}/runs", files=files, data={"domain": "rag", **form}, headers=auth)


@pytest.fixture
def rag_run(client, auth, pdfs) -> dict:
    r = upload(client, auth, list(pdfs.values()))
    assert r.status_code == 202, r.text
    return r.json()
