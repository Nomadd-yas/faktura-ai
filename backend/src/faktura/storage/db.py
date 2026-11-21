"""База данных и Unit of Work (план 5.5). SQLite (WAL) для CLI/тестов, PostgreSQL для сервера.

* `Database(url, clock=None)`: движок создаётся лениво. SQLite: WAL, foreign_keys=ON, busy_timeout,
  явный BEGIN (работают SAVEPOINT); lower() с поддержкой Unicode. Postgres: JSONB.
* `UnitOfWork`: одно соединение и одна транзакция; commit при выходе без исключения, иначе rollback.
  После явных commit()/rollback() открывается новая транзакция - UoW можно продолжать.
* SQLite допускает одного писателя: внутри процесса UoW одной базы выполняются по очереди
  (реентерабельная блокировка - вложенные UoW в одном потоке разрешены). Это исключает
  SQLITE_BUSY при параллельной обработке статей в потоках.
* `clock` - вызываемый объект без аргументов -> datetime UTC; подменяется в тестах.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Callable

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.pool import StaticPool

from ..config import Settings
from ..domain.errors import AppError
from ..domain.security import hash_password, validate_password
from ..domain.tables import ID_COLUMN, TABLES
from . import schema as S
from ._types import DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_NAME
from .repos import (ActionsRepo, AnchorsRepo, CorporaRepo, ExportsRepo, FactsRepo, FilesRepo, GoldRepo,
                    IdempotencyRepo, IssuesRepo, LoginAttemptsRepo, PapersRepo, ProfilesRepo, RunsRepo, SessionsRepo,
                    StatsRepo, SynonymsRepo, UsersRepo, WorkspacesRepo)
from .sub_rows import SubRowsRepo, _num as _sub_num, _page as _sub_page

log = logging.getLogger(__name__)

SQLITE_BUSY_TIMEOUT_MS = 30_000
LOCK_TIMEOUT_S = 120.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _py_lower(s):
    return s.lower() if isinstance(s, str) else s


def _normalize_url(url: str) -> str:
    """postgres:// и postgresql:// -> драйвер psycopg (extra `pg`), если не указан явно."""
    for p in ("postgres://", "postgresql://"):
        if url.startswith(p):
            return "postgresql+psycopg://" + url[len(p):]
    return url


class UnitOfWork:
    """Контекстный менеджер: commit при выходе без исключения, иначе rollback. Репозитории - атрибуты."""

    papers: PapersRepo
    facts: FactsRepo
    issues: IssuesRepo
    corpora: CorporaRepo
    files: FilesRepo
    runs: RunsRepo
    actions: ActionsRepo
    stats: StatsRepo
    gold: GoldRepo
    idempotency: IdempotencyRepo
    users: UsersRepo
    sessions: SessionsRepo
    workspaces: WorkspacesRepo
    profiles: ProfilesRepo
    anchors: AnchorsRepo
    exports: ExportsRepo
    login_attempts: LoginAttemptsRepo
    synonyms: SynonymsRepo
    sub: SubRowsRepo

    def __init__(self, db: "Database") -> None:
        self.db = db
        self._conn: sa.Connection | None = None
        self._tx: sa.RootTransaction | None = None
        self._locked = False
        self.papers = PapersRepo(self)
        self.facts = FactsRepo(self)
        self.issues = IssuesRepo(self)
        self.corpora = CorporaRepo(self)
        self.files = FilesRepo(self)
        self.runs = RunsRepo(self)
        self.actions = ActionsRepo(self)
        self.stats = StatsRepo(self)
        self.gold = GoldRepo(self)
        self.idempotency = IdempotencyRepo(self)
        self.users = UsersRepo(self)
        self.sessions = SessionsRepo(self)
        self.workspaces = WorkspacesRepo(self)
        self.profiles = ProfilesRepo(self)
        self.anchors = AnchorsRepo(self)
        self.exports = ExportsRepo(self)
        self.login_attempts = LoginAttemptsRepo(self)
        self.synonyms = SynonymsRepo(self)
        self.sub = SubRowsRepo(self)

    # --- жизненный цикл -------------------------------------------------------------------------

    @property
    def conn(self) -> sa.Connection:
        if self._conn is None:
            raise RuntimeError("UnitOfWork не открыт: используйте `with db.uow() as u:`")
        return self._conn

    def now(self) -> datetime:
        return self.db.now()

    def __enter__(self) -> "UnitOfWork":
        if self._conn is not None:
            raise RuntimeError("UnitOfWork уже открыт")
        self._locked = self.db._acquire()
        try:
            self._conn = self.db.engine.connect()
            self._tx = self._conn.begin()
        except BaseException:
            self._close()
            raise
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        try:
            if self._tx is not None and self._tx.is_active:
                if exc_type is None:
                    self._tx.commit()
                else:
                    self._tx.rollback()
        finally:
            self._close()

    def _close(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        finally:
            self._conn = None
            self._tx = None
            if self._locked:
                self._locked = False
                self.db._release()

    def commit(self) -> None:
        if self._tx is not None and self._tx.is_active:
            self._tx.commit()
        self._tx = self.conn.begin()

    def rollback(self) -> None:
        if self._tx is not None and self._tx.is_active:
            self._tx.rollback()
        self._tx = self.conn.begin()

    def savepoint(self):
        """Вложенная транзакция: `with uow.savepoint(): ...` - откат только своей части при исключении."""
        return self.conn.begin_nested()


class Database:
    """SQLite: WAL, foreign_keys=ON, busy_timeout; Postgres: JSONB через with_variant."""

    def __init__(self, url: str, clock: Callable[[], datetime] | None = None, *, echo: bool = False) -> None:
        self.url = url
        self.clock = clock or utcnow
        self.echo = echo
        self._engine: sa.Engine | None = None
        self._engine_lock = threading.Lock()
        self.is_sqlite = url.startswith("sqlite")
        self._lock = threading.RLock() if self.is_sqlite else None

    def now(self) -> datetime:
        t = self.clock()
        return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)

    # --- движок ---------------------------------------------------------------------------------

    @property
    def engine(self) -> sa.Engine:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    self._engine = self._make_engine()
        return self._engine

    def _make_engine(self) -> sa.Engine:
        url = sa.make_url(_normalize_url(self.url))
        if url.get_backend_name() != "sqlite":
            return sa.create_engine(url, echo=self.echo, pool_pre_ping=True, future=True)
        db = url.database or ""
        memory = db in ("", ":memory:") or db.startswith("file::memory:")
        kw: dict = {"connect_args": {"check_same_thread": False}}
        if memory:
            kw["poolclass"] = StaticPool
        else:
            Path(db).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        engine = sa.create_engine(url, echo=self.echo, **kw)

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _rec):  # pragma: no cover - вызывается драйвером
            dbapi_conn.isolation_level = None  # BEGIN выдаём сами (иначе не работают SAVEPOINT)
            cur = dbapi_conn.cursor()
            try:
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
                if not memory:
                    cur.execute("PRAGMA journal_mode=WAL")
                    cur.execute("PRAGMA synchronous=NORMAL")
            finally:
                cur.close()
            dbapi_conn.create_function("lower", 1, _py_lower, deterministic=True)

        @event.listens_for(engine, "begin")
        def _on_begin(conn):  # pragma: no cover
            conn.exec_driver_sql("BEGIN")

        return engine

    def _acquire(self) -> bool:
        if self._lock is None:
            return False
        if not self._lock.acquire(timeout=LOCK_TIMEOUT_S):
            raise AppError("internal_error", "База данных занята (SQLite): истекло ожидание блокировки")
        return True

    def _release(self) -> None:
        if self._lock is not None:
            self._lock.release()

    # --- схема ----------------------------------------------------------------------------------

    def create_all(self) -> None:
        """Создаёт недостающие таблицы и встроенный профиль; повторный вызов безопасен."""
        S.metadata.create_all(self.engine)
        self._add_missing_columns()
        for ix in S.sub_issues.indexes:  # индексы, добавленные после первого развёртывания (create_all их не трогает)
            if ix.name == "ix_sub_issues_corpus_check":
                ix.create(self.engine, checkfirst=True)
        self._migrate_sub_rows()
        with self.uow() as u:
            u.profiles.ensure_builtin()

    def _migrate_sub_rows(self) -> None:
        """Однократный перенос старых sub_* в sub_rows (domain=solar), если sub_rows пуста. Идемпотентно."""
        with self.engine.begin() as c:
            if c.execute(sa.select(S.sub_rows.c.id).limit(1)).first() is not None:
                return
            if not any(c.execute(sa.select(t.c.seq).limit(1)).first() is not None for t in S.SUB.values()):
                return
            total = 0
            for name, t in S.SUB.items():
                cols = TABLES[name]
                key = ID_COLUMN[name]
                q = sa.select(t).order_by(t.c.paper_id, t.c.position, t.c.seq)
                batch: list[dict] = []
                for r in c.execute(q).mappings().all():
                    data = {k: r[k] if r[k] is not None else "" for k in cols}
                    batch.append({
                        "paper_id": r["paper_id"], "corpus_id": r["corpus_id"] or "", "domain": "solar",
                        "publication_id": data.get("publication_id", ""), "table_name": name,
                        "position": r["position"], "row_id": data.get(key),
                        "metric_name": data.get("metric_name") if name == "results" else None,
                        "check_name": data.get("check_name") if name == "issues" else None,
                        "num_value": _sub_num(data.get("metric_value", "")) if name == "results" else None,
                        "source_page": _sub_page(data.get("source_page", "")) if name in ("results", "issues")
                        else None,
                        "quote_found": None, "data": data})
                    if len(batch) >= 1000:
                        c.execute(S.sub_rows.insert(), batch)
                        total += len(batch)
                        batch = []
                if batch:
                    c.execute(S.sub_rows.insert(), batch)
                    total += len(batch)
        log.info("Перенесено строк sub_* -> sub_rows: %d", total)

    def _add_missing_columns(self) -> None:
        """Колонки, добавленные после первого развёртывания: create_all не меняет существующие таблицы.
        Идемпотентно (PostgreSQL и SQLite): `ALTER TABLE ... ADD COLUMN` только для отсутствующих колонок."""
        insp = sa.inspect(self.engine)
        for table in S.LATE_COLUMNS:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in S.LATE_COLUMNS[table]:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=self.engine.dialect)}'
                with self.engine.begin() as c:
                    c.execute(sa.text(ddl))
                log.info("Добавлена колонка %s.%s", table.name, col.name)

    def drop_all(self) -> None:
        S.metadata.drop_all(self.engine)

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self)

    def ping(self) -> bool:
        """Проверка доступности БД (для /health)."""
        with self.engine.connect() as c:
            c.execute(sa.text("SELECT 1"))
        return True

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None


def seed_admin(db: Database, settings: Settings) -> None:
    """Первый администратор из FAKTURA_ADMIN_EMAIL / FAKTURA_ADMIN_PASSWORD.

    Пароль хешируется domain.security.hash_password; короче 8 символов -> AppError(validation_error)
    (ошибка запуска). Если пользователь уже есть - ничего не меняется (пароль не перехешируется).
    Без admin_email - ничего не делает.
    """
    email = (settings.admin_email or "").strip().lower()
    if not email:
        return
    password = settings.admin_password
    with db.uow() as u:
        existing = u.users.get_by_email(email)
        if existing is not None:
            if password is not None:
                validate_password(password)
            return
        if password is None:
            raise AppError("validation_error", "Не задан FAKTURA_ADMIN_PASSWORD", field="admin_password")
        pw_hash = hash_password(password)  # validate_password внутри
        u.workspaces.ensure(DEFAULT_WORKSPACE_ID, DEFAULT_WORKSPACE_NAME)
        u.users.create(workspace_id=DEFAULT_WORKSPACE_ID, email=email, name="Администратор", is_admin=True,
                       disabled=False, password_hash=pw_hash)
    log.info("Создан администратор %s", email)
