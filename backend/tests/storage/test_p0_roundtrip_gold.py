"""Этап A, (data): save_processed -> load_bundle на статьях dev_gold возвращает тот же PaperBundle."""
from __future__ import annotations

import pytest

from faktura.domain.entities import FactDraft, PaperStats
from faktura.domain.io import bundles_from_tables, read_tables


def _drafts(bundle):
    return [FactDraft(ref=r.result_id, target={"entity": "results", "id": r.result_id, "field": "metric_value"},
                      param="metric_value", metric_name=r.metric_name, raw_value=r.raw_value, raw_unit=r.raw_unit,
                      normalized_value=r.normalized_value if not isinstance(r.normalized_value, str) else None,
                      normalized_unit=r.normalized_unit, extraction_status=r.extraction_status,
                      source_page=r.source_page if isinstance(r.source_page, int) else None,
                      source_fragment=r.source_fragment) for r in bundle.results]


def _roundtrip(db, ws_id, corpus, bundles):
    bad = []
    for b in bundles:
        with db.uow() as u:
            p = u.papers.create(workspace_id=ws_id, corpus_id=corpus.id, publication_id=b.publication_id,
                                title=b.publication.title)
            u.papers.save_processed(p.id, b, _drafts(b), [], list(b.issues), stats=PaperStats(),
                                    model_version="mock", versions={})
        with db.uow() as u:
            got = u.papers.load_bundle(p.id)
        if got != b:
            bad.append(b.publication_id)
    return bad


@pytest.mark.data
def test_every_dev_gold_paper_roundtrips(db, ws_id, corpus, gold_bundles):
    assert len(gold_bundles) >= 20
    assert _roundtrip(db, ws_id, corpus, gold_bundles) == []


@pytest.mark.data
def test_dev_gold_roundtrip_keeps_order_decimals_meta(db, ws_id, corpus, gold_bundles):
    from decimal import Decimal
    b = max(gold_bundles, key=lambda x: len(x.results))
    assert _roundtrip(db, ws_id, corpus, [b]) == []
    with db.uow() as u:
        got = u.papers.load_bundle(_paper_ids(db, ws_id)[0])
    assert [r.result_id for r in got.results] == [r.result_id for r in b.results]
    assert [r.meta.ml_order for r in got.results] == [r.meta.ml_order for r in b.results]
    assert all(isinstance(r.metric_value, (Decimal, str, type(None))) for r in got.results)
    assert [r.metric_value for r in got.results] == [r.metric_value for r in b.results]
    assert [r.meta.notes_backend for r in got.results] == [r.meta.notes_backend for r in b.results]


def _paper_ids(db, ws_id):
    import sqlalchemy as sa
    eng = sa.create_engine(db.url)
    try:
        with eng.connect() as c:
            return [r[0] for r in c.execute(sa.text("SELECT id FROM papers"))]
    finally:
        eng.dispose()


@pytest.mark.data
def test_roundtrip_all_gold_in_single_database_does_not_mix_papers(db, ws_id, corpus, gold_bundles):
    _roundtrip(db, ws_id, corpus, gold_bundles)
    with db.uow() as u:
        for pid in _paper_ids(db, ws_id):
            got = u.papers.load_bundle(pid)
            assert all(r.publication_id == got.publication_id for r in got.results)


def test_roundtrip_mini_gold(db, ws_id, corpus, mini_gold_dir):
    """Без данных кейса: фикстура tests/fixtures/mini_gold (2 статьи)."""
    bundles = bundles_from_tables(read_tables(mini_gold_dir))
    assert len(bundles) == 2
    assert _roundtrip(db, ws_id, corpus, bundles) == []


@pytest.mark.data
def test_roundtrip_dev_gold_issue_status_and_lists(db, ws_id, corpus, gold_bundles):
    b = next(x for x in gold_bundles if x.issues)
    assert _roundtrip(db, ws_id, corpus, [b]) == []
