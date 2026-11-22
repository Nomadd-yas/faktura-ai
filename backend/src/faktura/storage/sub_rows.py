"""Строки таблиц сдачи всех областей в одной таблице `sub_rows` (docs/design/DOMAINS.md §2)."""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import sqlalchemy as sa

from ..domain.registry import get_domain
from . import schema as S
from .cursor import decode_cursor, encode_cursor
from .repos import _Repo, _bad

__all__ = ["SubRowsRepo", "CHUNK", "SORTS"]

CHUNK = 1000
# сортировка results: seq/publication/metric по возрастанию - keyset; value/page и desc - со смещением.
_KEYSET: dict[str, tuple[str, ...]] = {"seq": (), "publication": ("publication_id",),
                                       "metric": ("metric_name", "publication_id")}
_NUMERIC = {"value": "num_value", "page": "source_page"}
SORTS = (*_KEYSET, *_NUMERIC)


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _num(v: str) -> float | None:
    try:
        f = float(v.strip().replace(",", ".")) if v and v.strip() else None
    except ValueError:
        return None
    return f if f is not None and math.isfinite(f) else None


def _page(v: str) -> int | None:
    f = _num(v)
    return int(f) if f is not None and f == int(f) else None


def _offset(cursor: str | None, sig: str) -> int:
    if not cursor:
        return 0
    cur = decode_cursor(cursor)
    if len(cur) != 3 or cur[0] != "py" or cur[1] != sig or not isinstance(cur[2], int) or cur[2] < 0:
        raise _bad("Некорректный курсор", "cursor")
    return cur[2]


class SubRowsRepo(_Repo):
    """Запись - в транзакции сохранения статьи; чтение - интерфейс бывшего SubTablesRepo плюс domain."""

    def replace(self, paper_id: str, corpus_id: str | None, domain: str,
                tables: Mapping[str, Sequence[Mapping[str, Any]]],
                quote_found: Mapping[str, bool | None] | None = None,
                issue_quote_found: Mapping[str, bool | None] | None = None) -> None:
        """Удаляет строки статьи и вставляет новые пачками. quote_found: result_id -> проверка цитаты;
        issue_quote_found: issue_id -> проверка цитаты замечания."""
        spec = get_domain(domain)
        t = S.sub_rows
        self.c.execute(t.delete().where(t.c.paper_id == paper_id))
        quote_found = quote_found or {}
        issue_quote_found = issue_quote_found or {}
        batch: list[dict[str, Any]] = []  # пачка не больше CHUNK строк: список всех ~28k строк (D014) не строится
        for name, cols in spec.tables.items():
            key = spec.keys.get(name)
            for i, r in enumerate(tables.get(name) or ()):
                data = {k: _s(r.get(k)) for k in cols}
                batch.append({
                    "paper_id": paper_id, "corpus_id": corpus_id or "", "domain": domain,
                    "publication_id": data.get("publication_id", ""), "table_name": name, "position": i,
                    "row_id": data.get(key) if key else None,
                    "metric_name": data.get("metric_name") if name == "results" else None,
                    "check_name": data.get("check_name") if name == "issues" else None,
                    "num_value": _num(data.get("metric_value", "")) if name == "results" else None,
                    "source_page": _page(data.get("source_page", "")) if name in ("results", "issues") else None,
                    "quote_found": (quote_found if name == "results" else issue_quote_found).get(data.get(key, ""))
                    if name in ("results", "issues") and key else None,
                    "data": data,
                })
                if len(batch) >= CHUNK:
                    self.c.execute(t.insert(), batch)
                    batch = []
        if batch:
            self.c.execute(t.insert(), batch)

    def paper_tables(self, paper_id: str, domain: str = "solar") -> dict[str, list[dict[str, str]]]:
        """Все таблицы статьи области (ключ - имя таблицы шаблона), строки в порядке position."""
        spec = get_domain(domain)
        t = S.sub_rows
        out: dict[str, list[dict[str, str]]] = {n: [] for n in spec.tables}
        q = sa.select(t.c.table_name, t.c.data).where(t.c.paper_id == paper_id).order_by(
            t.c.table_name, t.c.position, t.c.id)
        for name, data in self.c.execute(q):
            if name in out:
                out[name].append({k: _s((data or {}).get(k)) for k in spec.tables[name]})
        return out

    def paper_table_page(self, paper_id: str, domain: str, table: str, limit: int = 100,
                         cursor: str | None = None) -> tuple[list[dict[str, str]], int, str | None]:
        """Страница строк одной таблицы статьи в порядке position: (rows, total, next_cursor). Курсор - keyset по id."""
        spec = get_domain(domain)
        if table not in spec.tables:
            raise _bad(f"Нет таблицы {table}", "tables")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise _bad("limit: положительное целое", "limit")
        t = S.sub_rows
        where = [t.c.paper_id == paper_id, t.c.table_name == table]
        total = self.c.execute(sa.select(sa.func.count()).select_from(t).where(*where)).scalar() or 0
        q_where = list(where)
        if cursor:
            cur = decode_cursor(cursor)
            if len(cur) != 3 or cur[0] != "pt" or cur[1] != table or not isinstance(cur[2], int):
                raise _bad("Некорректный курсор", "cursor")
            q_where.append(t.c.id > cur[2])
        rows = self.c.execute(sa.select(t.c.id, t.c.data).where(*q_where).order_by(t.c.id).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        nxt = encode_cursor(["pt", table, rows[-1][0]]) if more and rows else None
        cols = spec.tables[table]
        return [{k: _s((d or {}).get(k)) for k in cols} for _, d in rows], total, nxt

    def publication_titles(self, corpus_id: str, pids: Sequence[str] | None = None) -> dict[str, str]:
        """publication_id -> title из строк publications корпуса."""
        t = S.sub_rows
        q = sa.select(t.c.publication_id, t.c.data).where(t.c.corpus_id == corpus_id, t.c.table_name == "publications")
        if pids is not None:
            q = q.where(t.c.publication_id.in_(list(pids)))
        return {p: _s((d or {}).get("title")) for p, d in self.c.execute(q)}

    def quotes_for(self, corpus_id: str, table: str, pairs: Sequence[tuple[str, str]]) -> dict[tuple[str, str], bool | None]:
        """(publication_id, row_id) -> quote_found для заданных строк таблицы (results | issues)."""
        if not pairs:
            return {}
        t = S.sub_rows
        pids = {p for p, _ in pairs}
        ids = {r for _, r in pairs}
        q = sa.select(t.c.publication_id, t.c.row_id, t.c.quote_found).where(
            t.c.corpus_id == corpus_id, t.c.table_name == table, t.c.publication_id.in_(list(pids)),
            t.c.row_id.in_(list(ids)))
        want = set(pairs)
        return {(p, r): qf for p, r, qf in self.c.execute(q) if (p, r) in want}

    def issue_links(self, corpus_id: str, pids: Sequence[str]) -> dict[tuple[str, str], list[str]]:
        """(publication_id, result_id) -> issue_id замечаний, ссылающихся на строку (issues.record_ids)."""
        t = S.sub_rows
        out: dict[tuple[str, str], list[str]] = {}
        for pid, rid, d in self.c.execute(sa.select(t.c.publication_id, t.c.row_id, t.c.data).where(
                t.c.corpus_id == corpus_id, t.c.table_name == "issues", t.c.publication_id.in_(list(pids)))):
            for rec in [x.strip() for x in _s((d or {}).get("record_ids")).split(";") if x.strip()]:
                out.setdefault((pid, rec), []).append(rid or "")
        return out

    def paper_quotes(self, paper_id: str) -> dict[str, bool | None]:
        """result_id -> quote_found, сохранённый при записи (None - не проверялось или старые данные)."""
        t = S.sub_rows
        q = sa.select(t.c.row_id, t.c.quote_found).where(t.c.paper_id == paper_id, t.c.table_name == "results")
        return {rid: qf for rid, qf in self.c.execute(q) if rid}

    def paper_summary(self, paper_id: str) -> dict[str, Any]:
        """{table: число строк, ..., "metrics": [{metric_name, n}]}."""
        t = S.sub_rows
        out: dict[str, Any] = {}
        for name, n in self.c.execute(sa.select(t.c.table_name, sa.func.count()).where(t.c.paper_id == paper_id)
                                      .group_by(t.c.table_name)):
            out[name] = n
        n_ = sa.func.count().label("n")
        out["metrics"] = [{"metric_name": m, "n": n} for m, n in self.c.execute(
            sa.select(t.c.metric_name, n_).where(t.c.paper_id == paper_id, t.c.table_name == "results",
                                                 t.c.metric_name.is_not(None), t.c.metric_name != "")
            .group_by(t.c.metric_name).order_by(sa.desc("n"), t.c.metric_name))]
        return out

    # --- чтение для API
    def _where(self, corpus_id: str, table: str) -> list:
        t = S.sub_rows
        ok = sa.select(S.papers.c.id).where(S.papers.c.deleted_at.is_(None))
        return [t.c.corpus_id == corpus_id, t.c.table_name == table, t.c.paper_id.in_(ok)]

    def _total(self, where: list) -> int:
        return self.c.execute(sa.select(sa.func.count()).select_from(S.sub_rows).where(*where)).scalar() or 0

    def list_results(self, corpus_id: str, pids: Sequence[str] | None = None, metrics: Sequence[str] | None = None,
                     device_id: str | None = None, measurement_id: str | None = None,
                     material_id: str | None = None, exclude_duplicates: bool = False, sort: str = "seq",
                     limit: int = 50, cursor: str | None = None, *, dir: str = "asc", domain: str = "solar",
                     filters: Mapping[str, str] | None = None) -> tuple[list[dict[str, str]], int, str | None]:
        """Строки results корпуса: (rows, total, next_cursor).

        sort: seq | publication | metric | value | page. seq/publication/metric по возрастанию - keyset-курсор;
        value/page и dir=desc - курсор ["py", "sort:dir", смещение], пустые значения value/page - в конце.
        filters - равенство по колонке шаблона (data[col]); device_id/measurement_id/material_id - то же.
        """
        if sort not in SORTS:
            raise _bad(f"sort: одно из {', '.join(SORTS)}", "sort")
        if dir not in ("asc", "desc"):
            raise _bad("dir: asc или desc", "dir")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise _bad("limit: положительное целое", "limit")
        t = S.sub_rows
        where = self._where(corpus_id, "results")
        if pids:
            where.append(t.c.publication_id.in_(list(pids)))
        if metrics:
            where.append(t.c.metric_name.in_(list(metrics)))
        flt = {k: v for k, v in {"device_id": device_id, "measurement_id": measurement_id,
                                 "material_id": material_id, **(filters or {})}.items() if v}
        for col, v in flt.items():
            where.append(t.c.data[col].as_string() == v)
        if exclude_duplicates:
            dup = t.c.data["duplicate_of"].as_string()
            where.append(sa.or_(dup.is_(None), dup == ""))
        if sort in _KEYSET and dir == "asc":
            return self._keyset(domain, "results", where, _KEYSET[sort], sort, limit, cursor)
        sig = f"{sort}:{dir}"
        offset = _offset(cursor, sig)
        desc = dir == "desc"
        if sort in _NUMERIC:
            col = t.c[_NUMERIC[sort]]
            order = [sa.case((col.is_(None), 1), else_=0), col.desc() if desc else col.asc(), t.c.id]
        else:
            order = [t.c[k].desc() if desc else t.c[k].asc() for k in _KEYSET[sort]] + [t.c.id]
        cols = get_domain(domain).tables["results"]
        rows = self.c.execute(sa.select(t.c.data).where(*where).order_by(*order).limit(limit + 1)
                              .offset(offset)).all()
        nxt = encode_cursor(["py", sig, offset + limit]) if len(rows) > limit else None
        return [{k: _s((r[0] or {}).get(k)) for k in cols} for r in rows[:limit]], self._total(where), nxt

    def _keyset(self, domain: str, table: str, where: list, order: tuple[str, ...], sort_sig: str, limit: int,
                cursor: str | None) -> tuple[list[dict[str, str]], int, str | None]:
        t = S.sub_rows
        keys = [t.c[k] for k in order] + [t.c.id]
        q_where = list(where)
        if cursor:
            cur = decode_cursor(cursor)
            if len(cur) != len(keys) + 1 or cur[0] != sort_sig:
                raise _bad("Некорректный курсор", "cursor")
            vals = list(cur[1:])
            q_where.append(sa.tuple_(*keys) > sa.tuple_(*vals) if len(keys) > 1 else keys[0] > vals[0])
        cols = get_domain(domain).tables[table]
        rows = self.c.execute(sa.select(t.c.data, *keys).where(*q_where).order_by(*keys).limit(limit + 1)).all()
        more = len(rows) > limit
        rows = rows[:limit]
        nxt = encode_cursor([sort_sig, *rows[-1][1:]]) if more and rows else None
        return [{k: _s((r[0] or {}).get(k)) for k in cols} for r in rows], self._total(where), nxt

    def list_issues(self, corpus_id: str, pids: Sequence[str] | None = None,
                    check_names: Sequence[str] | None = None, limit: int = 50, cursor: str | None = None,
                    *, domain: str = "solar") -> tuple[list[dict[str, str]], int, str | None]:
        """Строки issues корпуса: (rows, total, next_cursor), порядок вставки."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise _bad("limit: положительное целое", "limit")
        t = S.sub_rows
        where = self._where(corpus_id, "issues")
        if pids:
            where.append(t.c.publication_id.in_(list(pids)))
        if check_names:
            where.append(t.c.check_name.in_(list(check_names)))
        return self._keyset(domain, "issues", where, (), "seq", limit, cursor)

    # --- счётчики для разбора (api/corpus/views.py)
    def counts_by_paper(self, corpus_id: str, table: str) -> dict[str, int]:
        t = S.sub_rows
        return {k: n for k, n in self.c.execute(
            sa.select(t.c.paper_id, sa.func.count()).where(t.c.corpus_id == corpus_id, t.c.table_name == table)
            .group_by(t.c.paper_id))}

    def counts_by_paper_tables(self, corpus_id: str, tables: Sequence[str]) -> dict[str, dict[str, int]]:
        """{table: {paper_id: число строк}} одним запросом (покрывающий индекс ix_sub_rows_corpus_paper_metric)."""
        t = S.sub_rows
        out: dict[str, dict[str, int]] = {n: {} for n in tables}
        for p, name, n in self.c.execute(
                sa.select(t.c.paper_id, t.c.table_name, sa.func.count())
                .where(t.c.corpus_id == corpus_id, t.c.table_name.in_(list(tables)))
                .group_by(t.c.paper_id, t.c.table_name)):
            out[name][p] = n
        return out

    def tables_of_corpus(self, corpus_id: str, domain: str, names: Sequence[str]) -> dict[str, dict[str, list[dict[str, str]]]]:
        """paper_id -> {table: строки по position} только для перечисленных (небольших) таблиц корпуса: один запрос
        вместо paper_tables на каждую статью."""
        spec = get_domain(domain)
        t = S.sub_rows
        out: dict[str, dict[str, list[dict[str, str]]]] = {}
        q = sa.select(t.c.paper_id, t.c.table_name, t.c.data).where(
            t.c.corpus_id == corpus_id, t.c.table_name.in_(list(names))).order_by(
            t.c.paper_id, t.c.table_name, t.c.position, t.c.id)
        for p, name, data in self.c.execute(q):
            if name in spec.tables:
                tabs = out.setdefault(p, {n: [] for n in names})
                tabs[name].append({k: _s((data or {}).get(k)) for k in spec.tables[name]})
        return out

    def metric_counts(self, corpus_id: str) -> list[tuple[str, str, int]]:
        """(paper_id, metric_name, число results) по корпусу."""
        t = S.sub_rows
        return [(p, m or "", n) for p, m, n in self.c.execute(
            sa.select(t.c.paper_id, t.c.metric_name, sa.func.count())
            .where(t.c.corpus_id == corpus_id, t.c.table_name == "results")
            .group_by(t.c.paper_id, t.c.metric_name))]

    def issue_counts_by_check(self, corpus_id: str) -> list[tuple[str, int]]:
        t = S.sub_rows
        n = sa.func.count().label("n")
        return [(k or "", c) for k, c in self.c.execute(
            sa.select(t.c.check_name, n).where(t.c.corpus_id == corpus_id, t.c.table_name == "issues")
            .group_by(t.c.check_name).order_by(sa.desc("n"), t.c.check_name))]

    def publication_scopes(self, corpus_id: str) -> dict[str, tuple[str, str]]:
        """paper_id -> (in_scope, exclusion_reason) из строк publications."""
        t = S.sub_rows
        return {p: (_s((d or {}).get("in_scope")), _s((d or {}).get("exclusion_reason")))
                for p, d in self.c.execute(sa.select(t.c.paper_id, t.c.data)
                                           .where(t.c.corpus_id == corpus_id, t.c.table_name == "publications"))}

    def publication_titles(self, corpus_id: str) -> dict[str, str]:
        """paper_id -> publications.title (непустые) - для поля title у файлов разбора."""
        t = S.sub_rows
        out: dict[str, str] = {}
        for p, d in self.c.execute(sa.select(t.c.paper_id, t.c.data)
                                   .where(t.c.corpus_id == corpus_id, t.c.table_name == "publications")):
            title = " ".join(_s((d or {}).get("title")).split())
            if title:
                out[p] = title
        return out

    def issue_ids_by_paper(self, corpus_id: str, paper_ids: Sequence[str]) -> dict[str, set[str]]:
        """paper_id -> issue_id замечаний статей (для счётчиков открытых замечаний v2)."""
        t = S.sub_rows
        out: dict[str, set[str]] = {}
        if not paper_ids:
            return out
        q = sa.select(t.c.paper_id, t.c.data).where(t.c.corpus_id == corpus_id, t.c.table_name == "issues",
                                                    t.c.paper_id.in_(list(paper_ids)))
        for p, d in self.c.execute(q):
            out.setdefault(p, set()).add(_s((d or {}).get("issue_id")))
        return out

    def results_of_metrics(self, corpus_id: str, metrics: Sequence[str], domain: str = "solar"
                           ) -> dict[str, list[dict[str, str]]]:
        """paper_id -> строки results указанных метрик (для «лучшего значения»), порядок вставки."""
        t = S.sub_rows
        cols = get_domain(domain).tables["results"]
        where = self._where(corpus_id, "results") + [t.c.metric_name.in_(list(metrics))]
        out: dict[str, list[dict[str, str]]] = {}
        for p, d in self.c.execute(sa.select(t.c.paper_id, t.c.data).where(*where).order_by(t.c.id)):
            out.setdefault(p, []).append({k: _s((d or {}).get(k)) for k in cols})
        return out
