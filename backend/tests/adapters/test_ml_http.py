"""ml.HttpExtractor на httpx.MockTransport: ретраи, ошибки, семафор, health (REQ-062…REQ-064, К-15)."""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from faktura.domain.errors import ExtractError
from faktura.domain.extraction import ExtractOutcome
from faktura.domain.ports import ExtractAdapter
from faktura.ml import HttpExtractor

DELAYS = (0.5, 1.0, 2.0)


class Recorder:
    """Скриптуемый обработчик: список ответов/исключений по очереди; последний повторяется."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def calls(self) -> int:
        return len(self.requests)


def ok(raw):
    return httpx.Response(200, json=raw)


def make(handler, sleeps=None, **kw):
    sleeps = sleeps if sleeps is not None else []
    kw.setdefault("retry_delays", DELAYS)
    ext = HttpExtractor("http://ml:8000", timeout_s=5, sleep=sleeps.append,
                        transport=httpx.MockTransport(handler), **kw)
    return ext, sleeps


def test_implements_protocol(example_7_0_1):
    ext, _ = make(Recorder(ok(example_7_0_1)))
    assert isinstance(ext, ExtractAdapter) and ext.name == "http"


# ---------------- успешный путь ----------------
def test_success_returns_parsed_outcome(example_7_0_1, extract_req):
    h = Recorder(ok(example_7_0_1))
    ext, sleeps = make(h)
    out = ext.extract(extract_req())
    assert isinstance(out, ExtractOutcome)
    assert out.model_version == "extract-0.3" and out.bundle.publication_id == "D021"
    assert len(out.bundle.results) == 3 and out.raw["model_version"] == "extract-0.3"
    assert h.calls == 1 and sleeps == []


def test_request_is_multipart_post_to_ml_extract(example_7_0_1, extract_req):
    h = Recorder(ok(example_7_0_1))
    ext, _ = make(h)
    ext.extract(extract_req(request_id="req-123"))
    r = h.requests[0]
    assert r.method == "POST" and r.url.path == "/ml/extract"
    assert r.headers["content-type"].startswith("multipart/form-data")
    body = r.read()
    for needle in (b'name="file"', b'name="publication_id"', b"D021", b'name="domain"', b"solar",
                   b'name="request_id"', b"req-123", b"%PDF-"):
        assert needle in body, needle


def test_trailing_slash_in_base_url(example_7_0_1, extract_req):
    h = Recorder(ok(example_7_0_1))
    ext = HttpExtractor("http://ml:8000/", timeout_s=5, retry_delays=DELAYS, sleep=lambda s: None,
                        transport=httpx.MockTransport(h))
    ext.extract(extract_req())
    assert h.requests[0].url.path == "/ml/extract"


def test_fields_sent_as_json_when_given(example_7_0_1, extract_req):
    h = Recorder(ok(example_7_0_1))
    ext, _ = make(h)
    ext.extract(extract_req())
    assert b'name="fields"' not in h.requests[0].read()  # без профиля поле не отправляется


# ---------------- ретраи ----------------
def test_429_with_retry_after_then_200(example_7_0_1, extract_req):
    h = Recorder(httpx.Response(429, headers={"Retry-After": "7"}, json={"code": "overloaded"}), ok(example_7_0_1))
    ext, sleeps = make(h)
    out = ext.extract(extract_req())
    assert out.bundle.publication_id == "D021" and h.calls == 2
    assert len(sleeps) == 1 and sleeps[0] >= 7


def test_429_without_retry_after_uses_delay(example_7_0_1, extract_req):
    h = Recorder(httpx.Response(429, json={"code": "overloaded"}), ok(example_7_0_1))
    ext, sleeps = make(h)
    ext.extract(extract_req())
    assert sleeps == [DELAYS[0]]


def test_504_then_200(example_7_0_1, extract_req):
    h = Recorder(httpx.Response(504, json={"code": "timeout", "retryable": True}), ok(example_7_0_1))
    ext, sleeps = make(h)
    assert ext.extract(extract_req()).bundle.publication_id == "D021"
    assert h.calls == 2 and sleeps == [DELAYS[0]]


def test_502_then_200(example_7_0_1, extract_req):
    h = Recorder(httpx.Response(502, json={"code": "router_error"}), ok(example_7_0_1))
    ext, _ = make(h)
    assert ext.extract(extract_req()).model_version == "extract-0.3"


def test_connect_error_then_200(example_7_0_1, extract_req):
    h = Recorder(httpx.ConnectError("refused"), ok(example_7_0_1))
    ext, sleeps = make(h)
    assert ext.extract(extract_req()).bundle.publication_id == "D021"
    assert h.calls == 2 and len(sleeps) == 1


def test_read_error_then_200(example_7_0_1, extract_req):
    h = Recorder(httpx.ReadError("reset"), ok(example_7_0_1))
    ext, _ = make(h)
    assert ext.extract(extract_req()).bundle.publication_id == "D021"


def test_retry_delays_follow_schedule(example_7_0_1, extract_req):
    h = Recorder(httpx.Response(502), httpx.Response(502), ok(example_7_0_1))
    ext, sleeps = make(h)
    ext.extract(extract_req())
    assert sleeps == [DELAYS[0], DELAYS[1]]


def test_three_502_gives_retryable_error(extract_req):
    h = Recorder(httpx.Response(502, json={"code": "router_error"}))
    ext, sleeps = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.retryable is True
    assert h.calls == len(DELAYS) + 1 and sleeps == list(DELAYS)


def test_connect_error_exhausted_is_unavailable(extract_req):
    h = Recorder(httpx.ConnectError("refused"))
    ext, _ = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == "ml_unavailable" and e.value.retryable is True


def test_no_retries_configured_fails_immediately(extract_req):
    h = Recorder(httpx.Response(502))
    ext, sleeps = make(h, retry_delays=())
    with pytest.raises(ExtractError):
        ext.extract(extract_req())
    assert h.calls == 1 and sleeps == []


# ---------------- ошибки без ретраев ----------------
@pytest.mark.parametrize("code", ["pdf_no_text", "pdf_encrypted", "pdf_broken"])
def test_422_maps_code_without_retry(extract_req, code):
    h = Recorder(httpx.Response(422, json={"code": code, "message": "x", "retryable": False}))
    ext, sleeps = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == code and e.value.retryable is False
    assert h.calls == 1 and sleeps == []


def test_422_unknown_code_is_ml_error(extract_req):
    h = Recorder(httpx.Response(422, json={"code": "weird"}))
    ext, _ = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert h.calls == 1 and e.value.code in {"ml_error", "pdf_broken"}


@pytest.mark.parametrize("status", [400, 413])
def test_400_413_not_retried(extract_req, status):
    h = Recorder(httpx.Response(status, json={"code": "bad_request"}))
    ext, sleeps = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert h.calls == 1 and sleeps == [] and e.value.retryable is False


def test_422_with_non_json_body(extract_req):
    h = Recorder(httpx.Response(422, text="plain"))
    ext, _ = make(h)
    with pytest.raises(ExtractError):
        ext.extract(extract_req())
    assert h.calls == 1


# ---------------- таймаут ----------------
def test_timeout_maps_to_ml_timeout(extract_req):
    h = Recorder(httpx.ReadTimeout("slow"))
    ext, _ = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == "ml_timeout"


def test_timeout_s_passed_to_client(example_7_0_1, extract_req):
    h = Recorder(ok(example_7_0_1))
    ext, _ = make(h)
    ext.extract(extract_req())
    assert h.requests[0].extensions.get("timeout", {}).get("read") in (5, 5.0, None)


# ---------------- схема ответа ----------------
def test_200_with_invalid_envelope_is_schema_mismatch(extract_req):
    h = Recorder(httpx.Response(200, json={"model_version": "x", "materials": []}))
    ext, _ = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == "ml_schema_mismatch" and h.calls == 1


def test_200_with_non_json_is_schema_mismatch(extract_req):
    h = Recorder(httpx.Response(200, text="<html>oops</html>"))
    ext, _ = make(h)
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert e.value.code == "ml_schema_mismatch"


# ---------------- семафор ----------------
def test_semaphore_limits_concurrency(example_7_0_1, extract_req):
    lock = threading.Lock()
    state = {"cur": 0, "max": 0}

    def handler(request):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.05)
        with lock:
            state["cur"] -= 1
        return ok(example_7_0_1)

    ext, _ = make(handler, concurrency=2)
    reqs = [extract_req(pid=f"D{i:03d}") for i in range(8)]
    errors: list[Exception] = []

    def run(r):
        try:
            ext.extract(r)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=run, args=(r,)) for r in reqs]
    [t.start() for t in threads]
    [t.join(10) for t in threads]
    assert not errors
    assert state["max"] <= 2 and state["max"] >= 1


def test_concurrency_one_serialises(example_7_0_1, extract_req):
    lock = threading.Lock()
    state = {"cur": 0, "max": 0}

    def handler(request):
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.02)
        with lock:
            state["cur"] -= 1
        return ok(example_7_0_1)

    ext, _ = make(handler, concurrency=1)
    ts = [threading.Thread(target=ext.extract, args=(extract_req(pid=f"D{i}"),)) for i in range(4)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert state["max"] == 1


# ---------------- health ----------------
def test_health_ok(example_7_0_1):
    h = Recorder(httpx.Response(200, json={"status": "ok", "model_version": "extract-0.3", "router": "ok"}))
    ext, _ = make(h)
    r = ext.health()
    assert r.status == "ok" and r.model_version == "extract-0.3"
    assert h.requests[0].method == "GET" and h.requests[0].url.path == "/health"


def test_health_503_is_down():
    ext, _ = make(Recorder(httpx.Response(503, json={"status": "down"})))
    assert ext.health().status == "down"


def test_health_503_retry_after_propagated():
    ext, _ = make(Recorder(httpx.Response(503, headers={"Retry-After": "45"})))
    r = ext.health()
    assert r.status == "down" and r.retry_after_s in (45, None)


def test_health_connect_error_is_down():
    ext, _ = make(Recorder(httpx.ConnectError("refused")))
    assert ext.health().status == "down"


def test_health_does_not_retry_with_sleep():
    ext, sleeps = make(Recorder(httpx.Response(503)))
    ext.health()
    assert sleeps == []


def test_health_degraded_passthrough():
    ext, _ = make(Recorder(httpx.Response(200, json={"status": "degraded", "model_version": "v1"})))
    assert ext.health().status in {"degraded", "ok"}


def test_health_garbage_body_is_not_ok():
    ext, _ = make(Recorder(httpx.Response(200, text="not json")))
    assert ext.health().status in {"down", "degraded"}


def test_extract_body_is_valid_json_for_fields(example_7_0_1, extract_req):
    from faktura.domain.profile import ProfileField

    h = Recorder(ok(example_7_0_1))
    ext, _ = make(h)
    ext.extract(extract_req(fields=[ProfileField(key="PCE", evaluated=True)]))
    body = h.requests[0].read().decode("utf-8", "replace")
    assert 'name="fields"' in body
    payload = body.split('name="fields"', 1)[1].split("\r\n\r\n", 1)[1].split("\r\n--", 1)[0]
    parsed = json.loads(payload)
    assert isinstance(parsed, list) and parsed[0]["key"] == "PCE"
