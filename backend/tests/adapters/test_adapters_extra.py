"""Дополнительные проверки adapters: поведение, которое не покрыто приёмкой плана, но на него опирается конвейер."""
from __future__ import annotations

import copy
import zipfile

import httpx
import pytest

from faktura.domain.errors import ExtractError
from faktura.ml import CachedExtractor, HttpExtractor, MlCache, RawDirExtractor, parse_extract_response, resolve_model_version
from faktura.ml.adapters import _parse_retry_after
from faktura.pdfio import ZipLimits, collect_inputs, dedupe_names, title_key
from faktura.pdfio.inputs import InputFile


# ---------------- pdfio ----------------
def test_collect_zip_reports_non_pdf_and_bad_signature(make_zip, pdf_bytes):
    z = make_zip({"a.pdf": pdf_bytes, "notes.docx": b"PK..", "fake.pdf": b"plain text"})
    r = collect_inputs(z)
    assert [f.name for f in r.files] == ["a.pdf"]
    assert sorted((x.name, x.code) for x in r.rejected) == [("fake.pdf", "unsupported_file"),
                                                           ("notes.docx", "unsupported_file")]












# ---------------- ml: разбор ----------------












# ---------------- ml: HTTP и кеш ----------------


def test_503_is_retried(example_7_0_1, extract_req):
    seq = [httpx.Response(503, headers={"Retry-After": "3"}), httpx.Response(200, json=example_7_0_1)]
    sleeps: list[float] = []
    ext = HttpExtractor("http://ml", timeout_s=5, retry_delays=(1,), sleep=sleeps.append,
                        transport=httpx.MockTransport(lambda r: seq.pop(0)))
    assert ext.extract(extract_req()).bundle.publication_id == "D021" and sleeps == [3.0]








