"""HttpExtractor в режиме protocol=pdf_dataset (прототип ML-1) на httpx.MockTransport."""
from __future__ import annotations

import httpx
import pytest

from faktura.domain.errors import ExtractError
from faktura.ml import HttpExtractor
from faktura.ml.pdf_dataset import pdf_dataset_to_contract


def body(**kw):
    b = {"ok": True, "publication_id": "p1", "extract_error": None, "failed_tables": [],
         "dataset": {"publications": [{"publication_id": "zzz", "title": "T", "year": 2020, "in_scope": True}],
                     "materials": [{"material_id": "m1", "publication_id": "p1"}],
                     "devices": [{"device_id": "d1", "publication_id": "p1", "area_value": 0.09}],
                     "layers": [{"layer_id": "l1", "device_id": "d1", "publication_id": "p1",
                                 "thickness_value": 300, "temperature_c": 100, "time_value": 10}]},
         "meta": {"model": "deepseek/x"}}
    b.update(kw)
    return b


def make(handler, **kw):
    calls = []

    def h(req):
        calls.append(req)
        return handler(req)
    kw.setdefault("retry_delays", (0.1, 0.1))
    ext = HttpExtractor("http://ml:1", timeout_s=5, sleep=lambda s: None, transport=httpx.MockTransport(h),
                        protocol="pdf_dataset", **kw)
    return ext, calls


def test_conversion_and_renames():
    c = pdf_dataset_to_contract(body(), "p1")
    assert c["model_version"] == "pdf-dataset/deepseek/x" and c["publication"]["publication_id"] == "p1"
    assert c["devices"][0]["active_area"] == 0.09 and "area_value" not in c["devices"][0]
    assert c["layers"][0]["layer_thickness"] == 300 and c["layers"][0]["annealing_temperature"] == 100
    assert c["results"] == [] and c["issues"] == []


def test_extract_ok_path_and_out_of_scope(extract_req):
    b = body()
    b["dataset"]["publications"][0].update(in_scope=False, exclusion_reason="review")
    ext, calls = make(lambda r: httpx.Response(200, json=b))
    out = ext.extract(extract_req())
    assert calls[0].url.path == "/extract"
    assert out.model_version == "pdf-dataset/deepseek/x"
    assert len(out.bundle.devices) == 1


def test_not_ok_null_dataset(extract_req):
    ext, calls = make(lambda r: httpx.Response(200, json=body(ok=False, dataset=None, extract_error="boom")))
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == "ml_error" and "boom" in e.value.message


def test_partial_has_warning(extract_req):
    ext, _ = make(lambda r: httpx.Response(200, json=body(ok=False, extract_error="x", failed_tables=["layers"])))
    out = ext.extract(extract_req())
    assert any("layers" in w for w in out.warnings)


def test_500_no_retry(extract_req):
    ext, calls = make(lambda r: httpx.Response(500, json={"detail": "no api key"}))
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == "ml_error" and "no api key" in e.value.message and not e.value.retryable
    assert len(calls) == 1


def test_timeout_no_retry_when_disabled(extract_req):
    def h(r):
        raise httpx.ReadTimeout("t")
    ext, calls = make(h, retry_on_timeout=False)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == "ml_timeout" and len(calls) == 1


def test_health_without_version():
    ext, _ = make(lambda r: httpx.Response(200, json={"status": "ok"}))
    h = ext.health()
    assert h.status == "ok" and h.model_version is None


def test_cache_ignores_other_ml_and_reuses_own(extract_req, tmp_path, example_7_0_1):
    from faktura.ml.adapters import CachedExtractor, MlCache
    cache = MlCache(tmp_path)
    req = extract_req(version="")
    cache.put(req.sha256, "gold-mock", example_7_0_1)  # ответ мока для того же PDF
    ext, calls = make(lambda r: httpx.Response(200, json=body()) if r.method == "POST"
                      else httpx.Response(200, json={"status": "ok"}))
    c = CachedExtractor(ext, cache)
    assert c.extract(req).model_version == "pdf-dataset/deepseek/x"
    assert c.extract(req).model_version == "pdf-dataset/deepseek/x"
    assert sum(r.method == "POST" for r in calls) == 1  # второй раз — из своего кеша, мок не подмешан
