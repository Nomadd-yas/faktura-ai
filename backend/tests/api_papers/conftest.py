"""Фикстуры тестов модуля api_papers (план 5.9).

Корпус dev обрабатывается моком ML-1 (extractor="mock", эталон dev_gold) через pipeline с InlineExecutor.
Все фикстуры собираются из публичных интерфейсов; пока модули-заглушки не реализованы, фикстуры
падают NotImplementedError (тест падает, а не «ошибка импорта»).

Окружения:
  dev_env      - весь корпус dev (21 PDF), только для чтения, один на сессию; эталон НЕ загружен.
  small_env    - 3 статьи (D001, D004, D021), свежее на каждый тест; для мутаций (удаление, ручные факты...).
  acc_env      - весь корпус dev, свежий на модуль; для тестов точности и /admin/gold.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

API = "/api/v1"
SMALL_SET = ("D001", "D004", "D021")  # годы 2021, 2024, 2024; 47 / 31 / 122 записей эталона


def url(path: str) -> str:
    return f"{API}{path}"


@dataclass
class Env:
    settings: Any
    services: Any
    app: Any
    client: Any            # TestClient с заголовком Authorization админа
    anon: Any              # TestClient без токена
    admin: Any             # User админа
    corpus_id: str
    papers: dict[str, str] = field(default_factory=dict)  # publication_id -> paper_id (pap_...)

    def uow(self):
        return self.services.db.uow()

    def login(self, email: str, password: str):
        """Новый TestClient с токеном пользователя."""
        from fastapi.testclient import TestClient

        c = TestClient(self.app)
        r = c.post(url("/auth/login"), json={"email": email, "password": password, "remember": False})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        return c

    def make_user(self, email: str = "reader@example.com", password: str = "reader-pass-1", *, admin: bool = False):
        from faktura.domain.security import hash_password

        with self.uow() as uow:
            uow.users.create(workspace_id=self.admin.workspace_id, email=email, name="Reader",
                             is_admin=admin, password_hash=hash_password(password))
        return self.login(email, password)

    def pid(self, publication_id: str) -> str:
        return self.papers[publication_id]

    def get(self, path: str, **params: Any):
        return self.client.get(url(path), params=params or None)


def build_env(root: Path, case_dir: Path, publication_ids: tuple[str, ...] | None) -> Env:
    from fastapi.testclient import TestClient

    from faktura.api.base import create_app
    from faktura.config import Settings
    from faktura.pdfio.inputs import collect_inputs
    from faktura.pipeline import CorpusService, InlineExecutor, RunService, build_services
    from faktura.storage.db import seed_admin

    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None, case_dir=case_dir, db_url=f"sqlite:///{(root / 'faktura.db').as_posix()}",
        storage_dir=root / "storage", cache_dir=root / "ml_cache", jwt_secret="test-secret-test-secret-test-secret",
        admin_email="admin@example.com", admin_password="admin-password-1", cookie_secure=False,
        extractor="mock",
    )
    services = build_services(settings, executor=InlineExecutor())
    services.db.create_all()
    seed_admin(services.db, settings)
    app = create_app(settings, services)

    with services.db.uow() as uow:
        admin = uow.users.get_by_email("admin@example.com")

    src = root / "dev_in"
    src.mkdir(exist_ok=True)
    for pdf in sorted((case_dir / "dev").glob("*.pdf")):
        if publication_ids is None or pdf.stem in publication_ids:
            shutil.copy(pdf, src / pdf.name)

    corpus = CorpusService(services).create(admin.workspace_id, admin.id, "dev", "solar")
    CorpusService(services).add_inputs(corpus.id, collect_inputs(src), mode="cli")
    runs = RunService(services)
    run = runs.start(corpus.id, "full", user_id=admin.id)
    runs.execute(run.id)

    anon = TestClient(app)
    client = TestClient(app)
    r = client.post(url("/auth/login"), json={"email": "admin@example.com", "password": "admin-password-1",
                                              "remember": False})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    with services.db.uow() as uow:
        papers = {f.publication_id: f.paper_id for f in uow.files.list(corpus.id) if f.paper_id}
    return Env(settings=settings, services=services, app=app, client=client, anon=anon, admin=admin,
               corpus_id=corpus.id, papers=papers)


@pytest.fixture(scope="session")
def dev_env(tmp_path_factory, case_dir) -> Env:
    return build_env(tmp_path_factory.mktemp("papers_dev"), case_dir, None)


@pytest.fixture
def small_env(tmp_path, case_dir) -> Env:
    return build_env(tmp_path / "small", case_dir, SMALL_SET)


@pytest.fixture(scope="module")
def acc_env(tmp_path_factory, case_dir) -> Env:
    return build_env(tmp_path_factory.mktemp("papers_acc"), case_dir, None)


@pytest.fixture
def paper_id(dev_env) -> str:
    """Главная тестовая статья: D001 (2021, 47 записей эталона)."""
    return dev_env.pid("D001")


@pytest.fixture
def normalizer():
    from faktura.norm import LocalNormalizer

    return LocalNormalizer()


@pytest.fixture
def profile():
    from faktura.domain.profile import builtin_solar_profile

    return builtin_solar_profile()


def all_facts(client, paper_id: str) -> list[dict]:
    r = client.get(url(f"/papers/{paper_id}/facts"))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def table_pages(client, limit: int, **params: Any) -> tuple[list[dict], list[dict]]:
    """Проходит /facts/table по курсору до конца. Возвращает (все строки, ответы страниц)."""
    rows: list[dict] = []
    pages: list[dict] = []
    cursor = None
    for _ in range(10_000):
        q = dict(params, limit=limit)
        if cursor:
            q["cursor"] = cursor
        r = client.get(url("/facts/table"), params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        pages.append(body)
        rows += body["data"]
        cursor = body["next_cursor"]
        if not cursor:
            break
    return rows, pages
