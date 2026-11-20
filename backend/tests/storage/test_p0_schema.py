"""Этап A: Database, UnitOfWork, схема (SQLite во временном файле; pg - по маркеру через db_url)."""
from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy as sa

from faktura.storage import Database, UnitOfWork

P0_TABLES = {"workspaces", "corpora", "corpus_files", "runs", "profiles", "papers", "tz_records", "facts",
             "anchors", "issues"}
P1_TABLES = {"users", "refresh_sessions", "login_attempts", "actions", "idempotency_keys", "exports",
             "corpus_stats", "dictionary_synonyms"}


def _tables(url: str) -> set[str]:
    eng = sa.create_engine(url)
    try:
        return set(sa.inspect(eng).get_table_names())
    finally:
        eng.dispose()


def test_create_all_creates_p0_tables(sqlite_url):
    Database(sqlite_url).create_all()
    assert P0_TABLES <= _tables(sqlite_url)


def test_create_all_creates_p1_tables(sqlite_url):
    Database(sqlite_url).create_all()
    got = _tables(sqlite_url)
    assert {"users", "refresh_sessions", "actions", "idempotency_keys", "corpus_stats"} <= got
    assert any(t.startswith("gold") for t in got)


def test_create_all_is_idempotent(db):
    db.create_all()
    db.create_all()  # повторный вызов не падает и не затирает данные
    with db.uow() as u:
        assert u.corpora.list("ws_none") == []


def test_create_all_on_pg_and_sqlite_same_tables(db, db_url):
    assert P0_TABLES <= _tables(db_url)


def test_sqlite_uses_wal(sqlite_url):
    Database(sqlite_url).create_all()
    path = sqlite_url.removeprefix("sqlite:///")
    con = sqlite3.connect(path)
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        con.close()


def test_builtin_profile_created_with_schema(db):
    """Профиль solar встроенный, создаётся вместе со схемой (план 5.5)."""
    eng = sa.create_engine(db.url)
    try:
        with eng.connect() as c:
            n = c.execute(sa.text("SELECT COUNT(*) FROM profiles")).scalar()
    finally:
        eng.dispose()
    assert n >= 1


def test_builtin_profile_not_duplicated_on_second_create_all(db):
    db.create_all()
    eng = sa.create_engine(db.url)
    try:
        with eng.connect() as c:
            n1 = c.execute(sa.text("SELECT COUNT(*) FROM profiles")).scalar()
        db.create_all()
        with eng.connect() as c:
            n2 = c.execute(sa.text("SELECT COUNT(*) FROM profiles")).scalar()
    finally:
        eng.dispose()
    assert n1 == n2


def test_uow_returns_unit_of_work_with_repos(db):
    u = db.uow()
    assert isinstance(u, UnitOfWork)
    with u as uu:
        for name in ("papers", "facts", "issues", "corpora", "files", "runs", "actions", "stats", "gold",
                     "idempotency", "users", "sessions"):
            assert getattr(uu, name) is not None, name


def test_uow_commits_on_clean_exit(db, ws_id):
    with db.uow() as u:
        c = u.corpora.create(workspace_id=ws_id, name="K")
    with db.uow() as u2:  # новая транзакция видит данные
        assert u2.corpora.get(c.id) is not None


def test_uow_rolls_back_on_exception(db, ws_id):
    created = {}
    with pytest.raises(RuntimeError, match="boom"):
        with db.uow() as u:
            created["c"] = u.corpora.create(workspace_id=ws_id, name="K")
            raise RuntimeError("boom")
    with db.uow() as u2:
        assert u2.corpora.get(created["c"].id) is None


def test_uow_explicit_rollback(db, ws_id):
    with db.uow() as u:
        c = u.corpora.create(workspace_id=ws_id, name="K")
        u.rollback()
        assert u.corpora.get(c.id) is None


def test_uow_explicit_commit_then_continue(db, ws_id):
    with db.uow() as u:
        c1 = u.corpora.create(workspace_id=ws_id, name="K1")
        u.commit()
        c2 = u.corpora.create(workspace_id=ws_id, name="K2")
        u.rollback()
    with db.uow() as u2:
        assert u2.corpora.get(c1.id) is not None
        assert u2.corpora.get(c2.id) is None


def test_uow_independent_instances(db, ws_id):
    """Незакоммиченное изменение одного UoW не видно другому (изоляция)."""
    u1, u2 = db.uow(), db.uow()
    with u1:
        c = u1.corpora.create(workspace_id=ws_id, name="K")
        with u2:
            assert u2.corpora.get(c.id) is None


def test_database_bad_url_fails_early():
    with pytest.raises(Exception) as ei:
        Database("nosuchdb://x/y").create_all()
    assert ei.type is not NotImplementedError


def test_sqlite_foreign_keys_enforced(db):
    """foreign_keys=ON: файл неизвестного корпуса не добавляется."""
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.files.add(corpus_id="crp_missing", name="a.pdf", publication_id="D001")
    assert ei.type is not NotImplementedError
