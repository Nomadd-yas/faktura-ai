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


















# ---- files -----------------------------------------------------------------------------------

def _add(u, corpus_id, name="a.pdf", pid="D001", sha="a" * 64, **kw):
    return u.files.add(corpus_id=corpus_id, name=name, publication_id=pid, sha256=sha, size_bytes=1234,
                       page_count=7, **kw)






















# ---- runs ------------------------------------------------------------------------------------

def test_run_create_get_defaults(db, corpus):
    with db.uow() as u:
        r = u.runs.create(corpus_id=corpus.id, kind=RunKind.full, started_by="usr_1")
    assert r.id and r.corpus_id == corpus.id and r.status == RunStatus.queued and r.kind == RunKind.full
    assert r.counts == RunCounts() and r.paused is False and r.output is None
    with db.uow() as u:
        assert u.runs.get(r.id) == r




















