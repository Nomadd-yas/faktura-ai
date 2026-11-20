"""pdfio.inspect_pdf: проверка PDF (REQ-010, spec/11)."""
from __future__ import annotations

import hashlib

import pytest

from faktura.domain.errors import PdfError
from faktura.pdfio import PdfInfo, inspect_pdf


def test_valid_pdf_info(make_pdf):
    p = make_pdf(["one", "two", "three"])
    info = inspect_pdf(p)
    assert isinstance(info, PdfInfo)
    assert info.page_count == 3
    assert info.size_bytes == p.stat().st_size
    assert info.sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_sha_differs_for_different_content(make_pdf):
    a = inspect_pdf(make_pdf(["a"]))
    b = inspect_pdf(make_pdf(["b"]))
    assert a.sha256 != b.sha256


def test_scan_pdf_is_valid_input(make_scan_pdf):
    # скан не отвергается при приёме: needs_ocr решается в parse
    assert inspect_pdf(make_scan_pdf(2)).page_count == 2


def test_not_a_pdf_extension(not_a_pdf):
    with pytest.raises(PdfError) as e:
        inspect_pdf(not_a_pdf)
    assert e.value.code == "unsupported_file"


def test_text_with_pdf_extension_rejected(pdf_dir):
    p = pdf_dir / "fake.pdf"
    p.write_text("hello, I am not a pdf", encoding="utf-8")
    with pytest.raises(PdfError) as e:
        inspect_pdf(p)
    assert e.value.code in {"unsupported_file", "pdf_broken"}


def test_broken_pdf(broken_pdf):
    with pytest.raises(PdfError) as e:
        inspect_pdf(broken_pdf)
    assert e.value.code == "pdf_broken"


def test_empty_file(pdf_dir):
    p = pdf_dir / "empty.pdf"
    p.write_bytes(b"")
    with pytest.raises(PdfError) as e:
        inspect_pdf(p)
    assert e.value.code in {"unsupported_file", "pdf_broken"}


def test_encrypted_pdf(make_encrypted_pdf):
    with pytest.raises(PdfError) as e:
        inspect_pdf(make_encrypted_pdf())
    assert e.value.code == "pdf_encrypted"


def test_too_many_pages(make_pdf):
    p = make_pdf(["a", "b", "c"])
    with pytest.raises(PdfError) as e:
        inspect_pdf(p, max_pages=2)
    assert e.value.code == "too_many_pages"


def test_pages_exactly_at_limit_ok(make_pdf):
    assert inspect_pdf(make_pdf(["a", "b"]), max_pages=2).page_count == 2


def test_file_too_large(make_pdf):
    p = make_pdf(["a"])
    with p.open("ab") as f:  # хвост после %%EOF: размер > 1 МБ
        f.write(b"\x00" * (1024 * 1024 + 10))
    with pytest.raises(PdfError) as e:
        inspect_pdf(p, max_mb=1)
    assert e.value.code == "file_too_large"


def test_size_limit_checked_before_parsing(pdf_dir):
    p = pdf_dir / "huge.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(PdfError) as e:
        inspect_pdf(p, max_mb=1)
    assert e.value.code == "file_too_large"


def test_error_is_app_error_with_http_status(broken_pdf):
    with pytest.raises(PdfError) as e:
        inspect_pdf(broken_pdf)
    assert e.value.http_status == 422
    assert e.value.message


@pytest.mark.data
def test_real_dev_pdfs_inspect(dev_dir):
    files = sorted(dev_dir.glob("*.pdf"))
    assert len(files) == 21
    for f in files:
        info = inspect_pdf(f)
        assert info.page_count >= 1 and len(info.sha256) == 64
