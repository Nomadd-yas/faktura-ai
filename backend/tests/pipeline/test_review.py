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










