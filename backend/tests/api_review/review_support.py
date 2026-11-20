"""Помощники тестов модуля api_review (план 5.10). Импорты тяжёлых модулей - внутри функций, чтобы падали тесты,
а не сбор: пока модули-заглушки не реализованы, build_env падает NotImplementedError."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

API = "/api/v1"
PAPERS = ("D001",)  # 47 строк эталона; параметры voc/jsc/ff/pce для jv_consistency

CATALOG_PARAMS = ("voc", "jsc", "ff", "pce")


def url(path: str) -> str:
    return f"{API}{path}"


@dataclass
class Env:
    settings: Any
    services: Any
    app: Any
    client: Any            # TestClient админа
    admin: Any             # User админа
    user: Any              # CurrentUser админа (для ReviewService)
    review: Any            # ReviewService
    corpus_id: str
    papers: dict[str, str] = field(default_factory=dict)  # publication_id -> paper_id

    def uow(self):
        return self.services.db.uow()

    def pid(self, publication_id: str = "D001") -> str:
        return self.papers[publication_id]

    # --- чтение через репозитории (без HTTP) ---
    def facts(self, paper_id: str | None = None) -> list[Any]:
        from faktura.domain.entities import FactFilter

        pid = paper_id or self.pid()
        with self.uow() as uow:
            page = uow.facts.query(FactFilter(paper_ids=[pid]), [], 5000, None)
        return [row.fact for row in page.data]

    def fact(self, fact_id: str) -> Any:
        for f in self.facts():
            if f.id == fact_id:
                return f
        raise AssertionError(f"нет факта {fact_id}")

    def paper(self, paper_id: str | None = None) -> Any:
        with self.uow() as uow:
            return uow.papers.get(paper_id or self.pid())

    def find_fact(self, param: str, *, row_id: str | None = None, status: str | None = None) -> Any:
        for f in self.facts():
            if f.param == param and (row_id is None or f.row_id == row_id) \
                    and (status is None or str(f.review_status) == status):
                return f
        raise AssertionError(f"нет факта param={param} row={row_id}")

    def jv_row(self) -> str:
        """row_id, у которого есть все четыре фактa voc/jsc/ff/pce (основа jv_consistency)."""
        rows: dict[str, set[str]] = {}
        for f in self.facts():
            if f.row_id:
                rows.setdefault(f.row_id, set()).add(f.param)
        for rid in sorted(rows):
            if set(CATALOG_PARAMS) <= rows[rid]:
                return rid
        raise AssertionError("в D001 нет строки с voc/jsc/ff/pce")

    def open_issue_ids(self, check_name: str | None = None) -> list[str]:
        r = self.client.get(url(f"/papers/{self.pid()}/issues"), params={"status": "open"})
        assert r.status_code == 200, r.text
        return [i["issue_id"] for i in r.json()["data"] if check_name in (None, i["check_name"])]

    def history(self, **params: Any) -> list[dict]:
        r = self.client.get(url("/history"), params=params or None)
        assert r.status_code == 200, r.text
        return r.json()["data"]

    def make_user(self, email: str = "second@example.com", password: str = "second-pass-1"):
        """Второй пользователь: (TestClient, CurrentUser)."""
        from fastapi.testclient import TestClient

        from faktura.domain.entities import CurrentUser
        from faktura.domain.security import hash_password

        with self.uow() as uow:
            u = uow.users.create(workspace_id=self.admin.workspace_id, email=email, name="Second",
                                 is_admin=False, password_hash=hash_password(password))
        c = TestClient(self.app)
        r = c.post(url("/auth/login"), json={"email": email, "password": password, "remember": False})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        return c, CurrentUser(id=u.id, workspace_id=u.workspace_id, email=email, is_admin=False)


def build_env(root: Path, case_dir: Path, publication_ids: tuple[str, ...] = PAPERS) -> Env:
    from fastapi.testclient import TestClient

    from faktura.api.base import create_app
    from faktura.config import Settings
    from faktura.domain.entities import CurrentUser
    from faktura.pdfio.inputs import collect_inputs
    from faktura.pipeline import CorpusService, InlineExecutor, RunService, build_services
    from faktura.review import ReviewService
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
        if pdf.stem in publication_ids:
            shutil.copy(pdf, src / pdf.name)

    corpus = CorpusService(services).create(admin.workspace_id, admin.id, "dev", "solar")
    CorpusService(services).add_inputs(corpus.id, collect_inputs(src), mode="cli")
    runs = RunService(services)
    run = runs.start(corpus.id, "full", user_id=admin.id)
    runs.execute(run.id)

    client = TestClient(app)
    r = client.post(url("/auth/login"), json={"email": "admin@example.com", "password": "admin-password-1",
                                              "remember": False})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    with services.db.uow() as uow:
        papers = {f.publication_id: f.paper_id for f in uow.files.list(corpus.id) if f.paper_id}
    user = CurrentUser(id=admin.id, workspace_id=admin.workspace_id, email=admin.email, is_admin=True)
    return Env(settings=settings, services=services, app=app, client=client, admin=admin, user=user,
               review=ReviewService(services), corpus_id=corpus.id, papers=papers)


def err(resp) -> dict:
    """Тело ошибки по api_contract §1.5."""
    return resp.json()["error"]


def dec(x: Any) -> Decimal:
    return Decimal(str(x))
