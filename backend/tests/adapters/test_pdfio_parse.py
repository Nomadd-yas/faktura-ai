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


def test_implements_protocol(adapter):
    assert isinstance(adapter, ParseAdapter)


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


def test_parse_scan_needs_ocr(adapter, make_scan_pdf):
    doc = adapter.parse(make_scan_pdf(2))
    assert doc.needs_ocr is True
    assert all(pg.needs_ocr for pg in doc.pages)
    assert doc.pages[0].has_text_layer is False and doc.pages[0].char_count < 100
    assert doc.pages[0].image_ratio > 0.7


def test_parse_mixed_only_scan_page_flagged(adapter, tmp_path):
    import fitz

    d = fitz.open()
    d.new_page().insert_text((72, 100), "Plenty of text on this page " * 8, fontsize=11)
    pg = d.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200))
    pix.clear_with(200)
    pg.insert_image(pg.rect, pixmap=pix)
    p = tmp_path / "mixed.pdf"
    d.save(p)
    doc = adapter.parse(p)
    assert [x.needs_ocr for x in doc.pages] == [False, True]
    assert doc.needs_ocr is True


def test_parse_small_image_with_text_is_not_scan(adapter, tmp_path):
    import fitz

    d = fitz.open()
    pg = d.new_page()
    pg.insert_text((72, 100), "Enough running text here to be a normal page. " * 6, fontsize=10)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
    pix.clear_with(100)
    pg.insert_image(fitz.Rect(400, 600, 450, 650), pixmap=pix)
    p = tmp_path / "fig.pdf"
    d.save(p)
    assert adapter.parse(p).needs_ocr is False


def test_parse_blank_page_not_scan(adapter, make_pdf):
    # пустая страница без изображений: текста нет, но это не скан
    doc = adapter.parse(make_pdf([""]))
    assert doc.pages[0].image_ratio == 0 and doc.needs_ocr is False


@pytest.mark.data
def test_parse_real_dev_pdf_has_text(adapter, dev_dir):
    doc = adapter.parse(sorted(dev_dir.glob("*.pdf"))[0])
    assert doc.page_count == len(doc.text_by_page) > 0
    assert doc.needs_ocr is False  # область 1: сканов нет


# ---------------- render_page ----------------
def _png_width(b: bytes) -> int:
    return int.from_bytes(b[16:20], "big")


def test_render_png(adapter, make_pdf):
    b = adapter.render_page(make_pdf(["hello"]), 1, scale=1.0, fmt="png")
    assert b[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_webp_default(adapter, make_pdf):
    b = adapter.render_page(make_pdf(["hello"]), 1)
    assert b[:4] == b"RIFF" and b[8:12] == b"WEBP"


def test_render_scale_changes_size(adapter, make_pdf):
    p = make_pdf(["hello"])
    w1 = _png_width(adapter.render_page(p, 1, scale=1.0, fmt="png"))
    w2 = _png_width(adapter.render_page(p, 1, scale=2.0, fmt="png"))
    assert w2 == pytest.approx(2 * w1, abs=2)


def test_render_second_page_is_one_based(adapter, make_pdf):
    p = make_pdf(["a", "b"])
    assert adapter.render_page(p, 2, scale=1.0, fmt="png")


@pytest.mark.parametrize("n", [0, 3, -1])
def test_render_page_out_of_range(adapter, make_pdf, n):
    with pytest.raises((ValueError, IndexError, AppError)):
        adapter.render_page(make_pdf(["a", "b"]), n)


# ---------------- page_size / find_rects ----------------
def test_page_size_default_a4(adapter, make_pdf):
    w, h = adapter.page_size(make_pdf(["a"]), 1)
    assert (w, h) == (pytest.approx(595, abs=2), pytest.approx(842, abs=2))


def test_page_size_custom(adapter, tmp_path):
    import fitz

    d = fitz.open()
    d.new_page(width=300, height=500)
    p = tmp_path / "c.pdf"
    d.save(p)
    assert adapter.page_size(p, 1) == (300.0, 500.0)


def test_page_size_out_of_range(adapter, make_pdf):
    with pytest.raises((ValueError, IndexError, AppError)):
        adapter.page_size(make_pdf(["a"]), 5)


def test_find_rects_finds_phrase(adapter, make_pdf):
    p = make_pdf(["The champion device reached PCE = 5.25% in this work"])
    rects = adapter.find_rects(p, 1, "reached PCE = 5.25%")
    assert rects and all(isinstance(r, Rect) for r in rects)
    r = rects[0]
    assert 0 <= r.x < 1 and 0 <= r.y < 1 and 0 < r.w <= 1 and 0 < r.h < 0.1
    # текст вставлен у y=100 pt на странице высотой 842 pt, начало координат сверху
    assert r.y == pytest.approx(100 / 842, abs=0.03)


def test_find_rects_not_found(adapter, make_pdf):
    assert adapter.find_rects(make_pdf(["abc def"]), 1, "zzz not here") == []


def test_find_rects_other_page_not_matched(adapter, make_pdf):
    p = make_pdf(["alpha", "beta unique phrase"])
    assert adapter.find_rects(p, 1, "beta unique phrase") == []
    assert adapter.find_rects(p, 2, "beta unique phrase")


def test_find_rects_empty_needle(adapter, make_pdf):
    assert adapter.find_rects(make_pdf(["abc"]), 1, "") == []


# ---------------- bbox_to_rects ----------------
def test_bbox_top_left():
    rects = bbox_to_rects((72.0, 100.0, 300.0, 112.0), 600, 800)
    assert len(rects) == 1
    r = rects[0]
    assert r.x == pytest.approx(72 / 600) and r.y == pytest.approx(100 / 800)
    assert r.w == pytest.approx(228 / 600) and r.h == pytest.approx(12 / 800)


def test_bbox_bottom_left_flips_y():
    r = bbox_to_rects((72.0, 100.0, 300.0, 112.0), 600, 800, origin="bottom-left")[0]
    assert r.x == pytest.approx(72 / 600)
    assert r.y == pytest.approx((800 - 112) / 800)  # верхний край прямоугольника
    assert r.w == pytest.approx(228 / 600) and r.h == pytest.approx(12 / 800)


def test_bbox_origins_differ_but_same_size():
    a = bbox_to_rects((10, 10, 50, 30), 100, 200)[0]
    b = bbox_to_rects((10, 10, 50, 30), 100, 200, origin="bottom-left")[0]
    assert (a.w, a.h) == (b.w, b.h) and a.y != b.y


def test_bbox_full_page():
    r = bbox_to_rects((0, 0, 595, 842), 595, 842)[0]
    assert (r.x, r.y, r.w, r.h) == pytest.approx((0, 0, 1, 1))


def test_bbox_swapped_corners_normalised():
    r = bbox_to_rects((300.0, 112.0, 72.0, 100.0), 600, 800)[0]
    assert r.w > 0 and r.h > 0


def test_bbox_example_from_spec():
    r = bbox_to_rects((356.1, 438.0, 391.2, 452.3), 595, 842)[0]
    assert 0 < r.x < 1 and 0 < r.y < 1 and r.w == pytest.approx(35.1 / 595)
