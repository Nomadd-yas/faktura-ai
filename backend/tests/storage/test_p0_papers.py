"""Этап A: papers (create/get, save_processed, load_bundle, поиск дублей), facts.manual_for_paper."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import DupKind, PaperStats, PaperStatus
from faktura.domain.errors import AppError
from faktura.domain.records import PaperBundle

NOT_FOUND = (AppError, KeyError, LookupError)


# ---- create / get ----------------------------------------------------------------------------

def test_paper_create_get(db, ws_id, corpus):
    with db.uow() as u:
        p = u.papers.create(workspace_id=ws_id, corpus_id=corpus.id, publication_id="D001", title="T",
                            year=2021, doi="10.1/abc", sha256="a" * 64, page_count=9)
    assert p.id and p.publication_id == "D001" and p.title == "T" and p.year == 2021 and p.doi == "10.1/abc"
    assert p.status == PaperStatus.queued and p.collection == "solar" and p.deleted_at is None
    assert p.stats == PaperStats() and p.versions == {} and p.needs_ocr is False
    with db.uow() as u:
        assert u.papers.get(p.id) == p


def test_paper_get_unknown_none(db):
    with db.uow() as u:
        assert u.papers.get("pap_nope") is None


def test_paper_create_ids_unique(db, ws_id, corpus):
    with db.uow() as u:
        ids = {u.papers.create(workspace_id=ws_id, corpus_id=corpus.id, publication_id=f"D{i:03d}").id
               for i in range(25)}
    assert len(ids) == 25


def test_paper_create_requires_publication_id(db, ws_id):
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.papers.create(workspace_id=ws_id)
    assert ei.type is not NotImplementedError


def test_paper_create_without_corpus_allowed(db, ws_id):
    """Статья из ручной загрузки может не иметь корпуса (corpus_id: str | None)."""
    with db.uow() as u:
        p = u.papers.create(workspace_id=ws_id, publication_id="D001")
    assert p.corpus_id is None


# ---- save_processed --------------------------------------------------------------------------

def test_save_processed_returns_ids_for_every_ref(db, new_paper, process):
    p = new_paper()
    res = process(p, n=5, anchors=True).result
    assert res.paper_id == p.id
    assert set(res.fact_ids) == {f"R{i:03d}" for i in range(1, 6)}
    assert set(res.anchor_ids) == {f"a_R{i:03d}" for i in range(1, 6)}
    assert len(set(res.fact_ids.values())) == 5 and all(res.fact_ids.values())


def test_save_processed_updates_paper_row(db, new_paper, process):
    p = new_paper()
    stats = PaperStats(facts_count=3, open_issues=2, top_level="warn")
    process(p, n=3, stats=stats)
    with db.uow() as u:
        g = u.papers.get(p.id)
    assert g.model_version == "m1" and g.versions == {"norm": "1", "rules": "1"}
    assert g.stats.facts_count == 3 and g.stats.open_issues == 2 and g.stats.top_level == "warn"
    assert g.processed_at is not None


def test_save_processed_roundtrip_bundle(db, new_paper, process):
    p = new_paper()
    bundle = process(p, n=6).bundle
    with db.uow() as u:
        loaded = u.papers.load_bundle(p.id)
    assert isinstance(loaded, PaperBundle)
    assert loaded == bundle


def test_load_bundle_preserves_types_and_order(db, new_paper, process):
    p = new_paper()
    process(p, n=12)
    with db.uow() as u:
        b = u.papers.load_bundle(p.id)
    assert [r.result_id for r in b.results] == [f"R{i:03d}" for i in range(1, 13)]
    assert [r.meta.ml_order for r in b.results] == list(range(12))
    assert all(isinstance(r.metric_value, Decimal) for r in b.results)
    assert b.results[0].metric_value == Decimal("10.0") and str(b.results[1].metric_value) == "10.1"
    assert b.layers[0].layer_thickness == "63-68"          # нераспознанный текст сохраняется как есть
    assert b.layers[1].layer_thickness == Decimal("100")
    assert b.measurements[0].device_id == ["DV1", "DV2"]   # список
    assert b.materials[0].additives == ["PEAI", "KI"]
    assert b.devices[0].encapsulation is False and b.measurements[0].certified is True
    assert b.publication.authors == ["A B.", "C D."]
    assert b.results[0].meta.extra == {"unmapped": "x0"}   # meta.extra


def test_load_bundle_orders_by_ml_order_not_insertion(db, new_paper, make_bundle, make_drafts):
    p = new_paper()
    bundle = make_bundle("D001", n=5)
    bundle.results.reverse()  # физический порядок в списке обратный, ml_order - прежний
    with db.uow() as u:
        u.papers.save_processed(p.id, bundle, make_drafts(bundle), [], [], stats=PaperStats(),
                                model_version="m1", versions={})
    with db.uow() as u:
        got = u.papers.load_bundle(p.id)
    assert [r.meta.ml_order for r in got.results] == [0, 1, 2, 3, 4]


def test_load_bundle_unknown_paper(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.papers.load_bundle("pap_nope")


def test_load_bundle_before_processing_is_stub_or_error(db, new_paper):
    """Статья без save_processed: либо failed_stub (только publications), либо not_found - но не мусор."""
    p = new_paper("D077")
    try:
        with db.uow() as u:
            b = u.papers.load_bundle(p.id)
    except NOT_FOUND:
        return
    assert b.publication.publication_id == "D077" and b.results == [] and b.devices == []


def test_save_processed_replaces_everything(db, new_paper, process):
    p = new_paper()
    first = process(p, n=8).result
    second = process(p, n=2, base=Decimal("20")).result
    with db.uow() as u:
        b = u.papers.load_bundle(p.id)
    assert [r.result_id for r in b.results] == ["R001", "R002"]
    assert b.results[0].metric_value == Decimal("20.0")
    assert set(second.fact_ids) == {"R001", "R002"}
    assert len(first.fact_ids) == 8


def test_save_processed_is_atomic_on_failure(db, new_paper, process, make_bundle, make_drafts):
    p = new_paper()
    original = process(p, n=4).bundle
    bad = make_bundle("D001", n=2, base=Decimal("99"))
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.papers.save_processed(p.id, bad, make_drafts(bad), [], [object()], stats=PaperStats(),
                                    model_version="m2", versions={})
    assert ei.type is not NotImplementedError
    with db.uow() as u:
        assert u.papers.load_bundle(p.id) == original
        assert u.papers.get(p.id).model_version == "m1"


def test_save_processed_unknown_paper(db, make_bundle, make_drafts):
    b = make_bundle("D001")
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.papers.save_processed("pap_nope", b, make_drafts(b), [], [], stats=PaperStats(),
                                    model_version="m1", versions={})


def test_save_processed_empty_facts_and_results(db, new_paper, make_bundle):
    p = new_paper()
    b = make_bundle("D001", n=0)
    with db.uow() as u:
        res = u.papers.save_processed(p.id, b, [], [], [], stats=PaperStats(), model_version="m1", versions={})
    assert res.fact_ids == {} and res.anchor_ids == {}
    with db.uow() as u:
        assert u.papers.load_bundle(p.id).results == []


def test_save_processed_failed_stub_bundle(db, new_paper):
    p = new_paper("D050")
    stub = PaperBundle.failed_stub("D050")
    with db.uow() as u:
        u.papers.save_processed(p.id, stub, [], [], [], stats=PaperStats(), model_version="m1", versions={})
    with db.uow() as u:
        b = u.papers.load_bundle(p.id)
    assert b.publication.publication_id == "D050" and b.results == [] and b.issues == []


def test_save_processed_issues_roundtrip(db, new_paper, process, make_issue):
    p = new_paper()
    issues = [make_issue("D001", "I-aaaa1111", records=("R001", "R002")),
              make_issue("D001", "I-bbbb2222", check="outlier", records=("R003",), description="другое")]
    bundle = process(p, n=3, issues=issues).bundle
    with db.uow() as u:
        got = u.papers.load_bundle(p.id)
    assert [i.key for i in got.issues] == ["I-aaaa1111", "I-bbbb2222"]
    assert got.issues[0].record_ids == ["R001", "R002"] and got.issues[1].check_name == "outlier"
    assert got == bundle


def test_save_processed_anchors_linked_to_facts(db, new_paper, process):
    """Якоря сохраняются, результат содержит соответствие ref -> id."""
    p = new_paper()
    res = process(p, n=3, anchors=True).result
    assert all(a for a in res.anchor_ids.values())


def test_save_processed_unknown_anchor_ref_fails_atomically(db, new_paper, process, make_bundle, make_drafts):
    p = new_paper()
    original = process(p, n=2).bundle
    b = make_bundle("D001", n=2, base=Decimal("50"))
    drafts = make_drafts(b, anchors=True)  # anchor_ref указывает на якоря, которых нет в списке
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.papers.save_processed(p.id, b, drafts, [], [], stats=PaperStats(), model_version="m1", versions={})
    assert ei.type is not NotImplementedError
    with db.uow() as u:
        assert u.papers.load_bundle(p.id) == original


def test_save_processed_does_not_touch_other_papers(db, new_paper, process):
    p1, p2 = new_paper("D001"), new_paper("D002")
    b1 = process(p1, n=3).bundle
    process(p2, n=7)
    process(p2, n=1)
    with db.uow() as u:
        assert u.papers.load_bundle(p1.id) == b1


# ---- find_by_doi / find_by_title_key / set_duplicate -----------------------------------------

def test_find_by_doi(db, ws_id, corpus, new_paper):
    a = new_paper("D001", doi="10.1000/xyz")
    b = new_paper("D002", doi="10.1000/xyz")
    new_paper("D003", doi="10.1000/other")
    with db.uow() as u:
        u.papers.create(workspace_id="ws_other", publication_id="D009", doi="10.1000/xyz")
        assert {p.id for p in u.papers.find_by_doi(ws_id, "10.1000/xyz")} == {a.id, b.id}
        assert u.papers.find_by_doi(ws_id, "10.9/none") == []
        assert [p.publication_id for p in u.papers.find_by_doi("ws_other", "10.1000/xyz")] == ["D009"]


def test_find_by_doi_case_insensitive(db, ws_id, new_paper):
    a = new_paper("D001", doi="10.1000/ABC")
    with db.uow() as u:
        assert [p.id for p in u.papers.find_by_doi(ws_id, "10.1000/abc")] == [a.id]


def test_find_by_doi_empty_or_none_returns_nothing(db, ws_id, new_paper):
    new_paper("D001")  # без DOI
    with db.uow() as u:
        assert u.papers.find_by_doi(ws_id, "") == []


def test_find_by_title_key(db, ws_id, new_paper):
    """Ключ - нормализованный заголовок (pdfio.title_key); хранение ключа - на стороне storage при create."""
    a = new_paper("D001", title="Perovskite Solar Cells: A Review", title_key="perovskite solar cells a review")
    new_paper("D002", title="Other", title_key="other")
    with db.uow() as u:
        assert [p.id for p in u.papers.find_by_title_key(ws_id, "perovskite solar cells a review")] == [a.id]
        assert u.papers.find_by_title_key(ws_id, "nothing") == []
        assert u.papers.find_by_title_key("ws_other", "other") == []


def test_find_excludes_soft_deleted_papers(db, ws_id, new_paper):
    """Допущение: papers.soft_delete(id)."""
    a = new_paper("D001", doi="10.1/x")
    with db.uow() as u:
        u.papers.soft_delete(a.id)
    with db.uow() as u:
        assert u.papers.find_by_doi(ws_id, "10.1/x") == []


def test_set_duplicate_and_clear(db, new_paper):
    a, b = new_paper("D001"), new_paper("D002")
    with db.uow() as u:
        u.papers.set_duplicate(b.id, a.id, DupKind.doi)
    with db.uow() as u:
        g = u.papers.get(b.id)
        assert g.dup_of == a.id and g.dup_kind == DupKind.doi
        u.papers.set_duplicate(b.id, None, None)
    with db.uow() as u:
        g = u.papers.get(b.id)
        assert g.dup_of is None and g.dup_kind is None


@pytest.mark.parametrize("kind", list(DupKind))
def test_set_duplicate_all_kinds(db, new_paper, kind):
    a, b = new_paper("D001"), new_paper("D002")
    with db.uow() as u:
        u.papers.set_duplicate(b.id, a.id, kind)
        assert u.papers.get(b.id).dup_kind == kind


def test_set_duplicate_unknown_paper(db, new_paper):
    a = new_paper("D001")
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.papers.set_duplicate("pap_nope", a.id, DupKind.file)


def test_set_duplicate_self_reference_rejected(db, new_paper):
    a = new_paper("D001")
    with pytest.raises((ValueError, AppError)):
        with db.uow() as u:
            u.papers.set_duplicate(a.id, a.id, DupKind.doi)


# ---- facts.manual_for_paper ------------------------------------------------------------------

def test_manual_for_paper_only_manual(db, new_paper, process):
    p = new_paper()
    res = process(p, n=5, manual_refs=("R002", "R004")).result
    with db.uow() as u:
        got = u.facts.manual_for_paper(p.id)
    assert {f.id for f in got} == {res.fact_ids["R002"], res.fact_ids["R004"]}
    assert all(f.added_by.value == "manual" and f.paper_id == p.id for f in got)
    assert {f.target["id"] for f in got} == {"R002", "R004"}


def test_manual_for_paper_empty(db, new_paper, process):
    p = new_paper()
    process(p, n=3)
    with db.uow() as u:
        assert u.facts.manual_for_paper(p.id) == []
        assert u.facts.manual_for_paper("pap_nope") == []


def test_manual_for_paper_other_paper_not_leaked(db, new_paper, process):
    p1, p2 = new_paper("D001"), new_paper("D002")
    process(p1, n=3, manual_refs=("R001",))
    process(p2, n=3)
    with db.uow() as u:
        assert u.facts.manual_for_paper(p2.id) == []


def test_manual_fact_fields_preserved(db, new_paper, process):
    p = new_paper()
    process(p, n=2, manual_refs=("R001",))
    with db.uow() as u:
        (f,) = u.facts.manual_for_paper(p.id)
    assert f.param == "metric_value" and f.metric_name == "PCE" and f.normalized_value == Decimal("10.0")
    assert f.source_page == 1 and f.source_fragment == "Table 1 row 0" and f.publication_id == "D001"
