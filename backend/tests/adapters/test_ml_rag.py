"""Область rag в адаптерах ML-1: pdf_dataset -> таблицы без переименований, HTTP, мок на dev_gold, мок-сервер, кеш."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from faktura.domain.errors import ExtractError
from faktura.domain.extraction import ExtractRequest, GenericOutcome
from faktura.domain.registry import get_domain
from faktura.ml import CachedExtractor, HttpExtractor, MlCache, MockGoldExtractor
from faktura.ml.generic import parse_generic_response, pdf_dataset_to_generic
from faktura.ml.mock_server import create_mock_app

RAG_GOLD = Path(__file__).resolve().parents[3] / "data" / "caseA_drive" / "dev_gold"
SPEC = get_domain("rag")


def body(**kw):
    b = {"ok": True, "publication_id": "p1", "extract_error": None, "failed_tables": [],
         "dataset": {"publications": [{"publication_id": "zzz", "title": "T", "year": 2020, "in_scope": True}],
                     "configurations": [{"config_id": "C1", "publication_id": "p1", "top_k": 5, "area_value": 1}],
                     "datasets": [], "tasks": [], "experiments": [],
                     "results": [{"result_id": "R1", "publication_id": "p1", "metric_name": "EM",
                                  "metric_value": 0.2, "source_page": 3}],
                     "issues": []},
         "meta": {"model": "deepseek/x"}, "model_version": "pdf-dataset/deepseek/x"}
    b.update(kw)
    return b


def make(handler, **kw):
    calls = []

    def h(req):
        calls.append(req)
        return handler(req)

    ext = HttpExtractor("http://ml:1", timeout_s=5, sleep=lambda s: None, transport=httpx.MockTransport(h),
                        protocol="pdf_dataset", retry_delays=(0.1,), **kw)
    return ext, calls


def test_conversion_keeps_columns_no_renames():
    raw = pdf_dataset_to_generic(body(), "p1", "rag")
    assert raw["domain"] == "rag" and raw["model_version"] == "pdf-dataset/deepseek/x"
    assert set(raw["tables"]) == set(SPEC.tables)
    assert raw["tables"]["configurations"][0]["area_value"] == 1  # лишние ключи не переименовываются
    assert raw["tables"]["results"][0]["metric_value"] == 0.2


def test_parse_generic_forces_publication_id_and_keeps_raw():
    out = parse_generic_response(pdf_dataset_to_generic(body(), "p1", "rag"), "p1", SPEC)
    assert isinstance(out, GenericOutcome) and out.tables["publications"] == [
        {"publication_id": "p1", "title": "T", "year": 2020, "in_scope": True}]
    assert out.raw["tables"]["results"] and out.model_version == "pdf-dataset/deepseek/x"


def test_http_rag_ok_and_domain_in_form(extract_req):
    ext, calls = make(lambda r: httpx.Response(200, json=body()))
    out = ext.extract(extract_req(pid="p1", domain="rag"))
    assert calls[0].url.path == "/extract" and b'name="domain"' in calls[0].content and b"rag" in calls[0].content
    assert isinstance(out, GenericOutcome) and out.tables["results"][0]["result_id"] == "R1"


def test_http_rag_partial_warns_and_errors_like_solar(extract_req):
    ext, _ = make(lambda r: httpx.Response(200, json=body(ok=False, failed_tables=["results"], extract_error="x")))
    assert "results" in ext.extract(extract_req(pid="p1", domain="rag")).warnings[0]
    ext, _ = make(lambda r: httpx.Response(200, json=body(ok=False, dataset=None, extract_error="boom")))
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req(pid="p1", domain="rag"))
    assert e.value.code == "ml_error"
    ext, _ = make(lambda r: httpx.Response(200, json={"ok": True}))
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req(pid="p1", domain="rag"))
    assert e.value.code == "ml_schema_mismatch"
    ext, _ = make(lambda r: httpx.Response(422, json={"code": "pdf_encrypted", "message": "m", "retryable": False}))
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req(pid="p1", domain="rag"))
    assert e.value.code == "pdf_encrypted"


def test_cache_is_domain_aware(extract_req, tmp_path):
    ext, calls = make(lambda r: httpx.Response(200, json=body()))
    cached = CachedExtractor(ext, MlCache(tmp_path / "c"))
    req = extract_req(pid="p1", domain="rag", version="pdf-dataset/deepseek/x")
    cached.extract(req)
    again = cached.extract(req)
    assert len(calls) == 1 and again.from_cache and isinstance(again, GenericOutcome)


@pytest.mark.skipif(not RAG_GOLD.is_dir(), reason="нет data/caseA_drive")
class TestGoldMock:
    def test_mock_rag_rows_and_unknown(self, extract_req, tmp_path):
        ext = MockGoldExtractor(tmp_path / "none", rag_gold_dir=RAG_GOLD)
        out = ext.extract(extract_req(pid="D020", domain="rag"))
        assert isinstance(out, GenericOutcome) and out.model_version.startswith("mock-gold-")
        assert len(out.tables["configurations"]) == 3 and len(out.tables["results"]) == 408
        assert set(out.tables["results"][0]) == set(SPEC.tables["results"])
        none = ext.extract(extract_req(pid="NOPE", domain="rag"))
        assert none.tables["publications"] == [{"publication_id": "NOPE"}] and none.tables["results"] == []
        assert ext.health().status == "ok"  # эталона solar нет, но есть rag

    def test_mock_server_accepts_rag(self, settings, extract_req):
        s = settings.model_copy(update={"rag_mock_gold_dir": RAG_GOLD})
        c = TestClient(create_mock_app(s))
        pdf = extract_req(pid="D020").pdf_path.read_bytes()
        r = c.post("/ml/extract", data={"publication_id": "D020", "domain": "rag"},
                   files={"file": ("D020.pdf", pdf, "application/pdf")})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["domain"] == "rag" and len(j["tables"]["results"]) == 408
        bad = c.post("/ml/extract", data={"publication_id": "D020", "domain": "xyz"},
                     files={"file": ("D020.pdf", pdf, "application/pdf")})
        assert bad.status_code == 400
        # контракт HTTP (protocol=contract) тоже разбирает ответ rag
        out = parse_generic_response(j, "D020", SPEC)
        assert len(out.tables["issues"]) == 8


def test_extract_request_default_domain_unchanged(extract_req):
    assert ExtractRequest(publication_id="x", pdf_path=Path("a"), sha256="a" * 64).domain == "solar"
