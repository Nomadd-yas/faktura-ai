"""Таблицы v2 (спека BACKEND_ONLY §2, §9): правки значений и состояния замечаний разбора.

Ключ - (corpus_id разбора, domain, publication_id, result_id | issue_id). Строки разбора (sub_rows) не меняются:
правки применяются при чтении и выгрузке (faktura.review.runs_v2).
"""
from __future__ import annotations

import sqlalchemy as sa

from .schema import JSONType, UTCDateTime, metadata

run_reviews = sa.Table(
    "run_reviews", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("corpus_id", sa.String(64), nullable=False),
    sa.Column("domain", sa.String(16), nullable=False),
    sa.Column("publication_id", sa.String(128), nullable=False),
    sa.Column("result_id", sa.String(128), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),            # new | accepted | rejected
    sa.Column("corrected_value", sa.String(64), nullable=True),     # в целевой единице, как строка
    sa.Column("at", UTCDateTime, nullable=False),
    sa.Column("who", sa.String(64), nullable=True),
    sa.Column("who_name", sa.String(320), nullable=True),
    sa.UniqueConstraint("corpus_id", "domain", "publication_id", "result_id", name="uq_run_reviews_key"),
    sa.Index("ix_run_reviews_corpus", "corpus_id", "domain", "publication_id"),
)

run_issue_states = sa.Table(
    "run_issue_states", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("corpus_id", sa.String(64), nullable=False),
    sa.Column("domain", sa.String(16), nullable=False),
    sa.Column("publication_id", sa.String(128), nullable=False),
    sa.Column("issue_id", sa.String(128), nullable=False),
    sa.Column("state", sa.String(16), nullable=False),             # open | fixed | ok
    sa.Column("auto", sa.Boolean, nullable=False, default=False),  # fixed выставлено автоматически
    sa.Column("at", UTCDateTime, nullable=False),
    sa.Column("who", sa.String(64), nullable=True),
    sa.UniqueConstraint("corpus_id", "domain", "publication_id", "issue_id", name="uq_run_issue_states_key"),
    sa.Index("ix_run_issue_states_corpus", "corpus_id", "domain", "publication_id"),
)

# замечания, которые открыл пересчёт проверок после правки (их нет в исходных строках разбора)
run_extra_issues = sa.Table(
    "run_extra_issues", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("corpus_id", sa.String(64), nullable=False),
    sa.Column("domain", sa.String(16), nullable=False),
    sa.Column("publication_id", sa.String(128), nullable=False),
    sa.Column("issue_id", sa.String(128), nullable=False),
    sa.Column("data", JSONType, nullable=False),                    # строка issues.csv
    sa.UniqueConstraint("corpus_id", "domain", "publication_id", "issue_id", name="uq_run_extra_issues_key"),
)
