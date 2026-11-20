"""Этап B: stats.update_paper / overview / corpus_summary (кеш corpus_stats)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import CorpusFileStatus, FactFilter, PaperStats, ReviewStatus
from faktura.domain.errors import AppError

NOT_FOUND = (AppError, KeyError, LookupError)


def _corpus_row(ov: dict, corpus_id: str) -> dict:
    return next(c for c in ov["corpora"] if c["corpus_id"] == corpus_id)


def test_overview_empty(db, ws_id):
    with db.uow() as u:
        ov = u.stats.overview(ws_id)
    assert ov["empty"] is True and ov["corpora"] == []
    assert ov["totals"]["papers"] == 0 and ov["totals"]["facts"] == 0 and ov["totals"]["open_issues"] == 0


def test_overview_corpus_without_papers(db, ws_id, corpus):
    with db.uow() as u:
        ov = u.stats.overview(ws_id)
    assert ov["empty"] is False
    row = _corpus_row(ov, corpus.id)
    assert row["name"] == corpus.name and row["collection"] == "solar" and row["facts"] == 0
    assert row["files"]["total"] == 0 and row["active_run"] is None and row["last_run"] is None


def test_overview_after_save_processed(db, ws_id, corpus, new_paper, process, make_issue):
    p1, p2 = new_paper("D001"), new_paper("D002")
    process(p1, n=5, issues=[make_issue("D001", "I-a"), make_issue("D001", "I-b", records=("R002",))],
            stats=PaperStats(facts_count=5, open_issues=2))
    process(p2, n=3)
    with db.uow() as u:
        ov = u.stats.overview(ws_id)
    row = _corpus_row(ov, corpus.id)
    assert row["facts"] == 8 and row["open_issues"] == 2
    assert ov["totals"]["papers"] == 2 and ov["totals"]["facts"] == 8 and ov["totals"]["open_issues"] == 2


def test_overview_file_counters(db, ws_id, corpus):
    with db.uow() as u:
        ids = [u.files.add(corpus_id=corpus.id, name=f"{i}.pdf", publication_id=f"D{i:03d}",
                           sha256=f"{i:064d}").id for i in range(6)]
        for i in ids[:3]:
            u.files.update_status(i, CorpusFileStatus.done)
        u.files.update_status(ids[3], CorpusFileStatus.failed, error_code="pdf_broken")
        u.files.update_status(ids[4], CorpusFileStatus.processing)
        ov = u.stats.overview(ws_id)
    assert _corpus_row(ov, corpus.id)["files"] == {"total": 6, "done": 3, "failed": 1, "processing": 1}


def test_overview_active_and_last_run(db, ws_id, corpus):
    from faktura.domain.entities import RunCounts, RunStatus
    with db.uow() as u:
        done = u.runs.create(corpus_id=corpus.id)
        u.runs.update(done.id, status=RunStatus.done_with_errors)
        act = u.runs.create(corpus_id=corpus.id)
        u.runs.update(act.id, status=RunStatus.running, counts=RunCounts(total=10, done=4))
        ov = u.stats.overview(ws_id)
    row = _corpus_row(ov, corpus.id)
    assert row["active_run"]["run_id"] == act.id and row["active_run"]["progress"] == 40
    assert row["last_run"]["run_id"] == done.id and row["last_run"]["status"] == "done_with_errors"


def test_overview_collection_filter(db, ws_id, corpus):
    with db.uow() as u:
        rag = u.corpora.create(workspace_id=ws_id, name="RAG", collection="rag")
        ov_all = u.stats.overview(ws_id)
        ov_solar = u.stats.overview(ws_id, collection="solar")
        ov_rag = u.stats.overview(ws_id, collection="rag")
        ov_none = u.stats.overview(ws_id, collection="zzz")
    assert {c["corpus_id"] for c in ov_all["corpora"]} == {corpus.id, rag.id}
    assert [c["corpus_id"] for c in ov_solar["corpora"]] == [corpus.id]
    assert [c["corpus_id"] for c in ov_rag["corpora"]] == [rag.id]
    assert ov_none["empty"] is True


def test_overview_workspace_isolation(db, ws_id, corpus, process, new_paper):
    process(new_paper("D001"), n=3)
    with db.uow() as u:
        u.corpora.create(workspace_id="ws_other", name="X")
        ov = u.stats.overview("ws_other")
    assert [c["name"] for c in ov["corpora"]] == ["X"] and ov["totals"]["facts"] == 0


def test_overview_matches_recount_after_review_changes(db, ws_id, corpus, new_paper, process, make_issue):
    """Кеш corpus_stats == пересчёт с нуля (corpus_summary читает papers/facts/issues напрямую)."""
    p1, p2 = new_paper("D001"), new_paper("D002")
    r1 = process(p1, n=10, issues=[make_issue("D001", "I-a")]).result
    process(p2, n=7)
    with db.uow() as u:
        u.facts.update_review(r1.fact_ids["R001"], review_status=ReviewStatus.accepted)
        u.facts.update_review(r1.fact_ids["R002"], review_status=ReviewStatus.rejected)
        u.facts.update_review(r1.fact_ids["R003"], review_status=ReviewStatus.accepted, corrected_value=Decimal("1"))
        u.issues.upsert_preserving_review(p2.id, [make_issue("D002", "I-z", records=("R001",))])
    with db.uow() as u:
        ov = u.stats.overview(ws_id)
        s = u.stats.corpus_summary(corpus.id)
    row = _corpus_row(ov, corpus.id)
    assert row["facts"] == s["facts"]["total"] == 17
    assert row["open_issues"] == s["issues"]["open"] == 2
    assert ov["totals"]["facts"] == s["facts"]["total"]
    assert (s["facts"]["accepted"], s["facts"]["rejected"], s["facts"]["new"]) == (2, 1, 14)


def test_overview_after_add_manual(db, ws_id, corpus, new_paper, process):
    from faktura.domain.entities import FactDraft
    p = new_paper()
    process(p, n=2)
    with db.uow() as u:
        u.facts.add_manual(p.id, FactDraft(ref="m", target={"entity": "results", "id": "R9", "field": "metric_value"},
                                           param="metric_value", metric_name="PCE", source_page=1))
        assert _corpus_row(u.stats.overview(ws_id), corpus.id)["facts"] == 3
        assert u.stats.corpus_summary(corpus.id)["facts"]["manual"] == 1


def test_overview_after_resave_replaces_counts(db, ws_id, corpus, new_paper, process):
    p = new_paper()
    process(p, n=9)
    process(p, n=2)
    with db.uow() as u:
        assert _corpus_row(u.stats.overview(ws_id), corpus.id)["facts"] == 2


def test_overview_excludes_duplicates_consistently(db, ws_id, corpus, new_paper, process):
    """Кеш и пересчёт согласованы и для статей-дублей."""
    from faktura.domain.entities import DupKind
    a, b = new_paper("D001"), new_paper("D002")
    process(a, n=3)
    process(b, n=3)
    with db.uow() as u:
        u.papers.set_duplicate(b.id, a.id, DupKind.doi)
        ov = u.stats.overview(ws_id)
        s = u.stats.corpus_summary(corpus.id)
    assert _corpus_row(ov, corpus.id)["facts"] == s["facts"]["total"]
    assert s["papers"]["possible_duplicates"] == 1


def test_overview_cache_rolls_back_with_transaction(db, ws_id, corpus, new_paper, process):
    p = new_paper()
    process(p, n=3)
    with pytest.raises(RuntimeError):
        with db.uow() as u:
            u.stats.update_paper(p.id, PaperStats(facts_count=99, open_issues=9))
            raise RuntimeError
    with db.uow() as u:
        assert _corpus_row(u.stats.overview(ws_id), corpus.id)["facts"] == 3


def test_overview_hides_soft_deleted_corpus(db, ws_id, corpus):
    with db.uow() as u:
        u.corpora.soft_delete(corpus.id)
        assert u.stats.overview(ws_id)["empty"] is True


# ---- update_paper ----------------------------------------------------------------------------

def test_update_paper_persists_stats(db, new_paper, process):
    p = new_paper()
    process(p, n=3)
    st = PaperStats(facts_count=3, open_issues=4, top_level="error")
    st.quality.score, st.quality.level = 0.42, "doubt"
    st.coverage.filled, st.coverage.total, st.coverage.ratio, st.coverage.missing = 5, 10, 0.5, ["certified"]
    with db.uow() as u:
        u.stats.update_paper(p.id, st)
    with db.uow() as u:
        g = u.papers.get(p.id).stats
    assert g == st


def test_update_paper_unknown(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.stats.update_paper("pap_nope", PaperStats())


# ---- corpus_summary --------------------------------------------------------------------------

def test_corpus_summary_shape(db, corpus, new_paper, process):
    process(new_paper("D001"), n=4)
    with db.uow() as u:
        s = u.stats.corpus_summary(corpus.id)
    assert s["corpus_id"] == corpus.id and s["collection"] == "solar"
    for k in ("files", "papers", "facts", "issues", "coverage", "not_found", "errors"):
        assert k in s, k
    assert s["papers"]["total"] == 1 and s["facts"]["total"] == 4
    assert s["facts"]["from_table"] == 4 and s["facts"]["from_text"] == 0
    assert 0.5 <= s["facts"]["mean_confidence"] <= 1.0


def test_corpus_summary_files_and_errors(db, corpus):
    with db.uow() as u:
        a = u.files.add(corpus_id=corpus.id, name="a.pdf", publication_id="D001", sha256="1" * 64)
        b = u.files.add(corpus_id=corpus.id, name="b.pdf", publication_id="D002", sha256="2" * 64)
        c = u.files.add(corpus_id=corpus.id, name="c.pdf", publication_id="D003", sha256="2" * 64)
        u.files.update_status(a.id, CorpusFileStatus.done)
        u.files.update_status(b.id, CorpusFileStatus.failed, error_code="pdf_encrypted")
        u.files.update_status(c.id, CorpusFileStatus.skipped_duplicate, duplicate_of=b.id, dup_kind="file")
        s = u.stats.corpus_summary(corpus.id)
    assert s["files"]["total"] == 3 and s["files"]["done"] == 1 and s["files"]["failed"] == 1
    assert s["files"]["duplicates"] == 1
    assert {"code": "pdf_encrypted", "count": 1} in s["errors"]


def test_corpus_summary_empty_corpus(db, corpus):
    with db.uow() as u:
        s = u.stats.corpus_summary(corpus.id)
    assert s["files"]["total"] == 0 and s["facts"]["total"] == 0 and s["issues"]["open"] == 0
    assert s["facts"]["mean_confidence"] is None


def test_corpus_summary_unknown(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.stats.corpus_summary("crp_nope")


def test_corpus_summary_issues_by_rule(db, corpus, new_paper, process, make_issue):
    process(new_paper("D001"), n=3, issues=[make_issue("D001", "I-1", check="jv_consistency"),
                                            make_issue("D001", "I-2", check="jv_consistency", records=("R002",)),
                                            make_issue("D001", "I-3", check="outlier", records=("R003",))])
    with db.uow() as u:
        s = u.stats.corpus_summary(corpus.id)
    by = {r["check_name"]: r["open"] for r in s["issues"]["by_rule"]}
    assert by == {"jv_consistency": 2, "outlier": 1} and s["issues"]["open"] == 3
    assert s["papers"]["with_issues"] == 1
