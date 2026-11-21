"""Поток NDJSON ответа ML-1 (§10): разбор, клиент HttpExtractor (откат на JSON, ошибки в потоке), мок-сервер в двух режимах,
ocr_confidence и suggested_domain в ответе."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from faktura.domain.errors import ExtractError
from faktura.domain.extraction import ExtractRequest, GenericOutcome
from faktura.ml import HttpExtractor, MockGoldExtractor
from faktura.ml.mock_server import create_mock_app
from faktura.ml.ndjson import (NDJSON_TYPE, StreamError, StreamInvalid, StreamTruncated, assemble_body,
                               events_from_raw, read_stream)
from faktura.ml.response import file_meta

RAG_GOLD = Path(__file__).resolve().parents[3] / "data" / "caseA_drive" / "dev_gold"


def line(**kw) -> bytes:
    return (json.dumps(kw, ensure_ascii=False) + "\n").encode()


def ml_events(n_results=2500, *, per=1000, error=None, truncate=False, meta=None) -> bytes:
    """Ответ pdf-dataset в NDJSON (как отдаёт ml/pdf-dataset/api.py)."""
    out = [line(type="header", protocol="ndjson/1", ok=True, model_version="pdf-dataset/m", publication_id="p1",
                extract_error=None, failed_tables=[], has_dataset=True,
                meta=meta or {"ocr_confidence": 0.8, "suggested_domain": None})]
    out.append(line(type="table", table="publications", rows=[{"publication_id": "p1", "title": "T", "in_scope": True}]))
    rows = [{"result_id": f"R{i}", "publication_id": "p1", "metric_name": "EM", "metric_value": 0.1} for i in range(n_results)]
    for i in range(0, n_results, per):
        out.append(line(type="table", table="results", rows=rows[i:i + per]))
    if error:
        out.append(line(type="error", **error))
    elif not truncate:
        out.append(line(type="end", counts={"publications": 1, "results": n_results}, ok=True))
    return b"".join(out)


def make(handler, **kw):
    calls = []

    def h(req):
        calls.append(req)
        return handler(req)

    ext = HttpExtractor("http://ml:1", timeout_s=5, sleep=lambda s: None, transport=httpx.MockTransport(h),
                        protocol="pdf_dataset", retry_delays=(0.1,), **kw)
    return ext, calls


def nd_resp(content: bytes) -> httpx.Response:
    return httpx.Response(200, content=content, headers={"content-type": NDJSON_TYPE})


# ---------------- разбор потока ----------------
def test_read_stream_assembles_and_checks_counts():
    h, t, c = read_stream(ml_events(2500).splitlines())
    assert h["publication_id"] == "p1" and len(t["results"]) == 2500 and c["results"] == 2500
    body = assemble_body(h, t, "pdf_dataset")
    assert body["dataset"]["results"][-1]["result_id"] == "R2499" and "protocol" not in body and "type" not in body
    assert body["meta"]["ocr_confidence"] == 0.8








# ---------------- клиент ----------------
















# ---------------- мок: оба режима ----------------
def post(c, pdf, pid, domain, **kw):
    return c.post("/ml/extract", data={"publication_id": pid, "domain": domain, **kw.pop("data", {})},
                  files={"file": (f"{pid}.pdf", pdf, "application/pdf")}, **kw)




@pytest.mark.skipif(not RAG_GOLD.is_dir(), reason="нет data/caseA_drive")
def test_http_client_against_mock_server_ndjson_and_json(settings, extract_req):
    s = settings.model_copy(update={"rag_mock_gold_dir": RAG_GOLD})
    app = TestClient(create_mock_app(s))

    class Transport(httpx.BaseTransport):
        def handle_request(self, request):
            r = app.request(request.method, request.url.path, headers=dict(request.headers), content=request.read())
            return httpx.Response(r.status_code, headers={"content-type": r.headers["content-type"]}, content=r.content)

    outs = []
    for mode in ("ndjson", "json"):
        ext = HttpExtractor("http://ml", transport=Transport(), stream=mode, retry_delays=())
        outs.append(ext.extract(extract_req(pid="D020", domain="rag")))
    a, b = outs
    assert a.tables == b.tables and len(a.tables["results"]) == 408
    assert a.ocr_confidence == b.ocr_confidence


# ---------------- мок: ocr_confidence и suggested_domain ----------------
