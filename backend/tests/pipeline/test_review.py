"""transfer_review и recheck_paper (план 5.6, P1)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import Fact, FactFilter, ReviewStatus
from faktura.domain.extraction import ExtractOutcome, MlHealth
from faktura.domain.records import (Device, Measurement, PaperBundle, Publication, Result)
from faktura.pipeline import PaperProcessor, recheck_paper, transfer_review


def fact(fid, value, *, status=ReviewStatus.accepted, metric="PCE", row="DV1", page=5, table="Table 1",
         corrected=None, **kw):
    return Fact(id=fid, paper_id="pap", publication_id="D001",
                target={"entity": "results", "id": f"R-{fid}", "field": "metric_value"}, param=metric.lower(),
                metric_name=metric, row_id=row, normalized_value=None if value is None else Decimal(str(value)),
                normalized_unit="%", review_status=status, source_page=page, source_table=table,
                corrected_value=None if corrected is None else Decimal(str(corrected)), **kw)


# --- transfer_review ---------------------------------------------------------------------------------------------

def test_same_place_and_value_transferred():
    r = transfer_review([fact("o1", 12.89)], [fact("n1", 12.89, status=ReviewStatus.new)])
    assert r.transferred == ["o1"] and r.to_manual == []


@pytest.mark.parametrize("new,ok", [("12.89", True), ("12.9", True), ("12.953", True), ("12.96", False),
                                    ("12.80", False), ("99", False)])
def test_value_tolerance_half_percent(new, ok):
    r = transfer_review([fact("o1", "12.89")], [fact("n1", new, status=ReviewStatus.new)])
    assert (r.transferred == ["o1"]) is ok and (r.to_manual == ["o1"]) is (not ok)


def test_zero_values_no_division_error():
    r = transfer_review([fact("o1", 0)], [fact("n1", 0, status=ReviewStatus.new)])
    assert r.transferred == ["o1"]


def test_zero_vs_nonzero_is_mismatch():
    r = transfer_review([fact("o1", 0)], [fact("n1", 0.5, status=ReviewStatus.new)])
    assert r.to_manual == ["o1"]


def test_no_counterpart_goes_to_manual():
    r = transfer_review([fact("o1", 12.89)], [])
    assert r.to_manual == ["o1"] and r.transferred == []


@pytest.mark.parametrize("change", [dict(metric="Voc"), dict(row="DV2"), dict(page=6), dict(table="Table 2")])
def test_key_parts_must_match(change):
    r = transfer_review([fact("o1", 12.89)], [fact("n1", 12.89, status=ReviewStatus.new, **change)])
    assert r.to_manual == ["o1"] and r.transferred == []


def test_unreviewed_old_facts_ignored():
    r = transfer_review([fact("o1", 12.89, status=ReviewStatus.new)], [fact("n1", 12.89, status=ReviewStatus.new)])
    assert r.transferred == [] and r.to_manual == []


def test_rejected_review_is_transferred_too():
    r = transfer_review([fact("o1", 12.89, status=ReviewStatus.rejected)], [fact("n1", 12.89, status=ReviewStatus.new)])
    assert r.transferred == ["o1"]


def test_corrected_value_transfers_when_machine_value_unchanged():
    old = fact("o1", 12.89, corrected=13.5)
    r = transfer_review([old], [fact("n1", 12.89, status=ReviewStatus.new)])
    assert r.transferred == ["o1"]


def test_one_new_fact_takes_only_one_old():
    r = transfer_review([fact("o1", 12.89), fact("o2", 12.89)], [fact("n1", 12.89, status=ReviewStatus.new)])
    assert len(r.transferred) == 1 and len(r.to_manual) == 1
    assert set(r.transferred) | set(r.to_manual) == {"o1", "o2"}


def test_each_old_fact_in_exactly_one_list():
    olds = [fact(f"o{i}", 10 + i, page=i) for i in range(6)]
    news = [fact(f"n{i}", 10 + i, status=ReviewStatus.new, page=i) for i in range(0, 6, 2)]
    r = transfer_review(olds, news)
    assert sorted(r.transferred + r.to_manual) == sorted(f.id for f in olds)
    assert not set(r.transferred) & set(r.to_manual)
    assert len(r.transferred) == 3


def test_empty_inputs():
    r = transfer_review([], [])
    assert r.transferred == [] and r.to_manual == []


def test_none_values_do_not_crash():
    r = transfer_review([fact("o1", None)], [fact("n1", None, status=ReviewStatus.new)])
    assert sorted(r.transferred + r.to_manual) == ["o1"]


def test_pure_function_does_not_mutate_inputs():
    olds, news = [fact("o1", 12.89)], [fact("n1", 12.89, status=ReviewStatus.new)]
    before = [f.model_dump() for f in olds + news]
    transfer_review(olds, news)
    assert [f.model_dump() for f in olds + news] == before


# --- recheck_paper -------------------------------------------------------------------------------------------------------

class JvInner:
    """Одна согласованная ВАХ: Voc 1.0 В, Jsc 20 мА/см2, FF 70 %, Pin 100 -> PCE 14 %."""
    name = "mock"

    def __init__(self, pce="14") -> None:
        self.pce = pce

    def extract(self, req):
        pid = req.publication_id
        b = PaperBundle(publication=Publication(publication_id=pid, title="JV", year=2023, DOI="10.9/jv",
                                                authors=["A B."], in_scope=True))
        b.devices = [Device(publication_id=pid, device_id="DV1", device_role="target", device_type="cell")]
        b.measurements = [Measurement(publication_id=pid, measurement_id="MS1", device_id=["DV1"],
                                      measurement_type="J-V", spectrum="AM1.5G", light_intensity="100",
                                      intensity_unit="mW/cm2")]
        rows = [("R1", "Voc", "1.0", "V"), ("R2", "Jsc", "20", "mA/cm2"), ("R3", "FF", "70", "%"),
                ("R4", "PCE", self.pce, "%")]
        for rid, m, v, u in rows:
            b.results.append(Result(publication_id=pid, result_id=rid, device_id="DV1", measurement_id="MS1",
                                    metric_name=m, raw_value=v, raw_unit=u, aggregation="champion",
                                    result_origin="primary", extraction_status="extracted", source_page=5,
                                    source_fragment=f"{m} {v}"))
        return ExtractOutcome(bundle=b, model_version="jv-1")

    def health(self):
        return MlHealth(status="ok", model_version="jv-1")


@pytest.fixture
def jv_paper(make_env, pdf_dir, fake_norm, profile):
    def _make(pce):
        env = make_env(extractor=JvInner(pce), normalizer=fake_norm)
        c = env.new_corpus()
        env.add_dir(pdf_dir, c.id)
        with env.uow() as uow:
            pid = next(f.paper_id for f in uow.files.list(c.id) if f.name == "D001.pdf")
        PaperProcessor(env.services).process(pid, profile=profile, mode="full", model_version="latest")
        return env, pid
    return _make


def open_checks(env, pid):
    with env.uow() as uow:
        return [i.check_name for i in uow.papers.load_bundle(pid).issues if i.status.value == "open"]


def edit_pce(env, pid, value):
    with env.uow() as uow:
        f = next(r.fact for r in uow.facts.query(FactFilter(paper_ids=[pid]), [], 1000, None).data
                 if r.fact.target.get("id") == "R4")
        uow.facts.update_review(f.id, corrected_value=Decimal(value), review_status=ReviewStatus.accepted)


def test_recheck_opens_jv_consistency_after_bad_edit(jv_paper, profile):
    env, pid = jv_paper("14")
    assert "jv_consistency" not in open_checks(env, pid)
    edit_pce(env, pid, "25")
    with env.uow() as uow:
        diff = recheck_paper(uow, pid, profile)
    assert diff.opened and "jv_consistency" in open_checks(env, pid)


def test_recheck_closes_jv_consistency_after_fix(jv_paper, profile):
    env, pid = jv_paper("25")
    assert "jv_consistency" in open_checks(env, pid)
    edit_pce(env, pid, "14")
    with env.uow() as uow:
        diff = recheck_paper(uow, pid, profile)
    assert diff.closed and "jv_consistency" not in open_checks(env, pid)


def test_recheck_without_changes_empty_diff(jv_paper, profile):
    env, pid = jv_paper("14")
    with env.uow() as uow:
        diff = recheck_paper(uow, pid, profile)
    assert (diff.opened, diff.closed, diff.changed) == ([], [], [])


def test_recheck_is_synchronous_and_does_not_call_ml(jv_paper, profile):
    env, pid = jv_paper("14")
    n = len(env.spy.calls)
    edit_pce(env, pid, "25")
    with env.uow() as uow:
        recheck_paper(uow, pid, profile)
    assert len(env.spy.calls) == n


def test_recheck_keeps_review_status_of_other_issues(jv_paper, profile):
    env, pid = jv_paper("25")
    with env.uow() as uow:
        b = uow.papers.load_bundle(pid)
        jv = next(i for i in b.issues if i.check_name == "jv_consistency")
    with env.uow() as uow:
        uow.issues.upsert_preserving_review(pid, [i.model_copy(update={"status": "ok"}) if i.key == jv.key else i
                                                  for i in b.issues])
        recheck_paper(uow, pid, profile)
    with env.uow() as uow:
        again = [i for i in uow.papers.load_bundle(pid).issues if i.key == jv.key]
    assert again and again[0].status.value == "ok"


def test_recheck_unknown_paper_not_found(make_env, profile):
    from faktura.domain.errors import AppError
    env = make_env()
    with env.uow() as uow, pytest.raises(AppError) as ei:
        recheck_paper(uow, "pap_nope", profile)
    assert ei.value.code == "not_found"
