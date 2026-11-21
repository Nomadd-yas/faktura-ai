"""Схема БД (SQLAlchemy Core). Одна схема для SQLite и PostgreSQL.

Соглашения:
  * у основных таблиц суррогатный целочисленный ключ `seq` (порядок вставки, тай-брейкер keyset)
    и строковый `id` с уникальным индексом (наружу отдаётся только он);
  * время - `UTCDateTime`: в БД UTC, наружу всегда datetime с tzinfo=UTC;
  * JSON - `JSONType` (JSON в SQLite, JSONB в Postgres);
  * точные числа фактов хранятся строкой (Decimal без потерь) + столбец float для сортировки и фильтров.
"""
from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from ..domain.tables import TABLES as _SUB_COLUMNS

metadata = sa.MetaData()

JSONType = sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class UTCDateTime(TypeDecorator):
    """datetime с часовым поясом: пишется в UTC, читается как aware UTC (SQLite хранит наивное UTC)."""

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"ожидается datetime, получено {type(value).__name__}")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _seq() -> sa.Column:
    return sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True)


def _id() -> sa.Column:
    return sa.Column("id", sa.String(64), nullable=False, unique=True)


def _paper_fk(nullable: bool = False) -> sa.Column:
    return sa.Column("paper_id", sa.String(64), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=nullable)


# --- рабочие пространства, профили -------------------------------------------------------------

workspaces = sa.Table(
    "workspaces", metadata,
    _seq(), _id(),
    sa.Column("name", sa.String(200), nullable=False),
    sa.Column("created_at", UTCDateTime, nullable=False),
)

profiles = sa.Table(
    "profiles", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), nullable=True),  # NULL - встроенный
    sa.Column("alias", sa.String(100), nullable=True),
    sa.Column("version", sa.Integer, nullable=False, default=1),
    sa.Column("name", sa.String(300), nullable=False),
    sa.Column("collection", sa.String(32), nullable=False),
    sa.Column("builtin", sa.Boolean, nullable=False, default=False),
    sa.Column("data", JSONType, nullable=False),
    sa.Column("created_by", sa.String(64), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
)

# --- корпус, файлы, прогоны -------------------------------------------------------------------

corpora = sa.Table(
    "corpora", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
    sa.Column("name", sa.String(300), nullable=False),
    sa.Column("collection", sa.String(32), nullable=False),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("profile_id", sa.String(64), nullable=True),
    sa.Column("project_id", sa.String(64), nullable=True),
    sa.Column("created_by", sa.String(64), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("deleted_at", UTCDateTime, nullable=True),
    sa.Column("pinned", sa.Boolean, nullable=True),  # закреплённый разбор (PATCH /runs/{id}); NULL = не закреплён
    sa.Index("ix_corpora_ws", "workspace_id"),
)

corpus_files = sa.Table(
    "corpus_files", metadata,
    _seq(), _id(),
    sa.Column("corpus_id", sa.String(64), sa.ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False),
    sa.Column("name", sa.String(500), nullable=False),
    sa.Column("publication_id", sa.String(200), nullable=False),
    sa.Column("sha256", sa.String(64), nullable=True),
    sa.Column("size_bytes", sa.BigInteger, nullable=True),
    sa.Column("page_count", sa.Integer, nullable=True),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("error_code", sa.String(64), nullable=True),
    sa.Column("error_message", sa.Text, nullable=True),
    sa.Column("paper_id", sa.String(64), nullable=True),
    sa.Column("duplicate_of", sa.String(64), nullable=True),
    sa.Column("dup_kind", sa.String(16), nullable=True),
    sa.Column("storage_key", sa.String(1000), nullable=True),
    sa.Column("source", sa.String(1000), nullable=True),
    sa.Column("stage", sa.String(32), nullable=True),
    sa.Column("progress", sa.Integer, nullable=True),
    sa.Column("is_scan", sa.Boolean, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("started_at", UTCDateTime, nullable=True),  # начало разбора файла (первый запуск этапа)
    sa.Column("stage_started_at", UTCDateTime, nullable=True),  # начало текущего этапа
    sa.Index("ix_files_corpus", "corpus_id", "seq"),
    sa.Index("ix_files_sha", "sha256"),
)

# колонки, добавленные позже первого развёртывания: Database.create_all добавляет их в существующие БД
LATE_COLUMNS: dict[sa.Table, tuple[sa.Column, ...]] = {
    corpus_files: (corpus_files.c.stage, corpus_files.c.progress, corpus_files.c.is_scan,
                   corpus_files.c.started_at, corpus_files.c.stage_started_at),
    corpora: (corpora.c.pinned,),
}

# скорость этапов (секунд на страницу) по завершённым файлам: скользящее среднее для оценки хода и ETA
stage_speeds = sa.Table(
    "stage_speeds", metadata,
    _seq(),
    sa.Column("domain", sa.String(32), nullable=False),
    sa.Column("stage", sa.String(32), nullable=False),
    sa.Column("spp", sa.Float, nullable=False),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index("ix_stage_speeds_ds", "domain", "stage", "seq"),
)

runs = sa.Table(
    "runs", metadata,
    _seq(), _id(),
    sa.Column("corpus_id", sa.String(64), sa.ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False),
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("profile_id", sa.String(64), nullable=True),
    sa.Column("model_version", sa.String(200), nullable=False),
    sa.Column("concurrency", sa.Integer, nullable=False),
    sa.Column("keep_review", sa.Boolean, nullable=False),
    sa.Column("build_output", sa.Boolean, nullable=False),
    sa.Column("file_ids", JSONType, nullable=True),
    sa.Column("counts", JSONType, nullable=False),
    sa.Column("paused", sa.Boolean, nullable=False),
    sa.Column("pause_reason", sa.String(200), nullable=True),
    sa.Column("output", JSONType, nullable=True),
    sa.Column("started_by", sa.String(64), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("started_at", UTCDateTime, nullable=True),
    sa.Column("finished_at", UTCDateTime, nullable=True),
    sa.Index("ix_runs_corpus", "corpus_id", "seq"),
)

# --- статьи и их содержимое -------------------------------------------------------------------

papers = sa.Table(
    "papers", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
    sa.Column("corpus_id", sa.String(64), sa.ForeignKey("corpora.id", ondelete="CASCADE"), nullable=True),
    sa.Column("file_id", sa.String(64), nullable=True),
    sa.Column("publication_id", sa.String(200), nullable=False),
    sa.Column("collection", sa.String(32), nullable=False),
    sa.Column("title", sa.Text, nullable=True),
    sa.Column("title_key", sa.Text, nullable=True),
    sa.Column("year", sa.Integer, nullable=True),
    sa.Column("doi", sa.String(300), nullable=True),
    sa.Column("doi_norm", sa.String(300), nullable=True),
    sa.Column("in_scope", sa.Boolean, nullable=True),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("sha256", sa.String(64), nullable=True),
    sa.Column("page_count", sa.Integer, nullable=True),
    sa.Column("needs_ocr", sa.Boolean, nullable=False),
    sa.Column("model_version", sa.String(200), nullable=True),
    sa.Column("versions", JSONType, nullable=False),
    sa.Column("dup_of", sa.String(64), nullable=True),
    sa.Column("dup_kind", sa.String(16), nullable=True),
    sa.Column("stats", JSONType, nullable=False),
    sa.Column("error_code", sa.String(64), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("processed_at", UTCDateTime, nullable=True),
    sa.Column("deleted_at", UTCDateTime, nullable=True),
    # счётчики статьи (пересчитываются из facts/issues в той же транзакции; основа corpus_stats)
    sa.Column("c_facts", sa.Integer, nullable=False, default=0),
    sa.Column("c_open_issues", sa.Integer, nullable=False, default=0),
    sa.Column("c_open_by_rule", JSONType, nullable=True),
    sa.Index("ix_papers_ws", "workspace_id"),
    sa.Index("ix_papers_corpus", "corpus_id"),
    sa.Index("ix_papers_doi", "workspace_id", "doi_norm"),
    sa.Index("ix_papers_title_key", "workspace_id", "title_key"),
)

tz_records = sa.Table(
    "tz_records", metadata,
    _seq(),
    _paper_fk(),
    sa.Column("table_name", sa.String(32), nullable=False),
    sa.Column("record_id", sa.String(200), nullable=False),
    sa.Column("ml_order", sa.Integer, nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("data", JSONType, nullable=False),
    sa.Index("ix_tz_paper", "paper_id", "table_name"),
)

facts = sa.Table(
    "facts", metadata,
    _seq(), _id(),
    _paper_fk(),
    sa.Column("ref", sa.String(200), nullable=True),
    # денормализация для фильтров таблицы фактов
    sa.Column("workspace_id", sa.String(64), nullable=False),
    sa.Column("corpus_id", sa.String(64), nullable=True),
    sa.Column("collection", sa.String(32), nullable=False),
    sa.Column("publication_id", sa.String(200), nullable=False),
    sa.Column("visible", sa.Boolean, nullable=False),
    sa.Column("is_dup", sa.Boolean, nullable=False),
    # поля факта
    sa.Column("target", JSONType, nullable=False),
    sa.Column("target_entity", sa.String(32), nullable=True),
    sa.Column("target_id", sa.String(200), nullable=True),
    sa.Column("target_field", sa.String(64), nullable=True),
    sa.Column("param", sa.String(64), nullable=False),
    sa.Column("metric_name", sa.String(64), nullable=True),
    sa.Column("row_id", sa.String(200), nullable=True),
    sa.Column("row_label", sa.Text, nullable=True),
    sa.Column("raw_value", sa.Text, nullable=True),
    sa.Column("raw_unit", sa.String(64), nullable=True),
    sa.Column("normalized_value", sa.String(64), nullable=True),
    sa.Column("normalized_num", sa.Float, nullable=True),
    sa.Column("normalized_unit", sa.String(64), nullable=True),
    sa.Column("norm_status", sa.String(32), nullable=True),
    sa.Column("extraction_status", sa.String(64), nullable=True),
    sa.Column("review_status", sa.String(16), nullable=False),
    sa.Column("corrected_value", sa.String(64), nullable=True),
    sa.Column("corrected_raw", sa.Text, nullable=True),
    sa.Column("value_num", sa.Float, nullable=True),  # corrected ?? normalized
    sa.Column("added_by", sa.String(16), nullable=False),
    sa.Column("confidence", sa.Float, nullable=True),
    sa.Column("source_page", sa.Integer, nullable=True),
    sa.Column("source_fragment", sa.Text, nullable=True),
    sa.Column("source_table", sa.String(200), nullable=True),
    sa.Column("source_figure", sa.String(200), nullable=True),
    sa.Column("source_kind", sa.String(16), nullable=True),
    sa.Column("quote_found", sa.Boolean, nullable=True),
    sa.Column("anchor_id", sa.String(64), nullable=True),
    sa.Column("duplicate_of", sa.String(64), nullable=True),
    sa.Column("model_version", sa.String(200), nullable=True),
    sa.Column("exportable", sa.Boolean, nullable=False),
    sa.Column("reviewed_by", sa.String(64), nullable=True),
    sa.Column("reviewed_at", UTCDateTime, nullable=True),
    sa.Column("trace", JSONType, nullable=True),
    sa.Column("open_issues", sa.Integer, nullable=False),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index("ix_facts_paper", "paper_id", "seq"),
    sa.Index("ix_facts_ws", "workspace_id", "visible", "seq"),
    sa.Index("ix_facts_ws_norm", "workspace_id", "normalized_num", "seq"),
    sa.Index("ix_facts_corpus", "corpus_id", "seq"),
)

anchors = sa.Table(
    "anchors", metadata,
    _seq(), _id(),
    _paper_fk(),
    sa.Column("ref", sa.String(200), nullable=True),
    sa.Column("kind", sa.String(16), nullable=False),
    sa.Column("page", sa.Integer, nullable=False),
    sa.Column("rects", JSONType, nullable=False),
    sa.Column("text", sa.Text, nullable=True),
    sa.Column("fact_ids", JSONType, nullable=False),
    sa.Index("ix_anchors_paper", "paper_id"),
)

issues = sa.Table(
    "issues", metadata,
    _seq(), _id(),
    _paper_fk(),
    sa.Column("key", sa.String(200), nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("check_name", sa.String(64), nullable=False),
    sa.Column("severity", sa.String(16), nullable=True),
    sa.Column("computed_by", sa.String(16), nullable=True),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("stale", sa.Boolean, nullable=False),
    sa.Column("record", JSONType, nullable=False),
    sa.Column("record_ids", JSONType, nullable=False),
    sa.Column("fact_ids", JSONType, nullable=False),
    sa.Column("anchor_ids", JSONType, nullable=False),
    sa.Column("rule_version", sa.String(64), nullable=True),
    sa.Column("resolved_by", sa.String(64), nullable=True),
    sa.Column("resolved_at", UTCDateTime, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("updated_at", UTCDateTime, nullable=False),
    sa.Index("ix_issues_paper", "paper_id", "position"),
)

# --- 7 таблиц шаблона сдачи (solar): строки статьи ровно как в CSV экспорта ----------------------------
# Колонки README 1:1 (domain.tables.TABLES), значения - строки, пустая строка = нет значения.

SUB_TABLES: tuple[str, ...] = tuple(_SUB_COLUMNS)


def _sub_table(name: str) -> sa.Table:
    cols = [
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        _paper_fk(),
        sa.Column("corpus_id", sa.String(64), nullable=False, default="", server_default=""),
        sa.Column("position", sa.Integer, nullable=False, default=0),
    ]
    cols += [sa.Column(c, sa.Text, nullable=False, default="", server_default="") for c in _SUB_COLUMNS[name]]
    idx = [sa.Index(f"ix_sub_{name}_paper", "paper_id", "position")]
    if name == "results":
        idx += [sa.Index("ix_sub_results_corpus_pub", "corpus_id", "publication_id"),
                sa.Index("ix_sub_results_corpus_metric", "corpus_id", "metric_name")]
    if name == "issues":
        idx += [sa.Index("ix_sub_issues_corpus_pub", "corpus_id", "publication_id"),
                sa.Index("ix_sub_issues_corpus_check", "corpus_id", "check_name")]
    return sa.Table(f"sub_{name}", metadata, *cols, *idx)


SUB: dict[str, sa.Table] = {n: _sub_table(n) for n in SUB_TABLES}
sub_publications, sub_materials, sub_devices, sub_layers, sub_measurements, sub_results, sub_issues = (
    SUB[n] for n in SUB_TABLES)

# --- sub_rows: строки таблиц сдачи всех областей (docs/design/DOMAINS.md §2) ---------------------------
# data = {колонка шаблона: строка как в CSV}; остальные колонки - денормализация для фильтров и сортировки.
# Старые sub_* выше остаются только для разового переноса в sub_rows (Database.create_all).

sub_rows = sa.Table(
    "sub_rows", metadata,
    sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"), primary_key=True, autoincrement=True),
    _paper_fk(),
    sa.Column("corpus_id", sa.String(64), nullable=False, default="", server_default=""),
    sa.Column("domain", sa.String(16), nullable=False, default="solar", server_default="solar"),
    sa.Column("publication_id", sa.String(128), nullable=False, default="", server_default=""),
    sa.Column("table_name", sa.String(32), nullable=False),
    sa.Column("position", sa.Integer, nullable=False, default=0),
    sa.Column("row_id", sa.String(128), nullable=True),
    sa.Column("metric_name", sa.String(128), nullable=True),
    sa.Column("check_name", sa.String(128), nullable=True),
    sa.Column("num_value", sa.Float, nullable=True),
    sa.Column("source_page", sa.Integer, nullable=True),
    sa.Column("quote_found", sa.Boolean, nullable=True),
    sa.Column("data", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
    sa.Index("ix_sub_rows_paper", "paper_id", "table_name", "position"),
    sa.Index("ix_sub_rows_corpus_pub", "corpus_id", "table_name", "publication_id"),
    sa.Index("ix_sub_rows_corpus_metric", "corpus_id", "table_name", "metric_name"),
    sa.Index("ix_sub_rows_corpus_check", "corpus_id", "table_name", "check_name"),
)

# --- кеш агрегатов корпуса (REQ-101) -----------------------------------------------------------

corpus_stats = sa.Table(
    "corpus_stats", metadata,
    sa.Column("corpus_id", sa.String(64), sa.ForeignKey("corpora.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("corpus_seq", sa.Integer, nullable=False),
    sa.Column("workspace_id", sa.String(64), nullable=False),
    sa.Column("collection", sa.String(32), nullable=False),
    sa.Column("name", sa.String(300), nullable=False),
    sa.Column("deleted", sa.Boolean, nullable=False),
    sa.Column("data", JSONType, nullable=False),
    sa.Column("updated_at", UTCDateTime, nullable=False),
    sa.Index("ix_cstats_ws", "workspace_id", "deleted"),
)

# --- пользователи и вход ----------------------------------------------------------------------

users = sa.Table(
    "users", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
    sa.Column("email", sa.String(320), nullable=False, unique=True),
    sa.Column("name", sa.String(200), nullable=True),
    sa.Column("is_admin", sa.Boolean, nullable=False),
    sa.Column("disabled", sa.Boolean, nullable=False),
    sa.Column("password_hash", sa.String(500), nullable=True),
    sa.Column("role", sa.String(80), nullable=True, default="", server_default=""),
    sa.Column("team", sa.String(80), nullable=True, default="", server_default=""),
    sa.Column("color", sa.String(16), nullable=True),
    sa.Column("focus", sa.String(300), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("last_login_at", UTCDateTime, nullable=True),
)

refresh_sessions = sa.Table(
    "refresh_sessions", metadata,
    _seq(), _id(),
    sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("token_hash", sa.String(200), nullable=False, unique=True),
    sa.Column("parent_id", sa.String(64), nullable=True),
    sa.Column("remember", sa.Boolean, nullable=False),
    sa.Column("expires_at", UTCDateTime, nullable=False),
    sa.Column("revoked_at", UTCDateTime, nullable=True),
    sa.Column("user_agent", sa.String(500), nullable=True),
    sa.Column("ip", sa.String(64), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index("ix_sessions_user", "user_id"),
)

login_attempts = sa.Table(
    "login_attempts", metadata,
    _seq(),
    sa.Column("email", sa.String(320), nullable=True),
    sa.Column("ip", sa.String(64), nullable=True),
    sa.Column("success", sa.Boolean, nullable=False),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index("ix_login_email", "email", "created_at"),
    sa.Index("ix_login_ip", "ip", "created_at"),
)

# --- журнал, идемпотентность, экспорт ----------------------------------------------------------

actions = sa.Table(
    "actions", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), nullable=True),
    sa.Column("user_id", sa.String(64), nullable=False),
    sa.Column("who", sa.String(320), nullable=True),
    sa.Column("paper_id", sa.String(64), nullable=True),
    sa.Column("fact_id", sa.String(64), nullable=True),
    sa.Column("group_id", sa.String(64), nullable=True),
    sa.Column("action", sa.String(64), nullable=False),
    sa.Column("label", sa.String(500), nullable=False),
    sa.Column("changes", JSONType, nullable=False),
    sa.Column("undoable", sa.Boolean, nullable=False),
    sa.Column("undone", sa.Boolean, nullable=False),
    sa.Column("undone_at", UTCDateTime, nullable=True),
    sa.Column("undone_by", sa.String(64), nullable=True),
    sa.Column("undo_of", sa.String(64), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Index("ix_actions_user", "user_id", "seq"),
    sa.Index("ix_actions_ws", "workspace_id", "seq"),
)

idempotency_keys = sa.Table(
    "idempotency_keys", metadata,
    _seq(),
    sa.Column("user_id", sa.String(64), nullable=False),
    sa.Column("key", sa.String(200), nullable=False),
    sa.Column("response", JSONType, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("expires_at", UTCDateTime, nullable=False),
    sa.UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
)

exports = sa.Table(
    "exports", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), nullable=False),
    sa.Column("user_id", sa.String(64), nullable=True),
    sa.Column("format", sa.String(32), nullable=False),
    sa.Column("scope", JSONType, nullable=False),
    sa.Column("options", JSONType, nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("file_key", sa.String(1000), nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("finished_at", UTCDateTime, nullable=True),
    sa.Index("ix_exports_ws", "workspace_id", "seq"),
)

# --- эталон, словарь ----------------------------------------------------------------------------

gold_sets = sa.Table(
    "gold_sets", metadata,
    _seq(), _id(),
    sa.Column("dataset", sa.String(100), nullable=False, unique=True),
    sa.Column("source_dir", sa.Text, nullable=True),
    sa.Column("papers", sa.Integer, nullable=False),
    sa.Column("results", sa.Integer, nullable=False),
    sa.Column("loaded_at", UTCDateTime, nullable=False),
)

gold_items = sa.Table(
    "gold_items", metadata,
    _seq(),
    sa.Column("set_id", sa.String(64), sa.ForeignKey("gold_sets.id", ondelete="CASCADE"), nullable=False),
    sa.Column("publication_id", sa.String(200), nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("data", JSONType, nullable=False),
    sa.Index("ix_gold_items_set", "set_id", "position"),
)

dictionary_synonyms = sa.Table(
    "dictionary_synonyms", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), nullable=False),
    sa.Column("collection", sa.String(32), nullable=False),
    sa.Column("param", sa.String(64), nullable=False),
    sa.Column("text", sa.String(300), nullable=False),
    sa.Column("text_norm", sa.String(300), nullable=False),
    sa.Column("custom", sa.Boolean, nullable=False),
    sa.Column("created_by", sa.String(64), nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.UniqueConstraint("workspace_id", "collection", "text_norm", name="uq_synonym_text"),
)

# --- задел P2: представления, проекты, чаты (минимальные поля) ---------------------------------

views = sa.Table(
    "views", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), nullable=False),
    sa.Column("owner_id", sa.String(64), nullable=False),
    sa.Column("collection", sa.String(32), nullable=True),
    sa.Column("name", sa.String(300), nullable=False),
    sa.Column("query", JSONType, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("deleted_at", UTCDateTime, nullable=True),
)

projects = sa.Table(
    "projects", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), nullable=False),
    sa.Column("owner_id", sa.String(64), nullable=True),
    sa.Column("name", sa.String(300), nullable=False),
    sa.Column("data", JSONType, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("deleted_at", UTCDateTime, nullable=True),
)

chats = sa.Table(
    "chats", metadata,
    _seq(), _id(),
    sa.Column("workspace_id", sa.String(64), nullable=False),
    sa.Column("project_id", sa.String(64), nullable=True),
    sa.Column("owner_id", sa.String(64), nullable=True),
    sa.Column("title", sa.String(300), nullable=True),
    sa.Column("data", JSONType, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
    sa.Column("deleted_at", UTCDateTime, nullable=True),
)

turns = sa.Table(
    "turns", metadata,
    _seq(), _id(),
    sa.Column("chat_id", sa.String(64), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
    sa.Column("question", sa.Text, nullable=True),
    sa.Column("answer", JSONType, nullable=True),
    sa.Column("created_at", UTCDateTime, nullable=False),
)
