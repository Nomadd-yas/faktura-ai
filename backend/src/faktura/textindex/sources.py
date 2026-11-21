"""Источники текста страниц: текстовый слой PDF (PyMuPDF) и OCR из ответа ML; индексация статьи и корпуса."""
from __future__ import annotations

import logging
import re
import threading
import time
import weakref
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from ..storage import schema as S
from . import store as T
from .chunking import MIN_TEXT_LAYER_CHARS

log = logging.getLogger(__name__)

LAZY_BUDGET_S = 10.0
_HTML_TAG = re.compile(r"</?(?:table|thead|tbody|tfoot|tr|td|th|br|p|b|i|sub|sup|div|span|img)\b[^>]*>", re.I)

# FileStore по базе: нужен ленивой индексации (search_text получает только uow); регистрирует build_services
_STORES: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()
_TRIED: "weakref.WeakKeyDictionary[Any, set[str]]" = weakref.WeakKeyDictionary()  # БД -> paper_id без повтора
_LOCK = threading.Lock()


def register_store(db: Any, store: Any) -> None:
    _STORES[db] = store


def pdf_page_texts(pdf: Path) -> list[str]:
    """Текст страниц PDF (индекс 0 = страница 1) из текстового слоя; подряд идущие одинаковые строки (двойная печать
    «жирного») схлопываются."""
    from ..pipeline._pdf import PDF_LOCK

    with PDF_LOCK:
        from ..pdfio.parse import _open

        out: list[str] = []
        with _open(Path(pdf)) as doc:
            for page in doc:
                try:
                    raw = page.get_text("text") or ""
                except Exception:  # noqa: BLE001 - битая страница не мешает остальным
                    raw = ""
                lines: list[str] = []
                for ln in raw.split("\n"):
                    if lines and ln.strip() and ln == lines[-1] and len(ln) > 3:
                        continue
                    lines.append(ln)
                out.append("\n".join(lines))
        return out


def strip_html(text: str) -> str:
    return _HTML_TAG.sub(" ", text or "")


def merge_pages(pdf_pages: list[str], ocr_pages: list[str] | None) -> list[str]:
    """Для страницы с пустым/коротким (< 50 символов) текстовым слоем берём OCR, иначе текст PDF."""
    if not ocr_pages:
        return list(pdf_pages)
    out = []
    for i, t in enumerate(pdf_pages):
        o = strip_html(ocr_pages[i]) if i < len(ocr_pages) and isinstance(ocr_pages[i], str) else ""
        out.append(o if len((t or "").strip()) < MIN_TEXT_LAYER_CHARS and len(o.strip()) > len((t or "").strip()) else t)
    return out


def index_pdf(db: Any, paper: Any, pdf: Path, ocr_pages: list[str] | None = None) -> int:
    """Индексирует обработанную статью (своей транзакцией); сбой индексации не роняет разбор. -> число чанков."""
    try:
        pages = merge_pages(pdf_page_texts(pdf), ocr_pages)
        with db.uow() as uow:
            return T.replace_paper(uow, paper.corpus_id, paper.id, paper.publication_id, pages)
    except Exception:  # noqa: BLE001
        log.exception("Текстовый индекс статьи %s не построен", getattr(paper, "publication_id", "?"))
        return 0


def _pending(uow, corpus_id: str) -> list:
    """Разобранные статьи корпуса, у которых нет текстового индекса."""
    have = T.indexed_paper_ids(uow, corpus_id)
    p = S.papers
    rows = uow.conn.execute(sa.select(p.c.id, p.c.publication_id, p.c.file_id).where(
        p.c.corpus_id == corpus_id, p.c.deleted_at.is_(None), p.c.status == "done").order_by(p.c.seq)).all()
    return [r for r in rows if r.id not in have]


def rebuild_corpus(uow, store: Any, corpus_id: str, *, only_missing: bool = False, budget_s: float | None = None,
                   progress: Any = None) -> dict:
    """Строит индекс корпуса из PDF файлового хранилища (текстовый слой). -> {"papers", "chunks", "skipped"}."""
    from ..pipeline._pdf import local_pdf

    corpus_id = T.norm_corpus_id(corpus_id)
    p = S.papers
    if only_missing:
        todo = _pending(uow, corpus_id)
    else:
        todo = uow.conn.execute(sa.select(p.c.id, p.c.publication_id, p.c.file_id).where(
            p.c.corpus_id == corpus_id, p.c.deleted_at.is_(None)).order_by(p.c.seq)).all()
    t0 = time.monotonic()
    res = {"papers": 0, "chunks": 0, "skipped": 0}
    for r in todo:
        if budget_s is not None and time.monotonic() - t0 > budget_s:
            log.info("Текстовый индекс %s: бюджет %.0f с исчерпан, остальное - командой faktura textindex rebuild",
                     corpus_id, budget_s)
            break
        f = uow.files.get(r.file_id) if r.file_id else None
        if f is None or not f.storage_key:
            res["skipped"] += 1
            continue
        try:
            with local_pdf(store, f.storage_key, f"{r.publication_id}.pdf") as pdf:
                pages = pdf_page_texts(pdf)
            with uow.savepoint():
                n = T.replace_paper(uow, corpus_id, r.id, r.publication_id, pages)
        except Exception as e:  # noqa: BLE001
            log.warning("Текстовый индекс %s: %s не прочитан (%s)", corpus_id, r.publication_id, type(e).__name__)
            res["skipped"] += 1
            continue
        res["papers"] += 1
        res["chunks"] += n
        if progress is not None:
            progress(r.publication_id, n)
    return res


def lazy_index(uow, corpus_id: str) -> None:
    """Первое обращение к поиску по корпусу, проиндексированному не при разборе: из текстового слоя, не дольше
    LAZY_BUDGET_S. Статья, которую не удалось прочитать, повторно не пробуется до перезапуска."""
    store = _STORES.get(uow.db)
    if store is None:
        return
    tried = _TRIED.setdefault(uow.db, set())
    todo = [r for r in _pending(uow, corpus_id) if r.id not in tried]
    if not todo:
        return
    with _LOCK:
        try:
            res = rebuild_corpus(uow, store, corpus_id, only_missing=True, budget_s=LAZY_BUDGET_S)
            if res["papers"] + res["skipped"] >= len(todo):  # бюджет не исчерпан: нечитаемые не пробуем снова
                for r in todo:
                    tried.add(r.id)
        except Exception:  # noqa: BLE001 - поиск не должен падать из-за ленивой индексации
            log.exception("Ленивая индексация %s не удалась", corpus_id)
