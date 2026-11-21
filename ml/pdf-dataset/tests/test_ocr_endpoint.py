"""[faktura-patch] POST /ocr, разбор страниц, кеш, склейка ячеек таблиц, env-ручки. Без vLLM и сети (фейковый парсер)."""
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

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

import api  # noqa: E402
import extract_dataset as ed  # noqa: E402
import ocr_service as osv  # noqa: E402
import pipeline  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def make_pdf(n=3) -> bytes:
    doc = pymupdf.open()
    for i in range(n):
        doc.new_page(width=600, height=800).insert_text((72, 100), f"Page {i + 1} text 12.5")
    data = doc.tobytes()
    doc.close()
    return data


JSON_PAGE = [
    {"index": 0, "label": "text", "native_label": "text", "content": "Hello 12.5", "bbox_2d": [10, 20, 500, 60]},
    {"index": 1, "label": "table", "native_label": "table", "content": "<table><tr><td>1</td></tr></table>",
     "bbox_2d": [10, 100, 900, 400]},
    {"index": 2, "label": "image", "native_label": "image", "content": None, "bbox_2d": [10, 500, 900, 900]},
]


class FakeResult:
    def __init__(self, n):
        self.json_result = [[dict(b) for b in JSON_PAGE] for _ in range(n)]


class FakeParser:
    def __init__(self):
        self.calls = []

    def parse(self, path):
        import pymupdf as m
        d = m.open(path)
        n = d.page_count
        d.close()
        self.calls.append(n)
        return FakeResult(n)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("pipeline:\n  ocr_api:\n    api_host: x\n    api_port: 1\n    model: fake/model\n", encoding="utf-8")
    monkeypatch.setattr(api, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(api, "config_path", lambda: cfg)
    monkeypatch.setattr(api, "ocr_reachable", lambda *a, **k: True)
    parser = FakeParser()
    calls = []

    def fake_isolated(pdf_path, pages, work):  # вместо пула процессов: тот же код воркера с фейковым парсером
        calls.append(list(pages))
        monkeypatch.setattr(pipeline, "_get_parser", lambda c: parser)
        monkeypatch.setattr(pipeline, "config_path", lambda: cfg)
        return osv.ocr_blocks_child(pdf_path, pages, work)

    monkeypatch.setattr(api, "_run_ocr_isolated", fake_isolated)
    c = TestClient(api.app)
    c.calls, c.parser = calls, parser
    return c


def post(c, data=None, **form):
    return c.post("/ocr", files={"file": ("a.pdf", data or make_pdf(3), "application/pdf")}, data=form)


def test_shape_both(client):
    r = post(client, pages="1,3")
    assert r.status_code == 200, r.text
    j = r.json()
    assert [p["page"] for p in j["pages"]] == [1, 3]
    assert j["model"] == "fake/model" and j["cached"] is False
    p = j["pages"][0]
    assert p["markdown"] == "Hello 12.5\n\n<table><tr><td>1</td></tr></table>"  # image пропущен
    assert [b["label"] for b in p["blocks"]] == ["text", "table", "image"]
    assert p["blocks"][1]["bbox"] == [10, 100, 900, 400]
    assert p["page_size_pt"] == [600.0, 800.0]
    assert client.calls == [[1, 3]] and client.parser.calls == [2]


def test_format_markdown_and_blocks(client):
    j = post(client, pages="2", format="markdown").json()
    assert "markdown" in j["pages"][0] and "blocks" not in j["pages"][0]
    j = post(client, pages="2", format="blocks").json()
    assert "blocks" in j["pages"][0] and "markdown" not in j["pages"][0]
    assert post(client, format="xml").status_code == 400


def test_cache_hit(client):
    pdf = make_pdf(3)
    assert post(client, pdf, pages="1-2").json()["cached"] is False
    n = len(client.calls)
    j = post(client, pdf, pages="1-2").json()
    assert j["cached"] is True and len(client.calls) == n
    j = post(client, pdf, pages="2-3").json()  # страница 3 не в кеше: считается только она
    assert j["cached"] is False and client.calls[-1] == [3]


def test_default_all_pages(client):
    assert len(post(client).json()["pages"]) == 3


@pytest.mark.parametrize("spec", ["0", "4", "3-1", "a", "1,,2", "1-9"])
def test_bad_pages_400(client, spec):
    r = post(client, pages=spec)
    assert r.status_code == 400
    j = r.json()
    assert set(j) >= {"code", "message", "retryable", "detail"} and j["code"] == "bad_request"


def test_too_many_pages_413(client, monkeypatch):
    monkeypatch.setenv("OCR_MAX_PAGES", "2")
    r = post(client)
    assert r.status_code == 413 and r.json()["code"] == "too_many_pages"
    assert post(client, pages="1-2").status_code == 200


def test_ocr_unreachable_503(client, monkeypatch):
    monkeypatch.setattr(api, "ocr_reachable", lambda *a, **k: False)
    r = post(client, pages="1")
    assert r.status_code == 503 and r.json()["code"] == "ocr_unavailable" and r.json()["retryable"] is True


def test_cached_works_when_ocr_down(client, monkeypatch):
    pdf = make_pdf(1)
    post(client, pdf)
    monkeypatch.setattr(api, "ocr_reachable", lambda *a, **k: False)
    assert post(client, pdf).status_code == 200


def test_connection_error_in_worker_is_503(tmp_path, monkeypatch):
    class Boom:
        def parse(self, p):
            raise RuntimeError("Failed to connect to API server")
    monkeypatch.setattr(pipeline, "_get_parser", lambda c: Boom())
    monkeypatch.setattr(pipeline, "config_path", lambda: tmp_path / "x.yaml")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(make_pdf(1))
    out = osv.ocr_blocks_child(str(pdf), [1], str(tmp_path))
    assert out["perr"]["code"] == "ocr_unavailable" and out["perr"]["status"] == 503


def test_broken_pdf_422(client):
    r = client.post("/ocr", files={"file": ("a.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 422 and r.json()["code"] == "pdf_broken"


def test_parse_pages():
    assert osv.parse_pages("1,3,5-7", 9) == [1, 3, 5, 6, 7]
    assert osv.parse_pages(None, 3) == [1, 2, 3]
    assert osv.parse_pages(" 2 - 3 , 2", 5) == [2, 3]


# ---------- пробелы в ячейках таблиц ----------
def test_html_cell_text_inserts_space_for_breaks():
    assert ed.html_cell_text("GEMINI<br>1.0 Pro") == "GEMINI 1.0 Pro"
    assert ed.html_cell_text("GPT-4o<br/>\n125-Preview") == "GPT-4o 125-Preview"
    assert ed.html_cell_text("<p>a</p><p>b</p>") == "a b"


def test_html_cell_text_inline_tags_no_space():
    assert ed.html_cell_text("V<sub>OC</sub> (mV)") == "VOC (mV)"
    assert ed.html_cell_text("<b>12</b>.5 &amp; x") == "12.5 & x"
    assert ed.html_cell_text("a   b\n c") == "a b c"  # внутренние пробелы модели сохраняются, лишние схлопываются


def test_table_candidates_use_spaced_cells():
    md = ("<!-- page 1 -->\n\nTable 1 results\n\n<table><tr><th>Model</th><th>PCE (%)</th></tr>"
          "<tr><td>GEMINI<br>1.0 Pro</td><td>12.5</td></tr></table>")
    out = pipeline._table_candidates(md)
    assert out and "GEMINI 1.0 Pro" in out[0]["context"]


# ---------- ручки env ----------
def test_overlay_noop_without_env(tmp_path):
    base = tmp_path / "c.yaml"
    base.write_text("pipeline: {}\n", encoding="utf-8")
    assert pipeline.apply_ocr_overlay(base, env={}) == base


def test_overlay_applies_knobs(tmp_path):
    yaml = pytest.importorskip("yaml")
    base = tmp_path / "c.yaml"
    base.write_text("pipeline:\n  page_loader:\n    pdf_dpi: 200\n    max_tokens: 8192\n  layout:\n    threshold: 0.3\n",
                    encoding="utf-8")
    env = {"OCR_PDF_DPI": "250", "OCR_MAX_TOKENS": "4096", "OCR_LAYOUT_THRESHOLD": "0.25",
           "OCR_LAYOUT_THRESHOLD_BY_CLASS": '{"table": 0.2}', "OCR_LAYOUT_UNCLIP": "1.05,1.1"}
    out = pipeline.apply_ocr_overlay(base, env=env)
    cfg = yaml.safe_load(out.read_text(encoding="utf-8"))["pipeline"]
    assert cfg["page_loader"] == {"pdf_dpi": 250, "max_tokens": 4096}
    assert cfg["layout"]["threshold"] == 0.25 and cfg["layout"]["threshold_by_class"] == {"table": 0.2}
    assert cfg["layout"]["layout_unclip_ratio"] == [1.05, 1.1]
    assert "pdf_dpi: 200" in base.read_text(encoding="utf-8")  # исходный файл не тронут
    with pytest.raises(pipeline.PipelineError):
        pipeline.apply_ocr_overlay(base, env={"OCR_PDF_DPI": "abc"})
