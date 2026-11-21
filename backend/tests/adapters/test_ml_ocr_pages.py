"""HttpExtractor.ocr_pages -> ML POST /ocr (мок транспорта, без сети)."""
import httpx
import pytest

from faktura.domain.errors import ExtractError
from faktura.ml.adapters import HttpExtractor


def _ex(handler):
    return HttpExtractor("http://ml", transport=httpx.MockTransport(handler))


def test_ocr_pages_posts_and_parses(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), req.content
        return httpx.Response(200, json={"pages": [{"page": 1, "markdown": "x", "blocks": []}], "model": "m",
                                         "seconds": 1.0, "cached": False})

    out = _ex(handler).ocr_pages(pdf, [1, 3, 4])
    assert out["pages"][0]["page"] == 1
    assert seen["url"] == "http://ml/ocr" and b'name="pages"' in seen["body"] and b"1,3,4" in seen["body"]


def test_ocr_pages_errors(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    ex = _ex(lambda r: httpx.Response(503, json={"code": "ocr_unavailable", "message": "down", "retryable": True}))
    with pytest.raises(ExtractError) as e:
        ex.ocr_pages(pdf)
    assert e.value.code == "ml_unavailable" and e.value.retryable
    ex = _ex(lambda r: httpx.Response(400, json={"code": "bad_request", "message": "pages", "retryable": False}))
    with pytest.raises(ExtractError) as e:
        ex.ocr_pages(pdf, "9")
    assert e.value.code == "ml_error"
