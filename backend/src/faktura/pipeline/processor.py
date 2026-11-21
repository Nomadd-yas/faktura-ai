"""Обработка одной статьи (spec/07 §7.4, план 5.6).

`full`: parse -> extract (адаптер ML-1, кеш ответов по sha256) -> область (in_scope=false: только publications)
-> apply_normalization -> link_duplicates (autolink) + check_paper (+merge) -> quote_found -> build_facts
-> paper_quality/coverage -> save_processed (1 транзакция) -> перенос правок -> смысловые дубли -> paper.done.
`renormalize`: то же без ML, из сохранённых сырых значений. `recheck`: только замечания (recheck_paper).

Ошибки: `ExtractError('ml_unavailable')` пробрасывается - решение о паузе принимает прогон; прочие ошибки ML и
PDF -> статья `failed` с кодом, прежний результат не стирается (сохранение - одной транзакцией в конце).
"""
from __future__ import annotations

import logging
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

from ..domain.entities import (AnchorDraft, DupKind, Fact, FactDraft, PaperStats, PaperStatus, ProcessOutcome,
                               ReviewStatus)
from ..domain.errors import AppError, ExtractError
from ..domain.extraction import ExtractOutcome, ExtractRequest, ParsedDocument
from ..domain.profile import ExtractionProfile
from ..domain.records import AddedBy, IssueRecord, IssueStatus, PaperBundle, Publication
from ..domain.services import Services
from ..domain.tables import CHECK_CATALOG
from ._pdf import PDF_LOCK, local_pdf
from .facts import build_facts
from .normalize import apply_normalization
from .review import is_reviewed, match_review

log = logging.getLogger(__name__)

PROCESS_MODES = ("full", "renormalize", "recheck")
_UI_ORDER = {"conflict": 3, "warn": 2, "doubt": 1}


# ---------------------------------------------------------------------------------------------
# смысловые дубли (REQ-074)
# ---------------------------------------------------------------------------------------------

def publication_title_key(pub: Publication) -> str | None:
    from ..pdfio.quotes import title_key

    year = pub.year if isinstance(pub.year, int) and not isinstance(pub.year, bool) else (
        pub.year if isinstance(pub.year, str) else None)
    first = pub.authors[0] if pub.authors else None
    title = pub.title if isinstance(pub.title, str) else None
    try:
        return title_key(title, year, first)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        return None


def _reference(candidates: list, paper_id: str) -> str | None:
    """Эталон группы - самая ранняя статья (порядок приёма). Статья-эталон сама дублем не считается."""
    if not candidates:
        return None
    ids = [c.id for c in candidates]
    first = ids[0]
    if first == paper_id:
        return None
    return first


def detect_semantic_duplicate(uow: Any, workspace_id: str, paper_id: str,
                              publication: Publication) -> tuple[str | None, DupKind | None]:
    """DOI, затем title_key (название + год + первый автор). Эталон - самая ранняя статья рабочего пространства
    с тем же ключом; сама статья и статьи других рабочих пространств не учитываются."""
    doi = publication.DOI if isinstance(publication.DOI, str) else None
    if doi and doi.strip():
        ref = _reference(uow.papers.find_by_doi(workspace_id, doi), paper_id)
        if ref is not None:
            return ref, DupKind.doi
    key = publication_title_key(publication)
    if key:
        ref = _reference(uow.papers.find_by_title_key(workspace_id, key), paper_id)
        if ref is not None:
            return ref, DupKind.title
    return None, None


def refresh_semantic_duplicate(uow: Any, paper_id: str, publication: Publication | None = None) -> None:
    """Пересчитать пометку смыслового дубля статьи (пометка «тот же файл» живёт у CorpusFile)."""
    p = uow.papers.get(paper_id)
    if p is None or p.deleted_at is not None:
        return
    if publication is None:
        publication = uow.papers.load_bundle(paper_id, apply_review=False).publication
    dup, kind = detect_semantic_duplicate(uow, p.workspace_id, paper_id, publication)
    if (dup, kind) != (p.dup_of, p.dup_kind):
        uow.papers.set_duplicate(paper_id, dup, kind)


# ---------------------------------------------------------------------------------------------
# вспомогательное
# ---------------------------------------------------------------------------------------------

def _out_of_scope(v: Any) -> bool:
    return v is False or (isinstance(v, str) and v.strip().lower() == "false")


def _open_exported(issues: list[IssueRecord]) -> list[IssueRecord]:
    out = []
    for i in issues:
        info = CHECK_CATALOG.get(i.check_name)
        if info is not None and not info.exported:
            continue
        if str(getattr(i.status, "value", i.status)) == IssueStatus.open.value:
            out.append(i)
    return out


def paper_stats(bundle: PaperBundle, facts: list[FactDraft] | list[Fact], issues: list[IssueRecord],
                profile: ExtractionProfile) -> PaperStats:
    from ..checks import paper_coverage, paper_quality

    open_issues = _open_exported(issues)
    top = None
    for i in open_issues:
        info = CHECK_CATALOG.get(i.check_name)
        lvl = info.ui_level if info is not None else "warn"
        if top is None or _UI_ORDER.get(lvl, 0) > _UI_ORDER.get(top, 0):
            top = lvl
    return PaperStats(quality=paper_quality(facts, issues), coverage=paper_coverage(bundle, profile),
                      open_issues=len(open_issues), top_level=top, facts_count=len(facts))


def _apply_links(bundle: PaperBundle, links) -> None:
    to = {l.result_id: l.duplicate_of for l in links}
    for r in bundle.results:
        if r.result_id in to:
            r.duplicate_of = to[r.result_id]
            r.meta.linked_by_backend = True


def _fill_quote_found(bundle: PaperBundle, quote) -> None:
    """quote_found по правилам валидатора (pypdf + norm()); ссылка «Table N, строка X» ищется по всему тексту.
    Непроверяемые фрагменты (Table/Supplementary) - None, не False."""
    from ..pdfio.quotes import is_checkable_fragment

    for r in bundle.results:
        frag = r.source_fragment
        if not isinstance(frag, str) or not frag.strip():
            r.meta.quote_found = None
            continue
        page = r.source_page if isinstance(r.source_page, int) and not isinstance(r.source_page, bool) else None
        found = quote.check(page, frag)
        if found is None and is_checkable_fragment(frag):
            found = bool(quote.find_pages(frag)) if hasattr(quote, "find_pages") else False
        r.meta.quote_found = None if found is None else bool(found)


def _manual_draft(f: Fact, *, detach: bool) -> FactDraft:
    """Черновик ручного факта из прежнего факта (сохранение ручных фактов и несовпавших правок)."""
    target = dict(f.target or {})
    if detach:
        target["id"] = None
    return FactDraft(ref=f"manual:{f.id}", target=target, param=f.param, metric_name=f.metric_name,
                     row_id=f.row_id, row_label=f.row_label, raw_value=f.raw_value, raw_unit=f.raw_unit,
                     normalized_value=f.normalized_value, normalized_unit=f.normalized_unit,
                     norm_status=f.norm_status, extraction_status=f.extraction_status or "extracted",
                     confidence=f.confidence, source_page=f.source_page, source_fragment=f.source_fragment,
                     source_table=f.source_table, source_figure=f.source_figure, source_kind=f.source_kind,
                     quote_found=f.quote_found, added_by=AddedBy.manual, model_version=f.model_version)


def _review_changes(f: Fact) -> dict[str, Any]:
    return {"review_status": f.review_status, "corrected_value": f.corrected_value,
            "corrected_raw": f.corrected_raw, "reviewed_by": f.reviewed_by,
            "reviewed_at": f.reviewed_at, "exportable": bool(f.exportable)}


class _Memo:
    """Ответы ML внутри прогона по (sha256, версия): файлы с одинаковым содержимым не вызывают ML повторно."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._data: dict[tuple[str, str], ExtractOutcome] = {}

    def lock_for(self, sha: str) -> threading.Lock:
        with self._lock:
            return self._locks.setdefault(sha, threading.Lock())

    def get(self, sha: str, version: str) -> ExtractOutcome | None:
        out = self._data.get((sha, version))
        return out.model_copy(deep=True) if out is not None else None

    def put(self, sha: str, version: str, out: ExtractOutcome) -> None:
        self._data[(sha, version)] = out.model_copy(deep=True)


# ---------------------------------------------------------------------------------------------
# PaperProcessor
# ---------------------------------------------------------------------------------------------

class PaperProcessor:
    """Обработка одной статьи. `dedupe` - пометки смысловых дублей (CLI --no-dedupe выключает);
    `autolink` - автосвязывание повторов (None: по настройке, auto = включено для http/raw, В-13)."""

    def __init__(self, services: Services, *, dedupe: bool = True, autolink: bool | None = None,
                 ocr: str = "auto") -> None:
        self.services = services
        self.dedupe = dedupe
        self._autolink = autolink
        self.ocr = ocr
        self._extractor = None
        self._ext_lock = threading.Lock()
        self.memo = _Memo()

    # --- зависимости ---
    @property
    def extractor(self):
        if self._extractor is None:
            with self._ext_lock:
                if self._extractor is None:
                    self._extractor = self.services.extractor_factory()
        return self._extractor

    def autolink(self) -> bool:
        if self._autolink is not None:
            return self._autolink
        mode = self.services.settings.autolink_duplicates
        if mode == "on":
            return True
        if mode == "off":
            return False
        return getattr(self.extractor, "name", "mock") in ("http", "raw")

    # --- главное ---
    def process(self, paper_id: str, *, profile: ExtractionProfile, mode: Literal["full", "renormalize", "recheck"],
                model_version: str, keep_review: bool = True, on_stage: Callable[[str, int], None] | None = None,
                abort: threading.Event | None = None) -> ProcessOutcome:
        """`on_stage(этап, прогресс)` - сообщает ход разбора («Извлечение» -> «Нормализация» -> «Проверка»);
        `abort` - флаг «файл брошен по таймауту»: перед записью результата разбор прерывается.
        parse -> extract (через MlCache) -> scope -> apply_normalization -> build_facts -> link_duplicates
        -> check_paper (+merge) -> quote_found -> paper_quality/coverage -> save_processed (1 транзакция)
        -> смысловые дубли -> paper.done."""
        if mode not in PROCESS_MODES:
            raise AppError("validation_error", f"Режим обработки: {', '.join(PROCESS_MODES)}", field="mode")
        db = self.services.db
        with db.uow() as uow:
            paper = uow.papers.get(paper_id)
            if paper is None or paper.deleted_at is not None:
                raise AppError("not_found", f"Статья {paper_id} не найдена")
            file = uow.files.get(paper.file_id) if paper.file_id else None
            if mode != "full" and paper.processed_at is None:
                raise AppError("conflict", "Статья ещё не разобрана: нечего пересчитывать")
        if (paper.collection or "solar") != "solar":  # rag: свой процессор, без bundle/фактов/замечаний бэка
            from .generic import GenericProcessor

            return GenericProcessor(self).process(paper, file, mode=mode, model_version=model_version,
                                                  on_stage=on_stage, abort=abort)
        if mode == "recheck":
            return self._recheck(paper, file, profile)
        if file is None or not file.storage_key:
            code = (file.error_code if file is not None else None) or "pdf_broken"
            return self._fail(paper, code, (file.error_message if file else None) or "Нет PDF статьи")
        with db.uow() as uow:
            uow.papers.update(paper_id, status=PaperStatus.processing, error_code=None)
        try:
            with local_pdf(self.services.store, file.storage_key, f"{paper.publication_id}.pdf") as pdf:
                return self._process_pdf(paper, file, pdf, profile=profile, mode=mode,
                                         model_version=model_version, keep_review=keep_review, on_stage=on_stage,
                                         abort=abort)
        except ExtractError as e:
            if e.code == "ml_unavailable":
                with db.uow() as uow:
                    uow.papers.update(paper_id, status=PaperStatus.queued)
                raise
            return self._fail(paper, e.code, e.message)
        except AppError as e:
            return self._fail(paper, e.code, e.message)

    def _fail(self, paper, code: str, message: str | None) -> ProcessOutcome:
        with self.services.db.uow() as uow:
            uow.papers.update(paper.id, status=PaperStatus.failed, error_code=code)
        self._publish(paper.workspace_id, "paper.failed", {"paper_id": paper.id, "code": code, "message": message})
        return ProcessOutcome(paper_id=paper.id, status=PaperStatus.failed, error_code=code, error_message=message)

    def _publish(self, workspace_id: str, event: str, data: dict) -> None:
        try:
            self.services.bus.publish(workspace_id, event, data)
        except Exception:  # noqa: BLE001 - события не роняют обработку
            log.exception("Событие %s не опубликовано", event)

    # --- извлечение ---
    def _extract(self, paper, pdf: Path, doc: ParsedDocument | None, profile: ExtractionProfile,
                 model_version: str) -> ExtractOutcome:
        sha = paper.sha256 or ""
        version = model_version or "latest"
        req = ExtractRequest(publication_id=paper.publication_id, pdf_path=pdf, sha256=sha,
                             domain=paper.collection or "solar",
                             fields=None if profile.builtin else [f for f in profile.fields if f.enabled],
                             model_version=version)
        if not sha or version == "latest":
            return self.extractor.extract(req)
        with self.memo.lock_for(sha):
            hit = self.memo.get(sha, version)
            if hit is not None:
                hit.from_cache = True
                return hit
            out = self.extractor.extract(req)
            self.memo.put(sha, version, out)
            return out

    def _process_pdf(self, paper, file, pdf: Path, *, profile: ExtractionProfile, mode: str, model_version: str,
                     keep_review: bool, on_stage: Callable[[str, int], None] | None = None,
                     abort: threading.Event | None = None) -> ProcessOutcome:
        def stage(name: str, progress: int) -> None:
            if on_stage is not None:
                try:
                    on_stage(name, progress)
                except Exception:  # noqa: BLE001 - ход разбора не роняет обработку
                    log.debug("on_stage", exc_info=True)

        s = self.services
        warnings: list[str] = []
        doc: ParsedDocument | None = None
        from_cache = False
        if mode == "full":
            try:
                with PDF_LOCK:
                    doc = s.parser.parse(pdf)
            except AppError:
                raise
            except Exception as e:  # noqa: BLE001
                raise AppError("pdf_broken", f"PDF не разбирается: {type(e).__name__}") from e
            if doc.needs_ocr:
                warnings.append("часть страниц - сканы без текстового слоя (OCR не выполняется)")
            out = self._extract(paper, pdf, doc, profile, model_version)
            warnings.extend(out.warnings)
            from_cache = out.from_cache
            mv = out.model_version
            bundle = out.bundle
            bundle.publication.publication_id = paper.publication_id
        else:  # renormalize: сохранённые сырые значения, без ML
            with s.db.uow() as uow:
                bundle = uow.papers.load_bundle(paper.id, apply_review=False)
            mv = paper.model_version or model_version
        stage("Нормализация", 60)
        pid = paper.publication_id
        for _, rows in bundle.iter_tables():
            for row in rows:
                row.publication_id = pid

        from ..checks import RULES_VERSION, check_paper
        from ..pdfio.quotes import PypdfQuoteIndex

        if _out_of_scope(bundle.publication.in_scope):
            bundle = PaperBundle(publication=bundle.publication)
            issues: list[IssueRecord] = []
        else:
            bundle = apply_normalization(bundle, s.normalizer, profile)
            stage("Проверка", 85)
            quote = PypdfQuoteIndex(pdf)
            outcome = check_paper(bundle, profile, quote=quote, autolink=self.autolink())
            _apply_links(bundle, outcome.links)
            issues = outcome.issues
            _fill_quote_found(bundle, quote)
        facts, anchors = build_facts(bundle, doc, s.parser, pdf, bbox_origin=s.settings.bbox_origin)
        for f in facts:
            f.model_version = mv
        stats = paper_stats(bundle, facts, issues, profile)
        versions = {"model": str(mv), "norm": str(getattr(s.normalizer, "version", "")),
                    "dictionary": str(getattr(s.normalizer, "dictionary_version", "")), "rules": str(RULES_VERSION),
                    "parser": str(getattr(s.parser, "version", "")) if mode == "full" else "",
                    "from_cache": "true" if from_cache else "false"}
        if mode != "full":
            with s.db.uow() as uow:
                prev = uow.papers.get(paper.id)
                versions["parser"] = (prev.versions or {}).get("parser", "") if prev else ""
                versions["from_cache"] = (prev.versions or {}).get("from_cache", "false") if prev else "false"
        if abort is not None and abort.is_set():
            raise AppError("file_timeout", "Разбор файла прерван по таймауту")
        self._save(paper, bundle, facts, anchors, issues, stats, mv, versions, keep_review=keep_review, profile=profile,
                   needs_ocr=bool(doc.needs_ocr) if doc is not None else None,
                   page_count=doc.page_count if doc is not None else None)
        self._publish(paper.workspace_id, "paper.done",
                      {"paper_id": paper.id, "chat_id": None, "title": bundle.publication.title,
                       "facts": stats.facts_count, "issues": stats.open_issues})
        return ProcessOutcome(paper_id=paper.id, status=PaperStatus.done, stats=stats, from_cache=from_cache,
                              warnings=warnings)

    def _save(self, paper, bundle: PaperBundle, facts: list[FactDraft], anchors: list[AnchorDraft],
              issues: list[IssueRecord], stats: PaperStats, mv: str, versions: dict, *, keep_review: bool,
              needs_ocr: bool | None, page_count: int | None, profile: ExtractionProfile | None = None) -> None:
        """Одна транзакция: замена строк, фактов, якорей и замечаний; перенос правок; смысловые дубли."""
        with self.services.db.uow() as uow:
            old_facts = uow.facts.list_for_paper(paper.id)
            old_status = {i.key: i.status for i in uow.issues.list(paper.id)} if keep_review else {}
            for rec in issues:
                st = old_status.get(rec.key)
                if st is not None:
                    rec.status = st
            uow.papers.save_processed(paper.id, bundle, facts, anchors, issues, stats=stats, model_version=str(mv),
                                      versions=versions)
            self._restore_review(uow, paper.id, old_facts, keep_review=keep_review)
            if profile is not None:
                self._save_sub_tables(uow, paper, profile)
            fields: dict[str, Any] = dict(status=PaperStatus.done, error_code=None,
                                          title_key=publication_title_key(bundle.publication))
            if needs_ocr is not None:
                fields["needs_ocr"] = needs_ocr
            if page_count:
                fields["page_count"] = page_count
            uow.papers.update(paper.id, **fields)
            if self.dedupe:
                refresh_semantic_duplicate(uow, paper.id, bundle.publication)
            elif paper.dup_kind is not None:
                uow.papers.set_duplicate(paper.id, None, None)

    @staticmethod
    def _save_sub_tables(uow, paper, profile: ExtractionProfile) -> None:
        """Строки 7 таблиц шаблона (sub_*) - те же, что выдал бы экспорт CSV: из сохранённой статьи с правками."""
        from ..export import paper_submission_tables

        saved = uow.papers.load_bundle(paper.id, apply_review=True)
        tables, _ = paper_submission_tables(saved, profile, manual_facts=uow.facts.manual_for_paper(paper.id))
        quotes = {r.result_id: r.meta.quote_found for r in saved.results}
        uow.sub.replace(paper.id, paper.corpus_id, "solar", tables, quote_found=quotes)

    @staticmethod
    def _restore_review(uow, paper_id: str, old_facts: list[Fact], *, keep_review: bool) -> None:
        """Ручные факты сохраняются всегда; правки человека переносятся на совпавшие новые факты (keep_review),
        несовпавшие правки становятся ручными фактами."""
        manual = [f for f in old_facts if f.added_by == AddedBy.manual]
        for f in manual:
            nf = uow.facts.add_manual(paper_id, _manual_draft(f, detach=False))
            uow.facts.update_review(nf.id, **_review_changes(f))
        if not keep_review:
            return
        reviewed = [f for f in old_facts if f.added_by != AddedBy.manual and is_reviewed(f)]
        if not reviewed:
            return
        new_auto = uow.facts.list_for_paper(paper_id, added_by=AddedBy.auto)
        pairs, unmatched = match_review(reviewed, new_auto)
        for old, new in pairs:
            uow.facts.update_review(new.id, **_review_changes(old))
        for old in unmatched:
            nf = uow.facts.add_manual(paper_id, _manual_draft(old, detach=True))
            uow.facts.update_review(nf.id, **_review_changes(old))

    # --- recheck ---
    def _recheck(self, paper, file, profile: ExtractionProfile) -> ProcessOutcome:
        from .review import bundle_for_checks, recheck_paper

        quote = None
        s = self.services
        with s.db.uow() as uow:
            if file is not None and file.storage_key and s.store.exists(file.storage_key):
                try:
                    with local_pdf(s.store, file.storage_key, f"{paper.publication_id}.pdf") as pdf:
                        from ..pdfio.quotes import PypdfQuoteIndex

                        quote = PypdfQuoteIndex(pdf)
                        _ = quote.pages  # текст читается, пока файл доступен
                except AppError:
                    quote = None
            diff = recheck_paper(uow, paper.id, profile, quote=quote)
            bundle = bundle_for_checks(uow, paper.id, profile)
            facts = uow.facts.list_for_paper(paper.id)
            issues = [i.record for i in uow.issues.list(paper.id, include_stale=False)]
            stats = paper_stats(bundle, facts, issues, profile)
            uow.papers.update(paper.id, stats=stats, status=PaperStatus.done, error_code=None)
            self._save_sub_tables(uow, paper, profile)
        if diff.opened or diff.closed:
            self._publish(paper.workspace_id, "issues.changed",
                          {"paper_id": paper.id, "opened": len(diff.opened), "closed": len(diff.closed)})
        self._publish(paper.workspace_id, "paper.done",
                      {"paper_id": paper.id, "chat_id": None, "title": paper.title, "facts": stats.facts_count,
                       "issues": stats.open_issues})
        return ProcessOutcome(paper_id=paper.id, status=PaperStatus.done, stats=stats)


def as_decimal(v: Any) -> Decimal | None:
    return v if isinstance(v, Decimal) else None
