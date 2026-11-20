"""Общие фикстуры тестов модуля export (сдача, оценка, паспорт).

Допущения, которые тесты фиксируют как контракт (если реализация решит иначе, поправить здесь и в тестах):
  * поле-уровневый отклонённый факт (rejected) передаётся в select_for_submission через `manual_facts`
    (любой Fact с target {"entity", "id", "field"}); строка-уровневый - через record.meta.review_status;
  * плоские строки для export_flat/preview - словари с ключами FLAT_KEYS (см. flat_rows);
  * EvalReport.per_metric[metric] / totals - словари с ключами EVAL_KEYS.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Callable

import pytest

from faktura.domain.entities import Fact
from faktura.domain.io import bundles_from_tables, read_tables, tables_from_bundles
from faktura.domain.profile import builtin_solar_profile
from faktura.domain.records import (AddedBy, Device, IssueRecord, Layer, Material, Measurement, PaperBundle,
                                    Publication, RecordMeta, Result)

FLAT_KEYS = ("paper_id", "paper_title", "year", "sample", "parameter", "raw", "value", "unit", "status", "page",
             "source", "quote")
EVAL_KEYS = ("gold", "pred", "tp", "fp", "fn", "precision", "recall", "f1")


@pytest.fixture
def profile():
    return builtin_solar_profile()


@pytest.fixture
def profile_export_absent(profile):
    p = profile.model_copy(deep=True)
    p.flags.export_absent = True
    return p


def _meta(n: int) -> RecordMeta:
    return RecordMeta(ml_order=n)


@pytest.fixture
def make_bundle() -> Callable[..., PaperBundle]:
    """Маленькая валидная статья: M1, DV1, DV1-L1, MS1, R001 (PCE), R002 (повтор R001), I1 -> R001."""

    def _make(pid: str = "P001") -> PaperBundle:
        return PaperBundle(
            publication=Publication(publication_id=pid, title="Title, with comma", year=2021, DOI="10.1/x",
                                    authors=["Ivanov A.", "Petrov B."], in_scope=True),
            materials=[Material(publication_id=pid, material_id="M1", material_name_raw="CsPbI2Br",
                                material_class="perovskite", bandgap_value=Decimal("1.9"), bandgap_unit="eV",
                                meta=_meta(0))],
            devices=[Device(publication_id=pid, device_id="DV1", absorber_material_id="M1", device_label="pristine",
                            device_role="control", device_type="cell", architecture="n-i-p",
                            active_area=Decimal("0.0625"), area_unit="cm2", meta=_meta(0))],
            layers=[Layer(publication_id=pid, layer_id="DV1-L1", device_id="DV1", layer_order=1,
                          layer_function="absorber", layer_material="CsPbI2Br", layer_thickness=Decimal("400"),
                          thickness_unit="nm", meta=_meta(0))],
            measurements=[Measurement(publication_id=pid, measurement_id="MS1", device_id=["DV1"],
                                      measurement_type="J-V", scan_direction="reverse", mask_used=True,
                                      meta=_meta(0))],
            results=[
                Result(publication_id=pid, result_id="R001", device_id="DV1", measurement_id="MS1", material_id="M1",
                       metric_name="PCE", metric_value=Decimal("20.1"), metric_unit="%", aggregation="champion",
                       raw_value="20.1", raw_unit="%", normalized_value=Decimal("20.1"), normalized_unit="%",
                       result_origin="primary", source_page=3, source_table="Table 1",
                       source_fragment="power conversion efficiency of 20.1%", extraction_status="extracted",
                       meta=_meta(0)),
                Result(publication_id=pid, result_id="R002", device_id="DV1", measurement_id="MS1", material_id="M1",
                       metric_name="PCE", metric_value=Decimal("20.1"), metric_unit="%", aggregation="champion",
                       raw_value="20.1", raw_unit="%", normalized_value=Decimal("20.1"), normalized_unit="%",
                       result_origin="primary", source_page=1, source_fragment="efficiency of 20.1%",
                       extraction_status="extracted", duplicate_of="R001", meta=_meta(1)),
            ],
            issues=[IssueRecord(publication_id=pid, issue_id="I1", record_ids=["R001"], check_name="jv_consistency",
                                description="d", source_page=3, source_fragment="efficiency of 20.1%",
                                meta=_meta(0))],
        )

    return _make


@pytest.fixture
def mini_bundles(mini_gold_dir: Path) -> list[PaperBundle]:
    return bundles_from_tables(read_tables(mini_gold_dir))


@pytest.fixture
def tables_of():
    return tables_from_bundles


@pytest.fixture
def make_fact() -> Callable[..., Fact]:
    n = {"i": 0}

    def _make(**kw) -> Fact:
        n["i"] += 1
        base = dict(
            id=f"fct_{n['i']}", paper_id="pap_1", publication_id="P001",
            target={"entity": "results", "id": None, "field": "metric_value"}, param="pce", metric_name="PCE",
            row_id="DV1", raw_value="20,1", raw_unit="%", normalized_value=Decimal("20.1"), normalized_unit="%",
            source_page=3, source_fragment="power conversion efficiency of 20.1%", added_by=AddedBy.manual)
        base.update(kw)
        return Fact(**base)

    return _make


@pytest.fixture
def flat_rows() -> list[dict]:
    return [
        dict(paper_id="pap_a", paper_title="Perovskite, with comma", year=2024, sample="A", parameter="Voc",
             raw="1100 mV", value=Decimal("1.08"), unit="V", status="accepted", page=4, source="Табл. 2, строка A",
             quote="open-circuit voltage of 1.08 V"),
        dict(paper_id="pap_a", paper_title="Perovskite, with comma", year=2024, sample="B", parameter="КПД",
             raw="18,6 %", value=Decimal("18.6"), unit="%", status="new", page=4, source="Табл. 2, строка B",
             quote=None),
        dict(paper_id="pap_b", paper_title="CIGS", year=2023, sample="C", parameter="FF", raw="0.71",
             value=Decimal("71"), unit="%", status="new", page=2, source="Table 1", quote="fill factor 0.71"),
    ]


@pytest.fixture
def clone():
    return deepcopy


@pytest.fixture
def fake_script(tmp_path: Path):
    """Фабрика поддельных check_submission.py: fake_script(body) -> Path (body - код Python)."""

    def _make(body: str) -> Path:
        p = tmp_path / "fake_check.py"
        p.write_text(body, encoding="utf-8")
        return p

    return _make


@pytest.fixture
def py():
    return sys.executable
