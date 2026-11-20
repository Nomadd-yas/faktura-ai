"""Агрегаты: счётчики статьи, кеш corpus_stats (REQ-101), сводка корпуса spec/13 §13.8.

Схема кеша двухуровневая:
  * у статьи - счётчики `c_facts`, `c_open_issues`, `c_open_by_rule`; пересчитываются по одной статье
    (дёшево) в той же транзакции, что меняет её факты или замечания;
  * строка `corpus_stats` корпуса - сумма счётчиков его статей + счётчики файлов + прогоны;
    пересчитывается из `papers`/`corpus_files`/`runs` без чтения фактов.
`overview` читает только `corpus_stats`. `corpus_summary` считает «с нуля» по papers/facts/issues -
им же проверяется согласованность кеша.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from ..domain.entities import CorpusFileStatus, PaperStats, RunStatus
from ..domain.errors import AppError
from ..domain.profile import BUILTIN_SOLAR_ID, ExtractionProfile, builtin_solar_profile
from ..domain.tables import CHECK_CATALOG
from . import schema as S

if TYPE_CHECKING:  # pragma: no cover
    from .db import UnitOfWork

ACTIVE_RUN = (RunStatus.queued.value, RunStatus.running.value, RunStatus.cancelling.value)
FINAL_RUN = (RunStatus.done.value, RunStatus.done_with_errors.value, RunStatus.cancelled.value,
             RunStatus.failed.value)
TOP_RULES = 5


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")


# --- счётчики статьи --------------------------------------------------------------------------

def refresh_paper_counters(uow: "UnitOfWork", paper_id: str) -> None:
    c = uow.conn
    n_facts = c.execute(sa.select(sa.func.count()).select_from(S.facts)
                        .where(S.facts.c.paper_id == paper_id)).scalar_one()
    by_rule: dict[str, int] = {}
    for name, n in c.execute(
            sa.select(S.issues.c.check_name, sa.func.count())
            .where(S.issues.c.paper_id == paper_id, S.issues.c.status == "open", S.issues.c.stale.is_(False))
            .group_by(S.issues.c.check_name)):
        by_rule[name] = int(n)
    c.execute(S.papers.update().where(S.papers.c.id == paper_id).values(
        c_facts=int(n_facts), c_open_issues=sum(by_rule.values()), c_open_by_rule=by_rule or None))


def recount_fact_open_issues(uow: "UnitOfWork", paper_id: str) -> None:
    """facts.open_issues = число открытых (не stale) замечаний статьи, где record_ids содержит target_id."""
    c = uow.conn
    counts: dict[str, int] = {}
    for (rids,) in c.execute(sa.select(S.issues.c.record_ids).where(
            S.issues.c.paper_id == paper_id, S.issues.c.status == "open", S.issues.c.stale.is_(False))):
        for rid in set(rids or []):
            counts[rid] = counts.get(rid, 0) + 1
    c.execute(S.facts.update().where(S.facts.c.paper_id == paper_id, S.facts.c.open_issues != 0)
              .values(open_issues=0))
    by_n: dict[int, list[str]] = {}
    for rid, n in counts.items():
        by_n.setdefault(n, []).append(rid)
    for n, rids in by_n.items():
        c.execute(S.facts.update().where(S.facts.c.paper_id == paper_id, S.facts.c.target_id.in_(rids))
                  .values(open_issues=n))


# --- кеш корпуса --------------------------------------------------------------------------------

def _run_brief(r) -> dict[str, Any]:
    return {"run_id": r.id, "status": r.status, "finished_at": _iso(r.finished_at)}


def _active_brief(r) -> dict[str, Any]:
    counts = r.counts or {}
    total = int(counts.get("total") or 0)
    finished = sum(int(counts.get(k) or 0) for k in ("done", "failed", "skipped", "cancelled"))
    progress = int(round(100 * finished / total)) if total else 0
    return {"run_id": r.id, "status": r.status, "progress": min(progress, 100), "eta_s": None,
            "paused": bool(r.paused)}


def refresh_corpus_stats(uow: "UnitOfWork", corpus_id: str | None) -> None:
    if not corpus_id:
        return
    c = uow.conn
    corp = c.execute(sa.select(S.corpora).where(S.corpora.c.id == corpus_id)).first()
    if corp is None:
        return
    files = {"total": 0, "done": 0, "failed": 0, "processing": 0}
    for status, n in c.execute(sa.select(S.corpus_files.c.status, sa.func.count())
                               .where(S.corpus_files.c.corpus_id == corpus_id).group_by(S.corpus_files.c.status)):
        n = int(n)
        files["total"] += n
        if status == CorpusFileStatus.done.value:
            files["done"] += n
        elif status == CorpusFileStatus.failed.value:
            files["failed"] += n
        elif status in (CorpusFileStatus.processing.value, CorpusFileStatus.downloading.value):
            files["processing"] += n
    alive = sa.and_(S.papers.c.corpus_id == corpus_id, S.papers.c.deleted_at.is_(None))
    n_papers, n_facts, n_open = c.execute(sa.select(
        sa.func.count(), sa.func.coalesce(sa.func.sum(S.papers.c.c_facts), 0),
        sa.func.coalesce(sa.func.sum(S.papers.c.c_open_issues), 0)).where(alive)).one()
    by_rule: dict[str, int] = {}
    for (br,) in c.execute(sa.select(S.papers.c.c_open_by_rule).where(alive, S.papers.c.c_open_issues > 0)):
        for k, v in (br or {}).items():
            by_rule[k] = by_rule.get(k, 0) + int(v)
    active = c.execute(sa.select(S.runs).where(S.runs.c.corpus_id == corpus_id, S.runs.c.status.in_(ACTIVE_RUN))
                       .order_by(S.runs.c.seq.desc()).limit(1)).first()
    last = c.execute(sa.select(S.runs).where(S.runs.c.corpus_id == corpus_id, S.runs.c.status.in_(FINAL_RUN))
                     .order_by(S.runs.c.seq.desc()).limit(1)).first()
    data = {"files": files, "papers": int(n_papers), "facts": int(n_facts), "open_issues": int(n_open),
            "open_by_rule": by_rule, "active_run": _active_brief(active) if active else None,
            "last_run": _run_brief(last) if last else None}
    values = dict(corpus_seq=corp.seq, workspace_id=corp.workspace_id, collection=corp.collection, name=corp.name,
                  deleted=corp.deleted_at is not None, data=data, updated_at=uow.now())
    exists = c.execute(sa.select(S.corpus_stats.c.corpus_id).where(S.corpus_stats.c.corpus_id == corpus_id)).first()
    if exists:
        c.execute(S.corpus_stats.update().where(S.corpus_stats.c.corpus_id == corpus_id).values(**values))
    else:
        c.execute(S.corpus_stats.insert().values(corpus_id=corpus_id, **values))


def refresh_paper_and_corpus(uow: "UnitOfWork", paper_id: str) -> None:
    refresh_paper_counters(uow, paper_id)
    corpus_id = uow.conn.execute(sa.select(S.papers.c.corpus_id).where(S.papers.c.id == paper_id)).scalar()
    refresh_corpus_stats(uow, corpus_id)


# --- репозиторий --------------------------------------------------------------------------------

class StatsRepo:
    def __init__(self, uow: "UnitOfWork") -> None:
        self.uow = uow

    @property
    def c(self) -> sa.Connection:
        return self.uow.conn

    def update_paper(self, paper_id: str, stats: PaperStats) -> None:
        """Сохраняет PaperStats статьи и обновляет кеш корпуса в той же транзакции."""
        if not isinstance(stats, PaperStats):
            stats = PaperStats.model_validate(stats)
        res = self.c.execute(S.papers.update().where(S.papers.c.id == paper_id)
                             .values(stats=stats.model_dump(mode="json")))
        if res.rowcount == 0:
            raise AppError("not_found", f"Статья {paper_id} не найдена")
        refresh_paper_and_corpus(self.uow, paper_id)

    def refresh_corpus(self, corpus_id: str) -> None:
        refresh_corpus_stats(self.uow, corpus_id)

    def rebuild_all(self) -> int:
        """Полный пересчёт кеша (после миграций/ручных правок БД). Возвращает число корпусов."""
        ids = [r[0] for r in self.c.execute(sa.select(S.papers.c.id))]
        for pid in ids:
            refresh_paper_counters(self.uow, pid)
        cids = [r[0] for r in self.c.execute(sa.select(S.corpora.c.id))]
        for cid in cids:
            refresh_corpus_stats(self.uow, cid)
        return len(cids)

    def overview(self, workspace_id: str, collection: str | None = None) -> dict:
        """GET /corpus/overview (spec/13 §13.8): только из corpus_stats."""
        q = sa.select(S.corpus_stats).where(S.corpus_stats.c.workspace_id == workspace_id,
                                            S.corpus_stats.c.deleted.is_(False))
        if collection:
            q = q.where(S.corpus_stats.c.collection == collection)
        rows = self.c.execute(q.order_by(S.corpus_stats.c.corpus_seq)).all()
        corpora: list[dict] = []
        totals = {"papers": 0, "facts": 0, "open_issues": 0}
        by_rule: dict[str, int] = {}
        for r in rows:
            d = r.data or {}
            corpora.append({"corpus_id": r.corpus_id, "name": r.name, "collection": r.collection,
                            "files": d.get("files") or {"total": 0, "done": 0, "failed": 0, "processing": 0},
                            "papers": int(d.get("papers") or 0), "facts": int(d.get("facts") or 0),
                            "open_issues": int(d.get("open_issues") or 0), "active_run": d.get("active_run"),
                            "last_run": d.get("last_run")})
            totals["papers"] += int(d.get("papers") or 0)
            totals["facts"] += int(d.get("facts") or 0)
            totals["open_issues"] += int(d.get("open_issues") or 0)
            for k, v in (d.get("open_by_rule") or {}).items():
                by_rule[k] = by_rule.get(k, 0) + int(v)
        top = sorted(by_rule.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_RULES]
        totals["errors_by_rule_top"] = [{"check_name": k, "open": v} for k, v in top]
        return {"corpora": corpora, "totals": totals, "empty": not corpora}

    # --- сводка корпуса (пересчёт с нуля) --------------------------------------------------------

    def _profile(self, profile_id: str | None) -> ExtractionProfile:
        if profile_id:
            row = self.c.execute(sa.select(S.profiles.c.data).where(
                sa.or_(S.profiles.c.id == profile_id, S.profiles.c.alias == profile_id))).first()
            if row is not None:
                try:
                    return ExtractionProfile.model_validate(row[0])
                except Exception:  # noqa: BLE001 - битый профиль не ломает сводку
                    pass
        return builtin_solar_profile()

    def corpus_summary(self, corpus_id: str) -> dict:
        """GET /corpora/{id}/summary (spec/13 §13.8). Считается по papers/facts/issues напрямую."""
        c = self.c
        corp = c.execute(sa.select(S.corpora).where(S.corpora.c.id == corpus_id)).first()
        if corp is None:
            raise AppError("not_found", f"Корпус {corpus_id} не найден")
        F, P, I = S.facts, S.papers, S.issues
        # файлы
        files = {"total": 0, "done": 0, "failed": 0, "duplicates": 0, "processing": 0, "queued": 0}
        for status, n in c.execute(sa.select(S.corpus_files.c.status, sa.func.count())
                                   .where(S.corpus_files.c.corpus_id == corpus_id)
                                   .group_by(S.corpus_files.c.status)):
            n = int(n)
            files["total"] += n
            if status == "done":
                files["done"] += n
            elif status == "failed":
                files["failed"] += n
            elif status == "skipped_duplicate":
                files["duplicates"] += n
            elif status in ("processing", "downloading"):
                files["processing"] += n
            elif status in ("queued", "pending"):
                files["queued"] += n
        errors = [{"code": code, "count": int(n)} for code, n in c.execute(
            sa.select(S.corpus_files.c.error_code, sa.func.count())
            .where(S.corpus_files.c.corpus_id == corpus_id, S.corpus_files.c.error_code.is_not(None))
            .group_by(S.corpus_files.c.error_code).order_by(sa.func.count().desc(), S.corpus_files.c.error_code))]
        # статьи
        alive = sa.and_(P.c.corpus_id == corpus_id, P.c.deleted_at.is_(None))
        open_issue = sa.and_(I.c.status == "open", I.c.stale.is_(False))
        with_issues_q = sa.select(sa.func.count(sa.distinct(I.c.paper_id))).select_from(I.join(P, P.c.id == I.c.paper_id)) \
            .where(alive, open_issue)
        n_total, n_in, n_out, n_dups = c.execute(sa.select(
            sa.func.count(),
            sa.func.coalesce(sa.func.sum(sa.case((P.c.in_scope.is_(True), 1), else_=0)), 0),
            sa.func.coalesce(sa.func.sum(sa.case((P.c.in_scope.is_(False), 1), else_=0)), 0),
            sa.func.coalesce(sa.func.sum(sa.case((P.c.dup_of.is_not(None), 1), else_=0)), 0),
        ).where(alive)).one()
        papers = {"total": int(n_total), "in_scope": int(n_in), "out_of_scope": int(n_out),
                  "possible_duplicates": int(n_dups), "with_issues": int(c.execute(with_issues_q).scalar_one())}
        # факты
        fj = F.join(P, P.c.id == F.c.paper_id)

        def cnt(cond):
            return sa.func.coalesce(sa.func.sum(sa.case((cond, 1), else_=0)), 0)

        row = c.execute(sa.select(
            sa.func.count(), cnt(F.c.review_status == "accepted"), cnt(F.c.review_status == "rejected"),
            cnt(F.c.review_status == "new"), cnt(F.c.added_by == "manual"), cnt(F.c.norm_status == "failed"),
            cnt(F.c.source_kind == "table"), cnt(F.c.source_kind == "text"), cnt(F.c.source_kind == "figure"),
            sa.func.avg(F.c.confidence)).select_from(fj).where(alive)).one()
        mean_conf = None if row[9] is None else round(float(row[9]), 4)
        facts = {"total": int(row[0]), "accepted": int(row[1]), "rejected": int(row[2]), "new": int(row[3]),
                 "manual": int(row[4]), "norm_failed": int(row[5]), "from_table": int(row[6]),
                 "from_text": int(row[7]), "from_figure": int(row[8]), "mean_confidence": mean_conf}
        # замечания
        ij = I.join(P, P.c.id == I.c.paper_id)
        by_rule = []
        n_open_total = 0
        for name, n_all, n_open in c.execute(
                sa.select(I.c.check_name, sa.func.count(), cnt(open_issue)).select_from(ij).where(alive)
                .group_by(I.c.check_name).order_by(I.c.check_name)):
            info = CHECK_CATALOG.get(name)
            by_rule.append({"check_name": name, "title": info.title if info else name,
                            "severity": info.severity if info else None, "open": int(n_open),
                            "total": int(n_all), "checked": None})
            n_open_total += int(n_open)
        by_rule.sort(key=lambda r: (-r["open"], r["check_name"]))
        issues = {"open": n_open_total, "by_rule": by_rule}
        # покрытие полей профиля
        profile = self._profile(corp.profile_id)
        coverage, not_found = [], []
        metric_papers: dict[str, int] = dict(c.execute(
            sa.select(F.c.metric_name, sa.func.count(sa.distinct(F.c.paper_id))).select_from(fj)
            .where(alive, F.c.metric_name.is_not(None), F.c.review_status != "rejected")
            .group_by(F.c.metric_name)).all())
        field_papers: dict[str, int] = dict(c.execute(
            sa.select(F.c.target_field, sa.func.count(sa.distinct(F.c.paper_id))).select_from(fj)
            .where(alive, F.c.target_field.is_not(None), F.c.review_status != "rejected")
            .group_by(F.c.target_field)).all())
        for f in profile.fields:
            if not f.enabled:
                continue
            if f.table == "results" and f.column is None:
                n = int(metric_papers.get(f.key, 0))
            else:
                n = int(field_papers.get(f.column or f.key, 0))
            item = {"field": f.key, "label": f.label or f.key, "papers_with": n,
                    "papers_total": papers["total"],
                    "pct": round(100.0 * n / papers["total"], 1) if papers["total"] else None}
            coverage.append(item)
            if papers["total"] and n == 0:
                not_found.append({"field": f.key, "label": f.label or f.key, "papers_with": 0})
        last = c.execute(sa.select(S.runs).where(S.runs.c.corpus_id == corpus_id, S.runs.c.status.in_(FINAL_RUN))
                         .order_by(S.runs.c.seq.desc()).limit(1)).first()
        model_version = c.execute(sa.select(P.c.model_version).where(alive, P.c.model_version.is_not(None))
                                  .order_by(P.c.processed_at.desc()).limit(1)).scalar()
        return {"corpus_id": corpus_id, "collection": corp.collection,
                "profile": {"id": corp.profile_id or BUILTIN_SOLAR_ID, "version": profile.version},
                "model_version": model_version, "last_run": _run_brief(last) if last else None,
                "files": files, "papers": papers, "facts": facts, "issues": issues, "coverage": coverage,
                "not_found": not_found, "errors": errors}
