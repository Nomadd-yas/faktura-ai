"""ml.build_extractor и faktura.ml.mock_server (REQ-065, REQ-072, Б-1)."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from faktura.domain.errors import AppError
from faktura.domain.extraction import ExtractRequest
from faktura.ml import CachedExtractor, HttpExtractor, MlCache, MockGoldExtractor, RawDirExtractor, build_extractor
from faktura.ml.mock_server import create_mock_app


@pytest.fixture
def cfg(settings, mini_gold_dir):
    return settings.model_copy(update={"mock_gold_dir": mini_gold_dir, "case_dir": None})


# ---------------- build_extractor ----------------
def test_build_default_mock(cfg):
    ext = build_extractor(cfg)
    assert isinstance(ext, MockGoldExtractor) and ext.gold_dir == cfg.mock_gold_dir
    assert ext.issues_mode == cfg.mock_issues


def test_build_mock_ml_only(cfg):
    ext = build_extractor(cfg.model_copy(update={"mock_issues": "ml_only"}))
    assert ext.issues_mode == "ml_only"


def test_build_url_implies_http(cfg):
    ext = build_extractor(cfg, url="http://ml:8000")
    assert isinstance(ext, HttpExtractor) and ext.base_url == "http://ml:8000"


def test_build_http_uses_settings(cfg):
    c = cfg.model_copy(update={"extract_timeout_s": 77, "extract_concurrency": 3, "extract_retry_delays": "1,2,3"})
    ext = build_extractor(c, kind="http", url="http://ml")
    assert ext.timeout_s == 77 and ext.concurrency == 3 and tuple(ext.retry_delays) == (1, 2, 3)


def test_build_http_default_retry_delays(cfg):
    ext = build_extractor(cfg, kind="http", url="http://ml")
    assert tuple(ext.retry_delays) == (30, 120, 600)


def test_build_retry_delays_override(cfg):
    ext = build_extractor(cfg, kind="http", url="http://ml", retry_delays=(0.1,))
    assert tuple(ext.retry_delays) == (0.1,)


def test_build_http_url_from_settings(cfg):
    ext = build_extractor(cfg.model_copy(update={"extractor": "http", "extract_url": "http://cfg-ml"}))
    assert isinstance(ext, HttpExtractor) and ext.base_url == "http://cfg-ml"


def test_build_http_without_url_fails(cfg):
    with pytest.raises((AppError, ValueError, KeyError, TypeError)):
        build_extractor(cfg, kind="http")


def test_build_raw(cfg, tmp_path):
    ext = build_extractor(cfg, kind="raw", raw_dir=tmp_path)
    assert isinstance(ext, RawDirExtractor) and ext.raw_dir == tmp_path


def test_build_raw_without_dir_fails(cfg):
    with pytest.raises((AppError, ValueError, KeyError, TypeError)):
        build_extractor(cfg, kind="raw")


def test_build_unknown_kind_fails(cfg):
    with pytest.raises((AppError, ValueError, KeyError, TypeError)):
        build_extractor(cfg, kind="gpt")


def test_build_http_with_cache_is_wrapped(cfg, tmp_path):
    cache = MlCache(tmp_path)
    ext = build_extractor(cfg, kind="http", url="http://ml", cache=cache)
    assert isinstance(ext, CachedExtractor) and isinstance(ext.inner, HttpExtractor) and ext.cache is cache
    assert ext.name == "http"


def test_build_raw_with_cache_is_wrapped(cfg, tmp_path):
    ext = build_extractor(cfg, kind="raw", raw_dir=tmp_path, cache=MlCache(tmp_path / "c"))
    assert isinstance(ext, CachedExtractor) and isinstance(ext.inner, RawDirExtractor)


def test_build_mock_never_cached(cfg, tmp_path):
    ext = build_extractor(cfg, kind="mock", cache=MlCache(tmp_path))
    assert isinstance(ext, MockGoldExtractor)


# ---------------- mock_server ----------------
@pytest.fixture
def client(cfg):
    return TestClient(create_mock_app(cfg))


def _bridge(client: TestClient) -> httpx.MockTransport:
    """Транспорт для HttpExtractor, ведущий запросы прямо в ASGI-приложение."""

    def handler(request: httpx.Request) -> httpx.Response:
        r = client.request(request.method, request.url.path, content=request.read(),
                           headers={k: v for k, v in request.headers.items() if k.lower() != "host"})
        return httpx.Response(r.status_code, headers=r.headers, content=r.content)

    return httpx.MockTransport(handler)


def test_server_health(client, cfg):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["model_version"] == MockGoldExtractor(cfg.mock_gold_dir).health().model_version


def test_server_extract_known_paper(client, make_pdf):
    pdf = make_pdf(["x"], name="D001.pdf")
    r = client.post("/ml/extract", files={"file": ("D001.pdf", pdf.read_bytes(), "application/pdf")},
                    data={"publication_id": "D001", "domain": "solar"})
    assert r.status_code == 200
    body = r.json()
    assert body["publication"]["publication_id"] == "D001" and body["results"] and body["model_version"]


def test_server_extract_unknown_paper_publication_only(client, make_pdf):
    pdf = make_pdf(["x"], name="Q1.pdf")
    r = client.post("/ml/extract", files={"file": ("Q1.pdf", pdf.read_bytes(), "application/pdf")},
                    data={"publication_id": "Q1", "domain": "solar"})
    assert r.status_code == 200 and r.json()["results"] == []


def test_server_extract_missing_file_is_400(client):
    r = client.post("/ml/extract", data={"publication_id": "D001", "domain": "solar"})
    assert r.status_code in (400, 422)


def test_server_bad_domain_is_400(client, make_pdf):
    pdf = make_pdf(["x"], name="D001.pdf")
    r = client.post("/ml/extract", files={"file": ("D001.pdf", pdf.read_bytes(), "application/pdf")},
                    data={"publication_id": "D001", "domain": "nonsense"})
    assert r.status_code == 400


def test_server_response_matches_contract_shape(client, make_pdf):
    pdf = make_pdf(["x"], name="D001.pdf")
    body = client.post("/ml/extract", files={"file": ("D001.pdf", pdf.read_bytes(), "application/pdf")},
                       data={"publication_id": "D001", "domain": "solar"}).json()
    for key in ("model_version", "publication", "materials", "devices", "layers", "measurements", "results", "issues"):
        assert key in body
    assert not any("normalized_value" in r and r["normalized_value"] not in (None, "") for r in body["results"])


def test_server_plus_http_extractor_equals_direct_mock(cfg, client, make_pdf):
    direct = MockGoldExtractor(cfg.mock_gold_dir)
    http = HttpExtractor("http://ml", timeout_s=5, retry_delays=(), sleep=lambda s: None, transport=_bridge(client))
    for pid in ("D001", "D003", "UNKNOWN"):
        pdf = make_pdf(["x"], name=f"{pid}.pdf")
        req = ExtractRequest(publication_id=pid, pdf_path=pdf, sha256="0" * 64)
        a, b = direct.extract(req), http.extract(req)
        assert a.model_version == b.model_version
        assert a.bundle.model_dump() == b.bundle.model_dump()


def test_http_health_via_server(client, cfg):
    http = HttpExtractor("http://ml", timeout_s=5, retry_delays=(), sleep=lambda s: None, transport=_bridge(client))
    h = http.health()
    assert h.status == "ok" and h.model_version == MockGoldExtractor(cfg.mock_gold_dir).health().model_version


@pytest.mark.data
def test_server_on_real_dev_gold_with_http(settings, dev_dir, dev_gold_dir, gold_bundles):
    cfg = settings.model_copy(update={"mock_gold_dir": dev_gold_dir})
    c = TestClient(create_mock_app(cfg))
    http = HttpExtractor("http://ml", timeout_s=5, retry_delays=(), sleep=lambda s: None, transport=_bridge(c))
    for gb in gold_bundles[:5]:
        out = http.extract(ExtractRequest(publication_id=gb.publication_id,
                                          pdf_path=dev_dir / f"{gb.publication_id}.pdf", sha256="0" * 64))
        assert len(out.bundle.results) == len(gb.results)
        assert len(out.bundle.issues) == len(gb.issues)
