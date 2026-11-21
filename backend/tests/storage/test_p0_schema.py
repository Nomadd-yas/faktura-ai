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






def test_create_all_is_idempotent(db):
    db.create_all()
    db.create_all()  # повторный вызов не падает и не затирает данные
    with db.uow() as u:
        assert u.corpora.list("ws_none") == []














def test_uow_rolls_back_on_exception(db, ws_id):
    created = {}
    with pytest.raises(RuntimeError, match="boom"):
        with db.uow() as u:
            created["c"] = u.corpora.create(workspace_id=ws_id, name="K")
            raise RuntimeError("boom")
    with db.uow() as u2:
        assert u2.corpora.get(created["c"].id) is None










