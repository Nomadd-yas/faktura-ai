"""pdfio.collect_inputs / safe_unzip / dedupe_names (REQ-010, REQ-079)."""
from __future__ import annotations

from pathlib import Path

import pytest

from faktura.domain.errors import AppError
from faktura.pdfio import (CollectResult, InputFile, ZipLimits, collect_inputs, dedupe_names, safe_unzip)


def _inside(root: Path, p: Path) -> bool:
    return root.resolve() in p.resolve().parents


# ---------------- collect_inputs ----------------
def test_collect_directory_recursive(tmp_path, make_pdf):
    root = tmp_path / "in"
    make_pdf(["a"], name="P001.pdf", directory=root)
    make_pdf(["b"], name="P002.pdf", directory=root / "sub")
    r = collect_inputs(root)
    assert isinstance(r, CollectResult)
    assert [f.rel_path for f in r.files] == ["P001.pdf", "sub/P002.pdf"]
    assert [f.publication_id for f in r.files] == ["P001", "P002"]
    assert [f.name for f in r.files] == ["P001.pdf", "P002.pdf"]
    assert all(f.source == "dir" and f.path.is_file() for f in r.files)
    assert r.rejected == []


def test_collect_empty_directory(tmp_path):
    (tmp_path / "e").mkdir()
    r = collect_inputs(tmp_path / "e")
    assert r.files == [] and r.rejected == []


def test_collect_non_pdf_is_rejected(tmp_path, make_pdf):
    root = tmp_path / "in"
    make_pdf(["a"], name="A.pdf", directory=root)
    (root / "readme.docx").write_bytes(b"PK")
    r = collect_inputs(root)
    assert [f.name for f in r.files] == ["A.pdf"]
    assert [(x.name, x.code) for x in r.rejected] == [("readme.docx", "unsupported_file")]


def test_collect_single_pdf_file(make_pdf):
    p = make_pdf(["a"], name="Solo.pdf")
    r = collect_inputs(p)
    assert len(r.files) == 1
    assert r.files[0].publication_id == "Solo" and r.files[0].source == "file"


def test_collect_txt_list_rejected_validation_error(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("a.pdf\nb.pdf\n", encoding="utf-8")
    r = collect_inputs(p)
    assert r.files == []
    assert [x.code for x in r.rejected] == ["validation_error"]


def test_collect_zip(make_zip, pdf_bytes):
    z = make_zip({"P001.pdf": pdf_bytes, "dir/P002.pdf": pdf_bytes})
    r = collect_inputs(z)
    assert sorted(f.rel_path for f in r.files) == ["P001.pdf", "dir/P002.pdf"]
    assert all(f.source == "zip" and f.path.is_file() for f in r.files)
    assert r.rejected == []


def test_collect_zip_skips_junk(make_zip, pdf_bytes):
    z = make_zip({"P001.pdf": pdf_bytes, "__MACOSX/._P001.pdf": b"junk", ".hidden.pdf": pdf_bytes})
    r = collect_inputs(z)
    assert [f.name for f in r.files] == ["P001.pdf"]


def test_collect_missing_path(tmp_path):
    with pytest.raises((AppError, FileNotFoundError)):
        collect_inputs(tmp_path / "nope")


# ---------------- safe_unzip ----------------
def test_unzip_normal(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"a.pdf": pdf_bytes, "d1/d2/b.pdf": pdf_bytes})
    dest = tmp_path / "out"
    out = safe_unzip(z, dest, ZipLimits())
    assert sorted(p.name for p in out) == ["a.pdf", "b.pdf"]
    assert all(p.is_file() and _inside(dest, p) for p in out)


def test_unzip_from_stream(zip_stream, pdf_bytes, tmp_path):
    out = safe_unzip(zip_stream({"a.pdf": pdf_bytes}), tmp_path / "o", ZipLimits())
    assert [p.name for p in out] == ["a.pdf"]


def test_unzip_zip_slip(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"../evil.pdf": pdf_bytes, "ok.pdf": pdf_bytes})
    dest = tmp_path / "out" / "x"
    try:
        out = safe_unzip(z, dest, ZipLimits())
    except AppError:
        out = []  # отказ всего архива тоже допустим
    assert not (dest.parent / "evil.pdf").exists()
    assert not (tmp_path / "evil.pdf").exists()
    assert "evil.pdf" not in [p.name for p in out]
    assert all(_inside(dest, p) for p in out)


def test_unzip_absolute_path_member(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"/abs_evil.pdf": pdf_bytes, "ok.pdf": pdf_bytes})
    dest = tmp_path / "out"
    try:
        out = safe_unzip(z, dest, ZipLimits())
    except AppError:
        out = []
    assert all(_inside(dest, p) for p in out)


def test_unzip_skips_macosx_and_hidden(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"__MACOSX/._a.pdf": pdf_bytes, ".hidden.pdf": pdf_bytes, "dir/.DS_Store": b"x", "a.pdf": pdf_bytes})
    out = safe_unzip(z, tmp_path / "o", ZipLimits())
    assert [p.name for p in out] == ["a.pdf"]


def test_unzip_skips_wrong_signature(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"fake.pdf": b"this is plain text", "a.pdf": pdf_bytes})
    out = safe_unzip(z, tmp_path / "o", ZipLimits())
    assert [p.name for p in out] == ["a.pdf"]


def test_unzip_depth_limit(make_zip, pdf_bytes, tmp_path):
    deep = "/".join(f"d{i}" for i in range(11)) + "/deep.pdf"  # вложенность 11
    z = make_zip({deep: pdf_bytes, "ok.pdf": pdf_bytes})
    dest = tmp_path / "o"
    try:
        out = safe_unzip(z, dest, ZipLimits(max_depth=10))
    except AppError as e:
        assert e.code in {"zip_bomb", "zip_too_large", "validation_error"}
        return
    assert "deep.pdf" not in [p.name for p in out]
    assert "ok.pdf" in [p.name for p in out]


def test_unzip_depth_within_limit_ok(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"a/b/c/x.pdf": pdf_bytes})
    out = safe_unzip(z, tmp_path / "o", ZipLimits(max_depth=10))
    assert [p.name for p in out] == ["x.pdf"]


def test_unzip_too_many_files(make_zip, pdf_bytes, tmp_path):
    z = make_zip({f"{i}.pdf": pdf_bytes for i in range(3)})
    with pytest.raises(AppError) as e:
        safe_unzip(z, tmp_path / "o", ZipLimits(max_files=2))
    assert e.value.code in {"zip_bomb", "zip_too_large"}


def test_unzip_unpacked_size_limit(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"a.pdf": pdf_bytes})
    with pytest.raises(AppError) as e:
        safe_unzip(z, tmp_path / "o", ZipLimits(max_unpacked_gb=0))
    assert e.value.code in {"zip_bomb", "zip_too_large"}


def test_unzip_archive_size_limit(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"a.pdf": pdf_bytes})
    with pytest.raises(AppError) as e:
        safe_unzip(z, tmp_path / "o", ZipLimits(max_zip_mb=0))
    assert e.value.code in {"zip_too_large", "zip_bomb"}


def test_unzip_highly_compressed_bomb(tmp_path, make_zip):
    # 50 МБ нулей сжимаются в ~50 КБ; при лимите 0 ГБ распаковка отвергается
    data = b"%PDF-1.4\n" + b"\x00" * (50 * 1024 * 1024)
    z = make_zip({"bomb.pdf": data})
    dest = tmp_path / "o"
    with pytest.raises(AppError) as e:
        safe_unzip(z, dest, ZipLimits(max_unpacked_gb=0))
    assert e.value.code in {"zip_bomb", "zip_too_large"}
    if dest.exists():
        assert not list(dest.rglob("bomb.pdf"))


def test_unzip_not_a_zip(tmp_path):
    p = tmp_path / "bad.zip"
    p.write_bytes(b"not a zip")
    with pytest.raises(AppError):
        safe_unzip(p, tmp_path / "o", ZipLimits())


# ---------------- dedupe_names ----------------
def _f(tmp_path: Path, rel: str) -> InputFile:
    name = rel.rsplit("/", 1)[-1]
    return InputFile(name=name, rel_path=rel, publication_id=name.rsplit(".", 1)[0], path=tmp_path / rel)


def test_dedupe_two_p001_in_subfolders(tmp_path):
    files = [_f(tmp_path, "a/P001.pdf"), _f(tmp_path, "b/P001.pdf"), _f(tmp_path, "b/P002.pdf")]
    r = dedupe_names(files, existing_names=set())
    assert [f.rel_path for f in r.files] == ["a/P001.pdf", "b/P002.pdf"]
    assert [(x.name, x.code) for x in r.rejected] == [("P001.pdf", "duplicate_name")]


def test_dedupe_against_existing_names(tmp_path):
    r = dedupe_names([_f(tmp_path, "P001.pdf"), _f(tmp_path, "P002.pdf")], existing_names={"P001.pdf"})
    assert [f.name for f in r.files] == ["P002.pdf"]
    assert r.rejected[0].code == "duplicate_name" and r.rejected[0].name == "P001.pdf"


def test_dedupe_no_duplicates_keeps_order(tmp_path):
    files = [_f(tmp_path, n) for n in ("C.pdf", "A.pdf", "B.pdf")]
    r = dedupe_names(files, existing_names=set())
    assert [f.name for f in r.files] == ["C.pdf", "A.pdf", "B.pdf"]
    assert r.rejected == []


def test_dedupe_empty(tmp_path):
    r = dedupe_names([], existing_names={"X.pdf"})
    assert r.files == [] and r.rejected == []


def test_dedupe_triple_only_first_kept(tmp_path):
    files = [_f(tmp_path, f"{d}/P.pdf") for d in "abc"]
    r = dedupe_names(files, existing_names=set())
    assert len(r.files) == 1 and len(r.rejected) == 2
    assert {x.code for x in r.rejected} == {"duplicate_name"}


def test_dedupe_does_not_mutate_existing_names(tmp_path):
    existing: set[str] = set()
    dedupe_names([_f(tmp_path, "A.pdf")], existing_names=existing)
    assert existing == set()
