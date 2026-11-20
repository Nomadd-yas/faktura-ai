"""write_meta, build_manifest, zip_dir (spec/13 §13.6; REQ-053, REQ-057)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

import pytest

from faktura.domain.entities import CheckReport, FileReport
from faktura.export import build_manifest, write_meta, write_submission, zip_dir
from faktura.passport import build_passport, passport_markdown

META_FILES = {"manifest.json", "passport.json", "passport.md", "files.csv", "facts_ui.json", "errors.log",
              "check_report.txt"}


def _meta_kwargs(profile, **over):
    passport = build_passport(profile)
    kw = dict(manifest={"tool": "faktura", "domain": "solar"},
              files_rows=[FileReport(file="P001.pdf", publication_id="P001", status="done", facts=2, issues=1,
                                     sha256="abc", pages=9),
                          FileReport(file="P002.pdf", publication_id="P002", status="failed", error_code="parse",
                                     message="битый, файл")],
              facts_ui={"P001": {"R001": {"confidence": 0.9}}}, errors=["P002: parse failed", "P003: кириллица"],
              passport=passport, passport_md=passport_markdown(passport),
              check_report=CheckReport(ran=True, ok=True, raw_output="таблиц: 7, ошибок: 0"))
    kw.update(over)
    return kw


def test_write_meta_creates_all_files(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile))
    assert {p.name for p in (tmp_path / "_meta").iterdir()} == META_FILES


def test_write_meta_without_check_report_skips_file(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile, check_report=None))
    names = {p.name for p in (tmp_path / "_meta").iterdir()}
    assert "check_report.txt" not in names and META_FILES - {"check_report.txt"} <= names


def test_write_meta_does_not_touch_csv_files(make_bundle, tmp_path, profile):
    write_submission([make_bundle()], tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.csv")}
    write_meta(tmp_path, **_meta_kwargs(profile))
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.csv")} == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == sorted(before)  # в корне только 7 CSV


def test_manifest_and_facts_json_roundtrip(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile))
    m = tmp_path / "_meta"
    assert json.loads((m / "manifest.json").read_text(encoding="utf-8")) == {"tool": "faktura", "domain": "solar"}
    assert json.loads((m / "facts_ui.json").read_text(encoding="utf-8"))["P001"]["R001"]["confidence"] == 0.9
    assert json.loads((m / "passport.json").read_text(encoding="utf-8"))["collection"] == "solar"


def test_passport_md_and_check_report_written_verbatim(tmp_path, profile):
    kw = _meta_kwargs(profile)
    write_meta(tmp_path, **kw)
    m = tmp_path / "_meta"
    assert (m / "passport.md").read_text(encoding="utf-8") == kw["passport_md"]
    assert (m / "check_report.txt").read_text(encoding="utf-8").strip() == "таблиц: 7, ошибок: 0"


def test_files_csv_rows_and_quoting(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile))
    with open(tmp_path / "_meta" / "files.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["publication_id"] for r in rows] == ["P001", "P002"]
    assert rows[0]["file"] == "P001.pdf" and rows[0]["status"] == "done" and rows[0]["facts"] == "2"
    assert rows[1]["status"] == "failed" and rows[1]["message"] == "битый, файл"
    assert set(FileReport.model_fields) <= set(rows[0])  # sha256, pages, from_cache, dup_of ...


def test_files_csv_empty_rows_has_header(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile, files_rows=[]))
    text = (tmp_path / "_meta" / "files.csv").read_text(encoding="utf-8-sig")
    assert text.splitlines()[0].startswith("file,")


def test_errors_log_one_line_per_error(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile))
    lines = (tmp_path / "_meta" / "errors.log").read_text(encoding="utf-8").splitlines()
    assert lines == ["P002: parse failed", "P003: кириллица"]


def test_errors_log_exists_even_if_empty(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile, errors=[]))
    assert (tmp_path / "_meta" / "errors.log").read_text(encoding="utf-8") == ""


def test_write_meta_idempotent_overwrite(tmp_path, profile):
    write_meta(tmp_path, **_meta_kwargs(profile))
    write_meta(tmp_path, **_meta_kwargs(profile, errors=["x"]))
    assert (tmp_path / "_meta" / "errors.log").read_text(encoding="utf-8").strip() == "x"


# --- build_manifest ----------------------------------------------------------------------------

def test_manifest_defaults_and_json_serializable():
    m = build_manifest()
    assert m["tool"] == "faktura"
    json.dumps(m)


def test_manifest_contains_given_fields():
    m = build_manifest(domain="solar", split="test", model_version="extract-0.3", from_cache=True,
                       profile={"id": "tz-solar-v1", "version": 1},
                       input={"path": "./test", "files": 90, "accepted": 90, "duplicates": 0},
                       result={"publications": 90, "in_scope": 84, "results": 2810, "issues": 230, "failed": 0},
                       check={"errors": 0, "warnings": 12, "quotes_checked": 2410, "quotes_not_found": 9},
                       versions={"norm": "2026-11-20", "rules": "1"})
    assert m["domain"] == "solar" and m["split"] == "test"
    assert m["model_version"] == "extract-0.3" and m["from_cache"] is True
    assert m["result"]["publications"] == 90 and m["check"]["quotes_not_found"] == 9
    assert m["versions"]["rules"] == "1"
    assert json.loads(json.dumps(m)) == m


def test_manifest_has_timestamps_when_given():
    m = build_manifest(started_at="2026-11-20T10:00:00Z", finished_at="2026-11-20T10:05:00Z")
    assert m["started_at"].startswith("2026-11-20") and m["finished_at"].endswith("Z")


# --- zip_dir -----------------------------------------------------------------------------------

def _tree(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    (d / "_meta").mkdir(parents=True)
    (d / "results.csv").write_bytes(b"\xef\xbb\xbfa,b\r\n1,2\r\n")
    (d / "issues.csv").write_text("x", encoding="utf-8")
    (d / "_meta" / "manifest.json").write_text("{}", encoding="utf-8")
    return d


def _names(data: bytes) -> list[str]:
    return sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())


def test_zip_dir_returns_valid_zip_with_relative_posix_names(tmp_path):
    data = zip_dir(_tree(tmp_path))
    assert isinstance(data, bytes)
    assert _names(data) == ["_meta/manifest.json", "issues.csv", "results.csv"]
    assert zipfile.ZipFile(io.BytesIO(data)).testzip() is None


def test_zip_dir_preserves_bytes(tmp_path):
    z = zipfile.ZipFile(io.BytesIO(zip_dir(_tree(tmp_path))))
    assert z.read("results.csv") == b"\xef\xbb\xbfa,b\r\n1,2\r\n"


def test_zip_dir_include_filter(tmp_path):
    d = _tree(tmp_path)
    data = zip_dir(d, include=lambda p: p.suffix == ".csv")
    assert _names(data) == ["issues.csv", "results.csv"]


def test_zip_dir_exclude_meta_via_include(tmp_path):
    d = _tree(tmp_path)
    data = zip_dir(d, include=lambda p: "_meta" not in p.parts)
    assert all(not n.startswith("_meta") for n in _names(data))


def test_zip_dir_empty_directory(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert _names(zip_dir(d)) == []


def test_zip_dir_missing_directory_raises(tmp_path):
    with pytest.raises((FileNotFoundError, NotADirectoryError, ValueError)):
        zip_dir(tmp_path / "nope")


def test_zip_of_submission_has_seven_csv_at_root(make_bundle, tmp_path):
    write_submission([make_bundle()], tmp_path / "o")
    names = _names(zip_dir(tmp_path / "o"))
    assert len(names) == 7 and all("/" not in n and n.endswith(".csv") for n in names)
