"""Прогоны: запуск, выполнение, пауза при недоступном ML, отмена, повтор (план 5.6; REQ-065, REQ-075…REQ-078; У-33).

Жизненный цикл: `start` (queued, файлы прогона -> queued) -> `submit`/`execute` (running) -> done |
done_with_errors | cancelled. Повторный `submit` и `execute` того же прогона безопасны: выполняет тот, кто
перевёл прогон из queued в running.

Недоступный ML-1 (`health()` = down перед прогоном или после ошибки `ml_unavailable`):
* API (по умолчанию): `paused = true`, `pause_reason = "ml_unavailable"`, статус остаётся `running`, файлы -
  `queued`, событие `run.paused`; `health()` опрашивается каждые `ml_pause_poll_s`; ML поднялся - `run.resumed`;
  не поднялся за `ml_pause_max_s` - оставшиеся файлы `failed`/`ml_unavailable` (кроме тех, что есть в кеше).
* CLI (`retry_delays`): ожидание по `--retry-delays`, затем то же самое.

События: `run.progress` (шина пропускает не чаще раза в секунду), `run.file` на каждый файл, `run.paused`,
`run.resumed`, `run.done` - последним.
"""
from __future__ import annotations

import logging
import math
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..domain.entities import (CorpusFile, CorpusFileStatus, PaperStatus, ProcessOutcome, Run, RunCounts, RunKind,
                               RunOutput, RunStatus)
from ..domain.errors import AppError, ExtractError
from ..domain.ports import CancelToken
from ..domain.profile import BUILTIN_SOLAR_ID, ExtractionProfile, builtin_solar_profile
from ..domain.services import Services
from .processor import PaperProcessor, refresh_semantic_duplicate

log = logging.getLogger(__name__)

FINAL = (RunStatus.done, RunStatus.done_with_errors, RunStatus.cancelled, RunStatus.failed)
ACTIVE = (RunStatus.queued, RunStatus.running, RunStatus.cancelling)
NO_ML_KINDS = (RunKind.renormalize, RunKind.recheck)
MAX_FILE_RETRIES = 3
OUTPUT_KEY = "runs/{run_id}/output.zip"


def output_key(run_id: str) -> str:
    """Ключ ZIP выходной папки прогона `POST /extract` в FileStore (`GET /runs/{id}/output`)."""
    return OUTPUT_KEY.format(run_id=run_id)


def _kind(kind: RunKind | str) -> RunKind:
    try:
        return RunKind(getattr(kind, "value", kind))
    except ValueError as e:
        raise AppError("validation_error", f"Неизвестный вид прогона «{kind}»: "
                                           f"{', '.join(k.value for k in RunKind)}", field="kind") from e


def _counts(files: list[CorpusFile]) -> RunCounts:
    c = RunCounts(total=len(files))
    for f in files:
        st = f.status
        if st in (CorpusFileStatus.pending, CorpusFileStatus.queued, CorpusFileStatus.downloading):
            c.queued += 1
        elif st == CorpusFileStatus.processing:
            c.processing += 1
        elif st == CorpusFileStatus.done:
            c.done += 1
        elif st in (CorpusFileStatus.failed, CorpusFileStatus.needs_ocr):
            c.failed += 1
        elif st == CorpusFileStatus.skipped_duplicate:
            c.skipped += 1
        elif st == CorpusFileStatus.cancelled:
            c.cancelled += 1
    return c


def _pause_schedule(poll_s: float, max_s: float) -> list[float]:
    poll = max(float(poll_s), 0.0)
    total = max(float(max_s), 0.0)
    if total <= 0:
        return []
    if poll <= 0:
        return [total]
    n = max(1, math.ceil(total / poll - 1e-9))
    return [poll] * (n - 1) + [round(total - poll * (n - 1), 6)]


class _Ctx:
    """Состояние одного выполнения прогона."""

    def __init__(self, run: Run, corpus, profile: ExtractionProfile, processor: PaperProcessor,
                 on_progress: Callable[[Run], None] | None, cancel: CancelToken | None, fail_fast: bool) -> None:
        self.run_id = run.id
        self.corpus_id = run.corpus_id
        self.workspace_id = corpus.workspace_id
        self.kind = run.kind
        self.keep_review = run.keep_review
        self.profile = profile
        self.processor = processor
        self.on_progress = on_progress
        self.cancel = cancel
        self.fail_fast = fail_fast
        self.stop = False
        self.cancelled = False
        self.model_version = run.model_version or "latest"
        self.lock = threading.RLock()
        self.done_n = 0
        self.total = 0
        self.facts_found = 0
        self.issues_found = 0


class _MlGate:
    """Доступность ML-1 для прогона: одна пауза на всех исполнителей, ожидание через подменяемый time.sleep."""

    def __init__(self, service: "RunService", ctx: _Ctx, extractor) -> None:
        self.service = service
        self.ctx = ctx
        self.extractor = extractor
        self.lock = threading.Lock()
        self.down = False  # ML так и не поднялся: оставшиеся файлы берутся только из кеша
        self.gen = 0       # число пауз, после которых ML поднялся

    def _health(self):
        try:
            return self.extractor.health()
        except Exception as e:  # noqa: BLE001 - сбой health = ML недоступен
            log.warning("health ML-1: %s", e)
            from ..domain.extraction import MlHealth
            return MlHealth(status="down")

    def ensure(self) -> tuple[bool, bool, str | None]:
        """(доступен, была пауза, model_version из health). Без паузы, если ML отвечает."""
        h = self._health()
        if h.status != "down":
            return True, False, h.model_version
        schedule = self.service.wait_schedule()
        self.service._set_paused(self.ctx, True, schedule[0] if schedule else None)
        for delay in schedule:
            if delay > 0:
                time.sleep(delay)
            h = self._health()
            if h.status != "down":
                self.service._set_paused(self.ctx, False, None, resumed=True)
                return True, True, h.model_version
        self.service._set_paused(self.ctx, False, None, resumed=False)
        self.service._say(f"ML-1 недоступен: ожидание исчерпано ({sum(schedule):g} с); оставшиеся файлы - только из "
                          f"кеша, иначе failed/ml_unavailable")
        return False, True, None

    def start(self) -> str | None:
        with self.lock:
            ok, _, mv = self.ensure()
            if not ok:
                self.down = True
            return mv

    def on_unavailable(self, seen_gen: int) -> bool:
        """После ошибки ml_unavailable: True - повторить файл (ML снова доступен после паузы), False - файл failed."""
        with self.lock:
            if self.down:
                return False
            if self.gen != seen_gen:
                return True
            ok, paused, _ = self.ensure()
            if not ok:
                self.down = True
                return False
            if paused:
                self.gen += 1
                return True
            return False


class RunService:
    """Прогоны корпуса. Параметры конструктора - для CLI: `profile` (подмена профиля, напр. без правил бэка),
    `retry_delays` (ожидание ML по --retry-delays вместо ml_pause_*), `dedupe`, `on_file(event)` (строка прогресса),
    `say(text)` (сообщения о паузе), `check_script` (для build_output прогона /extract)."""

    def __init__(self, services: Services, *, profile: ExtractionProfile | None = None,
                 retry_delays: tuple[float, ...] | None = None, dedupe: bool = True,
                 on_file: Callable[[dict], None] | None = None, say: Callable[[str], None] | None = None,
                 autolink: bool | None = None, ocr: str = "auto", check_script: Path | None = None) -> None:
        self.services = services
        self.profile_override = profile
        self.retry_delays = retry_delays
        self.dedupe = dedupe
        self.on_file = on_file
        self.say = say
        self.autolink = autolink
        self.ocr = ocr
        self.check_script = check_script

    # --- служебное ---------------------------------------------------------------------------------

    def _say(self, text: str) -> None:
        log.info(text)
        if self.say is not None:
            try:
                self.say(text)
            except Exception:  # noqa: BLE001
                pass

    def _publish(self, workspace_id: str, event: str, data: dict) -> None:
        try:
            self.services.bus.publish(workspace_id, event, data)
        except Exception:  # noqa: BLE001
            log.exception("Событие %s не опубликовано", event)

    def wait_schedule(self) -> list[float]:
        if self.retry_delays is not None:
            return [max(float(d), 0.0) for d in self.retry_delays]
        s = self.services.settings
        return _pause_schedule(s.ml_pause_poll_s, s.ml_pause_max_s)

    def _profile(self, uow, run: Run, corpus) -> ExtractionProfile:
        if self.profile_override is not None:
            return self.profile_override
        for pid in (run.profile_id, corpus.profile_id, BUILTIN_SOLAR_ID):
            if pid:
                p = uow.profiles.get(pid)
                if p is not None:
                    return p
        return builtin_solar_profile()

    # --- start --------------------------------------------------------------------------------------

    def _select_files(self, uow, corpus_id: str, kind: RunKind, file_ids: list[str] | None) -> list[CorpusFile]:
        files = uow.files.list(corpus_id)
        own = {p.id for p in uow.papers.list(corpus_id=corpus_id, include_deleted=False)}

        def own_paper(f: CorpusFile) -> bool:
            return f.paper_id is None or f.paper_id in own

        if kind == RunKind.selected:
            if not file_ids:
                raise AppError("validation_error", "Для прогона selected нужен список file_ids", field="file_ids")
            by_id = {f.id: f for f in files}
            missing = [i for i in file_ids if i not in by_id]
            if missing:
                raise AppError("validation_error", f"Файлы не из этого корпуса: {', '.join(missing)}",
                               field="file_ids")
            return [by_id[i] for i in dict.fromkeys(file_ids)]
        usable = [f for f in files if f.status != CorpusFileStatus.skipped_duplicate and own_paper(f)]
        if kind == RunKind.full:
            return usable
        if kind == RunKind.new_only:
            return [f for f in usable if f.status in (CorpusFileStatus.pending, CorpusFileStatus.queued,
                                                      CorpusFileStatus.cancelled)]
        if kind == RunKind.failed_only:
            return [f for f in usable if f.status in (CorpusFileStatus.failed, CorpusFileStatus.needs_ocr)]
        # renormalize / recheck: только разобранные статьи
        return [f for f in usable if f.status == CorpusFileStatus.done and f.paper_id]

    def start(self, corpus_id: str, kind: RunKind | str, *, file_ids: list[str] | None = None,
              profile_id: str | None = None, model_version: str = "latest", concurrency: int = 4,
              keep_review: bool = True, user_id: str | None, build_output: bool = False) -> Run:
        """AppError('conflict'), если у корпуса уже идёт прогон."""
        k = _kind(kind)
        try:
            conc = int(concurrency)
        except (TypeError, ValueError) as e:
            raise AppError("validation_error", "concurrency - целое число", field="concurrency") from e
        if conc < 1:
            raise AppError("validation_error", "concurrency должно быть не меньше 1", field="concurrency")
        with self.services.db.uow() as uow:
            corpus = uow.corpora.get(corpus_id)
            if corpus is None or corpus.deleted_at is not None:
                raise AppError("not_found", f"Корпус {corpus_id} не найден")
            if uow.runs.active_for_corpus(corpus_id) is not None:
                raise AppError("conflict", "Прогон уже идёт")
            if profile_id and uow.profiles.get(profile_id) is None:
                raise AppError("validation_error", f"Профиль {profile_id} не найден", field="profile_id")
            files = self._select_files(uow, corpus_id, k, file_ids)
            for f in files:
                if f.status != CorpusFileStatus.queued and not (k in NO_ML_KINDS):
                    if f.status == CorpusFileStatus.failed and not f.storage_key:
                        continue  # отклонён при приёме: останется failed
                    uow.files.update_status(f.id, CorpusFileStatus.queued)
            ids = [f.id for f in files]
            counts = RunCounts(total=len(ids), queued=len(ids))
            return uow.runs.create(corpus_id=corpus_id, kind=k, status=RunStatus.queued, profile_id=profile_id,
                                   model_version=model_version or "latest", concurrency=conc,
                                   keep_review=bool(keep_review), build_output=bool(build_output), file_ids=ids,
                                   counts=counts, started_by=user_id)

    def start_or_append(self, corpus_id: str, file_ids: list[str], *, user_id: str | None) -> Run:
        """Для корпуса kind='manual': добавить в активный прогон или начать selected (409 не бывает, У-30).
        Прогон не запускается сам: вызывающий делает `submit(run.id)` (повторный submit безопасен)."""
        with self.services.db.uow() as uow:
            corpus = uow.corpora.get(corpus_id)
            if corpus is None or corpus.deleted_at is not None:
                raise AppError("not_found", f"Корпус {corpus_id} не найден")
            if corpus.kind != "manual":
                raise AppError("validation_error", "Дозапись в прогон - только для корпуса «Загруженные вручную»",
                               field="corpus_id")
            active = uow.runs.active_for_corpus(corpus_id)
            if active is not None and active.status in (RunStatus.queued, RunStatus.running):
                by_id = {f.id: f for f in uow.files.list(corpus_id)}
                missing = [i for i in file_ids if i not in by_id]
                if missing:
                    raise AppError("validation_error", f"Файлы не из этого корпуса: {', '.join(missing)}",
                                   field="file_ids")
                ids = list(active.file_ids or [])
                for fid in file_ids:
                    if fid not in ids:
                        ids.append(fid)
                        uow.files.update_status(fid, CorpusFileStatus.queued)
                files = uow.files.list(corpus_id, ids=ids)
                return uow.runs.update(active.id, file_ids=ids, counts=_counts(files))
        return self.start(corpus_id, RunKind.selected, file_ids=list(file_ids), user_id=user_id)

    def retry_file(self, corpus_id: str, file_id: str, *, user_id: str | None = None) -> Run:
        with self.services.db.uow() as uow:
            f = uow.files.get(file_id)
            if f is None or f.corpus_id != corpus_id:
                raise AppError("not_found", f"Файл {file_id} не найден в корпусе")
        return self.start(corpus_id, RunKind.selected, file_ids=[file_id], user_id=user_id)

    # --- submit / cancel -------------------------------------------------------------------------------

    def submit(self, run_id: str) -> None:
        """Через services.executor."""
        def task() -> None:
            try:
                self.execute(run_id)
            except Exception:  # noqa: BLE001 - прогон помечается failed
                log.exception("Прогон %s упал", run_id)
                try:
                    with self.services.db.uow() as uow:
                        r = uow.runs.get(run_id)
                        if r is not None and r.status not in FINAL:
                            uow.runs.update(run_id, status=RunStatus.failed, paused=False,
                                            finished_at=uow.now())
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось отметить прогон %s", run_id)

        self.services.executor.submit(task)

    def cancel(self, run_id: str) -> Run:
        """Незапущенные файлы -> cancelled; готовое сохраняется. Завершённый прогон -> AppError('conflict')."""
        with self.services.db.uow() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise AppError("not_found", f"Прогон {run_id} не найден")
            if run.status in FINAL:
                raise AppError("conflict", "Прогон уже завершён")
            corpus = uow.corpora.get(run.corpus_id)
            if run.status == RunStatus.queued:
                self._cancel_rest(uow, run)
                files = uow.files.list(run.corpus_id, ids=run.file_ids or [])
                run = uow.runs.update(run_id, status=RunStatus.cancelled, counts=_counts(files), paused=False,
                                      finished_at=uow.now())
                done = True
            else:
                run = uow.runs.update(run_id, status=RunStatus.cancelling)
                done = False
        if done and corpus is not None:
            self._publish(corpus.workspace_id, "run.done", {"run_id": run.id, "corpus_id": run.corpus_id,
                                                            "status": run.status.value,
                                                            "counts": run.counts.model_dump(), "output": None})
        return run

    @staticmethod
    def _cancel_rest(uow, run: Run, skip: set[str] | None = None) -> None:
        for f in uow.files.list(run.corpus_id, ids=run.file_ids or []):
            if skip and f.id in skip:
                continue
            if f.status in (CorpusFileStatus.queued, CorpusFileStatus.pending, CorpusFileStatus.processing):
                uow.files.update_status(f.id, CorpusFileStatus.cancelled)
                if f.paper_id:
                    p = uow.papers.get(f.paper_id)
                    if p is not None and p.status in (PaperStatus.processing,):
                        uow.papers.update(f.paper_id, status=PaperStatus.queued)

    # --- пауза -----------------------------------------------------------------------------------------

    def _set_paused(self, ctx: _Ctx, paused: bool, retry_in_s: float | None, *, resumed: bool = False) -> None:
        with self.services.db.uow() as uow:
            run = uow.runs.update(ctx.run_id, paused=paused, pause_reason="ml_unavailable" if paused else None)
        if paused:
            self._say(f"ML-1 недоступен: пауза, повтор через {retry_in_s:g} с" if retry_in_s is not None
                      else "ML-1 недоступен: пауза")
            self._publish(ctx.workspace_id, "run.paused",
                          {"run_id": ctx.run_id, "corpus_id": ctx.corpus_id, "reason": "ml_unavailable",
                           "retry_in_s": retry_in_s})
        elif resumed:
            self._say("ML-1 снова доступен: продолжаем")
            self._publish(ctx.workspace_id, "run.resumed", {"run_id": ctx.run_id, "corpus_id": ctx.corpus_id})
        if ctx.on_progress is not None:
            ctx.on_progress(run)

    # --- execute ---------------------------------------------------------------------------------------

    def execute(self, run_id: str, *, on_progress: Callable[[Run], None] | None = None, fail_fast: bool = False,
                cancel: CancelToken | None = None) -> Run:
        """Блокирующее выполнение. Повторный вызов для запущенного или завершённого прогона ничего не делает."""
        db = self.services.db
        with db.uow() as uow:
            run = uow.runs.get(run_id)
            if run is None:
                raise AppError("not_found", f"Прогон {run_id} не найден")
            if run.status != RunStatus.queued:
                if run.status == RunStatus.cancelling:
                    return self._finish_cancelled(run_id)
                return run
            corpus = uow.corpora.get(run.corpus_id)
            profile = self._profile(uow, run, corpus)
            run = uow.runs.update(run_id, status=RunStatus.running, started_at=uow.now(), paused=False)
        processor = PaperProcessor(self.services, dedupe=self.dedupe, autolink=self.autolink, ocr=self.ocr)
        ctx = _Ctx(run, corpus, profile, processor, on_progress, cancel, fail_fast)
        gate: _MlGate | None = None
        if run.kind not in NO_ML_KINDS and self._needs_ml(run):
            gate = _MlGate(self, ctx, processor.extractor)
            mv = gate.start()
            if ctx.model_version == "latest":
                if gate.down:
                    ctx.model_version = "latest"
                else:
                    ctx.model_version = mv or getattr(processor.extractor, "name", None) or "latest"
        processed: set[str] = set()
        while True:
            with db.uow() as uow:
                cur = uow.runs.get(run_id)
                pending = [i for i in (cur.file_ids or []) if i not in processed]
                if not pending or ctx.stop:
                    break
            self._run_batch(ctx, gate, pending, cur.concurrency, processed)
            if ctx.stop:
                break
        if self.dedupe and run.kind not in NO_ML_KINDS:
            self._dedupe_pass(run_id)
        return self._finish(ctx)

    def _needs_ml(self, run: Run) -> bool:
        """Есть ли в прогоне файлы, которым нужен ML (иначе health не проверяется)."""
        with self.services.db.uow() as uow:
            for f in uow.files.list(run.corpus_id, ids=run.file_ids or []):
                if f.storage_key and f.paper_id:
                    return True
        return False

    def _run_batch(self, ctx: _Ctx, gate: _MlGate | None, ids: list[str], concurrency: int,
                   processed: set[str]) -> None:
        # параллельно - только запросы к контейнеру ML-1 (мок и raw упираются в CPU и SQLite: порядок детерминирован)
        parallel = (concurrency > 1 and len(ids) > 1 and gate is not None
                    and getattr(gate.extractor, "name", "") == "http")
        if not parallel:
            for fid in ids:
                processed.add(fid)
                if ctx.stop:
                    continue
                self._run_file(ctx, gate, fid)
            return
        with ThreadPoolExecutor(max_workers=min(concurrency, len(ids)), thread_name_prefix="faktura-file") as pool:
            futures = []
            for fid in ids:
                processed.add(fid)
                futures.append(pool.submit(self._run_file, ctx, gate, fid))
            for fu in futures:
                try:
                    fu.result()
                except Exception:  # noqa: BLE001
                    log.exception("Обработка файла упала")

    def _check_cancel(self, ctx: _Ctx) -> bool:
        if ctx.stop:
            return True
        if ctx.cancel is not None and ctx.cancel.is_set():
            ctx.stop = ctx.cancelled = True
            return True
        with self.services.db.uow() as uow:
            r = uow.runs.get(ctx.run_id)
            if r is not None and r.status == RunStatus.cancelling:
                ctx.stop = ctx.cancelled = True
                return True
        return False

    def _run_file(self, ctx: _Ctx, gate: _MlGate | None, file_id: str) -> None:
        db = self.services.db
        with ctx.lock:
            if self._check_cancel(ctx):
                return
        with db.uow() as uow:
            f = uow.files.get(file_id)
        if f is None or f.status in (CorpusFileStatus.cancelled, CorpusFileStatus.skipped_duplicate):
            return
        if f.paper_id is None or not f.storage_key:
            # отклонён при приёме (CLI): ML не вызывается, файл остаётся failed со своим кодом
            code = f.error_code or "pdf_broken"
            with db.uow() as uow:
                f = uow.files.update_status(file_id, CorpusFileStatus.failed, error_code=code,
                                            error_message=f.error_message)
            self._file_done(ctx, f, None, "failed", code, f.error_message)
            return
        with db.uow() as uow:
            uow.files.update_status(file_id, CorpusFileStatus.processing, error_code=None, error_message=None)
        mode = {RunKind.renormalize: "renormalize", RunKind.recheck: "recheck"}.get(ctx.kind, "full")
        outcome: ProcessOutcome | None = None
        code: str | None = None
        message: str | None = None
        attempts = 0
        while True:
            seen = gate.gen if gate is not None else 0
            offline = gate is not None and gate.down
            try:
                outcome = ctx.processor.process(f.paper_id, profile=ctx.profile, mode=mode,  # type: ignore[arg-type]
                                                model_version="latest" if offline else ctx.model_version,
                                                keep_review=ctx.keep_review)
                break
            except ExtractError as e:
                code, message = e.code, e.message
                if e.code == "ml_unavailable" and gate is not None and attempts < MAX_FILE_RETRIES \
                        and gate.on_unavailable(seen):
                    attempts += 1
                    continue
                break
            except AppError as e:
                code, message = e.code, e.message
                break
            except Exception as e:  # noqa: BLE001 - сбой одной статьи не роняет прогон
                log.exception("Статья %s: внутренняя ошибка", f.paper_id)
                code, message = "internal_error", f"{type(e).__name__}: {e}"
                break
        if outcome is not None and outcome.status == PaperStatus.done:
            with db.uow() as uow:
                f = uow.files.update_status(file_id, CorpusFileStatus.done, error_code=None, error_message=None)
            self._file_done(ctx, f, outcome, "done", None, None)
            return
        if outcome is not None:
            code, message = outcome.error_code or code or "internal_error", outcome.error_message or message
        code = code or "internal_error"
        with db.uow() as uow:
            f = uow.files.update_status(file_id, CorpusFileStatus.failed, error_code=code, error_message=message)
            if f.paper_id:
                p = uow.papers.get(f.paper_id)
                if p is not None and p.status != PaperStatus.failed and mode == "full":
                    uow.papers.update(f.paper_id, status=PaperStatus.failed, error_code=code)
        self._file_done(ctx, f, outcome, "failed", code, message)
        if ctx.fail_fast:
            ctx.stop = True

    def _file_done(self, ctx: _Ctx, f: CorpusFile, outcome: ProcessOutcome | None, status: str,
                   code: str | None, message: str | None) -> None:
        facts = outcome.stats.facts_count if outcome is not None and status == "done" else 0
        issues = outcome.stats.open_issues if outcome is not None and status == "done" else 0
        with ctx.lock:
            ctx.done_n += 1
            ctx.facts_found += facts
            ctx.issues_found += issues
            with self.services.db.uow() as uow:
                cur = uow.runs.get(ctx.run_id)
                files = uow.files.list(ctx.corpus_id, ids=cur.file_ids or [])
                run = uow.runs.update(ctx.run_id, counts=_counts(files))
            ctx.total = run.counts.total
            ev = {"run_id": ctx.run_id, "corpus_id": ctx.corpus_id, "file_id": f.id, "paper_id": f.paper_id,
                  "status": status, "stage": "done" if status == "done" else "failed", "facts": facts,
                  "issues": issues, "error": None if status == "done" else {"code": code, "message": message}}
            self._publish(ctx.workspace_id, "run.file", ev)
            if self.on_file is not None:
                try:
                    self.on_file({**ev, "name": f.name, "publication_id": f.publication_id, "index": ctx.done_n,
                                  "total": run.counts.total,
                                  "from_cache": bool(outcome.from_cache) if outcome is not None else False})
                except Exception:  # noqa: BLE001
                    log.exception("on_file")
            c = run.counts
            finished = c.done + c.failed + c.skipped + c.cancelled
            self._publish(ctx.workspace_id, "run.progress",
                          {"run_id": ctx.run_id, "corpus_id": ctx.corpus_id,
                           "progress": round(finished / c.total, 4) if c.total else 1.0,
                           "counts": c.model_dump(), "facts_found": ctx.facts_found,
                           "issues_found": ctx.issues_found, "eta_s": None})
            if ctx.on_progress is not None:
                ctx.on_progress(run)

    def _dedupe_pass(self, run_id: str) -> None:
        """Смысловые дубли по порядку приёма (после параллельной обработки эталон - самая ранняя статья)."""
        with self.services.db.uow() as uow:
            run = uow.runs.get(run_id)
            for f in uow.files.list(run.corpus_id, ids=run.file_ids or []):
                if f.paper_id and f.status == CorpusFileStatus.done:
                    p = uow.papers.get(f.paper_id)
                    if p is not None and p.corpus_id == run.corpus_id and p.status == PaperStatus.done:
                        refresh_semantic_duplicate(uow, p.id)

    def _finish_cancelled(self, run_id: str) -> Run:
        with self.services.db.uow() as uow:
            run = uow.runs.get(run_id)
            self._cancel_rest(uow, run)
            files = uow.files.list(run.corpus_id, ids=run.file_ids or [])
            run = uow.runs.update(run_id, status=RunStatus.cancelled, counts=_counts(files), paused=False,
                                  finished_at=uow.now())
            corpus = uow.corpora.get(run.corpus_id)
        if corpus is not None:
            self._publish(corpus.workspace_id, "run.done", {"run_id": run.id, "corpus_id": run.corpus_id,
                                                            "status": run.status.value,
                                                            "counts": run.counts.model_dump(), "output": None})
        return run

    def _finish(self, ctx: _Ctx) -> Run:
        with self.services.db.uow() as uow:
            run = uow.runs.get(ctx.run_id)
            cancelled = ctx.cancelled or run.status == RunStatus.cancelling
            if ctx.stop:
                self._cancel_rest(uow, run)
            files = uow.files.list(run.corpus_id, ids=run.file_ids or [])
            counts = _counts(files)
            if cancelled:
                status = RunStatus.cancelled
            elif counts.failed or (ctx.stop and counts.cancelled):
                status = RunStatus.done_with_errors
            else:
                status = RunStatus.done
            run = uow.runs.update(ctx.run_id, counts=counts, paused=False, pause_reason=None)
        output = None
        if run.build_output and status != RunStatus.cancelled:
            output = self._build_run_output(run, ctx.profile)
        with self.services.db.uow() as uow:
            fields: dict[str, Any] = dict(status=status, finished_at=uow.now())
            if output is not None:
                fields["output"] = output
            run = uow.runs.update(ctx.run_id, **fields)
        self._publish(ctx.workspace_id, "run.done",
                      {"run_id": run.id, "corpus_id": run.corpus_id, "status": run.status.value,
                       "counts": run.counts.model_dump(),
                       "output": run.output.model_dump(mode="json") if run.output is not None else None})
        if ctx.on_progress is not None:
            ctx.on_progress(run)
        return run

    def _build_run_output(self, run: Run, profile: ExtractionProfile) -> RunOutput | None:
        """Выходная папка прогона POST /extract: build_output -> ZIP в FileStore (`output_key(run_id)`)."""
        from ..export import check_status, zip_dir
        from .output import build_output

        script = self.check_script or self.services.settings.check_script
        try:
            with tempfile.TemporaryDirectory(prefix="faktura_out_") as tmp:
                out = Path(tmp) / "out"
                res = build_output(self.services, run.corpus_id, out, profile=profile,
                                   check_script=Path(script) if script else None, run=run)
                data = zip_dir(out)
            self.services.store.put_bytes(output_key(run.id), data, "application/zip")
        except Exception:  # noqa: BLE001 - выходная папка не роняет прогон
            log.exception("Выходная папка прогона %s не собрана", run.id)
            return RunOutput(available=False, check_status="not_run")
        st = check_status(res.check)
        mapped = {None: "not_run", "ok": "ok", "warnings": "ok", "failed": "errors"}.get(st, "errors")
        if res.check is not None and res.check.crashed:
            mapped = "crashed"
        return RunOutput(available=True, url=f"/api/v1/runs/{run.id}/output",
                         csv_files=[p.name for p in res.csv_paths], check_status=mapped,  # type: ignore[arg-type]
                         built_at=datetime.now().astimezone())

    # --- восстановление после перезапуска -----------------------------------------------------------------

    def resume_active(self) -> list[str]:
        """Незавершённые прогоны после перезапуска сервера: running -> queued и снова в очередь исполнителя."""
        ids: list[str] = []
        with self.services.db.uow() as uow:
            for r in uow.runs.list_active():
                if r.status == RunStatus.cancelling:
                    continue
                if r.status == RunStatus.running:
                    for f in uow.files.list(r.corpus_id, ids=r.file_ids or []):
                        if f.status == CorpusFileStatus.processing:
                            uow.files.update_status(f.id, CorpusFileStatus.queued)
                    uow.runs.update(r.id, status=RunStatus.queued, paused=False)
                ids.append(r.id)
        for rid in ids:
            self.submit(rid)
        return ids
