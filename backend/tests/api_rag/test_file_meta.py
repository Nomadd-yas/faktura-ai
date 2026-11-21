"""§10 ocr_confidence и §7а.3 suggested_domain в RunFile и сводке статьи (мок на dev_gold обеих областей)."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from .conftest import API, RAG_DIR, upload

SOLAR_DEV = Path(__file__).resolve().parents[3] / "data" / "caseB_kejs5" / "dev"


def _run(client, auth, rid):
    return client.get(f"{API}/runs/{rid}", headers=auth).json()


def test_ocr_confidence_in_run_file_and_paper(client, auth, rag_run):
    run = _run(client, auth, rag_run["id"])
    assert run["files"]
    for f in run["files"]:
        assert f["ocr_confidence"] is not None and 0.0 <= f["ocr_confidence"] <= 1.0
        assert f["suggested_domain"] is None
    f = run["files"][0]
    meta = client.get(f"{API}/papers/rag/{f['id']}", params={"run": rag_run["id"]}, headers=auth).json()["meta"]
    assert meta["ocr_confidence"] == f["ocr_confidence"] and meta["suggested_domain"] is None


@pytest.mark.skipif(not (SOLAR_DEV / "D022.pdf").is_file(), reason="нет data/caseB_kejs5")
def test_rag_run_of_solar_paper_suggests_solar(client, auth, tmp_path):
    shutil.copy(SOLAR_DEV / "D022.pdf", tmp_path / "D022.pdf")
    r = upload(client, auth, [tmp_path / "D022.pdf"])
    assert r.status_code == 202, r.text
    run = _run(client, auth, r.json()["id"])
    f = run["files"][0]
    assert f["status"] == "out" and f["suggested_domain"] == "solar" and f["reason"]
    paper = client.get(f"{API}/papers/rag/{f['id']}", params={"run": run["id"]}, headers=auth).json()
    assert paper["meta"]["suggested_domain"] == "solar" and paper["paper_status"] == "out"


def test_solar_run_of_rag_paper_suggests_rag(client, auth, tmp_path):
    shutil.copy(RAG_DIR / "dev" / "D011.pdf", tmp_path / "D011.pdf")
    r = upload(client, auth, [tmp_path / "D011.pdf"], domain="solar")
    assert r.status_code == 202, r.text
    run = _run(client, auth, r.json()["id"])
    f = run["files"][0]
    assert f["status"] == "out" and f["suggested_domain"] == "rag"
    assert f["ocr_confidence"] is not None
