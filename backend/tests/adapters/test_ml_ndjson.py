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


def test_read_stream_errors():
    with pytest.raises(StreamTruncated):
        read_stream(ml_events(truncate=True).splitlines())
    with pytest.raises(StreamTruncated):
        read_stream([])
    with pytest.raises(StreamError) as ei:
        read_stream(ml_events(error={"code": "ml_error", "message": "boom", "retryable": False,
                                     "detail": "boom"}).splitlines())
    assert ei.value.code == "ml_error" and not ei.value.retryable
    with pytest.raises(StreamInvalid):
        read_stream([line(type="table", table="x", rows=[])])  # без заголовка
    with pytest.raises(StreamInvalid):
        read_stream([b"not json"])
    bad = ml_events(10).replace(b'"results": 10}', b'"results": 11}')
    with pytest.raises(StreamInvalid):
        read_stream(bad.splitlines())


def test_events_from_raw_roundtrip_flat_and_nested():
    flat = {"model_version": "v", "publication": {"publication_id": "p"}, "results": [{"result_id": f"R{i}"} for i in range(5)],
            "materials": [], "ocr_confidence": 0.5}
    h, t, _ = read_stream(b"".join(events_from_raw(flat, rows_per_line=2)).splitlines())
    back = assemble_body(h, t, "contract")
    assert back["publication"] == {"publication_id": "p"} and len(back["results"]) == 5 and back["ocr_confidence"] == 0.5
    nested = {"model_version": "v", "domain": "rag", "tables": {"publications": [{"publication_id": "p"}], "results": [{"a": 1}]}}
    h, t, _ = read_stream(b"".join(events_from_raw(nested)).splitlines())
    assert assemble_body(h, t, "contract") == nested


def test_file_meta_validation():
    assert file_meta({"ocr_confidence": 0.5, "suggested_domain": "RAG"}) == (0.5, "rag")
    assert file_meta({"ocr_confidence": 1.5, "suggested_domain": "chem"}) == (None, None)
    assert file_meta({"ocr_confidence": True}) == (None, None) and file_meta(None) == (None, None)


# ---------------- клиент ----------------
def test_client_ndjson_default_rag(extract_req):
    ext, calls = make(lambda r: nd_resp(ml_events(2500)))
    out = ext.extract(extract_req(pid="p1", domain="rag"))
    assert isinstance(out, GenericOutcome) and len(out.tables["results"]) == 2500
    assert out.ocr_confidence == 0.8 and out.suggested_domain is None
    req = calls[0]
    assert NDJSON_TYPE in req.headers["accept"] and b'name="stream"' in req.content
    assert out.raw["ocr_confidence"] == 0.8  # попадает в кеш сырых ответов


def test_client_json_mode_does_not_ask_stream(extract_req):
    ext, calls = make(lambda r: httpx.Response(200, json={"ok": True, "dataset": {"publications": [{}], "results": []},
                                                          "meta": {"ocr_confidence": 0.9}, "model_version": "x"}),
                      stream="json")
    out = ext.extract(extract_req(pid="p1", domain="rag"))
    assert "accept" not in {k.lower() for k in calls[0].headers} or NDJSON_TYPE not in calls[0].headers["accept"]
    assert b'name="stream"' not in calls[0].content and out.ocr_confidence == 0.9


def test_client_falls_back_on_406_and_remembers(extract_req):
    def handler(req):
        if NDJSON_TYPE in req.headers.get("accept", ""):
            return httpx.Response(406, json={"code": "not_acceptable", "message": "no ndjson"})
        return httpx.Response(200, json={"ok": True, "dataset": {"publications": [{}], "results": [{"result_id": "R1"}]},
                                         "meta": {}, "model_version": "x"})

    ext, calls = make(handler)
    out = ext.extract(extract_req(pid="p1", domain="rag"))
    assert len(out.tables["results"]) == 1 and len(calls) == 2 and ext._ndjson_ok is False
    ext.extract(extract_req(pid="p1", domain="rag"))
    assert len(calls) == 3  # повторно NDJSON уже не просим


def test_client_accepts_json_from_server_ignoring_accept(extract_req):
    ext, calls = make(lambda r: httpx.Response(200, json={"ok": True, "dataset": {"publications": [{}], "results": []},
                                                          "meta": {}, "model_version": "x"}))
    out = ext.extract(extract_req(pid="p1", domain="rag"))
    assert out.tables["results"] == [] and len(calls) == 1 and ext._ndjson_ok is True


def test_client_stream_truncated_is_retried(extract_req):
    seq = [ml_events(5, truncate=True), ml_events(5)]
    ext, calls = make(lambda r: nd_resp(seq.pop(0)))
    out = ext.extract(extract_req(pid="p1", domain="rag"))
    assert len(out.tables["results"]) == 5 and len(calls) == 2


def test_client_error_in_stream_maps_like_http(extract_req):
    ext, _ = make(lambda r: nd_resp(ml_events(3, error={"code": "pdf_broken", "message": "bad pdf", "retryable": False,
                                                        "detail": "bad pdf"})))
    with pytest.raises(ExtractError) as ei:
        ext.extract(extract_req(pid="p1", domain="rag"))
    assert ei.value.code == "pdf_broken"
    ext, _ = make(lambda r: nd_resp(ml_events(3, error={"code": "ml_error", "message": "x", "retryable": False,
                                                        "detail": "x"})))
    with pytest.raises(ExtractError) as ei:
        ext.extract(extract_req(pid="p1", domain="rag"))
    assert ei.value.code == "ml_error" and not ei.value.retryable
    seq = [ml_events(3, error={"code": "ocr_unavailable", "message": "ocr", "retryable": True, "detail": "ocr"}),
           ml_events(3)]
    ext, calls = make(lambda r: nd_resp(seq.pop(0)))
    assert len(ext.extract(extract_req(pid="p1", domain="rag")).tables["results"]) == 3 and len(calls) == 2


def test_client_corrupt_stream_is_schema_mismatch(extract_req):
    ext, _ = make(lambda r: nd_resp(b"garbage\n"))
    with pytest.raises(ExtractError) as ei:
        ext.extract(extract_req(pid="p1", domain="rag"))
    assert ei.value.code == "ml_schema_mismatch"


def test_client_ndjson_solar_pdf_dataset(extract_req):
    ds = {"publications": [{"title": "T", "in_scope": False, "exclusion_reason": "sim"}], "materials": [], "devices": [],
          "layers": [], "measurements": [], "results": [], "issues": []}
    ev = [line(type="header", ok=True, model_version="pdf-dataset/m", publication_id="p1", has_dataset=True,
               meta={"suggested_domain": "rag", "ocr_confidence": 0.4})]
    ev += [line(type="table", table=t, rows=rows) for t, rows in ds.items() if rows]
    ev.append(line(type="end", counts={t: len(r) for t, r in ds.items()}))
    ext, _ = make(lambda r: nd_resp(b"".join(ev)))
    out = ext.extract(extract_req(pid="p1", domain="solar"))
    assert out.suggested_domain == "rag" and out.ocr_confidence == 0.4 and out.bundle.publication.in_scope is False


# ---------------- мок: оба режима ----------------
def post(c, pdf, pid, domain, **kw):
    return c.post("/ml/extract", data={"publication_id": pid, "domain": domain, **kw.pop("data", {})},
                  files={"file": (f"{pid}.pdf", pdf, "application/pdf")}, **kw)


@pytest.mark.skipif(not RAG_GOLD.is_dir(), reason="нет data/caseA_drive")
def test_mock_server_both_modes_equal_rag(settings, extract_req):
    s = settings.model_copy(update={"rag_mock_gold_dir": RAG_GOLD})
    c = TestClient(create_mock_app(s))
    pdf = extract_req(pid="D020").pdf_path.read_bytes()
    j = post(c, pdf, "D020", "rag")
    assert j.status_code == 200 and j.headers["content-type"].startswith("application/json")
    n = post(c, pdf, "D020", "rag", headers={"Accept": NDJSON_TYPE})
    assert n.headers["content-type"].startswith(NDJSON_TYPE)
    ev = [json.loads(x) for x in n.text.splitlines()]
    assert ev[0]["type"] == "header" and ev[-1]["type"] == "end"
    assert max(len(e["rows"]) for e in ev if e["type"] == "table") <= 1000
    h, t, _ = read_stream(n.text.splitlines())
    assert assemble_body(h, t, "contract") == j.json()  # поток и JSON дают один и тот же ответ
    f = post(c, pdf, "D020", "rag", data={"stream": "ndjson"})
    assert f.headers["content-type"].startswith(NDJSON_TYPE)
    assert post(c, pdf, "D020", "rag", headers={"Accept": NDJSON_TYPE}, data={"stream": "json"}).headers[
        "content-type"].startswith("application/json")


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
@pytest.mark.skipif(not RAG_GOLD.is_dir(), reason="нет data/caseA_drive")
def test_mock_suggested_domain_cross_domain(settings, mini_gold_dir, extract_req, make_pdf):
    ext = MockGoldExtractor(mini_gold_dir, rag_gold_dir=RAG_GOLD)
    solar_pids = set(ext.publication_ids())
    rag_only = sorted(set(ext.generic("rag").publication_ids()) - solar_pids)
    assert rag_only, "в эталоне rag должна быть статья, которой нет в эталоне solar"
    out = ext.extract(extract_req(pid=rag_only[0], domain="solar"))
    assert out.suggested_domain == "rag" and out.bundle.publication.in_scope is False
    assert out.ocr_confidence == 0.0  # одна страница из одного символа - текстового слоя нет
    good = make_pdf(["recognised text of the page " * 3, "second page with enough text " * 3], name="g.pdf")
    assert ext.extract(extract_req(pid=rag_only[0], domain="solar", pdf=good)).ocr_confidence == 1.0
    own = ext.extract(extract_req(pid=sorted(solar_pids)[0], domain="solar"))
    assert own.suggested_domain is None
    # rag-запрос статьи solar-эталона, которой нет в эталоне rag
    sol_only = sorted(solar_pids - set(ext.generic("rag").publication_ids()))
    if sol_only:
        g = ext.extract(extract_req(pid=sol_only[0], domain="rag"))
        assert g.suggested_domain == "solar" and g.tables["publications"][0]["in_scope"] == "false"
    unknown = ext.extract(extract_req(pid="NOPE", domain="solar"))
    assert unknown.suggested_domain is None
