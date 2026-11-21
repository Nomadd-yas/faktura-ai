"""Репозитории (план 5.5). Работают на соединении UnitOfWork (SQLAlchemy Core), транзакцией управляет UoW.

Ошибки: не найдено -> AppError("not_found"); неверные данные -> AppError("validation_error") или
ValueError/pydantic.ValidationError; неизвестное поле в update -> TypeError.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

import sqlalchemy as sa

from ..domain.entities import (Action, Anchor, AnchorDraft, Corpus, CorpusFile, CorpusFileStatus, DupKind, Export,
                               Fact, FactDraft, FactFilter, FactRow, Issue, IssuesDiff, Page, Paper, PaperStats,
                               Run, RunCounts, RunOutput, RunStatus, SaveResult, SortKey, User)
from ..domain.errors import AppError
from ..domain.ids import new_id, stable_id
from ..domain.io import bundles_from_tables, read_tables
from ..domain.profile import BUILTIN_SOLAR_ALIAS, BUILTIN_SOLAR_ID, ExtractionProfile, builtin_solar_profile
from ..domain.records import (ROW_MODELS, AddedBy, IssueRecord, IssueStatus, PaperBundle, Publication, ReviewStatus,
                              TraceStep)
from ..domain.tables import ID_COLUMN, TABLES
from . import query as Q
from . import schema as S
from ._types import (DEFAULT_WORKSPACE_NAME, RESTORE_WINDOW_DAYS, GoldSet, RefreshSession, StoredUser, Synonym,
                     Workspace)
from .cursor import decode_cursor, encode_cursor
from .codec import dec, dec_to_float, dec_to_str, dump_model, enc, load_model, str_to_dec, to_decimal
from .stats import (ACTIVE_RUN, StatsRepo, recount_fact_open_issues, refresh_corpus_stats, refresh_paper_and_corpus,
                    refresh_paper_counters)

if TYPE_CHECKING:  # pragma: no cover
    from .db import UnitOfWork

__all__ = ["WorkspacesRepo", "ProfilesRepo", "PapersRepo", "FactsRepo", "AnchorsRepo", "IssuesRepo", "CorporaRepo",
           "FilesRepo", "RunsRepo", "ActionsRepo", "StatsRepo", "GoldRepo", "IdempotencyRepo", "UsersRepo",
           "SessionsRepo", "LoginAttemptsRepo", "ExportsRepo", "SynonymsRepo"]


# --- общее -------------------------------------------------------------------------------------

def _not_found(what: str, id_: str) -> AppError:
    return AppError("not_found", f"{what} {id_} не найден(а)")


def _bad(msg: str, field: str | None = None) -> AppError:
    return AppError("validation_error", msg, field=field)


def _check_fields(fields: dict, allowed: Iterable[str], what: str) -> None:
    extra = set(fields) - set(allowed)
    if extra:
        raise TypeError(f"{what}: неизвестные поля {sorted(extra)}")


def _val(v: Any) -> Any:
    return getattr(v, "value", v)


class _Repo:
    def __init__(self, uow: "UnitOfWork") -> None:
        self.uow = uow

    @property
    def c(self) -> sa.Connection:
        return self.uow.conn

    def now(self) -> datetime:
        return self.uow.now()


# --- рабочие пространства ------------------------------------------------------------------------

class WorkspacesRepo(_Repo):
    def get(self, workspace_id: str) -> Workspace | None:
        r = self.c.execute(sa.select(S.workspaces).where(S.workspaces.c.id == workspace_id)).first()
        return None if r is None else Workspace(id=r.id, name=r.name, created_at=r.created_at)

    def ensure(self, workspace_id: str, name: str | None = None) -> Workspace:
        if not workspace_id:
            raise _bad("workspace_id обязателен", "workspace_id")
        w = self.get(workspace_id)
        if w is not None:
            return w
        self.c.execute(S.workspaces.insert().values(id=workspace_id, name=name or DEFAULT_WORKSPACE_NAME,
                                                    created_at=self.now()))
        return self.get(workspace_id)  # type: ignore[return-value]

    def list(self) -> list[Workspace]:
        return [Workspace(id=r.id, name=r.name, created_at=r.created_at)
                for r in self.c.execute(sa.select(S.workspaces).order_by(S.workspaces.c.seq))]


# --- профили -----------------------------------------------------------------------------------

class ProfilesRepo(_Repo):
    def ensure_builtin(self) -> None:
        exists = self.c.execute(sa.select(S.profiles.c.id).where(S.profiles.c.id == BUILTIN_SOLAR_ID)).first()
        if exists:
            return
        p = builtin_solar_profile()
        self.c.execute(S.profiles.insert().values(
            id=p.profile_id, workspace_id=None, alias=BUILTIN_SOLAR_ALIAS, version=p.version, name=p.name,
            collection=p.collection, builtin=True, data=p.model_dump(mode="json"), created_at=self.now()))

    def get(self, profile_id: str) -> ExtractionProfile | None:
        """По id или алиасу (tz-solar-v1)."""
        r = self.c.execute(sa.select(S.profiles.c.data).where(
            sa.or_(S.profiles.c.id == profile_id, S.profiles.c.alias == profile_id))
            .order_by(S.profiles.c.seq).limit(1)).first()
        return None if r is None else ExtractionProfile.model_validate(r[0])

    def list(self, workspace_id: str | None = None, collection: str | None = None) -> list[ExtractionProfile]:
        q = sa.select(S.profiles.c.data).where(sa.or_(S.profiles.c.workspace_id.is_(None),
                                                      S.profiles.c.workspace_id == workspace_id))
        if collection:
            q = q.where(S.profiles.c.collection == collection)
        return [ExtractionProfile.model_validate(r[0]) for r in self.c.execute(q.order_by(S.profiles.c.seq))]

    def save(self, profile: ExtractionProfile, *, workspace_id: str | None, created_by: str | None = None,
             alias: str | None = None) -> ExtractionProfile:
        """Создаёт профиль или сохраняет новую версию (version + 1). Встроенный профиль не меняется."""
        row = self.c.execute(sa.select(S.profiles).where(S.profiles.c.id == profile.profile_id)).first()
        if row is not None and row.builtin:
            raise AppError("forbidden", "Встроенный профиль не редактируется")
        if row is None:
            prof = profile.model_copy(update={"builtin": False})
            self.c.execute(S.profiles.insert().values(
                id=prof.profile_id, workspace_id=workspace_id, alias=alias, version=prof.version, name=prof.name,
                collection=prof.collection, builtin=False, data=prof.model_dump(mode="json"), created_by=created_by,
                created_at=self.now()))
            return prof
        prof = profile.model_copy(update={"builtin": False, "version": int(row.version) + 1})
        self.c.execute(S.profiles.update().where(S.profiles.c.id == prof.profile_id).values(
            version=prof.version, name=prof.name, collection=prof.collection, data=prof.model_dump(mode="json"),
            **({"alias": alias} if alias is not None else {})))
        return prof


# --- корпуса -----------------------------------------------------------------------------------

_CORPUS_CREATE = set(Corpus.model_fields) - {"id", "created_at", "deleted_at"}
_CORPUS_UPDATE = {"name", "collection", "kind", "profile_id", "project_id", "deleted_at", "pinned"}


def _corpus(r) -> Corpus:
    return Corpus(id=r.id, workspace_id=r.workspace_id, name=r.name, collection=r.collection, kind=r.kind,
                  profile_id=r.profile_id, project_id=r.project_id, created_by=r.created_by,
                  created_at=r.created_at, deleted_at=r.deleted_at, pinned=bool(r.pinned))


def _sync_corpus_visibility(uow: "UnitOfWork", corpus_id: str) -> None:
    """facts.visible = статья и корпус не удалены (для всех фактов корпуса)."""
    c = uow.conn
    deleted = c.execute(sa.select(S.corpora.c.deleted_at).where(S.corpora.c.id == corpus_id)).scalar()
    if deleted is not None:
        c.execute(S.facts.update().where(S.facts.c.corpus_id == corpus_id).values(visible=False))
        return
    alive = sa.select(S.papers.c.id).where(S.papers.c.corpus_id == corpus_id, S.papers.c.deleted_at.is_(None))
    c.execute(S.facts.update().where(S.facts.c.corpus_id == corpus_id, S.facts.c.paper_id.in_(alive))
              .values(visible=True))
    c.execute(S.facts.update().where(S.facts.c.corpus_id == corpus_id, S.facts.c.paper_id.not_in(alive))
              .values(visible=False))


def _restore_check(deleted_at: datetime | None, now: datetime, what: str) -> None:
    if deleted_at is not None and now - deleted_at > timedelta(days=RESTORE_WINDOW_DAYS):
        raise AppError("gone", f"{what} удалён(а) более {RESTORE_WINDOW_DAYS} суток назад")


class CorporaRepo(_Repo):
    def create(self, **fields: Any) -> Corpus:
        _check_fields(fields, _CORPUS_CREATE, "corpora.create")
        cid = new_id("crp")
        m = Corpus(id=cid, created_at=self.now(), **fields)
        WorkspacesRepo(self.uow).ensure(m.workspace_id)
        self.c.execute(S.corpora.insert().values(
            id=m.id, workspace_id=m.workspace_id, name=m.name, collection=m.collection, kind=m.kind,
            profile_id=m.profile_id, project_id=m.project_id, created_by=m.created_by, created_at=m.created_at))
        refresh_corpus_stats(self.uow, cid)
        return self.get(cid)  # type: ignore[return-value]

    def _row(self, corpus_id: str):
        return self.c.execute(sa.select(S.corpora).where(S.corpora.c.id == corpus_id)).first()

    def get(self, corpus_id: str) -> Corpus | None:
        r = self._row(corpus_id)
        return None if r is None else _corpus(r)

    def list(self, workspace_id: str, *, include_deleted: bool = False, collection: str | None = None,
             kind: str | None = None) -> list[Corpus]:
        q = sa.select(S.corpora).where(S.corpora.c.workspace_id == workspace_id)
        if not include_deleted:
            q = q.where(S.corpora.c.deleted_at.is_(None))
        if collection:
            q = q.where(S.corpora.c.collection == collection)
        if kind:
            q = q.where(S.corpora.c.kind == kind)
        return [_corpus(r) for r in self.c.execute(q.order_by(S.corpora.c.seq))]

    def update(self, corpus_id: str, **fields: Any) -> Corpus:
        _check_fields(fields, _CORPUS_UPDATE, "corpora.update")
        cur = self.get(corpus_id)
        if cur is None:
            raise _not_found("Корпус", corpus_id)
        m = Corpus.model_validate({**cur.model_dump(), **fields})
        values = {k: _val(getattr(m, k)) for k in fields}
        if values:
            self.c.execute(S.corpora.update().where(S.corpora.c.id == corpus_id).values(**values))
        if "deleted_at" in fields:
            _sync_corpus_visibility(self.uow, corpus_id)
        refresh_corpus_stats(self.uow, corpus_id)
        return self.get(corpus_id)  # type: ignore[return-value]

    def soft_delete(self, corpus_id: str) -> Corpus:
        cur = self.get(corpus_id)
        if cur is None:
            raise _not_found("Корпус", corpus_id)
        if cur.deleted_at is not None:
            return cur
        return self.update(corpus_id, deleted_at=self.now())

    def restore(self, corpus_id: str) -> Corpus:
        cur = self.get(corpus_id)
        if cur is None:
            raise _not_found("Корпус", corpus_id)
        if cur.deleted_at is None:
            return cur
        _restore_check(cur.deleted_at, self.now(), "Корпус")
        return self.update(corpus_id, deleted_at=None)

    def purge_expired(self) -> int:
        """Окончательно удаляет корпуса, удалённые больше 30 суток назад. Возвращает их число."""
        border = self.now() - timedelta(days=RESTORE_WINDOW_DAYS)
        ids = [r[0] for r in self.c.execute(sa.select(S.corpora.c.id).where(S.corpora.c.deleted_at < border))]
        for cid in ids:
            pids = sa.select(S.papers.c.id).where(S.papers.c.corpus_id == cid)
            for t in (S.facts, S.anchors, S.issues, S.tz_records):
                self.c.execute(t.delete().where(t.c.paper_id.in_(pids)))
            self.c.execute(S.papers.delete().where(S.papers.c.corpus_id == cid))
            for t in (S.corpus_files, S.runs, S.corpus_stats):
                self.c.execute(t.delete().where(t.c.corpus_id == cid))
            self.c.execute(S.corpora.delete().where(S.corpora.c.id == cid))
        return len(ids)


# --- скорость этапов -----------------------------------------------------------------------------

SPEED_WINDOW = 50  # скользящее среднее по последним N файлам
SPEED_KEEP = 200  # столько последних замеров хранится на (область, этап)


class StageSpeedsRepo(_Repo):
    def add(self, domain: str, stage: str, spp: float) -> None:
        if not (spp > 0) or spp != spp or spp == float("inf"):
            return
        t = S.stage_speeds
        self.c.execute(t.insert().values(domain=domain, stage=stage, spp=float(spp), created_at=self.now()))
        old = self.c.execute(sa.select(t.c.seq).where(t.c.domain == domain, t.c.stage == stage)
                             .order_by(t.c.seq.desc()).offset(SPEED_KEEP).limit(1)).first()
        if old is not None:
            self.c.execute(t.delete().where(t.c.domain == domain, t.c.stage == stage, t.c.seq <= old.seq))

    def averages(self, domain: str) -> dict[str, float]:
        """{этап: среднее секунд на страницу по последним SPEED_WINDOW замерам}; этапов без данных нет."""
        t = S.stage_speeds
        out: dict[str, float] = {}
        for stage in [r.stage for r in self.c.execute(sa.select(t.c.stage).where(t.c.domain == domain).distinct())]:
            vals = [float(r.spp) for r in self.c.execute(
                sa.select(t.c.spp).where(t.c.domain == domain, t.c.stage == stage)
                .order_by(t.c.seq.desc()).limit(SPEED_WINDOW))]
            if vals:
                out[stage] = sum(vals) / len(vals)
        return out


# --- файлы корпуса -----------------------------------------------------------------------------

_FILE_CREATE = set(CorpusFile.model_fields) - {"id", "created_at"}
_FILE_UPDATE = _FILE_CREATE - {"corpus_id"}


def _file(r) -> CorpusFile:
    return CorpusFile(id=r.id, corpus_id=r.corpus_id, name=r.name, publication_id=r.publication_id, sha256=r.sha256,
                      size_bytes=r.size_bytes, page_count=r.page_count, status=r.status, error_code=r.error_code,
                      error_message=r.error_message, paper_id=r.paper_id, duplicate_of=r.duplicate_of,
                      dup_kind=r.dup_kind, storage_key=r.storage_key, source=r.source, stage=r.stage,
                      progress=r.progress, is_scan=r.is_scan, created_at=r.created_at,
                      started_at=r.started_at, stage_started_at=r.stage_started_at)


class FilesRepo(_Repo):
    def add(self, **fields: Any) -> CorpusFile:
        _check_fields(fields, _FILE_CREATE, "files.add")
        fid = new_id("fil")
        m = CorpusFile(id=fid, created_at=self.now(), **fields)
        values = {k: _val(getattr(m, k)) for k in CorpusFile.model_fields}
        self.c.execute(S.corpus_files.insert().values(**values))
        refresh_corpus_stats(self.uow, m.corpus_id)
        return self.get(fid)  # type: ignore[return-value]

    def get(self, file_id: str) -> CorpusFile | None:
        r = self.c.execute(sa.select(S.corpus_files).where(S.corpus_files.c.id == file_id)).first()
        return None if r is None else _file(r)

    def list(self, corpus_id: str, *, status: CorpusFileStatus | Sequence[CorpusFileStatus] | None = None,
             ids: Sequence[str] | None = None) -> list[CorpusFile]:
        q = sa.select(S.corpus_files).where(S.corpus_files.c.corpus_id == corpus_id)
        if status is not None:
            if isinstance(status, (list, tuple, set, frozenset)):
                q = q.where(S.corpus_files.c.status.in_([_val(s) for s in status]))
            else:
                q = q.where(S.corpus_files.c.status == _val(status))
        if ids is not None:
            q = q.where(S.corpus_files.c.id.in_(list(ids)))
        return [_file(r) for r in self.c.execute(q.order_by(S.corpus_files.c.seq))]

    def update(self, file_id: str, **fields: Any) -> CorpusFile:
        _check_fields(fields, _FILE_UPDATE, "files.update")
        cur = self.get(file_id)
        if cur is None:
            raise _not_found("Файл", file_id)
        m = CorpusFile.model_validate({**cur.model_dump(), **fields})
        values = {k: _val(getattr(m, k)) for k in fields}
        if values:
            self.c.execute(S.corpus_files.update().where(S.corpus_files.c.id == file_id).values(**values))
        refresh_corpus_stats(self.uow, cur.corpus_id)
        return self.get(file_id)  # type: ignore[return-value]

    def update_status(self, file_id: str, status: CorpusFileStatus, **fields: Any) -> CorpusFile:
        return self.update(file_id, status=status, **fields)

    def find_by_sha(self, workspace_id: str, sha256: str) -> list[CorpusFile]:
        if not sha256:
            return []
        q = (sa.select(S.corpus_files).select_from(S.corpus_files.join(S.corpora,
                                                                       S.corpora.c.id == S.corpus_files.c.corpus_id))
             .where(S.corpora.c.workspace_id == workspace_id, S.corpora.c.deleted_at.is_(None),
                    S.corpus_files.c.sha256 == sha256).order_by(S.corpus_files.c.seq))
        return [_file(r) for r in self.c.execute(q)]

    def find_by_name(self, corpus_id: str, name: str) -> CorpusFile | None:
        r = self.c.execute(sa.select(S.corpus_files).where(S.corpus_files.c.corpus_id == corpus_id,
                                                           S.corpus_files.c.name == name)
                           .order_by(S.corpus_files.c.seq).limit(1)).first()
        return None if r is None else _file(r)

    def counts(self, corpus_id: str) -> dict[str, int]:
        return {s: int(n) for s, n in self.c.execute(
            sa.select(S.corpus_files.c.status, sa.func.count()).where(S.corpus_files.c.corpus_id == corpus_id)
            .group_by(S.corpus_files.c.status))}

    def delete(self, file_id: str) -> None:
        cur = self.get(file_id)
        if cur is None:
            raise _not_found("Файл", file_id)
        self.c.execute(S.corpus_files.delete().where(S.corpus_files.c.id == file_id))
        refresh_corpus_stats(self.uow, cur.corpus_id)


# --- прогоны -----------------------------------------------------------------------------------

_RUN_CREATE = set(Run.model_fields) - {"id", "created_at"}
_RUN_UPDATE = _RUN_CREATE - {"corpus_id"}


def _run(r) -> Run:
    return Run(id=r.id, corpus_id=r.corpus_id, kind=r.kind, status=r.status, profile_id=r.profile_id,
               model_version=r.model_version, concurrency=r.concurrency, keep_review=r.keep_review,
               build_output=r.build_output, file_ids=r.file_ids, counts=RunCounts.model_validate(r.counts or {}),
               paused=r.paused, pause_reason=r.pause_reason,
               output=None if r.output is None else RunOutput.model_validate(r.output), started_by=r.started_by,
               created_at=r.created_at, started_at=r.started_at, finished_at=r.finished_at)


def _run_values(m: Run, keys: Iterable[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        v = getattr(m, k)
        if k == "counts":
            v = v.model_dump(mode="json")
        elif k == "output":
            v = None if v is None else v.model_dump(mode="json")
        elif k == "file_ids":
            v = None if v is None else list(v)
        out[k] = _val(v)
    return out


class RunsRepo(_Repo):
    def create(self, **fields: Any) -> Run:
        _check_fields(fields, _RUN_CREATE, "runs.create")
        rid = new_id("run")
        m = Run(id=rid, created_at=self.now(), **fields)
        self.c.execute(S.runs.insert().values(id=rid, **_run_values(m, set(Run.model_fields) - {"id"})))
        refresh_corpus_stats(self.uow, m.corpus_id)
        return self.get(rid)  # type: ignore[return-value]

    def get(self, run_id: str) -> Run | None:
        r = self.c.execute(sa.select(S.runs).where(S.runs.c.id == run_id)).first()
        return None if r is None else _run(r)

    def update(self, run_id: str, **fields: Any) -> Run:
        _check_fields(fields, _RUN_UPDATE, "runs.update")
        cur = self.get(run_id)
        if cur is None:
            raise _not_found("Прогон", run_id)
        m = Run.model_validate({**cur.model_dump(), **fields})
        if fields:
            self.c.execute(S.runs.update().where(S.runs.c.id == run_id).values(**_run_values(m, fields)))
        refresh_corpus_stats(self.uow, cur.corpus_id)
        return self.get(run_id)  # type: ignore[return-value]

    def active_for_corpus(self, corpus_id: str) -> Run | None:
        r = self.c.execute(sa.select(S.runs).where(S.runs.c.corpus_id == corpus_id, S.runs.c.status.in_(ACTIVE_RUN))
                           .order_by(S.runs.c.seq.desc()).limit(1)).first()
        return None if r is None else _run(r)

    def list(self, corpus_id: str, *, limit: int | None = None) -> list[Run]:
        q = sa.select(S.runs).where(S.runs.c.corpus_id == corpus_id).order_by(S.runs.c.seq.desc())
        if limit:
            q = q.limit(limit)
        return [_run(r) for r in self.c.execute(q)]

    def list_active(self) -> list[Run]:
        """Все незавершённые прогоны (восстановление очереди после перезапуска сервера)."""
        return [_run(r) for r in self.c.execute(sa.select(S.runs).where(S.runs.c.status.in_(ACTIVE_RUN))
                                                .order_by(S.runs.c.seq))]


# --- статьи ------------------------------------------------------------------------------------

_PAPER_CREATE = (set(Paper.model_fields) - {"id", "created_at"}) | {"title_key"}
_PAPER_UPDATE = _PAPER_CREATE - {"workspace_id"}
_PAPER_SIMPLE = ("workspace_id", "corpus_id", "file_id", "publication_id", "collection", "title", "year", "doi",
                 "in_scope", "status", "sha256", "page_count", "needs_ocr", "model_version", "dup_of", "dup_kind",
                 "error_code", "created_at", "processed_at", "deleted_at")


def _norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for p in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if d.startswith(p):
            d = d[len(p):]
    return d.strip() or None


def _paper(r) -> Paper:
    return Paper(id=r.id, workspace_id=r.workspace_id, corpus_id=r.corpus_id, file_id=r.file_id,
                 publication_id=r.publication_id, collection=r.collection, title=r.title, year=r.year, doi=r.doi,
                 in_scope=r.in_scope, status=r.status, sha256=r.sha256, page_count=r.page_count,
                 needs_ocr=r.needs_ocr, model_version=r.model_version, versions=r.versions or {},
                 dup_of=r.dup_of, dup_kind=r.dup_kind, stats=PaperStats.model_validate(r.stats or {}),
                 error_code=r.error_code, created_at=r.created_at, processed_at=r.processed_at,
                 deleted_at=r.deleted_at)


def _paper_values(m: Paper, keys: Iterable[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in keys:
        if k == "stats":
            out["stats"] = m.stats.model_dump(mode="json")
        elif k == "versions":
            out["versions"] = dict(m.versions)
        elif k in _PAPER_SIMPLE:
            out[k] = _val(getattr(m, k))
        if k == "doi":
            out["doi_norm"] = _norm_doi(m.doi)
    return out


def _sync_paper_facts(uow: "UnitOfWork", paper_id: str) -> None:
    """Денормализованные поля фактов статьи: workspace, корпус, коллекция, видимость, дубль."""
    c = uow.conn
    p = c.execute(sa.select(S.papers).where(S.papers.c.id == paper_id)).first()
    if p is None:
        return
    corp_deleted = None
    if p.corpus_id:
        corp_deleted = c.execute(sa.select(S.corpora.c.deleted_at).where(S.corpora.c.id == p.corpus_id)).scalar()
    c.execute(S.facts.update().where(S.facts.c.paper_id == paper_id).values(
        workspace_id=p.workspace_id, corpus_id=p.corpus_id, collection=p.collection,
        publication_id=p.publication_id, visible=p.deleted_at is None and corp_deleted is None,
        is_dup=p.dup_of is not None))


_REC_ORDER = ("publications", "materials", "devices", "layers", "measurements", "results")


def _issue_content(rec: IssueRecord) -> Any:
    """Содержимое замечания для сравнения при пересчёте (без статуса и служебного meta)."""
    return enc(rec.model_dump(exclude={"status", "meta", "issue_id"}))


def _issue_record_json(rec: IssueRecord) -> Any:
    return dump_model(rec)


class PapersRepo(_Repo):
    def create(self, **fields: Any) -> Paper:
        _check_fields(fields, _PAPER_CREATE, "papers.create")
        title_key = fields.pop("title_key", None)
        pid = new_id("pap")
        if fields.get("corpus_id") and "collection" not in fields:
            coll = self.c.execute(sa.select(S.corpora.c.collection)
                                  .where(S.corpora.c.id == fields["corpus_id"])).scalar()
            if coll:
                fields["collection"] = coll
        m = Paper(id=pid, created_at=self.now(), **fields)
        WorkspacesRepo(self.uow).ensure(m.workspace_id)
        values = _paper_values(m, set(Paper.model_fields) - {"id"})
        self.c.execute(S.papers.insert().values(id=pid, title_key=title_key, c_facts=0, c_open_issues=0,
                                                c_open_by_rule=None, **values))
        refresh_corpus_stats(self.uow, m.corpus_id)
        return self.get(pid)  # type: ignore[return-value]

    def _row(self, paper_id: str):
        return self.c.execute(sa.select(S.papers).where(S.papers.c.id == paper_id)).first()

    def _require(self, paper_id: str):
        r = self._row(paper_id)
        if r is None:
            raise _not_found("Статья", paper_id)
        return r

    def get(self, paper_id: str) -> Paper | None:
        r = self._row(paper_id)
        return None if r is None else _paper(r)

    def get_many(self, paper_ids: Sequence[str]) -> list[Paper]:
        if not paper_ids:
            return []
        rows = self.c.execute(sa.select(S.papers).where(S.papers.c.id.in_(list(paper_ids)))).all()
        by = {r.id: _paper(r) for r in rows}
        return [by[i] for i in paper_ids if i in by]

    def title_key(self, paper_id: str) -> str | None:
        return self.c.execute(sa.select(S.papers.c.title_key).where(S.papers.c.id == paper_id)).scalar()

    def list(self, *, workspace_id: str | None = None, corpus_id: str | None = None, collection: str | None = None,
             status: str | None = None, include_deleted: bool = False, ids: Sequence[str] | None = None) -> list[Paper]:
        q = sa.select(S.papers)
        if workspace_id is not None:
            q = q.where(S.papers.c.workspace_id == workspace_id)
        if corpus_id is not None:
            q = q.where(S.papers.c.corpus_id == corpus_id)
        if collection is not None:
            q = q.where(S.papers.c.collection == collection)
        if status is not None:
            q = q.where(S.papers.c.status == _val(status))
        if ids is not None:
            q = q.where(S.papers.c.id.in_(list(ids)))
        if not include_deleted:
            q = q.where(S.papers.c.deleted_at.is_(None))
        return [_paper(r) for r in self.c.execute(q.order_by(S.papers.c.publication_id, S.papers.c.seq))]

    def get_by_publication(self, corpus_id: str, publication_id: str) -> Paper | None:
        r = self.c.execute(sa.select(S.papers).where(S.papers.c.corpus_id == corpus_id,
                                                     S.papers.c.publication_id == publication_id,
                                                     S.papers.c.deleted_at.is_(None))
                           .order_by(S.papers.c.seq).limit(1)).first()
        return None if r is None else _paper(r)

    def update(self, paper_id: str, **fields: Any) -> Paper:
        _check_fields(fields, _PAPER_UPDATE, "papers.update")
        cur = self._require(paper_id)
        values: dict[str, Any] = {}
        if "title_key" in fields:
            values["title_key"] = fields.pop("title_key")
        if fields:
            m = Paper.model_validate({**_paper(cur).model_dump(), **fields})
            if m.dup_of == paper_id:
                raise _bad("Статья не может быть дублем самой себя", "dup_of")
            values.update(_paper_values(m, fields))
        if values:
            self.c.execute(S.papers.update().where(S.papers.c.id == paper_id).values(**values))
        if set(fields) & {"corpus_id", "collection", "publication_id", "deleted_at", "dup_of", "workspace_id"}:
            _sync_paper_facts(self.uow, paper_id)
        if "corpus_id" in fields and cur.corpus_id != fields["corpus_id"]:
            refresh_corpus_stats(self.uow, cur.corpus_id)
        if set(fields) & {"corpus_id", "deleted_at", "stats", "in_scope", "dup_of", "model_version"}:
            refresh_corpus_stats(self.uow, self._row(paper_id).corpus_id)
        return self.get(paper_id)  # type: ignore[return-value]

    # --- обработка ------------------------------------------------------------------------------

    def save_processed(self, paper_id: str, bundle: PaperBundle, facts: list[FactDraft],
                       anchors: list[AnchorDraft], issues: list[IssueRecord], *, stats: PaperStats,
                       model_version: str, versions: dict) -> SaveResult:
        """Замена всего по статье одной транзакцией: строки 7 таблиц, факты, якоря, замечания, статистика.

        Замечания берутся из аргумента `issues` (bundle.issues не используется), статус - из записи
        (перенос статусов при пересчёте - issues.upsert_preserving_review). Статус статьи не меняется.
        """
        p = self._require(paper_id)
        # --- проверки до любых записей
        if not isinstance(bundle, PaperBundle):
            raise TypeError("bundle: ожидается PaperBundle")
        for i in issues:
            if not isinstance(i, IssueRecord):
                raise TypeError(f"issues: ожидается IssueRecord, получено {type(i).__name__}")
        for f in facts:
            if not isinstance(f, FactDraft):
                raise TypeError(f"facts: ожидается FactDraft, получено {type(f).__name__}")
        for a in anchors:
            if not isinstance(a, AnchorDraft):
                raise TypeError(f"anchors: ожидается AnchorDraft, получено {type(a).__name__}")
        anchor_refs = [a.ref for a in anchors]
        if len(set(anchor_refs)) != len(anchor_refs):
            raise _bad("Повторяющийся ref якоря", "anchors")
        aset = set(anchor_refs)
        for f in facts:
            if f.anchor_ref is not None and f.anchor_ref not in aset:
                raise _bad(f"Факт {f.ref}: якорь {f.anchor_ref} не передан", "facts")
        if not isinstance(stats, PaperStats):
            stats = PaperStats.model_validate(stats)
        now = self.now()
        c = self.c
        corp_deleted = None
        if p.corpus_id:
            corp_deleted = c.execute(sa.select(S.corpora.c.deleted_at).where(S.corpora.c.id == p.corpus_id)).scalar()
        visible = p.deleted_at is None and corp_deleted is None

        old = {r.key: r for r in c.execute(sa.select(S.issues.c.key, S.issues.c.record).where(
            S.issues.c.paper_id == paper_id).order_by(S.issues.c.position))}

        for t in (S.tz_records, S.facts, S.anchors, S.issues):
            c.execute(t.delete().where(t.c.paper_id == paper_id))

        # --- строки 7 таблиц (кроме issues)
        rec_rows = [dict(paper_id=paper_id, table_name="publications", record_id=bundle.publication.publication_id,
                         ml_order=bundle.publication.meta.ml_order, position=0, data=dump_model(bundle.publication))]
        for t in _REC_ORDER[1:]:
            col = ID_COLUMN[t]
            for pos, rec in enumerate(getattr(bundle, t)):
                rec_rows.append(dict(paper_id=paper_id, table_name=t, record_id=str(getattr(rec, col) or ""),
                                     ml_order=int(rec.meta.ml_order), position=pos, data=dump_model(rec)))
        c.execute(S.tz_records.insert(), rec_rows)

        # --- идентификаторы
        fact_ids: list[str] = [new_id("fct") for _ in facts]
        ref_to_ids: dict[str, list[str]] = defaultdict(list)
        for f, fid in zip(facts, fact_ids):
            ref_to_ids[f.ref].append(fid)
        anchor_ids = {a.ref: stable_id("anc", paper_id, a.ref) for a in anchors}

        # --- замечания: открытые по record_id (для facts.open_issues), связи с фактами
        target_to_fids: dict[str, list[str]] = defaultdict(list)
        for f, fid in zip(facts, fact_ids):
            tid = f.target.get("id") if isinstance(f.target, dict) else None
            if tid is not None:
                target_to_fids[str(tid)].append(fid)
        open_count: dict[str, int] = defaultdict(int)
        for rec in issues:
            if rec.status == IssueStatus.open:
                for rid in set(rec.record_ids):
                    open_count[rid] += 1

        # --- якоря
        if anchors:
            c.execute(S.anchors.insert(), [dict(
                id=anchor_ids[a.ref], paper_id=paper_id, ref=a.ref, kind=a.kind, page=a.page,
                rects=[r.model_dump() for r in a.rects], text=a.text,
                fact_ids=[fid for fr in a.fact_refs for fid in ref_to_ids.get(fr, [])]) for a in anchors])

        # --- факты
        if facts:
            rows = []
            for f, fid in zip(facts, fact_ids):
                tid = f.target.get("id") if isinstance(f.target, dict) else None
                dup = ref_to_ids.get(f.duplicate_of_ref) if f.duplicate_of_ref else None
                rows.append(_fact_row(f, fid, p, visible=visible, now=now,
                                      anchor_id=anchor_ids.get(f.anchor_ref) if f.anchor_ref else None,
                                      duplicate_of=dup[0] if dup else None,
                                      open_issues=open_count.get(str(tid), 0) if tid is not None else 0))
            c.execute(S.facts.insert(), rows)

        # --- замечания
        diff = IssuesDiff()
        seen_keys: dict[str, int] = {}
        issue_rows = []
        for pos, rec in enumerate(issues):
            n = seen_keys.get(rec.key, 0)
            seen_keys[rec.key] = n + 1
            iid = stable_id("iss", paper_id, rec.key) if n == 0 else stable_id("iss", paper_id, rec.key, n)
            fids = [fid for rid in dict.fromkeys(rec.record_ids) for fid in target_to_fids.get(rid, [])]
            aids = sorted({anchor_ids[f.anchor_ref] for f, fid in zip(facts, fact_ids)
                           if fid in set(fids) and f.anchor_ref}) if anchors else []
            issue_rows.append(dict(
                id=iid, paper_id=paper_id, key=rec.key, position=pos, check_name=rec.check_name,
                severity=rec.severity, computed_by=rec.computed_by, status=_val(rec.status), stale=False,
                record=_issue_record_json(rec), record_ids=list(rec.record_ids), fact_ids=fids, anchor_ids=aids,
                rule_version=(versions or {}).get("rules"), created_at=now, updated_at=now))
            if n == 0:
                if rec.key not in old:
                    diff.opened.append(rec.key)
                elif _issue_content(load_model(IssueRecord, old[rec.key].record)) != _issue_content(rec):
                    diff.changed.append(rec.key)
        if issue_rows:
            c.execute(S.issues.insert(), issue_rows)
        diff.closed = [k for k in old if k not in seen_keys]

        # --- статья
        pv: dict[str, Any] = dict(model_version=model_version, versions=dict(versions or {}),
                                  stats=stats.model_dump(mode="json"), processed_at=now)
        pub = bundle.publication
        if isinstance(pub.title, str) and pub.title.strip():
            pv["title"] = pub.title
        if isinstance(pub.year, int) and not isinstance(pub.year, bool):
            pv["year"] = pub.year
        if isinstance(pub.DOI, str) and pub.DOI.strip():
            pv["doi"] = pub.DOI.strip()
            pv["doi_norm"] = _norm_doi(pub.DOI)
        if isinstance(pub.in_scope, bool):
            pv["in_scope"] = pub.in_scope
        c.execute(S.papers.update().where(S.papers.c.id == paper_id).values(**pv))
        refresh_paper_and_corpus(self.uow, paper_id)
        return SaveResult(paper_id=paper_id,
                          fact_ids={ref: ids[0] for ref, ids in ref_to_ids.items()},
                          anchor_ids=dict(anchor_ids), issues=diff)

    def load_bundle(self, paper_id: str, *, apply_review: bool = True) -> PaperBundle:
        """PaperBundle статьи. apply_review: review_status/corrected_* автоматических фактов -> RecordMeta записи
        (при нескольких фактах одной записи главный - поле metric_value/значение записи)."""
        p = self._require(paper_id)
        c = self.c
        rows = c.execute(sa.select(S.tz_records.c.table_name, S.tz_records.c.data)
                         .where(S.tz_records.c.paper_id == paper_id)
                         .order_by(S.tz_records.c.table_name, S.tz_records.c.ml_order, S.tz_records.c.position)).all()
        by_table: dict[str, list] = defaultdict(list)
        for t, data in rows:
            by_table[t].append(load_model(ROW_MODELS[t], data))
        pubs = by_table.get("publications") or [Publication(publication_id=p.publication_id)]
        bundle = PaperBundle(publication=pubs[0], **{t: by_table.get(t, []) for t in _REC_ORDER[1:]})
        bundle.issues = [r for r, _ in self._issue_records(paper_id)]
        if apply_review:
            fr = c.execute(sa.select(S.facts.c.target_entity, S.facts.c.target_id, S.facts.c.target_field,
                                     S.facts.c.review_status, S.facts.c.corrected_value, S.facts.c.corrected_raw)
                           .where(S.facts.c.paper_id == paper_id, S.facts.c.added_by == AddedBy.auto.value,
                                  sa.or_(S.facts.c.review_status != ReviewStatus.new.value,
                                         S.facts.c.corrected_value.is_not(None),
                                         S.facts.c.corrected_raw.is_not(None)))
                           .order_by(S.facts.c.seq)).all()
            main_fields = {"metric_value", "normalized_value", None}
            for r in sorted(fr, key=lambda x: x.target_field in main_fields):
                if not r.target_entity or r.target_id is None or r.target_entity not in TABLES:
                    continue
                rec = bundle.by_id(r.target_entity, r.target_id)
                if rec is None:
                    continue
                rec.meta.review_status = ReviewStatus(r.review_status)
                rec.meta.corrected_value = str_to_dec(r.corrected_value)
                rec.meta.corrected_raw = r.corrected_raw
        return bundle

    def _issue_records(self, paper_id: str) -> list[tuple[IssueRecord, Any]]:
        out = []
        for r in self.uow.conn.execute(sa.select(S.issues).where(S.issues.c.paper_id == paper_id)
                                       .order_by(S.issues.c.position, S.issues.c.seq)):
            rec = load_model(IssueRecord, r.record)
            rec.status = IssueStatus(r.status)
            out.append((rec, r))
        return out

    def anchors(self, paper_id: str) -> list[Anchor]:
        return AnchorsRepo(self.uow).list_for_paper(paper_id)

    # --- дубли ----------------------------------------------------------------------------------

    def find_by_doi(self, workspace_id: str, doi: str) -> list[Paper]:
        d = _norm_doi(doi)
        if not d:
            return []
        q = sa.select(S.papers).where(S.papers.c.workspace_id == workspace_id, S.papers.c.doi_norm == d,
                                      S.papers.c.deleted_at.is_(None)).order_by(S.papers.c.seq)
        return [_paper(r) for r in self.c.execute(q)]

    def find_by_title_key(self, workspace_id: str, key: str) -> list[Paper]:
        if not key:
            return []
        q = sa.select(S.papers).where(S.papers.c.workspace_id == workspace_id, S.papers.c.title_key == key,
                                      S.papers.c.deleted_at.is_(None)).order_by(S.papers.c.seq)
        return [_paper(r) for r in self.c.execute(q)]

    def find_by_sha(self, workspace_id: str, sha256: str) -> list[Paper]:
        if not sha256:
            return []
        q = sa.select(S.papers).where(S.papers.c.workspace_id == workspace_id, S.papers.c.sha256 == sha256,
                                      S.papers.c.deleted_at.is_(None)).order_by(S.papers.c.seq)
        return [_paper(r) for r in self.c.execute(q)]

    def set_duplicate(self, paper_id: str, dup_of: str | None, dup_kind: DupKind | None) -> None:
        self._require(paper_id)
        if dup_of is not None and dup_of == paper_id:
            raise _bad("Статья не может быть дублем самой себя", "dup_of")
        if (dup_of is None) != (dup_kind is None):
            raise _bad("dup_of и dup_kind задаются вместе", "dup_kind")
        kind = None if dup_kind is None else DupKind(_val(dup_kind)).value
        self.c.execute(S.papers.update().where(S.papers.c.id == paper_id).values(dup_of=dup_of, dup_kind=kind))
        _sync_paper_facts(self.uow, paper_id)
        refresh_corpus_stats(self.uow, self._row(paper_id).corpus_id)

    # --- мягкое удаление ------------------------------------------------------------------------

    def soft_delete(self, paper_id: str) -> Paper:
        r = self._require(paper_id)
        if r.deleted_at is not None:
            return _paper(r)
        return self.update(paper_id, deleted_at=self.now())

    def restore(self, paper_id: str) -> Paper:
        r = self._require(paper_id)
        if r.deleted_at is None:
            return _paper(r)
        _restore_check(r.deleted_at, self.now(), "Статья")
        return self.update(paper_id, deleted_at=None)


# --- факты -------------------------------------------------------------------------------------

def _fact_row(f: FactDraft, fid: str, paper_row, *, visible: bool, now: datetime, anchor_id: str | None,
              duplicate_of: str | None, open_issues: int, added_by: AddedBy | None = None) -> dict[str, Any]:
    target = dict(f.target or {})
    nv = f.normalized_value
    return dict(
        id=fid, paper_id=paper_row.id, ref=f.ref, workspace_id=paper_row.workspace_id, corpus_id=paper_row.corpus_id,
        collection=paper_row.collection, publication_id=paper_row.publication_id, visible=visible,
        is_dup=paper_row.dup_of is not None, target=enc(target),
        target_entity=None if target.get("entity") is None else str(target.get("entity")),
        target_id=None if target.get("id") is None else str(target.get("id")),
        target_field=None if target.get("field") is None else str(target.get("field")),
        param=f.param, metric_name=f.metric_name, row_id=f.row_id, row_label=f.row_label, raw_value=f.raw_value,
        raw_unit=f.raw_unit, normalized_value=dec_to_str(nv), normalized_num=dec_to_float(nv),
        normalized_unit=f.normalized_unit, norm_status=_val(f.norm_status), extraction_status=f.extraction_status,
        review_status=ReviewStatus.new.value, corrected_value=None, corrected_raw=None, value_num=dec_to_float(nv),
        added_by=_val(added_by or f.added_by), confidence=f.confidence, source_page=f.source_page,
        source_fragment=f.source_fragment, source_table=f.source_table, source_figure=f.source_figure,
        source_kind=_val(f.source_kind), quote_found=f.quote_found, anchor_id=anchor_id, duplicate_of=duplicate_of,
        model_version=f.model_version, exportable=True, reviewed_by=None, reviewed_at=None,
        trace=[t.model_dump() for t in f.trace] or None, open_issues=open_issues, created_at=now)


def _fact(m) -> Fact:
    return Fact(id=m["id"], paper_id=m["paper_id"], publication_id=m["publication_id"], target=dec(m["target"]) or {},
                param=m["param"], metric_name=m["metric_name"], row_id=m["row_id"], row_label=m["row_label"],
                raw_value=m["raw_value"], raw_unit=m["raw_unit"], normalized_value=str_to_dec(m["normalized_value"]),
                normalized_unit=m["normalized_unit"], norm_status=m["norm_status"],
                extraction_status=m["extraction_status"], review_status=m["review_status"],
                corrected_value=str_to_dec(m["corrected_value"]), corrected_raw=m["corrected_raw"],
                added_by=m["added_by"], confidence=m["confidence"], source_page=m["source_page"],
                source_fragment=m["source_fragment"], source_table=m["source_table"],
                source_figure=m["source_figure"], source_kind=m["source_kind"], quote_found=m["quote_found"],
                anchor_id=m["anchor_id"], duplicate_of=m["duplicate_of"], model_version=m["model_version"],
                exportable=m["exportable"], reviewed_by=m["reviewed_by"], reviewed_at=m["reviewed_at"])


def _fact_row_entity(r, paper_title: str | None, paper_year: int | None) -> FactRow:
    m = r._mapping
    f = _fact(m)
    return FactRow(fact=f, paper_title=paper_title, paper_year=paper_year,
                   open_issues=int(m["open_issues"] or 0), display=Q.display(f.value, f.normalized_unit))


_REVIEW_FIELDS = {"review_status", "corrected_value", "corrected_raw", "reviewed_by", "reviewed_at", "exportable",
                  "duplicate_of", "anchor_id", "quote_found"}
_EXTRACTED_FIELDS = set(Fact.model_fields) - _REVIEW_FIELDS


class FactsRepo(_Repo):
    def get(self, fact_id: str) -> Fact | None:
        r = self.c.execute(sa.select(S.facts).where(S.facts.c.id == fact_id)).first()
        return None if r is None else _fact(r._mapping)

    def get_many(self, fact_ids: Sequence[str]) -> list[Fact]:
        if not fact_ids:
            return []
        rows = self.c.execute(sa.select(S.facts).where(S.facts.c.id.in_(list(fact_ids)))).all()
        by = {r.id: _fact(r._mapping) for r in rows}
        return [by[i] for i in fact_ids if i in by]

    def list_for_paper(self, paper_id: str, *, added_by: AddedBy | str | None = None) -> list[Fact]:
        q = sa.select(S.facts).where(S.facts.c.paper_id == paper_id)
        if added_by is not None:
            q = q.where(S.facts.c.added_by == _val(added_by))
        return [_fact(r._mapping) for r in self.c.execute(q.order_by(S.facts.c.seq))]

    def manual_for_paper(self, paper_id: str) -> list[Fact]:
        return self.list_for_paper(paper_id, added_by=AddedBy.manual)

    def trace(self, fact_id: str) -> list[TraceStep]:
        r = self.c.execute(sa.select(S.facts.c.trace).where(S.facts.c.id == fact_id)).first()
        if r is None:
            raise _not_found("Факт", fact_id)
        return [TraceStep.model_validate(t) for t in (r[0] or [])]

    # --- этап B ---------------------------------------------------------------------------------

    def query(self, filter: FactFilter, sort: list[SortKey], limit: int, cursor: str | None) -> Page[FactRow]:
        data, total, nxt = Q.run_query(self.c, filter, sort, limit, cursor, _fact_row_entity)
        return Page[FactRow](data=data, total=total, next_cursor=nxt)

    def facets(self, filter: FactFilter, fields: Sequence[str]) -> dict[str, dict[str, int]]:
        return Q.run_facets(self.c, filter, fields)

    def update_review(self, fact_id: str, **changes: Any) -> Fact:
        """Правка проверяемых полей: review_status, corrected_value/raw, reviewed_by/at, exportable,
        duplicate_of, anchor_id, quote_found. Извлечённые поля (raw_value, normalized_value, source_*) не меняются."""
        bad = set(changes) & _EXTRACTED_FIELDS
        if bad:
            raise _bad(f"Поля {sorted(bad)} не редактируются: правка через corrected_value/corrected_raw")
        _check_fields(changes, _REVIEW_FIELDS, "facts.update_review")
        r = self.c.execute(sa.select(S.facts).where(S.facts.c.id == fact_id)).first()
        if r is None:
            raise _not_found("Факт", fact_id)
        values: dict[str, Any] = {}
        if "review_status" in changes:
            try:
                values["review_status"] = ReviewStatus(_val(changes["review_status"])).value
            except ValueError as e:
                raise _bad(f"Неизвестный статус «{changes['review_status']}»", "review_status") from e
            if "reviewed_at" not in changes:
                values["reviewed_at"] = self.now()
        corrected = str_to_dec(r.corrected_value)
        if "corrected_value" in changes:
            try:
                corrected = to_decimal(changes["corrected_value"], field="corrected_value")
            except ValueError as e:
                raise _bad(str(e), "corrected_value") from e
            values["corrected_value"] = dec_to_str(corrected)
        for k in ("corrected_raw", "reviewed_by", "reviewed_at", "duplicate_of", "anchor_id"):
            if k in changes:
                values[k] = changes[k]
        for k in ("exportable", "quote_found"):
            if k in changes:
                v = changes[k]
                if v is not None and not isinstance(v, bool):
                    raise _bad(f"{k}: ожидается bool", k)
                if k == "exportable" and v is None:
                    raise _bad("exportable: ожидается bool", k)
                values[k] = v
        normalized = str_to_dec(r.normalized_value)
        eff = corrected if corrected is not None else normalized
        values["value_num"] = dec_to_float(eff)
        self.c.execute(S.facts.update().where(S.facts.c.id == fact_id).values(**values))
        if "review_status" in values or "corrected_value" in values:
            refresh_paper_and_corpus(self.uow, r.paper_id)
        return self.get(fact_id)  # type: ignore[return-value]

    def add_manual(self, paper_id: str, draft: FactDraft) -> Fact:
        """Ручной факт: added_by=manual независимо от черновика; якорь - по anchor_ref среди якорей статьи."""
        p = PapersRepo(self.uow)._require(paper_id)
        c = self.c
        corp_deleted = None
        if p.corpus_id:
            corp_deleted = c.execute(sa.select(S.corpora.c.deleted_at).where(S.corpora.c.id == p.corpus_id)).scalar()
        anchor_id = None
        if draft.anchor_ref:
            anchor_id = c.execute(sa.select(S.anchors.c.id).where(
                S.anchors.c.paper_id == paper_id,
                sa.or_(S.anchors.c.ref == draft.anchor_ref, S.anchors.c.id == draft.anchor_ref))).scalar()
        fid = new_id("fct")
        c.execute(S.facts.insert().values(**_fact_row(
            draft, fid, p, visible=p.deleted_at is None and corp_deleted is None, now=self.now(), anchor_id=anchor_id,
            duplicate_of=None, open_issues=0, added_by=AddedBy.manual)))
        recount_fact_open_issues(self.uow, paper_id)
        refresh_paper_and_corpus(self.uow, paper_id)
        return self.get(fid)  # type: ignore[return-value]

    def delete(self, fact_id: str) -> None:
        """Удаление факта (отмена добавления ручного факта)."""
        r = self.c.execute(sa.select(S.facts.c.paper_id).where(S.facts.c.id == fact_id)).first()
        if r is None:
            raise _not_found("Факт", fact_id)
        self.c.execute(S.facts.delete().where(S.facts.c.id == fact_id))
        refresh_paper_and_corpus(self.uow, r.paper_id)


# --- якоря -------------------------------------------------------------------------------------

def _anchor(r) -> Anchor:
    return Anchor(id=r.id, paper_id=r.paper_id, kind=r.kind, page=r.page, rects=r.rects or [], text=r.text,
                  fact_ids=r.fact_ids or [])


class AnchorsRepo(_Repo):
    def get(self, anchor_id: str) -> Anchor | None:
        r = self.c.execute(sa.select(S.anchors).where(S.anchors.c.id == anchor_id)).first()
        return None if r is None else _anchor(r)

    def list_for_paper(self, paper_id: str, *, page: int | None = None) -> list[Anchor]:
        q = sa.select(S.anchors).where(S.anchors.c.paper_id == paper_id)
        if page is not None:
            q = q.where(S.anchors.c.page == page)
        return [_anchor(r) for r in self.c.execute(q.order_by(S.anchors.c.seq))]


# --- замечания ---------------------------------------------------------------------------------

def _issue(r, rec: IssueRecord | None = None) -> Issue:
    rec = rec or load_model(IssueRecord, r.record)
    rec.status = IssueStatus(r.status)
    return Issue(id=r.id, paper_id=r.paper_id, key=r.key, record=rec, fact_ids=r.fact_ids or [],
                 anchor_ids=r.anchor_ids or [], status=r.status, stale=r.stale, rule_version=r.rule_version,
                 created_at=r.created_at)


class IssuesRepo(_Repo):
    def get(self, issue_id: str) -> Issue | None:
        r = self.c.execute(sa.select(S.issues).where(S.issues.c.id == issue_id)).first()
        return None if r is None else _issue(r)

    def list(self, paper_id: str, *, include_stale: bool = True, status: IssueStatus | str | None = None
             ) -> list[Issue]:
        q = sa.select(S.issues).where(S.issues.c.paper_id == paper_id)
        if not include_stale:
            q = q.where(S.issues.c.stale.is_(False))
        if status is not None:
            q = q.where(S.issues.c.status == _val(status))
        return [_issue(r) for r in self.c.execute(q.order_by(S.issues.c.position, S.issues.c.seq))]

    def list_for_papers(self, paper_ids: Sequence[str], *, only_open: bool = False) -> list[Issue]:
        if not paper_ids:
            return []
        q = sa.select(S.issues).where(S.issues.c.paper_id.in_(list(paper_ids)))
        if only_open:
            q = q.where(S.issues.c.status == "open", S.issues.c.stale.is_(False))
        return [_issue(r) for r in self.c.execute(q.order_by(S.issues.c.paper_id, S.issues.c.position))]

    def set_status(self, issue_id: str, status: IssueStatus | str, *, by: str | None = None) -> Issue:
        """Разбор замечания пользователем (open/fixed/ok)."""
        try:
            st = IssueStatus(_val(status))
        except ValueError as e:
            raise _bad(f"Неизвестный статус замечания «{status}»", "status") from e
        r = self.c.execute(sa.select(S.issues).where(S.issues.c.id == issue_id)).first()
        if r is None:
            raise _not_found("Замечание", issue_id)
        rec = load_model(IssueRecord, r.record)
        rec.status = st
        now = self.now()
        self.c.execute(S.issues.update().where(S.issues.c.id == issue_id).values(
            status=st.value, record=_issue_record_json(rec), updated_at=now,
            resolved_by=None if st == IssueStatus.open else by, resolved_at=None if st == IssueStatus.open else now))
        recount_fact_open_issues(self.uow, r.paper_id)
        refresh_paper_and_corpus(self.uow, r.paper_id)
        self._sync_paper_open(r.paper_id)
        return self.get(issue_id)  # type: ignore[return-value]

    def _sync_paper_open(self, paper_id: str) -> None:
        r = self.c.execute(sa.select(S.papers.c.stats, S.papers.c.c_open_issues).where(S.papers.c.id == paper_id)).first()
        if r is None:
            return
        st = PaperStats.model_validate(r.stats or {})
        if st.open_issues != int(r.c_open_issues or 0):
            st.open_issues = int(r.c_open_issues or 0)
            self.c.execute(S.papers.update().where(S.papers.c.id == paper_id).values(stats=st.model_dump(mode="json")))

    def upsert_preserving_review(self, paper_id: str, issues: list[IssueRecord]) -> IssuesDiff:
        """Пересчёт замечаний статьи с сохранением разбора: статус переносится по key; исчезнувшее
        проверенное (fixed/ok) остаётся с stale=true; исчезнувшее открытое удаляется (closed);
        появившееся вновь stale-замечание оживает со своим статусом."""
        PapersRepo(self.uow)._require(paper_id)
        for i in issues:
            if not isinstance(i, IssueRecord):
                raise TypeError(f"issues: ожидается IssueRecord, получено {type(i).__name__}")
        c = self.c
        now = self.now()
        fresh: dict[str, IssueRecord] = {}
        for rec in issues:
            fresh.setdefault(rec.key, rec)
        existing: dict[str, Any] = {}
        for r in c.execute(sa.select(S.issues).where(S.issues.c.paper_id == paper_id)
                           .order_by(S.issues.c.position, S.issues.c.seq)):
            if r.key in existing:
                c.execute(S.issues.delete().where(S.issues.c.id == r.id))  # дубль ключа - схлопывается
                continue
            existing[r.key] = r
        # связи с фактами
        target_to_fids: dict[str, list[str]] = defaultdict(list)
        fact_anchor: dict[str, str] = {}
        for fid, tid, aid in c.execute(sa.select(S.facts.c.id, S.facts.c.target_id, S.facts.c.anchor_id)
                                       .where(S.facts.c.paper_id == paper_id).order_by(S.facts.c.seq)):
            if tid is not None:
                target_to_fids[tid].append(fid)
            if aid:
                fact_anchor[fid] = aid
        diff = IssuesDiff()
        for pos, (key, rec) in enumerate(fresh.items()):
            fids = [fid for rid in dict.fromkeys(rec.record_ids) for fid in target_to_fids.get(rid, [])]
            aids = sorted({fact_anchor[f] for f in fids if f in fact_anchor})
            old = existing.get(key)
            if old is None:
                c.execute(S.issues.insert().values(
                    id=stable_id("iss", paper_id, key), paper_id=paper_id, key=key, position=pos,
                    check_name=rec.check_name, severity=rec.severity, computed_by=rec.computed_by,
                    status=_val(rec.status), stale=False, record=_issue_record_json(rec),
                    record_ids=list(rec.record_ids), fact_ids=fids, anchor_ids=aids, rule_version=None,
                    created_at=now, updated_at=now))
                diff.opened.append(key)
                continue
            old_rec = load_model(IssueRecord, old.record)
            new_rec = rec.model_copy(deep=True)
            new_rec.status = IssueStatus(old.status)
            if _issue_content(old_rec) != _issue_content(rec):
                diff.changed.append(key)
            c.execute(S.issues.update().where(S.issues.c.id == old.id).values(
                position=pos, check_name=rec.check_name, severity=rec.severity, computed_by=rec.computed_by,
                stale=False, record=_issue_record_json(new_rec), record_ids=list(rec.record_ids), fact_ids=fids,
                anchor_ids=aids, updated_at=now))
        tail = len(fresh)
        for key, old in existing.items():
            if key in fresh:
                continue
            if old.status in (IssueStatus.fixed.value, IssueStatus.ok.value):
                if not old.stale:
                    diff.stale.append(key)
                c.execute(S.issues.update().where(S.issues.c.id == old.id).values(
                    stale=True, position=tail, updated_at=now))
                tail += 1
            else:
                c.execute(S.issues.delete().where(S.issues.c.id == old.id))
                diff.closed.append(key)
        recount_fact_open_issues(self.uow, paper_id)
        refresh_paper_and_corpus(self.uow, paper_id)
        self._sync_paper_open(paper_id)
        return diff


# --- журнал действий ---------------------------------------------------------------------------

def _action(r) -> Action:
    return Action(id=r.id, user_id=r.user_id, workspace_id=r.workspace_id, paper_id=r.paper_id, fact_id=r.fact_id,
                  group_id=r.group_id, action=r.action, label=r.label, changes=dec(r.changes) or [],
                  undone=r.undone, created_at=r.created_at)


NOT_UNDOABLE = {"undo"}


class ActionsRepo(_Repo):
    def record(self, user: Any, action: str, label: str, changes: list[dict], *, paper_id: str | None = None,
               fact_id: str | None = None, group_id: str | None = None, undoable: bool | None = None,
               undo_of: str | None = None) -> Action:
        if isinstance(user, str):
            user_id, ws, who = user, None, None
        else:
            user_id = getattr(user, "id")
            ws = getattr(user, "workspace_id", None)
            who = getattr(user, "name", None) or getattr(user, "email", None)
        aid = new_id("act")
        self.c.execute(S.actions.insert().values(
            id=aid, workspace_id=ws, user_id=user_id, who=who, paper_id=paper_id, fact_id=fact_id, group_id=group_id,
            action=action, label=label, changes=enc(list(changes or [])),
            undoable=(action not in NOT_UNDOABLE) if undoable is None else bool(undoable), undone=False,
            undo_of=undo_of, created_at=self.now()))
        return self.get(aid)  # type: ignore[return-value]

    def get(self, action_id: str) -> Action | None:
        r = self.c.execute(sa.select(S.actions).where(S.actions.c.id == action_id)).first()
        return None if r is None else _action(r)

    def by_group(self, group_id: str) -> list[Action]:
        return [_action(r) for r in self.c.execute(sa.select(S.actions).where(S.actions.c.group_id == group_id)
                                                   .order_by(S.actions.c.seq))]

    def list(self, workspace_id: str, *, user_id: str | None = None, paper_id: str | None = None,
             limit: int = 100, before_seq: int | None = None, include_service: bool = True) -> list[Action]:
        q = sa.select(S.actions).where(S.actions.c.workspace_id == workspace_id)
        if user_id:
            q = q.where(S.actions.c.user_id == user_id)
        if paper_id:
            q = q.where(S.actions.c.paper_id == paper_id)
        if not include_service:
            q = q.where(S.actions.c.action.in_(["accept", "reject", "restore", "edit", "add", "acceptAll"]))
        if before_seq is not None:
            q = q.where(S.actions.c.seq < before_seq)
        return [_action(r) for r in self.c.execute(q.order_by(S.actions.c.seq.desc()).limit(limit))]

    def last_undoable(self, user_id: str, within_h: int = 24, depth: int = 50) -> Action | None:
        """Последнее неотменённое отменяемое действие среди `depth` последних действий пользователя
        не старше `within_h` часов."""
        border = self.now() - timedelta(hours=within_h)
        recent = (sa.select(S.actions.c.seq).where(S.actions.c.user_id == user_id)
                  .order_by(S.actions.c.seq.desc()).limit(depth)).subquery()
        r = self.c.execute(sa.select(S.actions).where(
            S.actions.c.seq.in_(sa.select(recent.c.seq)), S.actions.c.undone.is_(False),
            S.actions.c.undoable.is_(True), S.actions.c.created_at >= border)
            .order_by(S.actions.c.seq.desc()).limit(1)).first()
        return None if r is None else _action(r)

    def mark_undone(self, action_id: str, *, by: str | None = None) -> Action:
        res = self.c.execute(S.actions.update().where(S.actions.c.id == action_id).values(
            undone=True, undone_at=self.now(), undone_by=by))
        if res.rowcount == 0:
            raise _not_found("Действие", action_id)
        return self.get(action_id)  # type: ignore[return-value]


# --- эталон ------------------------------------------------------------------------------------

class GoldRepo(_Repo):
    def load_from_dir(self, dataset: str, dir: Any) -> GoldSet:
        """Загрузка эталона из каталога 7 CSV; повторная загрузка заменяет набор."""
        d = Path(dir)
        if not d.exists():
            raise FileNotFoundError(f"Каталог эталона не найден: {d}")
        if not d.is_dir():
            raise NotADirectoryError(f"Не каталог: {d}")
        bundles = bundles_from_tables(read_tables(d))
        c = self.c
        old = c.execute(sa.select(S.gold_sets.c.id).where(S.gold_sets.c.dataset == dataset)).scalar()
        if old:
            c.execute(S.gold_items.delete().where(S.gold_items.c.set_id == old))
            c.execute(S.gold_sets.delete().where(S.gold_sets.c.id == old))
        gid = new_id("gld")
        now = self.now()
        c.execute(S.gold_sets.insert().values(id=gid, dataset=dataset, source_dir=str(d), papers=len(bundles),
                                              results=sum(len(b.results) for b in bundles), loaded_at=now))
        if bundles:
            c.execute(S.gold_items.insert(), [dict(set_id=gid, publication_id=b.publication_id, position=i,
                                                   data=dump_model(b)) for i, b in enumerate(bundles)])
        return GoldSet(id=gid, dataset=dataset, source_dir=str(d), loaded_at=now, bundles=bundles)

    def get(self, dataset: str) -> GoldSet | None:
        r = self.c.execute(sa.select(S.gold_sets).where(S.gold_sets.c.dataset == dataset)).first()
        if r is None:
            return None
        items = self.c.execute(sa.select(S.gold_items.c.data).where(S.gold_items.c.set_id == r.id)
                               .order_by(S.gold_items.c.position)).all()
        return GoldSet(id=r.id, dataset=r.dataset, source_dir=r.source_dir, loaded_at=r.loaded_at,
                       bundles=[load_model(PaperBundle, x[0]) for x in items])

    def info(self, dataset: str) -> dict | None:
        """Краткие сведения без загрузки статей: {dataset, papers, results, loaded_at, source_dir}."""
        r = self.c.execute(sa.select(S.gold_sets).where(S.gold_sets.c.dataset == dataset)).first()
        if r is None:
            return None
        return {"dataset": r.dataset, "papers": r.papers, "results": r.results, "loaded_at": r.loaded_at,
                "source_dir": r.source_dir}

    def list(self) -> list[dict]:
        return [{"dataset": r.dataset, "papers": r.papers, "results": r.results, "loaded_at": r.loaded_at}
                for r in self.c.execute(sa.select(S.gold_sets).order_by(S.gold_sets.c.seq))]

    def delete(self, dataset: str) -> None:
        gid = self.c.execute(sa.select(S.gold_sets.c.id).where(S.gold_sets.c.dataset == dataset)).scalar()
        if gid:
            self.c.execute(S.gold_items.delete().where(S.gold_items.c.set_id == gid))
            self.c.execute(S.gold_sets.delete().where(S.gold_sets.c.id == gid))


# --- идемпотентность ---------------------------------------------------------------------------

class IdempotencyRepo(_Repo):
    def get(self, user_id: str, key: str) -> dict | None:
        r = self.c.execute(sa.select(S.idempotency_keys.c.response).where(
            S.idempotency_keys.c.user_id == user_id, S.idempotency_keys.c.key == key,
            S.idempotency_keys.c.expires_at > self.now())).first()
        return None if r is None else dec(r[0])

    def put(self, user_id: str, key: str, response: dict, ttl_h: int = 24) -> None:
        now = self.now()
        t = S.idempotency_keys
        self.c.execute(t.delete().where(t.c.user_id == user_id, t.c.key == key))
        self.c.execute(t.insert().values(user_id=user_id, key=key, response=enc(response), created_at=now,
                                         expires_at=now + timedelta(hours=ttl_h)))

    def purge_expired(self) -> int:
        return self.c.execute(S.idempotency_keys.delete().where(S.idempotency_keys.c.expires_at <= self.now())).rowcount


# --- пользователи и сессии ---------------------------------------------------------------------

_USER_CREATE = (set(StoredUser.model_fields) - {"id", "created_at"})
_USER_UPDATE = _USER_CREATE - {"workspace_id"}


def _user(r) -> StoredUser:
    return StoredUser(id=r.id, workspace_id=r.workspace_id, email=r.email, name=r.name, is_admin=r.is_admin,
                      disabled=r.disabled, password_hash=r.password_hash, created_at=r.created_at, role=r.role,
                      team=r.team, color=r.color, focus=r.focus, last_login_at=r.last_login_at)


def _norm_email(e: str) -> str:
    if not isinstance(e, str) or not e.strip():
        raise _bad("email обязателен", "email")
    return e.strip().lower()


class UsersRepo(_Repo):
    def get(self, user_id: str) -> User | None:
        r = self.c.execute(sa.select(S.users).where(S.users.c.id == user_id)).first()
        return None if r is None else _user(r)

    def get_by_email(self, email: str) -> User | None:
        if not email:
            return None
        r = self.c.execute(sa.select(S.users).where(S.users.c.email == email.strip().lower())).first()
        return None if r is None else _user(r)

    def create(self, **fields: Any) -> User:
        _check_fields(fields, _USER_CREATE, "users.create")
        if "email" in fields:
            fields["email"] = _norm_email(fields["email"])
        uid = new_id("usr")
        m = StoredUser(id=uid, created_at=self.now(), **fields)
        WorkspacesRepo(self.uow).ensure(m.workspace_id)
        self.c.execute(S.users.insert().values(**{k: getattr(m, k) for k in StoredUser.model_fields}))
        return self.get(uid)  # type: ignore[return-value]

    def update(self, user_id: str, **fields: Any) -> User:
        _check_fields(fields, _USER_UPDATE, "users.update")
        cur = self.get(user_id)
        if cur is None:
            raise _not_found("Пользователь", user_id)
        if "email" in fields:
            fields["email"] = _norm_email(fields["email"])
        m = StoredUser.model_validate({**cur.model_dump(), **fields})
        if fields:
            self.c.execute(S.users.update().where(S.users.c.id == user_id)
                           .values(**{k: getattr(m, k) for k in fields}))
        return self.get(user_id)  # type: ignore[return-value]

    def list(self, workspace_id: str) -> list[User]:
        return [_user(r) for r in self.c.execute(sa.select(S.users).where(S.users.c.workspace_id == workspace_id)
                                                 .order_by(S.users.c.seq))]

    def count_admins(self, workspace_id: str | None = None) -> int:
        q = sa.select(sa.func.count()).select_from(S.users).where(S.users.c.is_admin.is_(True),
                                                                   S.users.c.disabled.is_(False))
        if workspace_id:
            q = q.where(S.users.c.workspace_id == workspace_id)
        return int(self.c.execute(q).scalar_one())


def _session(r) -> RefreshSession:
    return RefreshSession(id=r.id, user_id=r.user_id, token_hash=r.token_hash, parent_id=r.parent_id,
                          remember=r.remember, expires_at=r.expires_at, revoked_at=r.revoked_at,
                          user_agent=r.user_agent, ip=r.ip, created_at=r.created_at)


class SessionsRepo(_Repo):
    """Refresh-сессии: в БД только хеш токена, цепочка ротации parent_id, гашение revoked_at."""

    _EXTRA = {"parent_id", "remember", "user_agent", "ip"}

    def create(self, user_id: str, token_hash: str, expires_at: datetime, **fields: Any) -> RefreshSession:
        _check_fields(fields, self._EXTRA, "sessions.create")
        sid = new_id("ses")
        self.c.execute(S.refresh_sessions.insert().values(
            id=sid, user_id=user_id, token_hash=token_hash, parent_id=fields.get("parent_id"),
            remember=bool(fields.get("remember", True)), expires_at=expires_at, revoked_at=None,
            user_agent=fields.get("user_agent"), ip=fields.get("ip"), created_at=self.now()))
        return self.get(sid)  # type: ignore[return-value]

    def get(self, session_id: str) -> RefreshSession | None:
        r = self.c.execute(sa.select(S.refresh_sessions).where(S.refresh_sessions.c.id == session_id)).first()
        return None if r is None else _session(r)

    def get_by_hash(self, token_hash: str) -> RefreshSession | None:
        """Сессия по хешу, в том числе погашенная (revoked_at задан) - для обнаружения повторного использования."""
        r = self.c.execute(sa.select(S.refresh_sessions).where(S.refresh_sessions.c.token_hash == token_hash)).first()
        return None if r is None else _session(r)

    def revoke(self, session_id: str) -> None:
        r = self.c.execute(sa.select(S.refresh_sessions.c.revoked_at)
                           .where(S.refresh_sessions.c.id == session_id)).first()
        if r is None:
            raise _not_found("Сессия", session_id)
        if r[0] is None:
            self.c.execute(S.refresh_sessions.update().where(S.refresh_sessions.c.id == session_id)
                           .values(revoked_at=self.now()))

    def revoke_all_for_user(self, user_id: str) -> int:
        return self.c.execute(S.refresh_sessions.update().where(S.refresh_sessions.c.user_id == user_id,
                                                                S.refresh_sessions.c.revoked_at.is_(None))
                              .values(revoked_at=self.now())).rowcount

    def revoke_chain(self, session_id: str) -> int:
        """Гасит всю цепочку ротации, в которую входит сессия (при повторном использовании токена)."""
        root = session_id
        seen = {root}
        while True:
            parent = self.c.execute(sa.select(S.refresh_sessions.c.parent_id)
                                    .where(S.refresh_sessions.c.id == root)).scalar()
            if not parent or parent in seen:
                break
            seen.add(parent)
            root = parent
        chain, frontier = {root}, [root]
        while frontier:
            kids = [r[0] for r in self.c.execute(sa.select(S.refresh_sessions.c.id)
                                                 .where(S.refresh_sessions.c.parent_id.in_(frontier)))]
            frontier = [k for k in kids if k not in chain]
            chain.update(frontier)
        return self.c.execute(S.refresh_sessions.update().where(S.refresh_sessions.c.id.in_(list(chain)),
                                                                S.refresh_sessions.c.revoked_at.is_(None))
                              .values(revoked_at=self.now())).rowcount


class LoginAttemptsRepo(_Repo):
    def record(self, *, email: str | None, ip: str | None, success: bool) -> None:
        self.c.execute(S.login_attempts.insert().values(email=(email or "").strip().lower() or None, ip=ip,
                                                        success=bool(success), created_at=self.now()))

    def failures_since(self, *, since: datetime, email: str | None = None, ip: str | None = None) -> int:
        t = S.login_attempts
        q = sa.select(sa.func.count()).select_from(t).where(t.c.success.is_(False), t.c.created_at >= since)
        if email is not None:
            q = q.where(t.c.email == email.strip().lower())
        if ip is not None:
            q = q.where(t.c.ip == ip)
        return int(self.c.execute(q).scalar_one())


# --- экспорт -----------------------------------------------------------------------------------

_EXPORT_CREATE = set(Export.model_fields) - {"id", "created_at"}


def _export(r) -> Export:
    return Export(id=r.id, workspace_id=r.workspace_id, user_id=r.user_id, format=r.format, scope=r.scope or {},
                  options=r.options or {}, status=r.status, file_key=r.file_key, error=r.error,
                  created_at=r.created_at, finished_at=r.finished_at)


class ExportsRepo(_Repo):
    def create(self, **fields: Any) -> Export:
        _check_fields(fields, _EXPORT_CREATE, "exports.create")
        eid = new_id("exp")
        m = Export(id=eid, created_at=self.now(), **fields)
        self.c.execute(S.exports.insert().values(**{k: getattr(m, k) for k in Export.model_fields}))
        return self.get(eid)  # type: ignore[return-value]

    def get(self, export_id: str) -> Export | None:
        r = self.c.execute(sa.select(S.exports).where(S.exports.c.id == export_id)).first()
        return None if r is None else _export(r)

    def update(self, export_id: str, **fields: Any) -> Export:
        _check_fields(fields, _EXPORT_CREATE - {"workspace_id"}, "exports.update")
        cur = self.get(export_id)
        if cur is None:
            raise _not_found("Выгрузка", export_id)
        m = Export.model_validate({**cur.model_dump(), **fields})
        if fields:
            self.c.execute(S.exports.update().where(S.exports.c.id == export_id)
                           .values(**{k: getattr(m, k) for k in fields}))
        return self.get(export_id)  # type: ignore[return-value]

    def list(self, workspace_id: str, *, user_id: str | None = None, limit: int = 50) -> list[Export]:
        q = sa.select(S.exports).where(S.exports.c.workspace_id == workspace_id)
        if user_id:
            q = q.where(S.exports.c.user_id == user_id)
        return [_export(r) for r in self.c.execute(q.order_by(S.exports.c.seq.desc()).limit(limit))]


# --- словарь: синонимы параметров --------------------------------------------------------------

def _syn(r) -> Synonym:
    return Synonym(id=r.id, workspace_id=r.workspace_id, collection=r.collection, param=r.param, text=r.text,
                   custom=r.custom, created_by=r.created_by, created_at=r.created_at)


class SynonymsRepo(_Repo):
    def add(self, workspace_id: str, collection: str, param: str, text: str, *, created_by: str | None = None,
            custom: bool = True) -> Synonym:
        norm = (text or "").strip().lower()
        if not norm:
            raise _bad("Пустой синоним", "text")
        t = S.dictionary_synonyms
        dup = self.c.execute(sa.select(t.c.id).where(t.c.workspace_id == workspace_id, t.c.collection == collection,
                                                     t.c.text_norm == norm)).first()
        if dup:
            raise AppError("conflict", f"Синоним «{text}» уже есть")
        sid = new_id("syn")
        self.c.execute(t.insert().values(id=sid, workspace_id=workspace_id, collection=collection, param=param,
                                         text=text.strip(), text_norm=norm, custom=custom, created_by=created_by,
                                         created_at=self.now()))
        return _syn(self.c.execute(sa.select(t).where(t.c.id == sid)).first())

    def list(self, workspace_id: str, collection: str | None = None) -> list[Synonym]:
        t = S.dictionary_synonyms
        q = sa.select(t).where(t.c.workspace_id == workspace_id)
        if collection:
            q = q.where(t.c.collection == collection)
        return [_syn(r) for r in self.c.execute(q.order_by(t.c.seq))]

    def remove(self, synonym_id: str) -> None:
        t = S.dictionary_synonyms
        if self.c.execute(t.delete().where(t.c.id == synonym_id)).rowcount == 0:
            raise _not_found("Синоним", synonym_id)
