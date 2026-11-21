"""Инструменты агента (§12.2): только чтение, область - ровно этот разбор (её подставляет бэк, модель не задаёт).

Строки берутся из `RunCtx` (отклонённые исключены, исправленные значения подставлены). Числовые фильтры и агрегаты
идут по действующему значению (`value` = normalized_value, иначе metric_value). Ответ инструмента - небольшой JSON.

Идентификаторы: строка results = `fact` `<pid>/<result_id>`; замечание = `<pid>/<issue_id>`; другая строка = `<pid>/<id>`.
"""
from __future__ import annotations

import importlib
import json
import statistics
from typing import Any

from ..ask import solar
from ..ask.builder import to_float
from ..ask.context import PaperData, RunCtx
from ..domain.registry import get_domain
from . import MAX_ROWS

OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "contains", "is_null")
FNS = ("count", "mean", "median", "min", "max", "sum")
MAX_CHARS = 14000  # потолок размера результата одного инструмента
GROUP_IDS = 50
# raw_value не входит: у исправленной вручную строки он хранит старое значение
FACT_NUM_FIELDS = ("value", "normalized_value", "metric_value", "spread_value", "baseline_value",
                   "n_devices", "sample_size", "metric_k", "time_point")
SOLAR_COND = ("measurement_type", "spectrum", "light_intensity", "intensity_unit", "scan_direction", "scan_rate",
              "mask_used", "temperature")
DEFAULT_COLS = {
    "solar": ["fact_id", "metric_name", "value", "unit", "aggregation", "device_label", "material_class", "source_page"],
    "rag": ["fact_id", "metric_name", "value", "unit", "metric_variant", "metric_k", "config_name", "dataset_name",
            "source_page"],
}
LONG = {"source_fragment": 240, "description": 240, "note": 120, "authors": 80, "title": 140}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else to_float(v)


def _eq(a: Any, b: Any) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= 1e-9 * max(1.0, abs(fa), abs(fb))
    return str(a).strip().lower() == str(b).strip().lower()


def _match(v: Any, op: str, val: Any) -> bool:
    s = "" if v is None else str(v)
    if op == "is_null":
        return (s.strip() == "") == (True if val is None else bool(val))
    if op == "contains":
        return str(val).lower() in s.lower()
    if op == "in":
        return any(_eq(s, x) for x in (val if isinstance(val, list) else [val]))
    if op == "=":
        return _eq(s, val)
    if op == "!=":
        return not _eq(s, val)
    a, b = _num(v), _num(val)
    if a is None or b is None:
        return False
    return a > b if op == ">" else a >= b if op == ">=" else a < b if op == "<" else a <= b


class ToolError(Exception):
    """Ошибка аргументов: идёт модели как {error}, чтобы она исправила вызов."""


def _short(v: Any, key: str) -> Any:
    n = LONG.get(key)
    return v[:n] + "…" if n and isinstance(v, str) and len(v) > n else v


def _r(v: float) -> float:
    return round(v, 6)


class Toolbox:
    def __init__(self, ctx: RunCtx, services: Any = None, corpus_id: str | None = None) -> None:
        self.ctx, self.services, self.corpus_id = ctx, services, corpus_id
        self.spec = get_domain(ctx.domain)
        self.aggs: dict[str, dict] = {}
        self.chunks: dict[str, dict] = {}
        self.compared: list[dict] = []
        self.calls: list[dict] = []
        self._facts: dict[str, tuple[PaperData, dict]] | None = None

    # --- доступ к строкам ------------------------------------------------------------------------------------

    @property
    def facts(self) -> dict[str, tuple[PaperData, dict]]:
        """Все действующие строки results области: `pid/result_id` -> (статья, строка)."""
        if self._facts is None:
            self._facts = {f"{p.pid}/{r.get('result_id', '')}": (p, r) for p in self.ctx.papers for r in p.results}
        return self._facts

    def _visible(self, table: str) -> list[tuple[PaperData, dict]]:
        out = []
        for p in self.ctx.papers:
            if table != "publications" and not (p.in_scope and (p.has_data or p.issues)):
                continue
            for r in p.tables.get(table) or []:
                if table == "results" and (r.get("duplicate_of")
                                           or r.get("extraction_status") not in ("extracted", "", None)):
                    continue
                out.append((p, r))
        return out

    def _view(self, p: PaperData, table: str, r: dict) -> dict:
        """Строка таблицы + виртуальные колонки (pid, id, fact_id, value, unit, material_class, ...)."""
        key = self.spec.keys[table]
        d = {k: v for k, v in r.items() if k != "publication_id"}
        d["pid"] = p.pid
        d["id"] = f"{p.pid}/{r.get(key, '')}"
        if table == "results":
            v = solar.value_of(r)
            d["fact_id"] = d["id"]
            d["value"] = v if v is not None else ""
            d["unit"] = solar.unit_of(r)
            if self.ctx.domain == "solar":
                d["material_class"] = solar.class_of(p, r)
                d["device_label"] = solar.device_name(p, r)
            else:
                from ..ask import rag  # noqa: PLC0415

                d["dataset_name"], d["task_type"] = rag.dataset_name(p, r), rag.task_name(p, r)
                d["config_name"] = rag.config_name(p, r)
                c = p.configs.get(r.get("config_id") or "") or {}
                for k in ("retriever_model", "generator_model", "embedding_model", "reranker_model"):
                    d[k] = c.get(k, "")
        return d

    def virtual(self, table: str) -> list[str]:
        v = ["pid", "id"]
        if table == "results":
            v += ["fact_id", "value", "unit"]
            v += ["material_class", "device_label"] if self.ctx.domain == "solar" else [
                "dataset_name", "task_type", "config_name", "retriever_model", "generator_model", "embedding_model",
                "reranker_model"]
        return v

    def columns(self, table: str) -> list[str]:
        if table not in self.spec.tables:
            raise ToolError(f"unknown table {table!r}; tables: {list(self.spec.tables)}")
        return [c for c in self.spec.tables[table] if c != "publication_id"] + self.virtual(table)

    def _select(self, table: str, where: list | None, pids: list | None = None) -> list[dict]:
        cols = set(self.columns(table))
        conds = []
        for w in where or []:
            if not isinstance(w, dict):
                raise ToolError("where: list of {col, op, value}")
            col = "pid" if w.get("col") == "publication_id" else w.get("col")
            op = w.get("op", "=")
            if col not in cols:
                raise ToolError(f"unknown column {col!r} of {table}; columns: {sorted(cols)}")
            if op not in OPS:
                raise ToolError(f"unknown op {op!r}; ops: {list(OPS)}")
            conds.append((col, op, w.get("value")))
        known = {p.pid for p in self.ctx.papers}
        bad = [x for x in pids or [] if x not in known]
        if bad:
            raise ToolError(f"papers not in this run: {bad}")
        out = []
        for p, r in self._visible(table):
            if pids and p.pid not in pids:
                continue
            v = self._view(p, table, r)
            if all(_match(v.get(c, ""), op, val) for c, op, val in conds):
                out.append(v)
        return out

    def _compact(self, table: str, row: dict, cols: list[str] | None) -> dict:
        if cols is None:
            if table == "results":
                cols = DEFAULT_COLS[self.ctx.domain]
            elif table == "issues":
                cols = ["id", "check_name", "description", "source_page", "record_ids"]
            else:
                cols = ["id"] + [c for c in self.spec.tables[table] if c not in ("publication_id", "note")][:9]
        elif "id" not in cols:
            cols = ["id"] + cols
        out = {c: _short(row[c], c) for c in cols if c in row and row[c] not in ("", None)}
        if table == "results" and row.get("result_origin") == "cited":  # значение, процитированное из другой работы
            out["result_origin"] = "cited"
        return out

    def _pack(self, rows: list[dict], table: str, cols, limit: int, extra: dict | None = None) -> dict:
        shown: list[dict] = []
        size = 200
        for r in rows[:limit]:
            c = self._compact(table, r, cols)
            size += len(json.dumps(c, ensure_ascii=False))
            if size > MAX_CHARS:
                break
            shown.append(c)
        out = {"table": table, "total": len(rows), "returned": len(shown), "rows": shown, **(extra or {})}
        if len(shown) < len(rows):
            out["truncated"] = True
        return out

    @staticmethod
    def _limit(v: Any, default: int = 20, cap: int = MAX_ROWS) -> int:
        try:
            return max(1, min(int(v), cap))
        except (TypeError, ValueError):
            return default

    def _cols(self, table: str, cols: Any) -> list[str] | None:
        if not cols:
            return None
        known = set(self.columns(table))
        bad = [c for c in cols if c not in known]
        if bad:
            raise ToolError(f"unknown columns {bad}; columns: {sorted(known)}")
        return list(cols)

    # --- инструменты -----------------------------------------------------------------------------------------

    def schema(self, **_: Any) -> dict:
        from ..passport.run_passport import _domain_content  # noqa: PLC0415

        ents = {e["id"]: e for e in json.loads(_domain_content(self.ctx.domain)).get("entities") or []}
        tables = []
        for t in self.spec.tables:
            fields = {f["name"]: f for f in (ents.get(t) or {}).get("fields", [])}
            virt = set(self.virtual(t))
            cols = []
            for c in self.columns(t):
                f = fields.get(c) or {}
                item: dict[str, Any] = {"name": c}
                if f.get("values"):
                    item["values"] = f["values"][:30]
                if f.get("description"):
                    item["desc"] = f["description"][:90]
                if c in virt:
                    item["virtual"] = True
                cols.append(item)
            n = sum(len(p.tables.get(t) or []) for p in self.ctx.papers)
            tables.append({"table": t, "key": self.spec.keys[t], "rows": n, "columns": cols})
        metrics = []
        for m, n, k in self.ctx.metric_stats()[:40]:
            units = sorted({solar.unit_of(r) for p in self.ctx.papers for r in p.results if r.get("metric_name") == m})
            metrics.append({"metric": m, "values": n, "papers": k, "units": units})
        return {"domain": self.ctx.domain, "papers": [p.pid for p in self.ctx.papers if p.has_data],
                "tables": tables, "metrics": metrics,
                "notes": ["value/unit = effective normalized value of a results row (human corrections applied)",
                          "paper code is column pid (not publication_id)"]}

    def filter(self, table: str = "results", where: list | None = None, columns: list | None = None,
               order_by: str | None = None, limit: Any = 20, pids: list | None = None, **_: Any) -> dict:
        rows = self._select(table, where, pids)
        cols = self._cols(table, columns)
        if order_by:
            self._sort(rows, str(order_by))
        return self._pack(rows, table, cols, self._limit(limit))

    @staticmethod
    def _sort(rows: list[dict], order: str, direction: str | None = None) -> None:
        parts = order.split()
        col = parts[0].lstrip("-")
        desc = parts[0].startswith("-") or (len(parts) > 1 and parts[1].lower() == "desc") or direction == "desc"
        have = [r for r in rows if r.get(col) not in ("", None)]
        miss = [r for r in rows if r.get(col) in ("", None)]
        if all(_num(r[col]) is not None for r in have):
            have.sort(key=lambda r: _num(r[col]), reverse=desc)
        else:
            have.sort(key=lambda r: str(r[col]).lower(), reverse=desc)
        rows[:] = have + miss

    def top(self, table: str = "results", col: str = "value", n: Any = 5, dir: str = "desc",
            where: list | None = None, columns: list | None = None, **_: Any) -> dict:
        if col not in self.columns(table):
            raise ToolError(f"unknown column {col!r}; columns: {self.columns(table)}")
        cols = self._cols(table, columns)
        rows = [r for r in self._select(table, where) if _num(r.get(col)) is not None]
        self._sort(rows, col, "asc" if dir == "asc" else "desc")
        out = self._pack(rows, table, cols, self._limit(n, 5, 50), {"col": col, "dir": dir})
        out["note"] = "use dir=asc when lower is better (Rs, HI, latency)"
        return out

    def group(self, table: str = "results", by: list | None = None, agg: list | None = None,
              where: list | None = None, limit: Any = 30, **_: Any) -> dict:
        rows = self._select(table, where)
        cols = set(self.columns(table))
        by = list(by or [])
        for c in by:
            if c not in cols:
                raise ToolError(f"unknown column {c!r} in by; columns: {sorted(cols)}")
        specs = []
        for a in agg or []:
            if not isinstance(a, dict) or a.get("fn") not in FNS or (a.get("fn") != "count" and a.get("col") not in cols):
                raise ToolError(f"agg: list of {{col, fn}} with fn in {list(FNS)} and a known col")
            specs.append((a.get("col"), a["fn"]))
        groups: dict[tuple, list[dict]] = {}
        for r in rows:
            groups.setdefault(tuple(str(r.get(c, "")) for c in by), []).append(r)
        ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        out = []
        for key, rs in ordered[:self._limit(limit, 30, 50)]:
            ids = [r["id"] for r in rs]
            g: dict[str, Any] = {**dict(zip(by, key)), "n": len(rs)}
            g["aggs"] = [self._agg("count", None, len(rs), len(rs), ids, table)]
            for col, fn in specs:
                vals = [x for x in (_num(r.get(col)) for r in rs) if x is not None]
                if fn == "count":
                    g["aggs"].append(self._agg("count", col, len([r for r in rs if r.get(col) not in ("", None)]),
                                               len(vals), ids, table))
                elif vals:
                    v = {"mean": statistics.fmean, "median": statistics.median, "min": min, "max": max,
                         "sum": sum}[fn](vals)
                    g["aggs"].append(self._agg(fn, col, _r(v), len(vals), ids, table))
                else:
                    g["aggs"].append({"fn": fn, "col": col, "value": None, "n_values": 0})
            if table == "results":
                units = sorted({r.get("unit", "") for r in rs if r.get("unit")})
                g["units"] = units
                if len(units) > 1 or len({r.get("metric_name") for r in rs}) > 1:
                    g["warning"] = ("mixed metrics or units in this group: aggregate is not meaningful, "
                                    "regroup by metric_name, unit")
            g["ids"] = ids[:GROUP_IDS]
            out.append(g)
        return {"table": table, "by": by, "groups_total": len(groups), "groups": out}

    def _agg(self, fn: str, col: str | None, value: float, n_values: int, ids: list[str], table: str) -> dict:
        aid = f"a{len(self.aggs) + 1}"
        self.aggs[aid] = {"id": aid, "fn": fn, "col": col, "value": value, "n": n_values, "ids": ids, "table": table}
        return {"agg_id": aid, "fn": fn, **({"col": col} if col else {}), "value": value, "n_values": n_values}

    def get_row(self, fact_id: str | None = None, table: str = "results", pid: str | None = None, id: str | None = None,
                **_: Any) -> dict:
        if fact_id and "/" in fact_id and not pid:
            pid, id = fact_id.split("/", 1)
        if not pid or not id:
            raise ToolError("pass fact_id='<pid>/<id>' or table+pid+id")
        key = id.split("/", 1)[-1]
        p = self.ctx.by_pid.get(pid)
        if p is None:
            raise ToolError(f"paper {pid!r} is not in this run")
        if table not in self.spec.tables:
            raise ToolError(f"unknown table {table!r}")
        row = next((r for r in p.tables.get(table) or [] if r.get(self.spec.keys[table]) == key), None)
        if row is None:
            return {"found": False, "note": "no such row in this run (it may be rejected)"}
        v = self._view(p, table, row)
        out = {k: _short(x, k) for k, x in v.items() if x not in ("", None)}
        out["title"] = p.title[:120]
        return {"found": True, "table": table, "row": out}

    def get_issues(self, pids: list | None = None, check: str | None = None, limit: Any = 20, **_: Any) -> dict:
        where = [{"col": "check_name", "op": "contains", "value": check}] if check else None
        rows = self._select("issues", where, pids)
        return self._pack(rows, "issues", None, self._limit(limit, 20, 50))

    def comparable(self, fact_ids: list | None = None, **_: Any) -> dict:
        ids = [str(x) for x in (fact_ids or [])][:60]
        if len(ids) < 2:
            raise ToolError("fact_ids: at least two '<pid>/<result_id>'")
        groups: dict[tuple, dict] = {}
        for fid in ids:
            hit = self.facts.get(fid)
            if hit is None:
                raise ToolError(f"unknown fact {fid!r}")
            key = self._cmp_key(*hit)
            g = groups.setdefault(tuple(sorted(key.items())), {"key": key, "fact_ids": []})
            g["fact_ids"].append(fid)
        gl = list(groups.values())
        reasons = []
        if len(gl) > 1:
            for f in sorted({k for g in gl for k in g["key"]}):
                if len({g["key"].get(f, "") for g in gl}) > 1:
                    reasons.append(f)
        metrics = {self.facts[i][1].get("metric_name") for i in ids}
        self.compared.append({"pids": sorted({i.split("/", 1)[0] for i in ids}),
                              "metric": next(iter(metrics)) if len(metrics) == 1 else "", "comparable": len(gl) == 1})
        return {"comparable": len(gl) == 1, "groups": gl, "differs_in": reasons,
                "hint": "values of different groups must not be compared directly" if len(gl) > 1 else "same conditions"}

    def _cmp_key(self, p: PaperData, r: dict) -> dict:
        unit = solar.unit_of(r)
        if self.ctx.domain == "rag":
            from ..ask import rag  # noqa: PLC0415

            ds = p.datasets.get(r.get("dataset_id") or "") or {}
            return {"task": rag.task_name(p, r), "dataset": rag.dataset_name(p, r), "split": ds.get("data_split", ""),
                    "metric": r.get("metric_name", ""), "variant": r.get("metric_variant", ""),
                    "k": r.get("metric_k", ""), "unit": unit, "origin": r.get("result_origin") or "primary"}
        m = p.index("measurements", "measurement_id").get(r.get("measurement_id") or "") or {}
        key = {"metric": r.get("metric_name", ""), "unit": unit, "aggregation": r.get("aggregation", ""),
               "origin": r.get("result_origin") or "primary"}
        key.update({c: m.get(c, "") for c in SOLAR_COND if m.get(c)})
        return key

    # --- текст статей (модуль textindex подключается лениво) ---------------------------------------------------

    @staticmethod
    def _textindex():
        try:
            return importlib.import_module("faktura.textindex")
        except ImportError:
            return None

    def search_text(self, query: str = "", pids: list | None = None, k: Any = 6, **_: Any) -> dict:
        q = (query or "").strip()
        if not q:
            raise ToolError("query: non-empty English search phrase")
        known = {p.pid for p in self.ctx.papers}
        bad = [x for x in pids or [] if x not in known]
        if bad:
            raise ToolError(f"papers not in this run: {bad}")
        mod = self._textindex()
        if mod is None or self.services is None:
            return {"results": [], "note": "text index is not available"}
        scope = list(pids) if pids else sorted(known)
        try:
            with self.services.db.uow() as uow:
                hits = mod.search_text(uow, self.corpus_id, q, pids=scope, k=self._limit(k, 6, 10)) or []
        except Exception as e:  # noqa: BLE001 - поиск необязателен: агент продолжит по таблицам
            return {"results": [], "note": f"text search failed: {type(e).__name__}"}
        out = []
        for h in hits:
            if h.get("pid") not in known:
                continue
            cid = str(h.get("chunk_id") or f"{h.get('pid')}:{h.get('page')}")
            self.chunks[cid] = {"pid": h["pid"], "page": h.get("page"), "text": h.get("text") or ""}
            out.append({"chunk_id": cid, "pid": h["pid"], "page": h.get("page"),
                        "score": round(float(h.get("score") or 0), 3), "text": (h.get("text") or "")[:700]})
        return {"results": out}

    def read_page(self, pid: str = "", page: Any = None, **_: Any) -> dict:
        if pid not in self.ctx.by_pid:
            raise ToolError(f"paper {pid!r} is not in this run")
        try:
            pg = int(page)
        except (TypeError, ValueError):
            raise ToolError("page: integer") from None
        mod = self._textindex()
        if mod is None or self.services is None:
            return {"found": False, "note": "text index is not available"}
        try:
            with self.services.db.uow() as uow:
                r = mod.read_page(uow, self.corpus_id, pid, pg)
        except Exception as e:  # noqa: BLE001
            return {"found": False, "note": f"read failed: {type(e).__name__}"}
        if not r:
            return {"found": False}
        cid = f"page:{pid}/{pg}"
        self.chunks[cid] = {"pid": pid, "page": pg, "text": r.get("text") or ""}
        return {"found": True, "chunk_id": cid, "pid": pid, "page": pg, "pages": r.get("pages"),
                "text": (r.get("text") or "")[:6000]}

    # --- вызов --------------------------------------------------------------------------------------------------

    def call(self, name: str, args: dict) -> tuple[dict, str]:
        """(результат, текст шага для фронта). Ошибки аргументов возвращаются как {error}."""
        fn = getattr(self, name, None) if name in TOOL_NAMES else None
        step = describe(name, args)
        if fn is None:
            res: dict = {"error": f"unknown tool {name!r}; tools: {list(TOOL_NAMES)}"}
        else:
            try:
                res = fn(**args)
            except ToolError as e:
                res = {"error": str(e)}
            except TypeError as e:
                res = {"error": f"bad arguments: {e}"}
        self.calls.append({"tool": name, "args": args, "ok": "error" not in res,
                           "rows": res.get("total", res.get("returned"))})
        return res, step

    @staticmethod
    def dump(res: dict) -> str:
        s = json.dumps(res, ensure_ascii=False, separators=(",", ":"))
        if len(s) > MAX_CHARS + 2000:
            s = json.dumps({"error": "result too large, narrow the query (where/limit/columns)"}, separators=(",", ":"))
        return s


def _where_text(where: Any) -> str:
    return ", ".join(f"{w.get('col')} {w.get('op', '=')} {w.get('value', '')}".strip()
                     for w in where or [] if isinstance(w, dict))


def describe(name: str, a: dict) -> str:
    """Шаг для фронта: что делает агент (по-русски)."""
    g = a.get
    if name in ("filter", "top"):
        w = _where_text(g("where"))
        return f"Запрос к таблице {g('table') or 'results'}" + (f": {w}" if w else "")
    if name == "group":
        w = _where_text(g("where"))
        return (f"Группирую таблицу {g('table') or 'results'} по {', '.join(g('by') or []) or 'всем строкам'}"
                + (f" ({w})" if w else ""))
    if name == "get_row":
        return f"Открываю строку {g('fact_id') or g('id') or ''}".strip()
    if name == "get_issues":
        return "Смотрю замечания" + (f" ({', '.join(g('pids') or [])})" if g("pids") else "")
    if name == "comparable":
        return f"Проверяю сопоставимость {len(g('fact_ids') or [])} значений"
    if name == "search_text":
        return f"Ищу в тексте статей: {g('query') or ''}"
    if name == "read_page":
        return f"Читаю страницу {g('page')} статьи {g('pid')}"
    if name == "schema":
        return "Смотрю структуру таблиц"
    return f"Вызов {name}"


def _where_schema() -> dict:
    return {"type": "array", "description": "conditions combined with AND",
            "items": {"type": "object", "properties": {"col": {"type": "string"}, "op": {"type": "string", "enum": list(OPS)},
                                                       "value": {}}, "required": ["col", "op"]}}


def _fn(name: str, desc: str, props: dict, required: list[str] | None = None) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props,
                                                            "required": required or []}}}


_STR_LIST = {"type": "array", "items": {"type": "string"}}
TABLE = {"type": "string", "description": "table name from schema"}
TOOL_SPECS = [
    _fn("schema", "Tables, columns (with enum values), metrics with units, paper codes of this run. Call first if unsure.", {}),
    _fn("filter", f"Rows of a table matching where (limit<={MAX_ROWS}). results rows carry fact_id, value, unit, source_page.",
        {"table": TABLE, "where": _where_schema(), "columns": _STR_LIST,
         "order_by": {"type": "string", "description": "column, prefix - for descending"},
         "limit": {"type": "integer"}, "pids": _STR_LIST}, ["table"]),
    _fn("group", "Group rows and aggregate; every aggregate has an agg_id you can cite with [[agg:ID]]. "
        "Group by metric_name+unit before averaging.",
        {"table": TABLE, "where": _where_schema(), "by": _STR_LIST,
         "agg": {"type": "array", "items": {"type": "object", "properties": {
             "col": {"type": "string"}, "fn": {"type": "string", "enum": list(FNS)}}, "required": ["fn"]}},
         "limit": {"type": "integer"}}, ["table"]),
    _fn("top", "Best rows by a numeric column (dir=desc largest first; use asc when lower is better).",
        {"table": TABLE, "col": {"type": "string"}, "n": {"type": "integer"},
         "dir": {"type": "string", "enum": ["desc", "asc"]}, "where": _where_schema(), "columns": _STR_LIST},
        ["table", "col"]),
    _fn("get_row", "One row with its source page and fragment. Pass fact_id '<pid>/<result_id>' or table+pid+id.",
        {"fact_id": {"type": "string"}, "table": TABLE, "pid": {"type": "string"}, "id": {"type": "string"}}),
    _fn("get_issues", "Open remarks/issues of papers (consistency checks, contradictions).",
        {"pids": _STR_LIST, "check": {"type": "string"}, "limit": {"type": "integer"}}),
    _fn("comparable", "Group results by comparability (same metric, unit, aggregation, conditions / task, dataset, k). "
        "Call before comparing values of different papers.",
        {"fact_ids": {"type": "array", "items": {"type": "string"}, "description": "'<pid>/<result_id>' items"}},
        ["fact_ids"]),
    _fn("search_text", "Search the paper texts (English). Returns chunks with chunk_id, pid, page; "
        "cite with [[chunk:ID|short verbatim quote]].",
        {"query": {"type": "string"}, "pids": _STR_LIST, "k": {"type": "integer"}}, ["query"]),
    _fn("read_page", "Full text of one page of a paper; cite with [[chunk:ID|short verbatim quote]].",
        {"pid": {"type": "string"}, "page": {"type": "integer"}}, ["pid", "page"]),
    _fn("clarify", "Use INSTEAD of answering when the question is ambiguous (which metric, which paper): ask one short "
        "question in Russian with 2-4 options. Ends the turn.",
        {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}, "minItems": 2,
                                                      "maxItems": 4}}, ["question", "options"]),
]
TOOL_NAMES = tuple(t["function"]["name"] for t in TOOL_SPECS if t["function"]["name"] != "clarify")
