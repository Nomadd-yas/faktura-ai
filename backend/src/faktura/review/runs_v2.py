"""v2: правки значений, состояния замечаний, журнал и отмена для разборов (спека BACKEND_ONLY §9).

Строки разбора (sub_rows) не меняются. Правки и состояния лежат отдельно (storage.review_tables) и применяются:
* при выгрузке (`export_tables`): rejected-строка не выгружается, убирается из issues.record_ids и duplicate_of
  (первый повтор становится основной записью); исправление -> normalized_value/metric_value = исправленное,
  raw_* из статьи, в note «Исправлено вручную: было X»; замечания в состоянии ok не выгружаются;
* при чтении (`paper_review_view`): карта правок и состояний для фронта.

Автозакрытие: замечание становится fixed (auto), когда все его record_ids исправлены или отклонены, либо когда
пересчёт правил бэка по исправленным значениям его больше не находит. Пересчёт (только solar, где правила бэка
есть) - разница «правила по исходным строкам» против «правила по строкам с правками»: новые находки становятся
дополнительными замечаниями (run_extra_issues, id «V…»), пропавшие - закрываются.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import sqlalchemy as sa

from ..domain.errors import AppError
from ..storage import review_tables as RT

STATUSES = ("new", "accepted", "rejected")
STATES = ("open", "fixed", "ok")
ACTION_PREFIX = "v2."
_TABLES = {"run_reviews": RT.run_reviews, "run_issue_states": RT.run_issue_states,
           "run_extra_issues": RT.run_extra_issues}
_KEYCOLS = {"run_reviews": ("corpus_id", "domain", "publication_id", "result_id"),
            "run_issue_states": ("corpus_id", "domain", "publication_id", "issue_id"),
            "run_extra_issues": ("corpus_id", "domain", "publication_id", "issue_id")}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ids(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(";") if x.strip()]


def parse_value(v: Any) -> str | None:
    """corrected_value -> нормализованная строка числа; None/"" -> None; не число -> 400."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        d = Decimal(str(v).strip().replace(",", "."))
    except InvalidOperation:
        raise AppError("validation_error", "corrected_value: нужно число", field="corrected_value") from None
    if not d.is_finite():
        raise AppError("validation_error", "corrected_value: нужно конечное число", field="corrected_value")
    s = format(d.normalize(), "f")
    return s if "." not in s else s.rstrip("0").rstrip(".") or "0"


# ------------------------------------------------------------------ чтение
def _rows(conn, table: sa.Table, corpus_id: str, domain: str | None = None, pid: str | None = None):
    q = sa.select(table).where(table.c.corpus_id == corpus_id)
    if domain:
        q = q.where(table.c.domain == domain)
    if pid:
        q = q.where(table.c.publication_id == pid)
    return [dict(r._mapping) for r in conn.execute(q)]


def reviews_of(conn, corpus_id: str, domain: str | None = None, pid: str | None = None) -> dict[tuple, dict]:
    return {(r["domain"], r["publication_id"], r["result_id"]): r for r in _rows(conn, RT.run_reviews, corpus_id, domain, pid)}


def states_of(conn, corpus_id: str, domain: str | None = None, pid: str | None = None) -> dict[tuple, dict]:
    return {(r["domain"], r["publication_id"], r["issue_id"]): r
            for r in _rows(conn, RT.run_issue_states, corpus_id, domain, pid)}


def extra_of(conn, corpus_id: str, domain: str | None = None, pid: str | None = None) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for r in _rows(conn, RT.run_extra_issues, corpus_id, domain, pid):
        out.setdefault((r["domain"], r["publication_id"]), []).append(dict(r["data"] or {}))
    return out


# ------------------------------------------------------------------ журнал изменений
def _ser(row: dict | None) -> dict | None:
    if row is None:
        return None
    out = {k: v for k, v in row.items() if k != "id"}
    if isinstance(out.get("at"), datetime):
        out["at"] = out["at"].isoformat()
    return out


def _deser(row: dict) -> dict:
    out = dict(row)
    if isinstance(out.get("at"), str):
        out["at"] = datetime.fromisoformat(out["at"])
    return out


class Tracker:
    """Снимки строк «до» при первом касании ключа и «после» в конце - для журнала и отмены."""

    def __init__(self, conn) -> None:
        self.conn = conn
        self.before: dict[tuple, dict | None] = {}

    def _get(self, tname: str, key: dict) -> dict | None:
        t = _TABLES[tname]
        q = sa.select(t).where(*[t.c[k] == key[k] for k in _KEYCOLS[tname]])
        r = self.conn.execute(q).first()
        return None if r is None else dict(r._mapping)

    def put(self, tname: str, key: dict, row: dict | None) -> None:
        """Записать строку (upsert) или удалить (row=None)."""
        k = (tname,) + tuple(key[c] for c in _KEYCOLS[tname])
        if k not in self.before:
            self.before[k] = _ser(self._get(tname, key))
        t = _TABLES[tname]
        where = [t.c[c] == key[c] for c in _KEYCOLS[tname]]
        self.conn.execute(t.delete().where(*where))
        if row is not None:
            self.conn.execute(t.insert().values(**{**row, **{c: key[c] for c in _KEYCOLS[tname]}}))

    def changes(self) -> list[dict]:
        out = []
        for k, before in self.before.items():
            tname = k[0]
            key = dict(zip(_KEYCOLS[tname], k[1:]))
            after = _ser(self._get(tname, key))
            if after != before:
                out.append({"t": tname, "key": key, "before": before, "after": after})
        return out


def apply_changes(conn, changes: list[dict], *, use: str) -> None:
    """use='before' - отмена, use='after' - повтор."""
    tr = Tracker(conn)
    for ch in (reversed(changes) if use == "before" else changes):
        row = ch.get(use)
        tr.put(ch["t"], ch["key"], None if row is None else _deser(row))


# ------------------------------------------------------------------ правила
def _issue_key(check_name: str | None, rids: Iterable[str]) -> tuple:
    return ((check_name or "").strip(), tuple(sorted(set(rids))))


def _effective_results(results: list[dict], revs: dict[str, dict]) -> list[dict]:
    """rejected убраны (и из duplicate_of; первый повтор - основная запись), исправления подставлены."""
    rejected = {rid for rid, r in revs.items() if r["status"] == "rejected"}
    promoted: dict[str, str] = {}
    out = []
    for row in results:
        rid = row.get("result_id", "")
        if rid in rejected:
            continue
        row = dict(row)
        main = row.get("duplicate_of") or ""
        if main in rejected:
            if main not in promoted:
                promoted[main] = rid
                row["duplicate_of"] = ""
            else:
                row["duplicate_of"] = promoted[main]
        rv = revs.get(rid)
        if rv and rv.get("corrected_value") is not None:
            was = row.get("normalized_value") or row.get("metric_value") or row.get("raw_value") or ""
            row["normalized_value"] = rv["corrected_value"]
            if "metric_value" in row:
                row["metric_value"] = rv["corrected_value"]
            note = (row.get("note") or "").strip()
            mark = f"Исправлено вручную: было {was}"
            row["note"] = f"{note} {mark}".strip() if note else mark
        out.append(row)
    return out


def _backend_rule_keys(tables: dict[str, list[dict]]) -> dict[tuple, Any]:
    """Находки правил бэка (solar) по таблицам одной статьи: {ключ: IssueRecord}."""
    from ..checks.rules import run_backend_rules
    from ..domain.io import bundles_from_tables
    from ..domain.profile import builtin_solar_profile
    bundles = bundles_from_tables(tables)
    if not bundles:
        return {}
    prof = builtin_solar_profile()
    out = {}
    for rec in run_backend_rules(bundles[0], prof.rules):
        out[_issue_key(rec.check_name, rec.record_ids)] = rec
    return out


def _extra_id(key: tuple) -> str:
    return "V" + hashlib.sha1(repr(key).encode()).hexdigest()[:8].upper()


def recompute(conn, tracker: Tracker, corpus_id: str, domain: str, pid: str, tables: dict[str, list[dict]],
              who: str | None) -> None:
    """Пересчёт дополнительных замечаний (solar) и автозакрытия для статьи; изменения - через tracker."""
    revs = {k[2]: v for k, v in reviews_of(conn, corpus_id, domain, pid).items()}
    closed_keys: set[tuple] = set()
    if domain == "solar" and tables.get("results"):
        try:
            orig = _backend_rule_keys(tables)
            eff_tables = {**tables, "results": _effective_results(tables["results"], revs)}
            new = _backend_rule_keys(eff_tables) if revs else orig
        except Exception:  # noqa: BLE001 - пересчёт не должен ломать правку
            orig, new = {}, {}
        closed_keys = set(orig) - set(new)
        want = {}
        for key in set(new) - set(orig):
            rec = new[key]
            want[_extra_id(key)] = {"publication_id": pid, "issue_id": _extra_id(key),
                                    "record_ids": ";".join(rec.record_ids), "check_name": rec.check_name,
                                    "description": rec.description or "", "source_page": str(rec.source_page or ""),
                                    "source_fragment": rec.source_fragment or ""}
        have = {d.get("issue_id"): d for d in extra_of(conn, corpus_id, domain, pid).get((domain, pid), [])}
        for iid in set(have) - set(want):
            tracker.put("run_extra_issues", {"corpus_id": corpus_id, "domain": domain, "publication_id": pid,
                                             "issue_id": iid}, None)
        for iid, data in want.items():
            if have.get(iid) != data:
                tracker.put("run_extra_issues", {"corpus_id": corpus_id, "domain": domain, "publication_id": pid,
                                                 "issue_id": iid}, {"data": data})
    states = {k[2]: v for k, v in states_of(conn, corpus_id, domain, pid).items()}
    issues = list(tables.get("issues") or []) + extra_of(conn, corpus_id, domain, pid).get((domain, pid), [])
    for iss in issues:
        iid = iss.get("issue_id") or ""
        rids = _ids(iss.get("record_ids"))
        done = bool(rids) and all(r in revs and (revs[r]["status"] == "rejected"
                                                 or revs[r].get("corrected_value") is not None) for r in rids)
        should = done or _issue_key(iss.get("check_name"), rids) in closed_keys
        st = states.get(iid)
        key = {"corpus_id": corpus_id, "domain": domain, "publication_id": pid, "issue_id": iid}
        if should and (st is None or st["state"] == "open"):
            tracker.put("run_issue_states", key, {"state": "fixed", "auto": True, "at": _now(), "who": who})
        elif not should and st is not None and st["state"] == "fixed" and st["auto"]:
            tracker.put("run_issue_states", key, None)


# ------------------------------------------------------------------ представления
def effective_issues(tables: dict[str, list[dict]], extra: list[dict], states: dict[str, dict],
                     revs: dict[str, dict]) -> list[dict]:
    """Замечания статьи с полем state (служебное, не колонка CSV); record_ids без отклонённых строк."""
    rejected = {rid for rid, r in revs.items() if r["status"] == "rejected"}
    out = []
    for iss in list(tables.get("issues") or []) + list(extra):
        iid = iss.get("issue_id") or ""
        st = states.get(iid)
        row = dict(iss)
        row["record_ids"] = ";".join(r for r in _ids(iss.get("record_ids")) if r not in rejected)
        row["_state"] = st["state"] if st else "open"
        row["_auto"] = bool(st and st["auto"])
        out.append(row)
    return out


def export_tables(tables: dict[str, list[dict]], revs: dict[str, dict], states: dict[str, dict],
                  extra: list[dict]) -> dict[str, list[dict]]:
    """Таблицы статьи для выгрузки с учётом правок (колонки шаблона сохраняются)."""
    out = dict(tables)
    if "results" in tables:
        out["results"] = _effective_results(tables["results"], revs)
    if "issues" in tables or extra:
        cols = list((tables.get("issues") or [{}])[0].keys()) or None
        rows = []
        for iss in effective_issues(tables, extra, states, revs):
            if iss.pop("_state") == "ok":
                continue
            iss.pop("_auto", None)
            rows.append({c: iss.get(c, "") for c in cols} if cols else iss)
        out["issues"] = rows
    return out


def export_paper_tables(uow, corpus_id: str, domain: str, paper_id: str) -> dict[str, list[dict]]:
    """Таблицы статьи разбора для выгрузки с правками v2 (без правок - как в sub_rows)."""
    tables = uow.sub.paper_tables(paper_id, domain)
    pub = (tables.get("publications") or [{}])[0].get("publication_id") or ""
    if not pub:
        return tables
    revs = {k[2]: v for k, v in reviews_of(uow.conn, corpus_id, domain, pub).items()}
    states = {k[2]: v for k, v in states_of(uow.conn, corpus_id, domain, pub).items()}
    extra = extra_of(uow.conn, corpus_id, domain, pub).get((domain, pub), [])
    if not (revs or states or extra):
        return tables
    return export_tables(tables, revs, states, extra)


def open_issue_count(tables: dict[str, list[dict]], extra: list[dict], states: dict[str, dict],
                     revs: dict[str, dict]) -> int:
    return sum(1 for i in effective_issues(tables, extra, states, revs) if i["_state"] == "open")


def review_out(r: dict | None, rid: str) -> dict:
    if r is None:
        return {"result_id": rid, "status": "new", "corrected_value": None, "at": None, "who": None}
    return {"result_id": rid, "status": r["status"], "corrected_value": r.get("corrected_value"),
            "at": r["at"].isoformat() if isinstance(r.get("at"), datetime) else r.get("at"),
            "who": r.get("who_name") or r.get("who")}
