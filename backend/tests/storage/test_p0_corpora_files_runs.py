"""Этап A: corpora, files, runs."""
from __future__ import annotations

import pytest

from faktura.domain.entities import CorpusFileStatus, RunCounts, RunKind, RunStatus
from faktura.domain.errors import AppError

from .conftest import ensure_ws

NOT_FOUND = (AppError, KeyError, LookupError)


# ---- corpora ---------------------------------------------------------------------------------

def test_corpus_create_get(db, ws_id):
    with db.uow() as u:
        ensure_ws(u, ws_id)
        c = u.corpora.create(workspace_id=ws_id, name="Солнечные", collection="solar", kind="regular",
                             created_by="usr_1")
    assert c.id and c.workspace_id == ws_id and c.name == "Солнечные"
    assert c.collection == "solar" and c.kind == "regular" and c.deleted_at is None and c.created_at is not None
    with db.uow() as u:
        g = u.corpora.get(c.id)
    assert g == c


def test_corpus_defaults(db, ws_id):
    with db.uow() as u:
        c = u.corpora.create(workspace_id=ws_id, name="K")
    assert c.collection == "solar" and c.kind == "regular"


def test_corpus_get_unknown_is_none(db):
    with db.uow() as u:
        assert u.corpora.get("crp_nope") is None


def test_corpus_ids_unique(db, ws_id):
    with db.uow() as u:
        ids = {u.corpora.create(workspace_id=ws_id, name=f"K{i}").id for i in range(20)}
    assert len(ids) == 20


def test_corpus_list_by_workspace(db, ws_id):
    with db.uow() as u:
        a = u.corpora.create(workspace_id=ws_id, name="A")
        b = u.corpora.create(workspace_id=ws_id, name="B")
        other = u.corpora.create(workspace_id="ws_other", name="X")
    with db.uow() as u:
        ids = {c.id for c in u.corpora.list(ws_id)}
        assert ids == {a.id, b.id}
        assert [c.id for c in u.corpora.list("ws_other")] == [other.id]
        assert u.corpora.list("ws_empty") == []


def test_corpus_list_hides_deleted_unless_asked(db, ws_id):
    """Допущение: corpora.soft_delete(id) помечает deleted_at."""
    with db.uow() as u:
        a = u.corpora.create(workspace_id=ws_id, name="A")
        b = u.corpora.create(workspace_id=ws_id, name="B")
        u.corpora.soft_delete(b.id)
    with db.uow() as u:
        assert [c.id for c in u.corpora.list(ws_id)] == [a.id]
        assert {c.id for c in u.corpora.list(ws_id, include_deleted=True)} == {a.id, b.id}


def test_corpus_kind_manual_and_extract(db, ws_id):
    with db.uow() as u:
        m = u.corpora.create(workspace_id=ws_id, name="Загруженные вручную", kind="manual")
        e = u.corpora.create(workspace_id=ws_id, name="Извлечение", kind="extract")
    assert (m.kind, e.kind) == ("manual", "extract")


def test_corpus_invalid_kind_rejected(db, ws_id):
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.corpora.create(workspace_id=ws_id, name="K", kind="bogus")
    assert ei.type is not NotImplementedError


def test_corpus_requires_workspace_and_name(db):
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.corpora.create(name="K")
    assert ei.type is not NotImplementedError


# ---- files -----------------------------------------------------------------------------------

def _add(u, corpus_id, name="a.pdf", pid="D001", sha="a" * 64, **kw):
    return u.files.add(corpus_id=corpus_id, name=name, publication_id=pid, sha256=sha, size_bytes=1234,
                       page_count=7, **kw)


def test_file_add_get(db, corpus):
    with db.uow() as u:
        f = _add(u, corpus.id)
    assert f.id and f.corpus_id == corpus.id and f.name == "a.pdf" and f.publication_id == "D001"
    assert f.status == CorpusFileStatus.pending and f.size_bytes == 1234 and f.page_count == 7
    with db.uow() as u:
        assert u.files.get(f.id) == f


def test_file_get_unknown_none(db):
    with db.uow() as u:
        assert u.files.get("fil_nope") is None


def test_file_list_and_status_filter(db, corpus):
    with db.uow() as u:
        f1 = _add(u, corpus.id, "a.pdf", "D001", "1" * 64)
        f2 = _add(u, corpus.id, "b.pdf", "D002", "2" * 64)
        u.files.update_status(f2.id, CorpusFileStatus.done)
    with db.uow() as u:
        assert {f.id for f in u.files.list(corpus.id)} == {f1.id, f2.id}
        assert [f.id for f in u.files.list(corpus.id, status=CorpusFileStatus.done)] == [f2.id]
        assert u.files.list(corpus.id, status=CorpusFileStatus.failed) == []
        assert u.files.list("crp_other") == []


def test_file_list_stable_order(db, corpus):
    with db.uow() as u:
        ids = [_add(u, corpus.id, f"{i:02d}.pdf", f"D{i:03d}", f"{i:064d}").id for i in range(15)]
    with db.uow() as u:
        got = [f.id for f in u.files.list(corpus.id)]
    assert got == ids  # порядок добавления (или иной детерминированный, но один и тот же)
    with db.uow() as u:
        assert [f.id for f in u.files.list(corpus.id)] == got


def test_file_update_status_with_fields(db, corpus):
    with db.uow() as u:
        f = _add(u, corpus.id)
        g = u.files.update_status(f.id, CorpusFileStatus.failed, error_code="pdf_broken",
                                  error_message="повреждён")
    assert g.status == CorpusFileStatus.failed and g.error_code == "pdf_broken" and g.error_message == "повреждён"
    with db.uow() as u:
        assert u.files.get(f.id).status == CorpusFileStatus.failed


def test_file_update_status_links_paper(db, corpus, new_paper):
    p = new_paper("D001")
    with db.uow() as u:
        f = _add(u, corpus.id)
        g = u.files.update_status(f.id, CorpusFileStatus.done, paper_id=p.id)
    assert g.paper_id == p.id


def test_file_update_status_unknown_raises(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.files.update_status("fil_nope", CorpusFileStatus.done)


@pytest.mark.parametrize("status", list(CorpusFileStatus))
def test_file_every_status_roundtrips(db, corpus, status):
    with db.uow() as u:
        f = _add(u, corpus.id)
        assert u.files.update_status(f.id, status).status == status


def test_file_duplicate_marker(db, corpus):
    with db.uow() as u:
        a = _add(u, corpus.id, "a.pdf", "D001", "1" * 64)
        b = _add(u, corpus.id, "b.pdf", "D002", "1" * 64)
        g = u.files.update_status(b.id, CorpusFileStatus.skipped_duplicate, duplicate_of=a.id, dup_kind="file")
    assert g.duplicate_of == a.id and g.dup_kind == "file"


def test_file_find_by_sha_in_workspace(db, ws_id, corpus):
    sha = "f" * 64
    with db.uow() as u:
        other = u.corpora.create(workspace_id="ws_other", name="X")
        c2 = u.corpora.create(workspace_id=ws_id, name="K2")
        a = _add(u, corpus.id, "a.pdf", "D001", sha)
        b = _add(u, c2.id, "a_copy.pdf", "D009", sha)
        _add(u, other.id, "a.pdf", "D001", sha)  # другое пространство - не находится
        _add(u, corpus.id, "z.pdf", "D002", "0" * 64)
    with db.uow() as u:
        assert {f.id for f in u.files.find_by_sha(ws_id, sha)} == {a.id, b.id}
        assert u.files.find_by_sha(ws_id, "e" * 64) == []
        assert len(u.files.find_by_sha("ws_other", sha)) == 1


# ---- runs ------------------------------------------------------------------------------------

def test_run_create_get_defaults(db, corpus):
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id, kind=RunKind.full, started_by="usr_1")
    assert r.id and r.corpus_id == corpus.id and r.status == RunStatus.queued and r.kind == RunKind.full
    assert r.counts == RunCounts() and r.paused is False and r.output is None
    with db.uow() as u:
        assert u.runs.get(r.id) == r


def test_run_get_unknown_none(db):
    with db.uow() as u:
        assert u.runs.get("run_nope") is None


def test_run_file_ids_roundtrip(db, corpus):
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id, kind=RunKind.selected, file_ids=["f1", "f2"])
    with db.uow() as u:
        assert u.runs.get(r.id).file_ids == ["f1", "f2"]


def test_run_update_counts_status(db, corpus):
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id)
        g = u.runs.update(r.id, status=RunStatus.running, counts=RunCounts(total=10, done=3, failed=1))
    assert g.status == RunStatus.running and g.counts.total == 10 and g.counts.done == 3
    with db.uow() as u:
        assert u.runs.get(r.id).counts == RunCounts(total=10, done=3, failed=1)


def test_run_update_pause_flags(db, corpus):
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id)
        g = u.runs.update(r.id, paused=True, pause_reason="ml_unavailable")
    assert g.paused is True and g.pause_reason == "ml_unavailable"


def test_run_update_output(db, corpus):
    from faktura.domain.entities import RunOutput
    out = RunOutput(available=True, csv_files=["publications.csv"], check_status="ok")
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id)
        u.runs.update(r.id, status=RunStatus.done, output=out)
    with db.uow() as u:
        assert u.runs.get(r.id).output == out


def test_run_update_unknown_raises(db):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.runs.update("run_nope", status=RunStatus.done)


def test_run_update_unknown_field_raises(db, corpus):
    with pytest.raises((TypeError, ValueError, AppError)):
        with db.uow() as u:
            r = u.runs.create(corpus_id=corpus.id)
            u.runs.update(r.id, bogus=1)


@pytest.mark.parametrize("active", [RunStatus.queued, RunStatus.running, RunStatus.cancelling])
def test_run_active_for_corpus_active_statuses(db, corpus, active):
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id)
        u.runs.update(r.id, status=active)
    with db.uow() as u:
        assert u.runs.active_for_corpus(corpus.id).id == r.id


@pytest.mark.parametrize("final", [RunStatus.done, RunStatus.done_with_errors, RunStatus.cancelled, RunStatus.failed])
def test_run_active_for_corpus_none_when_finished(db, corpus, final):
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id)
        u.runs.update(r.id, status=final)
    with db.uow() as u:
        assert u.runs.active_for_corpus(corpus.id) is None


def test_run_active_for_corpus_ignores_other_corpus_and_old_runs(db, ws_id, corpus):
    with db.uow() as u:
        c2 = u.corpora.create(workspace_id=ws_id, name="K2")
        old = u.runs.create(corpus_id=corpus.id)
        u.runs.update(old.id, status=RunStatus.done)
        other = u.runs.create(corpus_id=c2.id)
        u.runs.update(other.id, status=RunStatus.running)
        cur = u.runs.create(corpus_id=corpus.id)
        u.runs.update(cur.id, status=RunStatus.running)
    with db.uow() as u:
        assert u.runs.active_for_corpus(corpus.id).id == cur.id
        assert u.runs.active_for_corpus(c2.id).id == other.id
        assert u.runs.active_for_corpus("crp_none") is None
