"""Фикстуры тестов модуля api_corpus: TestClient + SQLite + мок ML-1 + InlineExecutor.

Тесты падают на заглушках (NotImplementedError из create_app / build_services), а не на ошибках импорта.
Первоисточник: docs/wave1/api_contract.md §4.4-4.6, §4.12-4.13; spec/13 §13.2-13.8.
"""
from __future__ import annotations

import io
import sys
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

API = "/api/v1"
CSV_NAMES = ["publications.csv", "materials.csv", "devices.csv", "layers.csv", "measurements.csv",
             "results.csv", "issues.csv"]
ACTION_HEADER = "X-Action-Id"


class GatedExecutor:
    """Исполнитель, удерживающий задачи до gate.set() (проверка конфликта и отмены активного прогона).

    После open() работает синхронно, как InlineExecutor. wait_idle() ждёт завершения фоновых потоков.
    """

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.threads: list[threading.Thread] = []
        self.submitted = 0

    def submit(self, fn: Callable[[], None]) -> None:
        self.submitted += 1

        def _job() -> None:
            self.gate.wait(30)
            fn()

        t = threading.Thread(target=_job, daemon=True)
        self.threads.append(t)
        t.start()

    def open(self) -> None:
        self.gate.set()

    def wait_idle(self, timeout: float = 60) -> None:
        for t in self.threads:
            t.join(timeout)


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def make_app(settings):
    """Фабрика (app, services): make_app(executor=..., **settings_overrides)."""
    from faktura.api.base.app import create_app
    from faktura.pipeline import InlineExecutor, build_services

    def _make(executor: Any = None, service_overrides: dict[str, Any] | None = None, **settings_over: Any):
        s = settings.model_copy(update=settings_over) if settings_over else settings
        overrides = dict(service_overrides or {})
        overrides.setdefault("executor", executor or InlineExecutor())
        services = build_services(s, **overrides)
        return create_app(s, services), services

    return _make


def _login(client, settings) -> dict[str, str]:
    r = client.post(f"{API}/auth/login", json={"email": settings.admin_email, "password": settings.admin_password,
                                               "remember": False})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def make_client(make_app, settings):
    """make_client(**kw) -> (client, services, headers)."""
    from fastapi.testclient import TestClient

    def _make(**kw: Any):
        app, services = make_app(**kw)
        client = TestClient(app)
        return client, services, _login(client, settings)

    return _make


@pytest.fixture
def ctx(make_client):
    """Клиент с InlineExecutor: (client, services, headers)."""
    return make_client()


@pytest.fixture
def client(ctx):
    return ctx[0]


@pytest.fixture
def services(ctx):
    return ctx[1]


@pytest.fixture
def auth(ctx) -> dict[str, str]:
    return ctx[2]


@pytest.fixture
def mkcorpus(ctx):
    """mkcorpus(name='C') -> dict корпуса (через POST /corpora)."""
    client, _, headers = ctx
    counter = {"n": 0}

    def _make(name: str | None = None, **extra: Any) -> dict:
        counter["n"] += 1
        body = {"name": name or f"Корпус {counter['n']}", "collection": "solar", **extra}
        r = client.post(f"{API}/corpora", json=body, headers=headers)
        assert r.status_code == 201, r.text
        return r.json()

    return _make


@pytest.fixture
def dev_pdf_paths(dev_dir: Path) -> list[Path]:
    pdfs = sorted(dev_dir.glob("*.pdf"))
    assert len(pdfs) >= 3
    return pdfs


@pytest.fixture
def dev_zip3(dev_pdf_paths: list[Path]) -> bytes:
    """ZIP с тремя PDF dev (реальные файлы кейса)."""
    return make_zip({p.name: p.read_bytes() for p in dev_pdf_paths[:3]})


@pytest.fixture
def fake_pdf_bytes(make_pdf) -> bytes:
    return make_pdf(["Synthetic page"], name="synthetic.pdf").read_bytes()


def write_validator(path: Path, *, ok: bool) -> Path:
    """Поддельный check_submission.py: ok -> код 0; иначе печатает ошибки и выходит с 1."""
    body = ("import sys\nprint('ERROR: results.csv row 2: bad metric_name')\nprint('ERROR: issues.csv row 3: x')\n"
            "sys.exit(1)\n") if not ok else "print('OK: no errors')\n"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def fake_validator(tmp_path: Path):
    def _make(ok: bool) -> Path:
        return write_validator(tmp_path / ("ok_check.py" if ok else "bad_check.py"), ok=ok)

    return _make


def upload_zip(client, headers, corpus_id: str, data: bytes, name: str = "a.zip"):
    return client.post(f"{API}/corpora/{corpus_id}/files", headers=headers,
                       files={"archive": (name, data, "application/zip")})


def start_run(client, headers, corpus_id: str, kind: str = "full", **extra: Any):
    return client.post(f"{API}/corpora/{corpus_id}/runs", headers=headers, json={"kind": kind, **extra})


def wait_run(client, headers, run_id: str, timeout: float = 60, until=("done", "done_with_errors", "failed",
                                                                          "cancelled")) -> dict:
    import time

    end = time.time() + timeout
    while True:
        r = client.get(f"{API}/runs/{run_id}", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        if body["status"] in until or time.time() > end:
            return body
        time.sleep(0.2)


def zip_names(data: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


@pytest.fixture
def python_exe() -> str:
    return sys.executable


class _Helpers:
    API = API
    CSV_NAMES = CSV_NAMES
    GatedExecutor = GatedExecutor
    make_zip = staticmethod(make_zip)
    upload_zip = staticmethod(upload_zip)
    start_run = staticmethod(start_run)
    wait_run = staticmethod(wait_run)
    zip_names = staticmethod(zip_names)


@pytest.fixture
def h() -> type[_Helpers]:
    """Хелперы (в importlib-режиме conftest не импортируется напрямую)."""
    return _Helpers
