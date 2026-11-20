"""Этап B: мягкое удаление и восстановление корпусов и статей, gone после 30 суток (часы подменены).

Допущение об интерфейсе: corpora.soft_delete(id) / corpora.restore(id) -> Corpus;
papers.soft_delete(id) / papers.restore(id) -> Paper; просроченное восстановление -> AppError("gone").
"""
from __future__ import annotations

import pytest

from faktura.domain.entities import FactFilter
from faktura.domain.errors import AppError

NOT_FOUND = (AppError, KeyError, LookupError)


@pytest.fixture
def cdb(clocked_db):
    return clocked_db


def _corpus(cdb, ws_id):
    with cdb.uow() as u:
        return u.corpora.create(workspace_id=ws_id, name="K")


def _paper(cdb, ws_id, corpus_id, pid="D001", **kw):
    with cdb.uow() as u:
        return u.papers.create(workspace_id=ws_id, corpus_id=corpus_id, publication_id=pid, **kw)


def test_corpus_soft_delete_sets_deleted_at(cdb, clock, ws_id):
    c = _corpus(cdb, ws_id)
    with cdb.uow() as u:
        u.corpora.soft_delete(c.id)
    with cdb.uow() as u:
        g = u.corpora.get(c.id)
    assert g is not None and g.deleted_at is not None  # запись остаётся: get видит её, list - нет
    assert g.deleted_at == clock()


def test_corpus_restore_within_30_days(cdb, clock, ws_id):
    c = _corpus(cdb, ws_id)
    with cdb.uow() as u:
        u.corpora.soft_delete(c.id)
    clock.advance(days=29, hours=23)
    with cdb.uow() as u:
        r = u.corpora.restore(c.id)
    assert r.id == c.id and r.deleted_at is None
    with cdb.uow() as u:
        assert [x.id for x in u.corpora.list(ws_id)] == [c.id]


def test_corpus_restore_after_30_days_is_gone(cdb, clock, ws_id):
    c = _corpus(cdb, ws_id)
    with cdb.uow() as u:
        u.corpora.soft_delete(c.id)
    clock.advance(days=30, minutes=1)
    with pytest.raises(AppError) as ei:
        with cdb.uow() as u:
            u.corpora.restore(c.id)
    assert ei.value.code == "gone" and ei.value.http_status == 410


def test_corpus_restore_boundary_exactly_30_days(cdb, clock, ws_id):
    c = _corpus(cdb, ws_id)
    with cdb.uow() as u:
        u.corpora.soft_delete(c.id)
    clock.advance(days=30)
    with cdb.uow() as u:
        assert u.corpora.restore(c.id).deleted_at is None  # граница включительно


def test_corpus_restore_not_deleted_is_noop_or_conflict(cdb, ws_id):
    c = _corpus(cdb, ws_id)
    try:
        with cdb.uow() as u:
            r = u.corpora.restore(c.id)
    except AppError as e:
        assert e.code in ("conflict", "not_found", "validation_error")
    else:
        assert r.deleted_at is None


def test_corpus_restore_unknown(cdb):
    with pytest.raises(NOT_FOUND):
        with cdb.uow() as u:
            u.corpora.restore("crp_nope")


def test_corpus_soft_delete_unknown(cdb):
    with pytest.raises(NOT_FOUND):
        with cdb.uow() as u:
            u.corpora.soft_delete("crp_nope")


def test_corpus_soft_delete_twice_keeps_first_timestamp(cdb, clock, ws_id):
    c = _corpus(cdb, ws_id)
    with cdb.uow() as u:
        u.corpora.soft_delete(c.id)
    first = clock()
    clock.advance(days=5)
    with cdb.uow() as u:
        u.corpora.soft_delete(c.id)
        assert u.corpora.get(c.id).deleted_at == first


def test_corpus_soft_delete_cascades_to_papers_and_facts(cdb, ws_id, process_clocked):
    c, p = process_clocked
    with cdb.uow() as u:
        assert len(u.facts.query(FactFilter(workspace_id=ws_id), [], 10, None).data) == 3
        u.corpora.soft_delete(c.id)
    with cdb.uow() as u:
        assert u.facts.query(FactFilter(workspace_id=ws_id), [], 10, None).data == []
        assert u.stats.overview(ws_id)["empty"] is True


def test_corpus_restore_brings_back_papers_and_facts(cdb, clock, ws_id, process_clocked):
    c, p = process_clocked
    with cdb.uow() as u:
        u.corpora.soft_delete(c.id)
    clock.advance(days=10)
    with cdb.uow() as u:
        u.corpora.restore(c.id)
    with cdb.uow() as u:
        assert len(u.facts.query(FactFilter(workspace_id=ws_id), [], 10, None).data) == 3
        assert u.papers.get(p.id).deleted_at is None
        assert u.stats.overview(ws_id)["totals"]["facts"] == 3


@pytest.fixture
def process_clocked(cdb, ws_id, make_bundle, make_drafts):
    from faktura.domain.entities import PaperStats
    c = _corpus(cdb, ws_id)
    p = _paper(cdb, ws_id, c.id)
    b = make_bundle("D001", n=3)
    with cdb.uow() as u:
        u.papers.save_processed(p.id, b, make_drafts(b), [], [], stats=PaperStats(facts_count=3),
                                model_version="m", versions={})
    return c, p


def test_paper_soft_delete_and_restore(cdb, clock, ws_id, process_clocked):
    c, p = process_clocked
    with cdb.uow() as u:
        u.papers.soft_delete(p.id)
        assert u.papers.get(p.id).deleted_at is not None
        assert u.facts.query(FactFilter(workspace_id=ws_id), [], 10, None).data == []
    clock.advance(days=3)
    with cdb.uow() as u:
        r = u.papers.restore(p.id)
        assert r.deleted_at is None
        assert len(u.facts.query(FactFilter(workspace_id=ws_id), [], 10, None).data) == 3
        assert u.papers.load_bundle(p.id).publication.publication_id == "D001"


def test_paper_restore_after_30_days_gone(cdb, clock, process_clocked):
    c, p = process_clocked
    with cdb.uow() as u:
        u.papers.soft_delete(p.id)
    clock.advance(days=31)
    with pytest.raises(AppError) as ei:
        with cdb.uow() as u:
            u.papers.restore(p.id)
    assert ei.value.code == "gone"


def test_paper_soft_delete_updates_overview_cache(cdb, ws_id, process_clocked):
    c, p = process_clocked
    with cdb.uow() as u:
        u.papers.soft_delete(p.id)
        ov = u.stats.overview(ws_id)
    assert ov["totals"]["papers"] == 0 and ov["totals"]["facts"] == 0


def test_paper_restore_unknown(cdb):
    with pytest.raises(NOT_FOUND):
        with cdb.uow() as u:
            u.papers.restore("pap_nope")


def test_default_clock_is_utc_system_time(db, ws_id):
    """Без подмены часов created_at - текущее время (в пределах минуты)."""
    from datetime import datetime, timedelta, timezone
    with db.uow() as u:
        c = u.corpora.create(workspace_id=ws_id, name="K")
    ca = c.created_at if c.created_at.tzinfo else c.created_at.replace(tzinfo=timezone.utc)
    assert abs(datetime.now(timezone.utc) - ca) < timedelta(minutes=1)
