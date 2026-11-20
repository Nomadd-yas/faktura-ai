"""Фикстуры тестов модуля storage (план 5.5).

Допущения об интерфейсе, которых нет в зафиксированных сигнатурах (при реализации сверить и поправить здесь):
  * Database(url, clock=None): clock - вызываемый объект без аргументов, возвращает datetime (UTC);
    по умолчанию системные часы. Нужен для подмены часов (30 суток, 24 часа, TTL).
  * Рабочее пространство: если у UoW есть репозиторий `workspaces` с методом `ensure(id)`, фикстура его
    вызывает; иначе считается, что репозитории создают workspace неявно (или FK не требует строки).
  * Мягкое удаление: corpora/papers .soft_delete(id) и .restore(id); после 30 суток restore -> AppError("gone").
  * actions.mark_undone(action_id) - пометка отменённого действия (нужна для теста глубины отмены).
Каждое допущение продублировано в docstring соответствующего теста.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from faktura.domain.entities import AnchorDraft, FactDraft, PaperStats
from faktura.domain.records import (Device, IssueRecord, Layer, Material, Measurement, PaperBundle, Publication,
                                    RecordMeta, Result)
from faktura.storage import Database

WS = "ws_test"
METRICS = ["PCE", "Voc", "Jsc", "FF"]
UNITS = {"PCE": "%", "Voc": "V", "Jsc": "mA/cm2", "FF": "%"}


class FakeClock:
    """Управляемые часы: clock() -> datetime."""

    def __init__(self) -> None:
        self.t = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=["sqlite", pytest.param("pg", marks=pytest.mark.pg)])
def db_url(request, tmp_path):
    """SQLite во временном файле либо Postgres (схема на тест) по FAKTURA_TEST_PG_URL."""
    if request.param == "sqlite":
        yield f"sqlite:///{(tmp_path / 'storage_test.db').as_posix()}"
        return
    import sqlalchemy as sa

    base = os.environ["FAKTURA_TEST_PG_URL"]
    schema = "t_" + uuid.uuid4().hex[:12]
    eng = sa.create_engine(base)
    with eng.begin() as c:
        c.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    sep = "&" if "?" in base else "?"
    yield f"{base}{sep}options=-csearch_path%3D{schema}"
    with eng.begin() as c:
        c.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
    eng.dispose()


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'sqlite_only.db').as_posix()}"


@pytest.fixture
def db_factory(db_url):
    """db_factory(clock=None) -> Database со схемой."""

    def _make(clock=None) -> Database:
        d = Database(db_url, clock=clock) if clock is not None else Database(db_url)
        d.create_all()
        return d

    return _make


@pytest.fixture
def db(db_factory) -> Database:
    return db_factory()


@pytest.fixture
def clocked_db(db_factory, clock) -> Database:
    return db_factory(clock=clock)


@pytest.fixture
def ws_id() -> str:
    return WS


def ensure_ws(uow, ws: str = WS) -> None:
    repo = getattr(uow, "workspaces", None)
    if repo is not None:
        repo.ensure(ws)


@pytest.fixture
def corpus(db, ws_id):
    with db.uow() as u:
        ensure_ws(u, ws_id)
        return u.corpora.create(workspace_id=ws_id, name="Корпус 1", collection="solar", created_by="usr_1")


@pytest.fixture
def make_bundle():
    """make_bundle(pid, n=3, base=Decimal(10)) -> PaperBundle: устройство, материал, 2 слоя, измерение, n результатов."""

    def _make(pid: str = "D001", n: int = 3, base: Decimal = Decimal("10"), title: str | None = None,
              year: int = 2022, doi: str | None = None) -> PaperBundle:
        pub = Publication(publication_id=pid, title=title or f"Title {pid}", year=year, DOI=doi,
                          authors=["A B.", "C D."], in_scope=True, meta=RecordMeta(ml_order=0))
        results = []
        for i in range(n):
            m = METRICS[i % len(METRICS)]
            v = base + Decimal(i) / Decimal(10)
            results.append(Result(
                publication_id=pid, result_id=f"R{i + 1:03d}", device_id="DV1", measurement_id="MS1",
                material_id="M1", metric_name=m, metric_value=v, metric_unit=UNITS[m], raw_value=str(v),
                raw_unit=UNITS[m], normalized_value=v, normalized_unit=UNITS[m], result_origin="primary",
                source_page=(i % 9) + 1, source_fragment=f"Table 1 row {i}", extraction_status="extracted",
                meta=RecordMeta(ml_order=i, confidence=round(0.5 + (i % 50) / 100, 2), page=(i % 9) + 1,
                                extra={"unmapped": f"x{i}"} if i == 0 else {})))
        return PaperBundle(
            publication=pub,
            materials=[Material(publication_id=pid, material_id="M1", material_name_raw="MAPbI3",
                                additives=["PEAI", "KI"], bandgap_value=Decimal("1.55"), bandgap_unit="eV",
                                meta=RecordMeta(ml_order=0))],
            devices=[Device(publication_id=pid, device_id="DV1", absorber_material_id="M1",
                            active_area=Decimal("0.09"), area_unit="cm2", encapsulation=False,
                            meta=RecordMeta(ml_order=0))],
            layers=[Layer(publication_id=pid, layer_id="L1", device_id="DV1", layer_order=1,
                          layer_thickness="63-68", thickness_unit="nm", meta=RecordMeta(ml_order=0)),
                    Layer(publication_id=pid, layer_id="L2", device_id="DV1", layer_order=2,
                          layer_thickness=Decimal("100"), thickness_unit="nm", meta=RecordMeta(ml_order=1))],
            measurements=[Measurement(publication_id=pid, measurement_id="MS1", device_id=["DV1", "DV2"],
                                      light_intensity=Decimal("100"), certified=True, meta=RecordMeta(ml_order=0))],
            results=results,
            issues=[],
        )

    return _make


@pytest.fixture
def make_drafts():
    """make_drafts(bundle, manual_refs=(), anchors=False) -> list[FactDraft], по одному на результат."""

    def _make(bundle: PaperBundle, manual_refs=(), anchors: bool = False) -> list[FactDraft]:
        out = []
        for r in bundle.results:
            out.append(FactDraft(
                ref=r.result_id, target={"entity": "results", "id": r.result_id, "field": "metric_value"},
                param="metric_value", metric_name=r.metric_name, row_id=r.device_id, row_label="Device 1",
                raw_value=r.raw_value, raw_unit=r.raw_unit, normalized_value=r.normalized_value,
                normalized_unit=r.normalized_unit, norm_status="ok", extraction_status="extracted",
                confidence=r.meta.confidence, source_page=r.source_page, source_fragment=r.source_fragment,
                source_kind="table", quote_found=True, anchor_ref=(f"a_{r.result_id}" if anchors else None),
                added_by="manual" if r.result_id in manual_refs else "auto", model_version="m1"))
        return out

    return _make


@pytest.fixture
def make_anchors():
    def _make(bundle: PaperBundle) -> list[AnchorDraft]:
        return [AnchorDraft(ref=f"a_{r.result_id}", kind="value", page=int(r.source_page or 1),
                            rects=[{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.05}], text=r.source_fragment,
                            fact_refs=[r.result_id]) for r in bundle.results]

    return _make


@pytest.fixture
def new_paper(db, ws_id, corpus):
    """new_paper(pid='D001', **fields) -> Paper (status queued)."""

    def _make(pid: str = "D001", **fields):
        fields.setdefault("workspace_id", ws_id)
        fields.setdefault("corpus_id", corpus.id)
        fields.setdefault("publication_id", pid)
        fields.setdefault("title", f"Title {pid}")
        fields.setdefault("year", 2022)
        with db.uow() as u:
            return u.papers.create(**fields)

    return _make


@pytest.fixture
def process(db, make_bundle, make_drafts, make_anchors):
    """process(paper, bundle=None, n=3, issues=(), manual_refs=(), stats=None, anchors=False, **bundle_kw)
    -> SimpleNamespace(bundle, result)."""

    def _go(paper, bundle=None, n: int = 3, issues=(), manual_refs=(), stats=None, anchors=False, **kw):
        bundle = bundle or make_bundle(paper.publication_id, n=n, **kw)
        bundle.issues = list(issues)
        with db.uow() as u:
            res = u.papers.save_processed(
                paper.id, bundle, make_drafts(bundle, manual_refs, anchors),
                make_anchors(bundle) if anchors else [], list(issues),
                stats=stats or PaperStats(facts_count=len(bundle.results)), model_version="m1",
                versions={"norm": "1", "rules": "1"})
        return SimpleNamespace(bundle=bundle, result=res)

    return _go


@pytest.fixture
def make_issue():
    def _make(pid: str, key: str, check: str = "jv_consistency", records=("R001",), status="open",
              description: str = "desc") -> IssueRecord:
        return IssueRecord(publication_id=pid, check_name=check, record_ids=list(records), description=description,
                           key=key, status=status, severity="warn", computed_by="backend")

    return _make


@pytest.fixture
def user():
    from faktura.domain.entities import CurrentUser
    return CurrentUser(id="usr_1", workspace_id=WS, email="u@example.com", is_admin=False)
