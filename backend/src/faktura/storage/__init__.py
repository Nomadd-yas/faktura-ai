"""Хранение: БД (SQLite/Postgres), репозитории, файловое хранилище (план 5.5).

Этап A (P0, нужен CLI): papers, corpora, files, runs, save_processed/load_bundle, LocalFileStore.
Этап B (P1): keyset-запросы фактов, действия, статистика, эталон, идемпотентность, пользователи, S3.
"""
from __future__ import annotations

from ._types import DEFAULT_WORKSPACE_ID, GoldSet, RefreshSession, StoredUser, Synonym, Workspace
from .cursor import decode_cursor, encode_cursor
from .db import Database, UnitOfWork, seed_admin
from .files import LocalFileStore, S3FileStore, build_file_store, normalize_key

__all__ = ["Database", "UnitOfWork", "seed_admin", "LocalFileStore", "S3FileStore", "build_file_store",
           "normalize_key", "encode_cursor", "decode_cursor", "DEFAULT_WORKSPACE_ID", "GoldSet", "RefreshSession",
           "StoredUser", "Synonym", "Workspace"]
