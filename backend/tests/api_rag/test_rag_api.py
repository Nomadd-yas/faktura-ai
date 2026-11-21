"""R2: область rag через API (мок на dev_gold): разбор, сводка статьи, results/issues, submission.zip + check."""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile

import pytest

from faktura.domain.registry import get_domain

from .conftest import API, SMALL, upload

SPEC = get_domain("rag")










def test_submission_zip_and_check(client, auth, rag_run, gold):
    rid = rag_run["id"]
    z = client.get(f"{API}/runs/{rid}/submission.zip", headers=auth)
    assert z.status_code == 200 and z.headers["x-export-tables"] == "7"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert sorted(zf.namelist()) == sorted([*(f"{t}.csv" for t in SPEC.tables), "PASSPORT.md", "passport.json"])
    for t, cols in SPEC.tables.items():
        rows = list(csv.DictReader(io.StringIO(zf.read(f"{t}.csv").decode("utf-8-sig"), newline="")))
        assert list(rows[0].keys()) == list(cols)
        assert len(rows) == sum(len(gold[t].get(p, [])) for p in SMALL), t
    passport = json.loads(zf.read("passport.json"))
    assert [d["id"] for d in passport["domains"]] == ["rag"] and len(passport["domains"][0]["entities"]) == 7
    assert passport["versions"]["model"] and passport["domains"][0]["normalization"]
    assert "### Нормализация" in zf.read("PASSPORT.md").decode("utf-8")

    c = client.get(f"{API}/runs/{rid}/submission.zip", params={"check": "true"}, headers=auth)
    assert c.status_code == 200, c.text[:500]
    assert c.headers["x-check-status"] in ("ok", "warnings")
    rep = zipfile.ZipFile(io.BytesIO(c.content)).read("check_report.txt").decode("utf-8")
    assert "ERROR:" not in rep and "fontTools" not in rep






def _scan_pdf(path):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
    pix.clear_with(180)
    page.insert_image(page.rect, pixmap=pix)
    doc.save(path)
    doc.close()
    return path




