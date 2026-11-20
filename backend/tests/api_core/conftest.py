"""Фикстуры api_core: приложение на SQLite, мок-экстрактор (dev_gold), синхронный исполнитель, 3 PDF из dev."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API = "/api/v1"


@pytest.fixture
def services(settings, case_dir):
    from faktura.pipeline import InlineExecutor, InMemoryEventBus, build_services
    from faktura.storage import seed_admin

    s = settings.model_copy(update={"mock_gold_dir": case_dir / "dev_gold", "extractor": "mock"})
    sv = build_services(s, executor=InlineExecutor(), bus=InMemoryEventBus())
    sv.db.create_all()
    seed_admin(sv.db, s)
    return sv


@pytest.fixture
def app(services):
    from faktura.api.base import create_app

    return create_app(services.settings, services)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture
def auth(client, settings) -> dict[str, str]:
    r = client.post(f"{API}/auth/login", json={"email": settings.admin_email, "password": settings.admin_password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def pdfs(tmp_path: Path, dev_dir: Path) -> dict[str, Path]:
    d = tmp_path / "pdfs"
    d.mkdir()
    out = {}
    for pid in ("D001", "D002", "D006"):
        shutil.copy(dev_dir / f"{pid}.pdf", d / f"{pid}.pdf")
        out[pid] = d / f"{pid}.pdf"
    return out


def upload(client, auth, paths, **form):
    files = [("file", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    return client.post(f"{API}/runs", files=files, data=form, headers=auth)


@pytest.fixture
def run_two(client, auth, pdfs) -> dict:
    r = upload(client, auth, [pdfs["D001"], pdfs["D002"]])
    assert r.status_code == 202, r.text
    return r.json()
