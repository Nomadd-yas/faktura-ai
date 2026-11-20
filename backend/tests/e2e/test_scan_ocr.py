"""E2E 3: PDF без текстового слоя (задел RAG, caseA_drive) - диагностика и статус needs_ocr.

Область 2 (RAG) в волне 1 не реализуется, но разбор входа обязан распознать скан и не падать молча
(spec/11-ocr; код файла pdf_no_text / статус needs_ocr). Если caseA_drive нет - тесты пропускаются.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCAN = ROOT / "data" / "caseA_drive" / "dev" / "D002.pdf"   # 17 стр., текстового слоя нет
TEXT = ROOT / "data" / "caseA_drive" / "dev" / "D016.pdf"   # текстовый слой есть

pytestmark = pytest.mark.skipif(not SCAN.is_file(), reason="нет data/caseA_drive")


def test_inspect_pdf_reads_scan_metadata():
    from faktura.pdfio import inspect_pdf

    info = inspect_pdf(SCAN)
    assert info.page_count == 17 and len(info.sha256) == 64


def test_scan_has_no_text_layer_and_text_has():
    from faktura.pdfio import LocalParseAdapter

    ad = LocalParseAdapter()
    assert _has_text(ad, SCAN) is False
    assert _has_text(ad, TEXT) is True


def _has_text(adapter, path: Path) -> bool:
    pages = adapter.layout(path, pages=[1]) if hasattr(adapter, "layout") else adapter.page_info(path)
    p = pages[0]
    return bool(p["has_text_layer"] if isinstance(p, dict) else p.has_text_layer)


def test_scan_in_pipeline_marked_needs_ocr(tmp_path, settings):
    """Прогон по скану: файл получает needs_ocr (или failed с pdf_no_text), конвейер не падает."""
    from faktura.pipeline import build_services

    src = tmp_path / "in"
    src.mkdir()
    shutil.copy2(SCAN, src / "D002.pdf")
    svc = build_services(settings.model_copy(update={"mock_gold_dir": tmp_path}))
    corpus = svc.corpora.create(name="scan", collection="solar")
    svc.corpora.add_files(corpus.corpus_id, src)
    run = svc.runs.start(corpus.corpus_id, kind="full")
    files = svc.corpora.list_files(corpus.corpus_id)
    assert files[0].status == "needs_ocr" or (files[0].status == "failed" and files[0].error["code"] == "pdf_no_text")
    assert run.status in ("done", "done_with_errors")
