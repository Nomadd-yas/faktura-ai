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




# ---------------- успешный путь ----------------
def test_success_returns_parsed_outcome(example_7_0_1, extract_req):
    h = Recorder(ok(example_7_0_1))
    ext, sleeps = make(h)
    out = ext.extract(extract_req())
    assert isinstance(out, ExtractOutcome)
    assert out.model_version == "extract-0.3" and out.bundle.publication_id == "D021"
    assert len(out.bundle.results) == 3 and out.raw["model_version"] == "extract-0.3"
    assert h.calls == 1 and sleeps == []








# ---------------- ретраи ----------------
def test_429_with_retry_after_then_200(example_7_0_1, extract_req):
    h = Recorder(httpx.Response(429, headers={"Retry-After": "7"}, json={"code": "overloaded"}), ok(example_7_0_1))
    ext, sleeps = make(h)
    out = ext.extract(extract_req())
    assert out.bundle.publication_id == "D021" and h.calls == 2
    assert len(sleeps) == 1 and sleeps[0] >= 7




















# ---------------- ошибки без ретраев ----------------








# ---------------- таймаут ----------------




# ---------------- схема ответа ----------------




# ---------------- семафор ----------------




# ---------------- health ----------------














