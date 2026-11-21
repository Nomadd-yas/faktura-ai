"""ml.MockGoldExtractor: мок ML-1 на dev_gold (TASKS BE п.9, Б-1)."""
from __future__ import annotations

import re

import pytest

from faktura.domain.errors import ExtractError
from faktura.domain.extraction import ExtractOutcome, ExtractRequest, MlHealth
from faktura.domain.ports import ExtractAdapter
from faktura.ml import MockGoldExtractor

TABLES = ("materials", "devices", "layers", "measurements", "results", "issues")
NUMERIC_CHECKS = {"jv_consistency", "eqe_consistency", "champion_vs_mean"}


def _req(pid, pdf):
    return ExtractRequest(publication_id=pid, pdf_path=pdf, sha256="0" * 64)


def _nonempty(v) -> bool:
    return v not in (None, "")


# ---------------- на mini_gold (без данных кейса) ----------------








def test_extract_known_paper_matches_gold(mini_gold_dir, make_pdf, gold_bundles_mini):
    m = MockGoldExtractor(mini_gold_dir)
    for gb in gold_bundles_mini:
        out = m.extract(_req(gb.publication_id, make_pdf(["x"], name=f"{gb.publication_id}.pdf")))
        assert isinstance(out, ExtractOutcome)
        assert out.model_version == m.health().model_version
        assert out.from_cache is False
        assert out.bundle.publication.title == gb.publication.title
        for t in TABLES:
            assert len(getattr(out.bundle, t)) == len(getattr(gb, t)), t








def test_ml_only_drops_numeric_checks(mini_gold_dir, make_pdf):
    full = MockGoldExtractor(mini_gold_dir, issues_mode="all")
    ml_only = MockGoldExtractor(mini_gold_dir, issues_mode="ml_only")
    for pid in ("D001", "D003"):
        pdf = make_pdf(["x"], name=f"{pid}.pdf")
        all_checks = {i.check_name for i in full.extract(_req(pid, pdf)).bundle.issues}
        ml_checks = {i.check_name for i in ml_only.extract(_req(pid, pdf)).bundle.issues}
        assert ml_checks == all_checks - NUMERIC_CHECKS
    d1 = {i.check_name for i in full.extract(_req("D001", make_pdf(["x"], name="D001.pdf"))).bundle.issues}
    assert "champion_vs_mean" in d1










@pytest.fixture(scope="module")
def gold_bundles_mini():
    from pathlib import Path

    from faktura.domain.io import bundles_from_tables, read_tables

    return bundles_from_tables(read_tables(Path(__file__).resolve().parents[1] / "fixtures" / "mini_gold"))


# ---------------- на реальных dev / dev_gold ----------------




