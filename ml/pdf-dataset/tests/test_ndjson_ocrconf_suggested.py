"""[faktura-patch] §10 / §7а.3: NDJSON-режим /extract, ocr_confidence, suggested_domain. Без сети и ключей."""
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

import extract_dataset as ed  # noqa: E402
import pipeline  # noqa: E402


# ---------- suggested_domain ----------
def _pub_fake(row):
    def fake(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
             retry=True, reasoning_tokens=None):
        if label == "publications":
            assert "suggested_domain" in schema["properties"]["publications"]["items"]["properties"]
            return {"publications": [dict(row)]}
        return {schema_name.replace("table_", ""): []}
    return fake


@pytest.mark.parametrize("domain,sug,expected", [
    ("solar", "rag", "rag"), ("solar", "solar", None), ("rag", "solar", "solar"), ("rag", None, None)])
def test_suggested_domain_goes_to_meta_not_row(monkeypatch, tmp_path, domain, sug, expected):
    monkeypatch.setattr(ed, "call_llm", _pub_fake({"in_scope": False, "exclusion_reason": "x", "suggested_domain": sug}))
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    md = tmp_path / "a.md"
    md.write_text("text", encoding="utf-8")
    meta = tmp_path / "m.json"
    res = ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k", domain=domain,
                     meta_path=str(meta))
    assert "suggested_domain" not in res["dataset"]["publications"][0]
    assert json.loads(meta.read_text(encoding="utf-8"))["suggested_domain"] == expected


def test_suggested_domain_ignored_when_in_scope(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "call_llm", _pub_fake({"in_scope": True, "suggested_domain": "rag"}))
    monkeypatch.setattr(ed, "OpenAI", lambda **kw: object())
    md = tmp_path / "a.md"
    md.write_text("text", encoding="utf-8")
    meta = tmp_path / "m.json"
    ed.extract(md, publication_id="P1", out=str(tmp_path / "o.json"), api_key="k", domain="solar",
               meta_path=str(meta), no_issues=True)
    assert json.loads(meta.read_text(encoding="utf-8"))["suggested_domain"] is None


def test_prompts_mention_suggested_domain():
    assert "suggested_domain" in ed.STEP_PROMPTS["publications"]
    assert "suggested_domain" in ed.RAG_STEP_PROMPTS["publications"]
    assert "suggested_domain" not in ed.TABLES["publications"]["properties"]  # шаблон не тронут


# ---------- ocr_confidence ----------
def test_ocr_confidence_text_layer_only():
    pages = [{"source": "pdf", "reason": "text_layer_ok"}, {"source": "pdf", "reason": "mode_pdf; text_layer_poor"}]
    v, basis = pipeline.compute_ocr_confidence(pages)
    assert v == 0.75 and basis["pdf_pages"] == 2 and basis["ocr_pages"] == 0


def test_ocr_confidence_ocr_heuristic_and_native():
    clean = {"source": "ocr", "reason": "text_layer_none", "text": "Normal recognised text. " * 20}
    garbage = {"source": "ocr", "text": "�" * 50 + "ab" * 20}
    empty = {"source": "ocr", "text": ""}
    assert pipeline.ocr_page_score(clean["text"]) == 0.9
    assert pipeline.ocr_page_score(garbage["text"]) == 0.0
    assert pipeline.ocr_page_score("") == 0.5
    v, basis = pipeline.compute_ocr_confidence([clean, empty])
    assert v == 0.7 and basis["method"] == "text_layer_ocr_heuristic"
    v, basis = pipeline.compute_ocr_confidence([dict(clean, conf=0.8), dict(empty, conf=0.6)])
    assert v == 0.7 and basis["method"] == "glmocr_confidence"
    assert pipeline.compute_ocr_confidence([]) == (None, {"method": "none", "pages": 0})


def test_ocr_page_confs_from_blocks():
    jr = [[{"content": "a", "confidence": 0.9}, {"content": "b", "score": 0.7}], [{"content": "c"}]]
    assert pipeline._ocr_page_confs(jr) == [pytest.approx(0.8), None]
    assert pipeline._ocr_page_confs({"x": 1}) is None


# ---------- NDJSON ----------
def _body(n_results=2500):
    return {"ok": True, "model_version": "pdf-dataset/m", "publication_id": "P1", "extract_error": None,
            "failed_tables": [], "out_dir": "/out/x", "meta": {"ocr_confidence": 0.9, "suggested_domain": None},
            "dataset": {"publications": [{"publication_id": "P1"}],
                        "results": [{"result_id": f"R{i}"} for i in range(n_results)], "issues": []}}


def _parse(chunks):
    return [json.loads(c) for c in chunks]


def test_wants_ndjson_negotiation():
    pytest.importorskip("fastapi")
    import api
    assert api.wants_ndjson("application/x-ndjson", None)
    assert api.wants_ndjson("application/json, application/x-ndjson;q=0.9", None)
    assert not api.wants_ndjson("application/json", None) and not api.wants_ndjson(None, None)
    assert api.wants_ndjson(None, "ndjson") and not api.wants_ndjson("application/x-ndjson", "json")


def test_ndjson_events_shape():
    pytest.importorskip("fastapi")
    import api
    ev = _parse(api.ndjson_events(_body(), rows_per_line=1000))
    assert ev[0]["type"] == "header" and ev[0]["meta"]["ocr_confidence"] == 0.9 and ev[0]["has_dataset"] is True
    tables = [e for e in ev if e["type"] == "table"]
    assert [len(e["rows"]) for e in tables if e["table"] == "results"] == [1000, 1000, 500]
    assert ev[-1] == {"type": "end", "counts": {"publications": 1, "results": 2500, "issues": 0}, "ok": True}


def test_ndjson_events_error_in_stream():
    pytest.importorskip("fastapi")
    import api
    body = _body()
    body["dataset"]["issues"] = [{"y": object()}]  # json.dumps не сериализует object()
    ev = _parse(api.ndjson_events(body))
    assert ev[-1]["type"] == "error" and ev[-1]["code"] == "ml_error" and ev[-1]["detail"] == ev[-1]["message"]
    assert ev[-1]["retryable"] is False and not any(e["type"] == "end" for e in ev)


def _client(monkeypatch):
    pytest.importorskip("fastapi.testclient")
    pytest.importorskip("multipart")
    from fastapi.testclient import TestClient
    import api
    result = {"ok": True, "publication_id": "P1", "extract_error": None, "failed_tables": [], "out_dir": "/out",
              "model_version": "pdf-dataset/m", "meta": {"counts": {"results": 3}}, "dataset": _body(3)["dataset"]}
    monkeypatch.setattr(api, "_run_isolated", lambda *a, **k: {"result": result})
    return TestClient(api.app)


def test_extract_endpoint_ndjson_and_json_default(monkeypatch):
    c = _client(monkeypatch)
    files = {"file": ("a.pdf", b"%PDF-1.4")}
    r = c.post("/extract", files=files, data={"domain": "rag"})
    assert r.headers["content-type"].startswith("application/json") and r.json()["dataset"]["results"]
    r = c.post("/extract", files=files, data={"domain": "rag"}, headers={"Accept": "application/x-ndjson"})
    assert r.headers["content-type"].startswith("application/x-ndjson")
    ev = [json.loads(x) for x in r.text.splitlines()]
    assert [e["type"] for e in ev] == ["header", "table", "table", "end"]
    r = c.post("/extract", files=files, data={"domain": "rag", "stream": "ndjson"})
    assert r.headers["content-type"].startswith("application/x-ndjson")
