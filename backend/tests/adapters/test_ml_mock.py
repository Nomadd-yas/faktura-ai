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
def test_mock_implements_protocol(mini_gold_dir):
    m = MockGoldExtractor(mini_gold_dir)
    assert isinstance(m, ExtractAdapter) and m.name == "mock"


def test_model_version_format(mini_gold_dir):
    v = MockGoldExtractor(mini_gold_dir).health().model_version
    assert re.fullmatch(r"mock-gold-[0-9a-f]{8}", v)


def test_model_version_stable_and_changes_with_gold(mini_gold_dir, tmp_path):
    import shutil

    a = MockGoldExtractor(mini_gold_dir).health().model_version
    assert MockGoldExtractor(mini_gold_dir).health().model_version == a
    copy = tmp_path / "g"
    shutil.copytree(mini_gold_dir, copy)
    p = copy / "issues.csv"
    p.write_bytes(p.read_bytes() + b"\n")  # любое изменение байтов эталона меняет версию
    assert MockGoldExtractor(copy).health().model_version != a


def test_health_ok(mini_gold_dir):
    h = MockGoldExtractor(mini_gold_dir).health()
    assert isinstance(h, MlHealth) and h.status == "ok"


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


def test_extract_unknown_paper_only_publication(mini_gold_dir, make_pdf):
    out = MockGoldExtractor(mini_gold_dir).extract(_req("ZZZ999", make_pdf(["x"], name="ZZZ999.pdf")))
    assert out.bundle.publication_id == "ZZZ999"
    for t in TABLES:
        assert getattr(out.bundle, t) == []


def test_mock_ignores_pdf_content(mini_gold_dir, make_pdf):
    m = MockGoldExtractor(mini_gold_dir)
    a = m.extract(_req("D001", make_pdf(["aaa"], name="a.pdf")))
    b = m.extract(_req("D001", make_pdf(["bbb"], name="b.pdf")))
    assert a.bundle.model_dump() == b.bundle.model_dump()


def test_gold_issues_marked_computed_by_ml(mini_gold_dir, make_pdf):
    out = MockGoldExtractor(mini_gold_dir).extract(_req("D001", make_pdf(["x"], name="D001.pdf")))
    assert out.bundle.issues
    for i in out.bundle.issues:
        assert i.computed_by == "ml"
        assert i.confidence == 1.0 or i.meta.confidence == 1.0


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


def test_ml_only_keeps_other_tables(mini_gold_dir, make_pdf):
    pdf = make_pdf(["x"], name="D001.pdf")
    a = MockGoldExtractor(mini_gold_dir, "all").extract(_req("D001", pdf)).bundle
    b = MockGoldExtractor(mini_gold_dir, "ml_only").extract(_req("D001", pdf)).bundle
    for t in ("materials", "devices", "layers", "measurements", "results"):
        assert len(getattr(a, t)) == len(getattr(b, t))


def test_mock_missing_gold_dir(tmp_path):
    with pytest.raises((ExtractError, FileNotFoundError, OSError)):
        MockGoldExtractor(tmp_path / "nope").extract(_req("D001", tmp_path / "x.pdf"))


def test_mock_result_fields_not_normalised(mini_gold_dir, make_pdf):
    """Мок отдаёт raw-значения; metric_*/normalized_* отбрасывает parse_extract_response (считает бэк)."""
    out = MockGoldExtractor(mini_gold_dir).extract(_req("D001", make_pdf(["x"], name="D001.pdf")))
    for r in out.bundle.results:
        assert r.normalized_value in (None, "") and r.metric_value in (None, "")


def test_mock_meta_ml_order_sequential(mini_gold_dir, make_pdf):
    out = MockGoldExtractor(mini_gold_dir).extract(_req("D001", make_pdf(["x"], name="D001.pdf")))
    orders = [r.meta.ml_order for r in out.bundle.results]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)


@pytest.fixture(scope="module")
def gold_bundles_mini():
    from pathlib import Path

    from faktura.domain.io import bundles_from_tables, read_tables

    return bundles_from_tables(read_tables(Path(__file__).resolve().parents[1] / "fixtures" / "mini_gold"))


# ---------------- на реальных dev / dev_gold ----------------
@pytest.mark.data
def test_mock_every_dev_paper_matches_gold_rows(dev_dir, dev_gold_dir, gold_bundles):
    m = MockGoldExtractor(dev_gold_dir)
    assert len(gold_bundles) == 21
    spreads = 0
    for gb in gold_bundles:
        out = m.extract(_req(gb.publication_id, dev_dir / f"{gb.publication_id}.pdf"))
        for t in TABLES:
            assert len(getattr(out.bundle, t)) == len(getattr(gb, t)), (gb.publication_id, t)
        spreads += sum(1 for r in out.bundle.results if _nonempty(r.spread_value))
    assert spreads == 49


@pytest.mark.data
def test_mock_ml_only_on_dev(dev_dir, dev_gold_dir, gold_bundles):
    full, ml_only = MockGoldExtractor(dev_gold_dir, "all"), MockGoldExtractor(dev_gold_dir, "ml_only")
    seen_full: set[str] = set()
    for gb in gold_bundles:
        pdf = dev_dir / f"{gb.publication_id}.pdf"
        seen_full |= {i.check_name for i in full.extract(_req(gb.publication_id, pdf)).bundle.issues}
        got = {i.check_name for i in ml_only.extract(_req(gb.publication_id, pdf)).bundle.issues}
        assert not (got & NUMERIC_CHECKS)
    assert NUMERIC_CHECKS <= seen_full


@pytest.mark.data
def test_mock_test_set_paper_gives_publication_only(test_dir, dev_gold_dir):
    pdf = sorted(test_dir.glob("*.pdf"))[0]
    out = MockGoldExtractor(dev_gold_dir).extract(_req(pdf.stem, pdf))
    assert out.bundle.results == [] and out.bundle.publication_id == pdf.stem
