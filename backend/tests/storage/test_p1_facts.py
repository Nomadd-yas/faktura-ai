"""Этап B: facts.query (keyset), facets, update_review, add_manual."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import FactDraft, FactFilter, PaperStats, ReviewStatus, SortKey
from faktura.domain.errors import AppError

NOT_FOUND = (AppError, KeyError, LookupError)
BAD = (ValueError, AppError)


def _walk(u, flt, sort, limit=100, max_pages=200):
    """Обход страниц; возвращает список id в порядке выдачи."""
    out, cursor = [], None
    for _ in range(max_pages):
        page = u.facts.query(flt, sort, limit, cursor)
        out += [r.fact.id for r in page.data]
        cursor = page.next_cursor
        if not cursor:
            return out
    raise AssertionError("курсор не заканчивается")


@pytest.fixture
def thousand(db, ws_id, corpus, new_paper, process):
    """1000 фактов в 4 статьях; значения с множеством совпадений (проверка устойчивости при равных ключах)."""
    ids = {}
    for k in range(4):
        p = new_paper(f"D{k + 1:03d}", year=2019 + k)
        res = process(p, n=250, base=Decimal(k % 2)).result  # значения 0..2.4 / 1..3.4 -> много повторов
        ids[p.id] = set(res.fact_ids.values())
    return ids


# ---- keyset ----------------------------------------------------------------------------------

def test_query_first_page_shape(db, ws_id, thousand):
    with db.uow() as u:
        page = u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")], 100, None)
    assert len(page.data) == 100 and page.next_cursor and page.total == 1000
    row = page.data[0]
    assert row.fact.id and row.paper_title and row.paper_year in range(2019, 2023) and row.open_issues == 0


def test_query_walk_1000_by_100_no_gaps_no_dups(db, ws_id, thousand):
    allids = set().union(*thousand.values())
    with db.uow() as u:
        got = _walk(u, FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")], 100)
    assert len(got) == 1000 and len(set(got)) == 1000 and set(got) == allids


def test_query_walk_is_stable_between_runs(db, ws_id, thousand):
    f, s = FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")]
    with db.uow() as u:
        assert _walk(u, f, s, 100) == _walk(u, f, s, 100) == _walk(u, f, s, 333)


def test_query_inserts_between_pages_no_skips_no_repeats(db, ws_id, thousand, new_paper, process):
    """Вставки между запросами: исходные факты не пропускаются и не повторяются (offset-пагинация сломалась бы)."""
    flt, sort = FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")]
    original = set().union(*thousand.values())
    seen: list[str] = []
    cursor = None
    extra = 0
    while True:
        with db.uow() as u:
            page = u.facts.query(flt, sort, 100, cursor)
        seen += [r.fact.id for r in page.data]
        cursor = page.next_cursor
        # между страницами добавляем статью с очень малыми значениями (попадают в уже пройденную часть)
        extra += 1
        p = new_paper(f"X{extra:03d}")
        process(p, n=5, base=Decimal("-5"))
        if not cursor:
            break
    assert len(seen) == len(set(seen))
    assert original <= set(seen)


def test_query_deletes_between_pages_no_repeats(db, ws_id, thousand):
    flt, sort = FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")]
    with db.uow() as u:
        p1 = u.facts.query(flt, sort, 100, None)
        victim = p1.data[0].fact.id
        u.facts.update_review(victim, review_status=ReviewStatus.rejected)
    seen = [r.fact.id for r in p1.data]
    cursor = p1.next_cursor
    while cursor:
        with db.uow() as u:
            page = u.facts.query(flt, sort, 100, cursor)
        seen += [r.fact.id for r in page.data]
        cursor = page.next_cursor
    assert len(seen) == len(set(seen))


def test_query_edit_of_sort_key_between_pages_no_repeats(db, ws_id, thousand):
    """Правка значения, по которому идёт сортировка (corrected_value не влияет на normalized_value)."""
    flt, sort = FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")]
    with db.uow() as u:
        first = u.facts.query(flt, sort, 100, None)
        u.facts.update_review(first.data[5].fact.id, review_status=ReviewStatus.accepted,
                              corrected_value=Decimal("999"))
        rest = []
        cursor = first.next_cursor
        while cursor:
            page = u.facts.query(flt, sort, 100, cursor)
            rest += [r.fact.id for r in page.data]
            cursor = page.next_cursor
    ids = [r.fact.id for r in first.data] + rest
    assert len(ids) == len(set(ids)) == 1000


def test_query_sort_asc_desc(db, ws_id, thousand):
    with db.uow() as u:
        asc = u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value", dir="asc")], 50, None)
        desc = u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value", dir="desc")], 50, None)
    a = [r.fact.normalized_value for r in asc.data]
    d = [r.fact.normalized_value for r in desc.data]
    assert a == sorted(a) and d == sorted(d, reverse=True) and a[0] < d[0]


def test_query_multi_field_sort(db, ws_id, thousand):
    sort = [SortKey(field="metric_name", dir="asc"), SortKey(field="normalized_value", dir="desc"),
            SortKey(field="paper_year", dir="asc")]
    with db.uow() as u:
        rows = []
        cursor = None
        while True:
            page = u.facts.query(FactFilter(workspace_id=ws_id), sort, 100, cursor)
            rows += page.data
            cursor = page.next_cursor
            if not cursor:
                break
    assert len(rows) == 1000 and len({r.fact.id for r in rows}) == 1000
    keys = [(r.fact.metric_name, -r.fact.normalized_value, r.paper_year) for r in rows]
    assert keys == sorted(keys)


@pytest.mark.parametrize("field", ["normalized_value", "confidence", "source_page", "metric_name", "paper_year",
                                   "paper_title", "review_status"])
def test_query_sort_by_each_field_is_complete(db, ws_id, thousand, field):
    with db.uow() as u:
        got = _walk(u, FactFilter(workspace_id=ws_id), [SortKey(field=field, dir="desc")], 150)
    assert len(got) == len(set(got)) == 1000


def test_query_nulls_sort_deterministically(db, ws_id, new_paper, process, make_bundle, make_drafts):
    p = new_paper()
    b = make_bundle("D001", n=10)
    drafts = make_drafts(b)
    for d in drafts[::2]:
        d.confidence = None
    with db.uow() as u:
        u.papers.save_processed(p.id, b, drafts, [], [], stats=PaperStats(), model_version="m", versions={})
    with db.uow() as u:
        got = _walk(u, FactFilter(workspace_id=ws_id), [SortKey(field="confidence")], 3)
    assert len(got) == len(set(got)) == 10


def test_query_default_sort_when_empty(db, ws_id, thousand):
    with db.uow() as u:
        got = _walk(u, FactFilter(workspace_id=ws_id), [], 200)
    assert len(got) == len(set(got)) == 1000


def test_query_unknown_sort_field_rejected(db, ws_id, thousand):
    with pytest.raises(BAD):
        with db.uow() as u:
            u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="id; DROP TABLE facts")], 10, None)


@pytest.mark.parametrize("limit", [0, -1])
def test_query_bad_limit(db, ws_id, thousand, limit):
    with pytest.raises(BAD):
        with db.uow() as u:
            u.facts.query(FactFilter(workspace_id=ws_id), [], limit, None)


def test_query_limit_capped(db, ws_id, thousand):
    with db.uow() as u:
        page = u.facts.query(FactFilter(workspace_id=ws_id), [], 100000, None)
    assert len(page.data) <= 1000


def test_query_invalid_cursor(db, ws_id, thousand):
    with pytest.raises(BAD):
        with db.uow() as u:
            u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")], 10, "garbage!!")


def test_query_cursor_from_other_sort_rejected(db, ws_id, thousand):
    with db.uow() as u:
        page = u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")], 10, None)
        with pytest.raises(BAD):
            u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="metric_name"), SortKey(field="confidence")],
                          10, page.next_cursor)


def test_query_last_page_has_no_cursor(db, ws_id, thousand):
    with db.uow() as u:
        page = u.facts.query(FactFilter(workspace_id=ws_id), [], 1000, None)
        assert len(page.data) == 1000 and page.next_cursor is None
        page = u.facts.query(FactFilter(workspace_id=ws_id), [], 999, None)
        assert page.next_cursor is not None
        rest = u.facts.query(FactFilter(workspace_id=ws_id), [], 999, page.next_cursor)
        assert len(rest.data) == 1 and rest.next_cursor is None


def test_query_empty_result(db, ws_id):
    with db.uow() as u:
        page = u.facts.query(FactFilter(workspace_id=ws_id), [], 10, None)
    assert page.data == [] and page.next_cursor is None and page.total == 0


# ---- фильтры ---------------------------------------------------------------------------------

def _q(db, **kw):
    with db.uow() as u:
        return _walk(u, FactFilter(**kw), [SortKey(field="normalized_value")], 500)


def test_filter_workspace_isolation(db, ws_id, thousand, new_paper, process):
    with db.uow() as u:
        other = u.papers.create(workspace_id="ws_other", publication_id="Z1", title="Z")
    from types import SimpleNamespace
    process(other, n=3)
    assert len(_q(db, workspace_id=ws_id)) == 1000
    assert len(_q(db, workspace_id="ws_other")) == 3


def test_filter_corpus_and_papers(db, ws_id, corpus, thousand):
    assert len(_q(db, corpus_id=corpus.id)) == 1000
    pid = next(iter(thousand))
    assert set(_q(db, workspace_id=ws_id, paper_ids=[pid])) == thousand[pid]
    assert _q(db, workspace_id=ws_id, paper_ids=[]) == []  # пустой список - ничего


def test_filter_metric_names_and_params(db, ws_id, thousand):
    got = _q(db, workspace_id=ws_id, metric_names=["PCE"])
    assert len(got) == 4 * 63  # 250 результатов, PCE на i % 4 == 0 -> 63
    assert len(_q(db, workspace_id=ws_id, metric_names=["PCE", "Voc"])) == 4 * (63 + 63)
    assert len(_q(db, workspace_id=ws_id, params=["metric_value"])) == 1000
    assert _q(db, workspace_id=ws_id, params=["nonexistent"]) == []


def test_filter_value_range(db, ws_id, thousand):
    with db.uow() as u:
        rows = u.facts.query(FactFilter(workspace_id=ws_id, value_min=Decimal("1"), value_max=Decimal("2")),
                             [], 1000, None).data
    assert rows and all(Decimal("1") <= r.fact.normalized_value <= Decimal("2") for r in rows)


def test_filter_value_uses_corrected_value(db, ws_id, thousand):
    with db.uow() as u:
        fid = u.facts.query(FactFilter(workspace_id=ws_id), [SortKey(field="normalized_value")], 1, None).data[0].fact.id
        u.facts.update_review(fid, review_status=ReviewStatus.accepted, corrected_value=Decimal("500"))
    assert _q(db, workspace_id=ws_id, value_min=Decimal("400")) == [fid]


def test_filter_confidence_range(db, ws_id, thousand):
    with db.uow() as u:
        rows = u.facts.query(FactFilter(workspace_id=ws_id, min_confidence=0.8, max_confidence=0.9), [], 1000,
                             None).data
    assert rows and all(0.8 <= r.fact.confidence <= 0.9 for r in rows)


def test_filter_review_status(db, ws_id, thousand):
    with db.uow() as u:
        ids = [r.fact.id for r in u.facts.query(FactFilter(workspace_id=ws_id), [], 5, None).data]
        u.facts.update_review(ids[0], review_status=ReviewStatus.accepted)
        u.facts.update_review(ids[1], review_status=ReviewStatus.rejected)
    assert _q(db, workspace_id=ws_id, review_status=[ReviewStatus.accepted]) == [ids[0]]
    assert set(_q(db, workspace_id=ws_id, review_status=[ReviewStatus.accepted, ReviewStatus.rejected])) == set(ids[:2])
    assert len(_q(db, workspace_id=ws_id, review_status=[ReviewStatus.new])) == 998


def test_filter_text_search(db, ws_id, thousand):
    got = _q(db, workspace_id=ws_id, text="row 7")
    assert got  # ищет по source_fragment / row_label / paper_title (без учёта регистра)
    assert set(_q(db, workspace_id=ws_id, text="ROW 7")) == set(got)
    assert _q(db, workspace_id=ws_id, text="zzz-no-such") == []


def test_filter_text_escapes_like_wildcards(db, ws_id, thousand):
    assert _q(db, workspace_id=ws_id, text="%") == []
    assert _q(db, workspace_id=ws_id, text="_") == []


def test_filter_has_open_issues(db, ws_id, new_paper, process, make_issue):
    p1, p2 = new_paper("D001"), new_paper("D002")
    process(p1, n=3, issues=[make_issue("D001", "I-1", records=("R002",))])
    process(p2, n=3)
    with db.uow() as u:
        rows = u.facts.query(FactFilter(workspace_id=ws_id, has_open_issues=True), [], 100, None).data
    assert len(rows) == 1 and rows[0].fact.target["id"] == "R002" and rows[0].open_issues == 1
    assert len(_q(db, workspace_id=ws_id, has_open_issues=False)) == 5


def test_filter_include_duplicates(db, ws_id, new_paper, process):
    a, b = new_paper("D001"), new_paper("D002")
    process(a, n=2)
    process(b, n=2)
    from faktura.domain.entities import DupKind
    with db.uow() as u:
        u.papers.set_duplicate(b.id, a.id, DupKind.doi)
    assert len(_q(db, workspace_id=ws_id, include_duplicates=True)) == 4
    assert len(_q(db, workspace_id=ws_id, include_duplicates=False)) == 2


def test_filter_excludes_soft_deleted_papers(db, ws_id, new_paper, process):
    """Допущение: papers.soft_delete(id)."""
    a = new_paper("D001")
    process(a, n=2)
    with db.uow() as u:
        u.papers.soft_delete(a.id)
    assert _q(db, workspace_id=ws_id) == []


def test_filter_combined(db, ws_id, thousand):
    got = _q(db, workspace_id=ws_id, metric_names=["PCE"], value_min=Decimal("1"), review_status=[ReviewStatus.new])
    assert got


# ---- facets ----------------------------------------------------------------------------------

def test_facets_counts(db, ws_id, thousand):
    with db.uow() as u:
        f = u.facts.facets(FactFilter(workspace_id=ws_id), ["metric_name", "review_status", "source_page"])
    assert f["metric_name"] == {"PCE": 252, "Voc": 252, "Jsc": 248, "FF": 248}
    assert f["review_status"] == {"new": 1000}
    assert sum(f["source_page"].values()) == 1000


def test_facets_respect_filter(db, ws_id, thousand):
    with db.uow() as u:
        f = u.facts.facets(FactFilter(workspace_id=ws_id, metric_names=["PCE"]), ["metric_name", "review_status"])
    assert f["metric_name"] == {"PCE": 252}
    assert sum(f["review_status"].values()) == sum(f["metric_name"].values())


def test_facets_reflect_updates(db, ws_id, thousand):
    with db.uow() as u:
        fid = u.facts.query(FactFilter(workspace_id=ws_id), [], 1, None).data[0].fact.id
        u.facts.update_review(fid, review_status=ReviewStatus.accepted)
        f = u.facts.facets(FactFilter(workspace_id=ws_id), ["review_status"])
    assert f["review_status"] == {"new": 999, "accepted": 1}


def test_facets_empty_and_unknown_field(db, ws_id):
    with db.uow() as u:
        assert u.facts.facets(FactFilter(workspace_id=ws_id), ["metric_name"]) == {"metric_name": {}}
        with pytest.raises(BAD):
            u.facts.facets(FactFilter(workspace_id=ws_id), ["no_such_field"])
        assert u.facts.facets(FactFilter(workspace_id=ws_id), []) == {}


def test_facets_null_values_bucket(db, ws_id, new_paper, make_bundle, make_drafts):
    p = new_paper()
    b = make_bundle("D001", n=4)
    drafts = make_drafts(b)
    drafts[0].metric_name = None
    with db.uow() as u:
        u.papers.save_processed(p.id, b, drafts, [], [], stats=PaperStats(), model_version="m", versions={})
        f = u.facts.facets(FactFilter(workspace_id=ws_id), ["metric_name"])
    assert sum(f["metric_name"].values()) == 4  # пустые не теряются (ключ - "" или None-метка)


# ---- update_review ---------------------------------------------------------------------------

def _one(db, ws_id):
    with db.uow() as u:
        return u.facts.query(FactFilter(workspace_id=ws_id), [], 1, None).data[0].fact


def test_update_review_accept_sets_reviewer(db, ws_id, new_paper, process):
    process(new_paper(), n=3)
    f = _one(db, ws_id)
    with db.uow() as u:
        g = u.facts.update_review(f.id, review_status=ReviewStatus.accepted, reviewed_by="usr_1")
    assert g.id == f.id and g.review_status == ReviewStatus.accepted and g.reviewed_by == "usr_1"
    assert g.reviewed_at is not None


def test_update_review_corrected_value_wins(db, ws_id, new_paper, process):
    process(new_paper(), n=3)
    f = _one(db, ws_id)
    with db.uow() as u:
        g = u.facts.update_review(f.id, review_status=ReviewStatus.accepted, corrected_value=Decimal("17.5"),
                                  corrected_raw="17,5")
    assert g.corrected_value == Decimal("17.5") and g.corrected_raw == "17,5"
    assert g.value == Decimal("17.5") and g.normalized_value == f.normalized_value


def test_update_review_restore_clears_correction(db, ws_id, new_paper, process):
    process(new_paper(), n=3)
    f = _one(db, ws_id)
    with db.uow() as u:
        u.facts.update_review(f.id, review_status=ReviewStatus.accepted, corrected_value=Decimal("1"))
        g = u.facts.update_review(f.id, review_status=ReviewStatus.new, corrected_value=None, corrected_raw=None)
    assert g.review_status == ReviewStatus.new and g.corrected_value is None and g.value == f.normalized_value


def test_update_review_persisted(db, ws_id, new_paper, process):
    process(new_paper(), n=3)
    f = _one(db, ws_id)
    with db.uow() as u:
        u.facts.update_review(f.id, review_status=ReviewStatus.rejected)
    with db.uow() as u:
        rows = u.facts.query(FactFilter(workspace_id=ws_id, review_status=[ReviewStatus.rejected]), [], 10, None).data
    assert [r.fact.id for r in rows] == [f.id]


def test_update_review_unknown_fact(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.facts.update_review("fct_nope", review_status=ReviewStatus.accepted)


def test_update_review_unknown_field(db, ws_id, new_paper, process):
    process(new_paper(), n=1)
    f = _one(db, ws_id)
    with pytest.raises((TypeError, ValueError, AppError)):
        with db.uow() as u:
            u.facts.update_review(f.id, bogus=1)


def test_update_review_cannot_change_extracted_fields(db, ws_id, new_paper, process):
    """raw_value, normalized_value, source_* - не ревьюируемые поля: правка только через corrected_*."""
    process(new_paper(), n=1)
    f = _one(db, ws_id)
    with pytest.raises((TypeError, ValueError, AppError)):
        with db.uow() as u:
            u.facts.update_review(f.id, normalized_value=Decimal("1"))


def test_update_review_invalid_status(db, ws_id, new_paper, process):
    process(new_paper(), n=1)
    f = _one(db, ws_id)
    with pytest.raises((ValueError, AppError)):
        with db.uow() as u:
            u.facts.update_review(f.id, review_status="maybe")


def test_update_review_rolls_back_with_uow(db, ws_id, new_paper, process):
    process(new_paper(), n=1)
    f = _one(db, ws_id)
    with pytest.raises(RuntimeError):
        with db.uow() as u:
            u.facts.update_review(f.id, review_status=ReviewStatus.accepted)
            raise RuntimeError
    assert _one(db, ws_id).review_status == ReviewStatus.new


def test_update_review_visible_in_load_bundle(db, ws_id, new_paper, process):
    p = new_paper()
    res = process(p, n=3).result
    with db.uow() as u:
        u.facts.update_review(res.fact_ids["R002"], review_status=ReviewStatus.accepted,
                              corrected_value=Decimal("12.34"), corrected_raw="12,34")
        u.facts.update_review(res.fact_ids["R003"], review_status=ReviewStatus.rejected)
        applied = u.papers.load_bundle(p.id, apply_review=True)
        plain = u.papers.load_bundle(p.id, apply_review=False)
    r2, r3 = applied.results[1].meta, applied.results[2].meta
    assert r2.review_status == ReviewStatus.accepted and r2.corrected_value == Decimal("12.34")
    assert r2.corrected_raw == "12,34" and r3.review_status == ReviewStatus.rejected
    assert applied.results[0].meta.review_status == ReviewStatus.new
    assert all(r.meta.review_status == ReviewStatus.new and r.meta.corrected_value is None for r in plain.results)


# ---- add_manual ------------------------------------------------------------------------------

def _draft(**kw):
    base = dict(ref="MAN1", target={"entity": "results", "id": "R900", "field": "metric_value"},
                param="metric_value", metric_name="PCE", raw_value="18,2", raw_unit="%",
                normalized_value=Decimal("18.2"), normalized_unit="%", norm_status="ok", source_page=3,
                source_fragment="PCE of 18.2%", source_kind="manual")
    base.update(kw)
    return FactDraft(**base)


def test_add_manual(db, ws_id, new_paper, process):
    p = new_paper()
    process(p, n=2)
    with db.uow() as u:
        f = u.facts.add_manual(p.id, _draft())
    assert f.id and f.paper_id == p.id and f.publication_id == "D001" and f.added_by.value == "manual"
    assert f.normalized_value == Decimal("18.2") and f.source_page == 3 and f.target["id"] == "R900"
    with db.uow() as u:
        assert [x.id for x in u.facts.manual_for_paper(p.id)] == [f.id]
        assert len(u.facts.query(FactFilter(workspace_id=ws_id), [], 10, None).data) == 3


def test_add_manual_forces_manual_origin(db, new_paper, process):
    p = new_paper()
    process(p, n=1)
    with db.uow() as u:
        f = u.facts.add_manual(p.id, _draft(added_by="auto"))
    assert f.added_by.value == "manual"


def test_add_manual_unknown_paper(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.facts.add_manual("pap_nope", _draft())
