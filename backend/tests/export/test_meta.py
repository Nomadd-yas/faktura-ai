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




















# --- build_manifest ----------------------------------------------------------------------------







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














