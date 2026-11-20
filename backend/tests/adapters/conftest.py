"""Фикстуры модуля adapters: генераторы PDF и ZIP, фейковые адаптеры ML-1, in-memory FileStore."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Callable

import pytest

from faktura.domain.errors import ExtractError
from faktura.domain.extraction import ExtractOutcome, ExtractRequest, MlHealth


# ---------- PDF ----------
@pytest.fixture
def pdf_dir(tmp_path: Path) -> Path:
    d = tmp_path / "pdfs"
    d.mkdir()
    return d


@pytest.fixture
def make_scan_pdf(pdf_dir: Path):
    """PDF из страниц-картинок без текстового слоя."""
    import fitz

    def _make(n_pages: int = 1, name: str = "scan.pdf") -> Path:
        doc = fitz.open()
        for _ in range(n_pages):
            page = doc.new_page()
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
            pix.clear_with(180)
            page.insert_image(page.rect, pixmap=pix)
        path = pdf_dir / name
        doc.save(path)
        doc.close()
        return path

    return _make


@pytest.fixture
def make_encrypted_pdf(pdf_dir: Path):
    import fitz

    def _make(name: str = "enc.pdf") -> Path:
        doc = fitz.open()
        doc.new_page().insert_text((72, 100), "secret", fontsize=11)
        path = pdf_dir / name
        doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
        doc.close()
        return path

    return _make


@pytest.fixture
def broken_pdf(pdf_dir: Path) -> Path:
    p = pdf_dir / "broken.pdf"
    p.write_bytes(b"%PDF-1.4\n" + b"\x00garbage not a real pdf structure " * 20)
    return p


@pytest.fixture
def not_a_pdf(pdf_dir: Path) -> Path:
    p = pdf_dir / "notes.txt"
    p.write_text("just some text, not a pdf", encoding="utf-8")
    return p


# ---------- ZIP ----------
@pytest.fixture
def pdf_bytes(make_pdf) -> bytes:
    return make_pdf(["zip member"], name="_member.pdf").read_bytes()


@pytest.fixture
def make_zip(tmp_path: Path):
    """make_zip({'a/b.pdf': bytes, ...}) -> Path. Имена записей пишутся как есть (в т.ч. '../evil.pdf')."""

    def _make(members: dict[str, bytes], name: str = "in.zip") -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for arc, data in members.items():
                z.writestr(arc, data)
        return path

    return _make


@pytest.fixture
def zip_stream(make_zip):
    def _make(members: dict[str, bytes]) -> io.BytesIO:
        return io.BytesIO(make_zip(members, name="stream.zip").read_bytes())

    return _make


# ---------- ML-1 фейки ----------
class FakeExtractor:
    """Счётчик вызовов; отдаёт заранее заданный ответ или бросает ошибку."""

    def __init__(self, raw: dict, *, version: str = "extract-0.3", name: str = "http") -> None:
        self.raw = raw
        self.version = version
        self.name = name
        self.calls: list[ExtractRequest] = []
        self.health_calls = 0
        self.fail: ExtractError | None = None
        self.health_status = "ok"

    def extract(self, req: ExtractRequest) -> ExtractOutcome:
        from faktura.ml.response import parse_extract_response

        self.calls.append(req)
        if self.fail is not None:
            raise self.fail
        raw = dict(self.raw, model_version=self.version)
        return parse_extract_response(raw, req.publication_id)

    def health(self) -> MlHealth:
        self.health_calls += 1
        if self.health_status == "down":
            return MlHealth(status="down", model_version=None, retry_after_s=30)
        return MlHealth(status=self.health_status, model_version=self.version)


@pytest.fixture
def fake_extractor(example_7_0_1) -> FakeExtractor:
    return FakeExtractor(example_7_0_1)


@pytest.fixture
def extract_req(make_pdf) -> Callable[..., ExtractRequest]:
    def _make(pid: str = "D021", sha: str = "a" * 64, version: str = "extract-0.3", pdf: Path | None = None,
              **kw) -> ExtractRequest:
        return ExtractRequest(publication_id=pid, pdf_path=pdf or make_pdf(["x"], name=f"{pid}.pdf"), sha256=sha,
                              model_version=version, **kw)

    return _make


class MemoryFileStore:
    """Минимальный FileStore в памяти (протокол domain.ports.FileStore)."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def put_bytes(self, key, data, content_type=None):
        self.data[key] = bytes(data)

    def put_file(self, key, path, content_type=None):
        self.data[key] = Path(path).read_bytes()

    def get_bytes(self, key):
        if key not in self.data:
            raise FileNotFoundError(key)
        return self.data[key]

    def open(self, key):
        return io.BytesIO(self.get_bytes(key))

    def exists(self, key):
        return key in self.data

    def delete(self, key):
        self.data.pop(key, None)

    def copy_to(self, key, path):
        Path(path).write_bytes(self.get_bytes(key))

    def signed_url(self, key, ttl_s):
        return None


@pytest.fixture
def memory_store() -> MemoryFileStore:
    return MemoryFileStore()
