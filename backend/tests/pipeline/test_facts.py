"""build_facts: факты и якоря из bundle; bbox -> доли страницы, запасной find_rects (план 5.6)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.pipeline import build_facts


def prepared(synth_bundle):
    """Bundle с уже посчитанными normalized_* (нормализация здесь не проверяется)."""
    b = synth_bundle()
    for r in b.results:
        if r.raw_value and r.raw_value.replace(".", "").isdigit():
            r.normalized_value = Decimal(r.raw_value)
            r.normalized_unit = r.raw_unit
            r.metric_value, r.metric_unit = r.normalized_value, r.normalized_unit
    return b


def result_facts(facts):
    return [f for f in facts if f.target.get("entity") == "results"]


def test_one_fact_per_result_row(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    facts, _ = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    rf = result_facts(facts)
    assert sorted(f.target["id"] for f in rf) == sorted(r.result_id for r in b.results)
    assert all(f.target["field"] == "metric_value" for f in rf)


def test_fact_fields_copied_from_result(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    facts, _ = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    f = next(f for f in result_facts(facts) if f.target["id"] == "R2")
    assert f.param == "pce" and f.metric_name == "PCE"
    assert f.raw_value == "12.89" and f.raw_unit == "%"
    assert f.normalized_value == Decimal("12.89") and f.normalized_unit == "%"
    assert f.source_page == 5 and f.source_fragment == "PCE 12.89 %"
    assert f.extraction_status == "extracted"
    assert f.ref  # локальная ссылка для save_processed


def test_not_extractable_row_still_gets_fact(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    facts, _ = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    f = next(f for f in result_facts(facts) if f.target["id"] == "R4")
    assert f.extraction_status == "present_not_extractable" and f.normalized_value is None


def test_refs_unique(synth_bundle, fake_parser, tmp_path):
    facts, anchors = build_facts(prepared(synth_bundle), None, fake_parser, tmp_path / "a.pdf")
    assert len({f.ref for f in facts}) == len(facts)
    assert len({a.ref for a in anchors}) == len(anchors)


def test_bbox_top_left_converted_to_fractions(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    r = b.results[1]
    r.meta.bbox, r.meta.page = (60.0, 80.0, 120.0, 100.0), 5
    facts, anchors = build_facts(b, None, fake_parser, tmp_path / "a.pdf", bbox_origin="top-left")
    f = next(f for f in facts if f.target["id"] == "R2")
    a = next(a for a in anchors if a.ref == f.anchor_ref)
    assert a.page == 5 and len(a.rects) == 1
    rc = a.rects[0]
    assert (rc.x, rc.y) == pytest.approx((0.1, 0.1))
    assert (rc.w, rc.h) == pytest.approx((0.1, 0.025))
    assert f.ref in a.fact_refs
    assert fake_parser.find_calls == [] or all(n != "PCE 12.89 %" for _, n in fake_parser.find_calls)


def test_bbox_bottom_left_flips_y(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    r = b.results[1]
    r.meta.bbox, r.meta.page = (60.0, 80.0, 120.0, 100.0), 5
    facts, anchors = build_facts(b, None, fake_parser, tmp_path / "a.pdf", bbox_origin="bottom-left")
    f = next(f for f in facts if f.target["id"] == "R2")
    rc = next(a for a in anchors if a.ref == f.anchor_ref).rects[0]
    assert rc.y == pytest.approx((800 - 100) / 800)
    assert rc.h == pytest.approx(0.025)


@pytest.mark.parametrize("bad", [(0, 0, 0, 0), (50.0, 50.0, 40.0, 40.0), (-10.0, 0.0, 1000.0, 2000.0)])
def test_degenerate_or_outside_bbox_does_not_crash(synth_bundle, fake_parser, tmp_path, bad):
    b = prepared(synth_bundle)
    b.results[1].meta.bbox, b.results[1].meta.page = bad, 5
    facts, anchors = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    for a in anchors:
        for rc in a.rects:
            assert 0 <= rc.x <= 1 and 0 <= rc.y <= 1 and rc.w >= 0 and rc.h >= 0
            assert rc.x + rc.w <= 1.0001 and rc.y + rc.h <= 1.0001


def test_without_bbox_falls_back_to_find_rects(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    facts, anchors = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    f = next(f for f in facts if f.target["id"] == "R2")
    assert any(p == 5 for p, _ in fake_parser.find_calls)
    a = next(a for a in anchors if a.ref == f.anchor_ref)
    assert a.rects == fake_parser.find_rects(None, 5, "PCE 12.89 %")


def test_nothing_found_fact_kept_without_anchor(synth_bundle, tmp_path):
    class Empty:
        def page_size(self, *_): return (600.0, 800.0)
        def find_rects(self, *_): return []
        def parse(self, *_): raise AssertionError
        def render_page(self, *_, **__): return b""

    b = prepared(synth_bundle)
    facts, anchors = build_facts(b, None, Empty(), tmp_path / "a.pdf")
    f = next(f for f in result_facts(facts) if f.target["id"] == "R2")
    assert f.anchor_ref is None
    assert anchors == [] or all(a.ref != f.anchor_ref for a in anchors)


def test_no_pdf_path_and_no_bbox_no_anchors(synth_bundle, fake_parser):
    facts, anchors = build_facts(prepared(synth_bundle), None, fake_parser, None)
    assert result_facts(facts) and anchors == []


def test_page_from_source_page_when_meta_page_missing(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    b.results[1].meta.bbox, b.results[1].meta.page = (10.0, 10.0, 20.0, 20.0), None
    facts, anchors = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    f = next(f for f in facts if f.target["id"] == "R2")
    assert next(a for a in anchors if a.ref == f.anchor_ref).page == 5


def test_two_results_same_place_share_or_have_own_anchors(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    for r in b.results[:2]:
        r.meta.bbox, r.meta.page = (10.0, 10.0, 50.0, 20.0), 5
    facts, anchors = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    refs = {f.anchor_ref for f in facts if f.target["id"] in ("R1", "R2")}
    assert None not in refs
    anchored = {fr for a in anchors for fr in a.fact_refs}
    assert {f.ref for f in facts if f.target["id"] in ("R1", "R2")} <= anchored


def test_empty_bundle(fake_parser, tmp_path):
    from faktura.domain.records import PaperBundle, Publication
    facts, anchors = build_facts(PaperBundle(publication=Publication(publication_id="E")), None, fake_parser,
                                 tmp_path / "a.pdf")
    assert result_facts(facts) == [] and anchors == []


@pytest.mark.data
def test_gold_bundles_facts_equal_result_rows(gold_bundles, fake_parser):
    total_rows = sum(len(b.results) for b in gold_bundles)
    total_facts = sum(len(result_facts(build_facts(b, None, fake_parser, None)[0])) for b in gold_bundles)
    assert total_facts == total_rows == 659


@pytest.mark.data
def test_real_pdf_find_rects_fallback(settings, dev_dir, gold_bundles):
    """Реальный парсер: без bbox якорь ищется по source_fragment, rects в долях страницы."""
    from faktura.pipeline import build_services
    parser = build_services(settings).parser
    b = next(x for x in gold_bundles if x.publication_id == "D001")
    facts, anchors = build_facts(b, parser.parse(dev_dir / "D001.pdf"), parser, dev_dir / "D001.pdf")
    assert anchors, "хотя бы часть цитат должна найтись в тексте PDF"
    for a in anchors:
        for rc in a.rects:
            assert 0 <= rc.x <= 1 and 0 <= rc.y <= 1 and 0 < rc.w <= 1 and 0 < rc.h <= 1
