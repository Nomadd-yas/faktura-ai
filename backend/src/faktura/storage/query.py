"""Таблица фактов: фильтры, keyset-пагинация, фасеты (spec/13 §13.7, REQ-088, REQ-090).

Keyset: ORDER BY для каждого ключа сортировки - (ключ IS NULL) ASC, ключ ASC|DESC; в конце - facts.seq ASC
(уникален). NULL всегда в конце независимо от направления. Курсор = [подпись сортировки, значения ключей
последней строки..., seq]. Вставки и правки между страницами не дают ни пропусков, ни повторов строк,
у которых не менялся ключ сортировки.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Sequence

import sqlalchemy as sa

from ..domain.entities import FactFilter, SortKey
from ..domain.errors import AppError
from ..domain.ids import sha8
from ..domain.io import format_cell
from . import schema as S
from .cursor import decode_cursor, encode_cursor

F, P = S.facts, S.papers

MAX_LIMIT = 1000

# имя поля API -> выражение
SORT_FIELDS: dict[str, sa.ColumnElement] = {
    "normalized_value": F.c.normalized_num,
    "value": F.c.value_num,
    "confidence": F.c.confidence,
    "source_page": F.c.source_page,
    "metric_name": F.c.metric_name,
    "param": F.c.param,
    "review_status": F.c.review_status,
    "extraction_status": F.c.extraction_status,
    "norm_status": F.c.norm_status,
    "added_by": F.c.added_by,
    "source_kind": F.c.source_kind,
    "row_id": F.c.row_id,
    "row_label": F.c.row_label,
    "publication_id": F.c.publication_id,
    "paper_id": F.c.paper_id,
    "normalized_unit": F.c.normalized_unit,
    "open_issues": F.c.open_issues,
    "id": F.c.id,
    "paper_title": P.c.title,
    "paper_year": P.c.year,
}
SORT_ALIASES = {"year": "paper_year", "title": "paper_title", "fact_id": "id", "page": "source_page",
                "status": "review_status"}

FACET_FIELDS: dict[str, sa.ColumnElement] = {
    "metric_name": F.c.metric_name,
    "param": F.c.param,
    "review_status": F.c.review_status,
    "extraction_status": F.c.extraction_status,
    "norm_status": F.c.norm_status,
    "added_by": F.c.added_by,
    "source_kind": F.c.source_kind,
    "source_page": F.c.source_page,
    "normalized_unit": F.c.normalized_unit,
    "publication_id": F.c.publication_id,
    "paper_id": F.c.paper_id,
    "corpus_id": F.c.corpus_id,
    "collection": F.c.collection,
    "quote_found": F.c.quote_found,
    "paper_year": P.c.year,
    "has_issues": sa.case((F.c.open_issues > 0, "true"), else_="false"),
}
FACET_ALIASES = {"year": "paper_year"}


def _bad(msg: str, field: str | None = None) -> AppError:
    return AppError("validation_error", msg, field=field)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _num(v: Decimal | float | int | None) -> float | None:
    return None if v is None else float(v)


def filter_conditions(flt: FactFilter) -> list[sa.ColumnElement]:
    conds: list[sa.ColumnElement] = [F.c.visible.is_(True)]
    if flt.workspace_id is not None:
        conds.append(F.c.workspace_id == flt.workspace_id)
    if flt.corpus_id is not None:
        conds.append(F.c.corpus_id == flt.corpus_id)
    if flt.paper_ids is not None:
        conds.append(F.c.paper_id.in_(flt.paper_ids) if flt.paper_ids else sa.false())
    if flt.collection is not None:
        conds.append(F.c.collection == flt.collection)
    if flt.params is not None:
        conds.append(F.c.param.in_(flt.params) if flt.params else sa.false())
    if flt.metric_names is not None:
        conds.append(F.c.metric_name.in_(flt.metric_names) if flt.metric_names else sa.false())
    if flt.review_status is not None:
        vals = [getattr(s, "value", s) for s in flt.review_status]
        conds.append(F.c.review_status.in_(vals) if vals else sa.false())
    if flt.extraction_status is not None:
        conds.append(F.c.extraction_status.in_(flt.extraction_status) if flt.extraction_status else sa.false())
    if flt.has_open_issues is True:
        conds.append(F.c.open_issues > 0)
    elif flt.has_open_issues is False:
        conds.append(F.c.open_issues == 0)
    if flt.value_min is not None:
        conds.append(F.c.value_num >= _num(flt.value_min))
    if flt.value_max is not None:
        conds.append(F.c.value_num <= _num(flt.value_max))
    if flt.min_confidence is not None:
        conds.append(F.c.confidence >= float(flt.min_confidence))
    if flt.max_confidence is not None:
        conds.append(F.c.confidence <= float(flt.max_confidence))
    if flt.text:
        pat = f"%{_like_escape(flt.text.lower())}%"
        conds.append(sa.or_(*(sa.func.lower(col).like(pat, escape="\\") for col in
                              (F.c.source_fragment, F.c.row_label, F.c.raw_value, F.c.metric_name, P.c.title))))
    if not flt.include_duplicates:
        conds.append(F.c.is_dup.is_(False))
        conds.append(F.c.duplicate_of.is_(None))
    return conds


def _base_from():
    return F.join(P, P.c.id == F.c.paper_id)


def _resolve_sort(sort: Sequence[SortKey]) -> list[tuple[str, sa.ColumnElement, str]]:
    out = []
    for k in sort or []:
        name = SORT_ALIASES.get(k.field, k.field)
        col = SORT_FIELDS.get(name)
        if col is None:
            raise _bad(f"Нельзя сортировать по полю «{k.field}»", "sort")
        out.append((name, col, k.dir))
    return out


def _signature(keys: list[tuple[str, sa.ColumnElement, str]]) -> str:
    return sha8([[n, d] for n, _, d in keys])


def _keyset_condition(keys, values: tuple, last_seq: int) -> sa.ColumnElement:
    """Строки строго после курсора в порядке ORDER BY (NULL - в конце каждого ключа)."""
    terms: list[sa.ColumnElement] = []
    eqs: list[sa.ColumnElement] = []
    for (name, col, direction), v in zip(keys, values):
        if v is None:
            eqs.append(col.is_(None))  # среди NULL дальше решают следующие ключи
            continue
        terms.append(sa.and_(*eqs, col.is_(None)))  # NULL идут после любого значения
        terms.append(sa.and_(*eqs, col > v if direction == "asc" else col < v))
        eqs.append(col == v)
    terms.append(sa.and_(*eqs, F.c.seq > last_seq))
    return sa.or_(*terms)


def _uses_papers(keys, flt: FactFilter) -> bool:
    return bool(flt.text) or any(col.table is P for _, col, _ in keys)


def run_query(conn: sa.Connection, flt: FactFilter, sort: Sequence[SortKey], limit: int,
              cursor: str | None, row_builder) -> tuple[list[Any], int, str | None]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise _bad("limit должен быть положительным целым", "limit")
    limit = min(limit, MAX_LIMIT)
    keys = _resolve_sort(sort)
    sig = _signature(keys)
    conds = filter_conditions(flt)
    join = _uses_papers(keys, flt)
    src = _base_from() if join else F
    total = conn.execute(sa.select(sa.func.count()).select_from(src).where(*conds)).scalar_one()
    where = list(conds)
    if cursor:
        vals = decode_cursor(cursor)
        if len(vals) != len(keys) + 2 or vals[0] != sig or not isinstance(vals[-1], int)                 or isinstance(vals[-1], bool):
            raise _bad("Курсор не подходит к этому запросу", "cursor")
        where.append(_keyset_condition(keys, vals[1:-1], vals[-1]))
    order = []
    for _, col, d in keys:
        order.append(col.is_(None).asc())
        order.append(col.asc() if d == "asc" else col.desc())
    order.append(F.c.seq.asc())
    sort_cols = [col.label(f"_k{i}") for i, (_, col, _) in enumerate(keys)]
    q = (sa.select(F, *sort_cols).select_from(src).where(*where).order_by(*order).limit(limit + 1))
    rows = conn.execute(q).all()
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]._mapping
        next_cursor = encode_cursor([sig, *[last[f"_k{i}"] for i in range(len(keys))], last["seq"]])
    pids = list({r._mapping["paper_id"] for r in rows})
    papers = {}
    if pids:
        papers = {r.id: (r.title, r.year) for r in conn.execute(
            sa.select(P.c.id, P.c.title, P.c.year).where(P.c.id.in_(pids)))}
    return [row_builder(r, *papers.get(r._mapping["paper_id"], (None, None))) for r in rows], int(total), next_cursor


def _facet_key(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def run_facets(conn: sa.Connection, flt: FactFilter, fields: Sequence[str]) -> dict[str, dict[str, int]]:
    resolved = []
    for f in fields:
        name = FACET_ALIASES.get(f, f)
        col = FACET_FIELDS.get(name)
        if col is None:
            raise _bad(f"Нет фасета «{f}»", "facets")
        resolved.append((f, col))
    conds = filter_conditions(flt)
    out: dict[str, dict[str, int]] = {}
    for f, col in resolved:
        expr = col.label("v")
        src = _base_from() if (flt.text or getattr(col, "table", None) is P) else F
        q = (sa.select(expr, sa.func.count()).select_from(src).where(*conds).group_by(col)
             .order_by(sa.func.count().desc()))
        bucket: dict[str, int] = {}
        for v, n in conn.execute(q):
            k = _facet_key(v)
            bucket[k] = bucket.get(k, 0) + int(n)
        out[f] = bucket
    return out


def display(value: Decimal | None, unit: str | None) -> str | None:
    if value is None:
        return None
    s = format_cell("results", "metric_value", value)
    return f"{s} {unit}" if unit else s
