"""pdfio.LocalParseAdapter и bbox_to_rects (REQ-010, spec/11 §11.3, К-2)."""
from __future__ import annotations

import pytest

from faktura.domain.errors import AppError
from faktura.domain.extraction import ParsedDocument
from faktura.domain.ports import ParseAdapter
from faktura.domain.records import Rect
from faktura.pdfio import LocalParseAdapter, bbox_to_rects


@pytest.fixture
def adapter() -> LocalParseAdapter:
    return LocalParseAdapter()




# ---------------- parse ----------------
def test_parse_text_pdf(adapter, make_pdf):
    p = make_pdf(["First page text " * 10, "Second page text " * 10])
    doc = adapter.parse(p)
    assert isinstance(doc, ParsedDocument)
    assert doc.page_count == 2 and len(doc.pages) == 2 and len(doc.text_by_page) == 2
    assert doc.needs_ocr is False
    assert "First page" in doc.text_by_page[0] and "Second page" in doc.text_by_page[1]
    assert [pg.n for pg in doc.pages] == [1, 2]
    pg = doc.pages[0]
    assert pg.has_text_layer and pg.char_count > 100 and not pg.needs_ocr
    assert pg.width_pt == pytest.approx(595, abs=2) and pg.height_pt == pytest.approx(842, abs=2)
    assert doc.parser_version












# ---------------- render_page ----------------
def _png_width(b: bytes) -> int:
    return int.from_bytes(b[16:20], "big")












# ---------------- page_size / find_rects ----------------














# ---------------- bbox_to_rects ----------------










