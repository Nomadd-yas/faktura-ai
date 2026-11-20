"""Фикстуры тестов api_base: приложение на SQLite, Services собраны вручную (без pipeline.build_services).

ДОГОВОРЁННОСТИ ОБ ИНТЕРФЕЙСАХ, которых нет в заглушках и которые нужны тестам (реализация api_base обязана их дать):

* ЧАСЫ. Всё время в api.base берётся из функции ``faktura.api.base.auth.utcnow() -> datetime`` (aware, UTC) и
  вызывается как атрибут модуля (``auth.utcnow()``), чтобы тест подменял её через monkeypatch. Фикстура ``clock``
  подменяет ``utcnow`` в auth и во всех модулях пакета, где она есть. Требование: JWT, срок refresh-сессий,
  LoginLimiter и RateLimiter в HTTP-слое читают время только оттуда.
* СЕКРЕТ JWT. ``issue_tokens``/``decode_access`` берут ключ из ``faktura.config.get_settings().jwt_secret``
  (переменная FAKTURA_JWT_SECRET), юнит-тесты задают его через env и сбрасывают ``get_settings.cache_clear()``.
* ЛИМИТЕРЫ. Ключи LoginLimiter: ``"email:<почта>"`` (лимит 5 за 15 мин) и ``"ip:<адрес>"`` (лимит 20 за 15 мин).
  RateLimiter по умолчанию: 600 обращений за 60 с на ключ.
* ПУТИ. Все маршруты под ``faktura.api.API_PREFIX`` (``/api/v1``).
"""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Any

import pytest
from fastapi.testclient import TestClient

from faktura.api import API_PREFIX
from faktura.domain.extraction import MlHealth
from faktura.domain.security import hash_password
from faktura.domain.services import Services

UTC = timezone.utc
COOKIE_NAME = "faktura_refresh"
THIRTY_DAYS = 30 * 24 * 3600
TWELVE_HOURS = 12 * 3600


def url(path: str) -> str:
    return f"{API_PREFIX}{path}"


# --- поддельные адаптеры ---------------------------------------------------------------------

class FakeExtractor:
    name = "mock"

    def __init__(self) -> None:
        self.status = "ok"
        self.model_version = "extract-0.3"
        self.raise_exc: Exception | None = None

    def extract(self, req):  # pragma: no cover - не используется в api_base
        raise NotImplementedError

    def health(self) -> MlHealth:
        if self.raise_exc is not None:
            raise self.raise_exc
        return MlHealth(status=self.status, model_version=self.model_version)


class FakeNormalizer:
    version = "fake-1"
    dictionary_version = "fake-1"

    def __init__(self) -> None:
        self.display_calls: list[tuple[Any, Any]] = []

    def format_display(self, value, unit, *, digits=None) -> str:
        self.display_calls.append((value, unit))
        if value is None:
            return ""
        return f"{str(value).replace('.', ',')} {unit or ''}".strip()


class FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []

    def publish(self, workspace_id: str, event: str, data: dict) -> str:
        self.published.append((workspace_id, event, data))
        return str(len(self.published))

    def subscribe(self, workspace_id, last_event_id):  # pragma: no cover
        raise NotImplementedError


class InlineFakeExecutor:
    def submit(self, fn) -> None:
        fn()


class FakeParser:  # pragma: no cover - не используется в api_base
    pass


class FakeEvaluator:  # pragma: no cover
    def evaluate(self, gold_dir, pred_dir):
        raise NotImplementedError


class BrokenDb:
    """Любое обращение к БД падает (проверка health: db -> error, 503)."""
    url = "sqlite:///broken"

    def __getattr__(self, name: str):
        raise RuntimeError("db is down")


class BrokenStore:
    """Любое обращение к хранилищу файлов падает."""

    def __getattr__(self, name: str):
        raise RuntimeError("storage is down")


# --- сборка Services и приложения -------------------------------------------------------------

@pytest.fixture
def extractor() -> FakeExtractor:
    return FakeExtractor()


@pytest.fixture
def services(settings, extractor) -> Services:
    """Services вручную: SQLite, локальное хранилище, поддельные ML/нормализатор/шина/исполнитель."""
    from faktura.storage import Database, LocalFileStore, seed_admin

    db = Database(settings.db_url)
    db.create_all()
    seed_admin(db, settings)
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return Services(
        settings=settings,
        db=db,
        store=LocalFileStore(settings.storage_dir),
        parser=FakeParser(),
        normalizer=FakeNormalizer(),
        bus=FakeBus(),
        executor=InlineFakeExecutor(),
        extractor_factory=lambda: extractor,
        evaluator=FakeEvaluator(),
    )


@pytest.fixture
def app(settings, services):
    from faktura.api.base import create_app

    return create_app(settings, services)


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


def new_client(app) -> TestClient:
    """Клиент с чистым cookie-хранилищем."""
    return TestClient(app)


@pytest.fixture
def clock(monkeypatch):
    """Замороженные часы: clock.advance(seconds=...). Подменяет utcnow в auth и в соседних модулях api.base."""

    class Clock:
        def __init__(self) -> None:
            self.now = datetime.now(UTC).replace(microsecond=0)

        def __call__(self) -> datetime:
            return self.now

        def advance(self, **kw: float) -> None:
            self.now += timedelta(**kw)

    c = Clock()
    auth = importlib.import_module("faktura.api.base.auth")
    monkeypatch.setattr(auth, "utcnow", c, raising=True)
    for name in ("app", "deps", "serializers", "users", "limits", "middleware"):
        try:
            mod = importlib.import_module(f"faktura.api.base.{name}")
        except ModuleNotFoundError:
            continue
        if hasattr(mod, "utcnow"):
            monkeypatch.setattr(mod, "utcnow", c)
    return c


# --- пользователи и вход ----------------------------------------------------------------------

@pytest.fixture
def admin_creds(settings) -> tuple[str, str]:
    return settings.admin_email, settings.admin_password


def make_user(services: Services, email: str, password: str = "user-password-1", *, is_admin: bool = False,
              disabled: bool = False, name: str = "Тест Пользователь"):
    with services.db.uow() as uow:
        admin = uow.users.get_by_email(services.settings.admin_email)
        return uow.users.create(workspace_id=admin.workspace_id, email=email, name=name,
                                password_hash=hash_password(password), is_admin=is_admin, disabled=disabled)


def login(client: TestClient, email: str, password: str, **extra: Any):
    body = {"email": email, "password": password, "remember": True}
    body.update(extra)
    return client.post(url("/auth/login"), json=body)


def login_token(client: TestClient, email: str, password: str, **extra: Any) -> str:
    r = login(client, email, password, **extra)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def parse_set_cookie(resp, name: str = COOKIE_NAME):
    """-> морсель SimpleCookie или None. Ключи атрибутов в нижнем регистре: max-age, path, httponly, samesite, secure."""
    for raw in resp.headers.get_list("set-cookie"):
        jar = SimpleCookie()
        jar.load(raw)
        if name in jar:
            return jar[name]
    return None


def cookie_value(resp, name: str = COOKIE_NAME) -> str | None:
    m = parse_set_cookie(resp, name)
    return m.value if m is not None else None


def refresh_with(app, token: str):
    """POST /auth/refresh с явным cookie (чистый клиент, без cookie-хранилища)."""
    return new_client(app).post(url("/auth/refresh"), headers={"Cookie": f"{COOKIE_NAME}={token}"})


@pytest.fixture
def admin_token(client, admin_creds) -> str:
    return login_token(client, *admin_creds)


@pytest.fixture
def user_creds(services) -> tuple[str, str]:
    make_user(services, "user@example.com", "user-password-1")
    return "user@example.com", "user-password-1"


@pytest.fixture
def user_token(client, user_creds) -> str:
    return login_token(client, *user_creds)


def assert_error(resp, status: int, code: str, *, field: str | None = None) -> dict:
    """Единый формат ошибок (api_contract §1.5)."""
    assert resp.status_code == status, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert set(body) == {"error"}, body
    err = body["error"]
    assert {"code", "message", "field", "details", "request_id"} <= set(err), err
    assert err["code"] == code, err
    assert isinstance(err["message"], str) and err["message"]
    if field is not None:
        assert err["field"] == field, err
    return err


class _Helpers:
    """Пространство имён помощников: в режиме importlib тесты не могут импортировать conftest, берут фикстуру ``h``."""
    UTC = UTC
    COOKIE_NAME = COOKIE_NAME
    THIRTY_DAYS = THIRTY_DAYS
    TWELVE_HOURS = TWELVE_HOURS
    url = staticmethod(url)
    new_client = staticmethod(new_client)
    make_user = staticmethod(make_user)
    login = staticmethod(login)
    login_token = staticmethod(login_token)
    bearer = staticmethod(bearer)
    parse_set_cookie = staticmethod(parse_set_cookie)
    cookie_value = staticmethod(cookie_value)
    refresh_with = staticmethod(refresh_with)
    assert_error = staticmethod(assert_error)
    BrokenDb = BrokenDb
    BrokenStore = BrokenStore
    FakeNormalizer = FakeNormalizer
    FakeExtractor = FakeExtractor


@pytest.fixture
def h() -> type[_Helpers]:
    return _Helpers
