"""[faktura-patch] R6: TEXT_SOURCE — маршрутизация страниц (текстовый слой / OCR). Синтетические PDF, без сети и OCR."""
import json
import sys
import types
from pathlib import Path

import pytest

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))
try:
    import openai  # noqa: F401
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import pymupdf  # noqa: E402

import extract_dataset as ed  # noqa: E402
import pipeline  # noqa: E402

BODY = ("The quick brown fox jumps over the lazy dog while the experiment was repeated under different "
        "conditions and the results were recorded carefully by the authors of this study. ") * 2


def _page(doc, text=BODY, y=100, x=60, w=470, size=11):
    pg = doc.new_page()
    if text:
        pg.insert_textbox(pymupdf.Rect(x, y, x + w, y + 300), text, fontsize=size)
    return pg


def _grid(pg, x0=80, y0=160, cw=110, rh=22, rows=None):
    rows = rows or [["Sample", "Voc (V)", "PCE (%)"], ["A", "1.10", "12.5"], ["B", "1.05", "11.2"]]
    for i, r in enumerate(rows):
        for j, c in enumerate(r):
            rect = pymupdf.Rect(x0 + j * cw, y0 + i * rh, x0 + (j + 1) * cw, y0 + (i + 1) * rh)
            pg.draw_rect(rect, color=(0, 0, 0), width=0.8)
            pg.insert_textbox(rect + (3, 3, -3, 0), c, fontsize=10)


def _save(doc, tmp_path, name="t.pdf"):
    p = tmp_path / name
    doc.save(str(p))
    return p


def _plan(pdf, mode="auto"):
    return {i["page"]: i for i in pipeline.analyze_text_source(pdf, mode)}


def test_routing_text_page_pdf_empty_page_ocr(tmp_path):
    doc = pymupdf.open()
    _page(doc)
    _page(doc, text="")  # скан без текстового слоя
    plan = _plan(_save(doc, tmp_path))
    assert plan[1]["source"] == "pdf"
    assert plan[2]["source"] == "ocr" and plan[2]["reason"].startswith("text_layer_")


def test_table_caption_with_grid_goes_to_pdf_with_html_table(tmp_path):
    doc = pymupdf.open()
    pg = _page(doc, text="Table 1. Photovoltaic parameters of the devices measured under AM1.5G illumination. " + BODY, y=60)
    _grid(pg, y0=200)
    pdf = _save(doc, tmp_path)
    inf = _plan(pdf)[1]
    assert inf["source"] == "pdf", inf["reason"]
    md = pipeline.build_pdf_page_markdown(inf)
    assert "<table>" in md and "<td>Voc (V)</td>" in md and "<td>12.5</td>" in md


def test_table_caption_with_image_goes_to_ocr(tmp_path):
    doc = pymupdf.open()
    pg = _page(doc, text="", y=100)
    pg.insert_textbox(pymupdf.Rect(60, 60, 540, 100), "Table 1. Results of the experiments " + BODY[:150], fontsize=11)
    pg.insert_textbox(pymupdf.Rect(60, 560, 540, 760), BODY, fontsize=11)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 150), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    pg.insert_image(pymupdf.Rect(60, 110, 540, 450), pixmap=pix)
    inf = _plan(_save(doc, tmp_path))[1]
    assert inf["source"] == "ocr" and inf["reason"] == "image_table"


def test_two_column_reading_order(tmp_path):
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_textbox(pymupdf.Rect(50, 80, 290, 400), "LEFT-ONE first paragraph of the left column.\n\nLEFT-TWO second paragraph of the left column.", fontsize=11)
    pg.insert_textbox(pymupdf.Rect(310, 80, 550, 400), "RIGHT-ONE first paragraph of the right column.\n\nRIGHT-TWO second paragraph of the right column.", fontsize=11)
    pg.insert_textbox(pymupdf.Rect(50, 450, 550, 500), "FULLWIDTH " + BODY[:200], fontsize=11)
    md = pipeline.build_pdf_page_markdown(_plan(_save(doc, tmp_path))[1])
    order = [md.index(k) for k in ("LEFT-ONE", "LEFT-TWO", "RIGHT-ONE", "RIGHT-TWO", "FULLWIDTH")]
    assert order == sorted(order)


def test_running_header_footer_and_page_numbers_dropped(tmp_path):
    doc = pymupdf.open()
    for i in range(4):
        pg = _page(doc, text=f"Unique body paragraph number {i} with enough words to be a text layer. " + BODY, y=100)
        pg.insert_textbox(pymupdf.Rect(60, 20, 540, 40), f"Journal of Foo Vol. 12 ({2020 + i}) 123", fontsize=9)
        pg.insert_textbox(pymupdf.Rect(60, 810, 540, 830), str(i + 1), fontsize=9)
    plan = pipeline.analyze_text_source(_save(doc, tmp_path), "auto")
    md = pipeline.build_pdf_page_markdown(plan[1])
    assert "Journal of Foo" not in md and "Unique body paragraph number 1" in md
    assert not md.strip().endswith("2")


def test_text_source_env_default_and_invalid(monkeypatch):
    monkeypatch.delenv("TEXT_SOURCE", raising=False)
    assert pipeline.text_source_mode() == "ocr"
    monkeypatch.setenv("TEXT_SOURCE", "AUTO")
    assert pipeline.text_source_mode() == "auto"
    monkeypatch.setenv("TEXT_SOURCE", "bogus")
    assert pipeline.text_source_mode() == "ocr"


class _FakeResult:
    def __init__(self, stem, n):
        self.json_result = [[{"label": "text", "content": f"OCR {stem} page {i + 1}"}] for i in range(n)]
        self.markdown_result = "\n".join(f"OCR {stem} page {i + 1}" for i in range(n))
        self._stem = stem

    def save(self, output_dir):
        d = Path(output_dir) / self._stem
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{self._stem}.md").write_text(self.markdown_result, encoding="utf-8")


class _FakeParser:
    def __init__(self):
        self.calls = []

    def parse(self, path):
        n = pymupdf.open(path).page_count
        self.calls.append((Path(path).name, n))
        return _FakeResult(Path(path).stem, n)


@pytest.fixture
def server(monkeypatch, tmp_path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("pipeline: {}\n", encoding="utf-8")
    fake = _FakeParser()
    monkeypatch.setenv("ROUTERAI_API_KEY", "x")
    monkeypatch.setitem(sys.modules, "glmocr", types.ModuleType("glmocr"))
    monkeypatch.setattr(pipeline, "config_path", lambda: cfg)
    monkeypatch.setattr(pipeline, "ocr_reachable", lambda c, timeout=2.0: True)
    monkeypatch.setattr(pipeline, "_get_parser", lambda c: fake)
    monkeypatch.setattr(ed, "extract", lambda *a, **k: {"failed_tables": []})
    return fake


def _three_page_pdf(tmp_path):
    doc = pymupdf.open()
    _page(doc)
    _page(doc, text="")
    _page(doc)
    return _save(doc, tmp_path, "paper.pdf")


def test_ocr_mode_unchanged_and_auto_runs_ocr_only_on_needed_pages(server, monkeypatch, tmp_path):
    pdf = _three_page_pdf(tmp_path)
    monkeypatch.setenv("TEXT_SOURCE", "ocr")
    r = pipeline.run_pipeline(pdf, out_dir=tmp_path / "o1")
    assert server.calls == [("paper.pdf", 3)]
    assert r["meta"]["text_source"]["mode"] == "ocr"
    server.calls.clear()

    monkeypatch.setenv("TEXT_SOURCE", "auto")
    r = pipeline.run_pipeline(pdf, out_dir=tmp_path / "o2")
    assert server.calls == [("paper.ocr_subset.pdf", 1)]  # OCR только на странице 2
    ts = r["meta"]["text_source"]
    assert ts["mode"] == "auto" and ts["ocr_pages"] == 1 and ts["pdf_pages"] == 2
    assert [p["source"] for p in ts["pages"]] == ["pdf", "ocr", "pdf"]
    md = Path(r["paths"]["markdown"]).read_text(encoding="utf-8")
    assert md.index("<!-- page 1 -->") < md.index("<!-- page 2 -->") < md.index("<!-- page 3 -->")
    assert "OCR paper.ocr_subset page 1" in md.split("<!-- page 2 -->")[1].split("<!-- page 3 -->")[0]
    assert "quick brown fox" in md.split("<!-- page 1 -->")[1].split("<!-- page 2 -->")[0]
