"""RunService: start/execute/submit/cancel/retry_file/start_or_append, виды прогонов, keep_review (план 5.6, P0+P1)."""
from __future__ import annotations

import pytest

from faktura.domain.entities import CorpusFileStatus, FactFilter, ReviewStatus, RunKind, RunStatus
from faktura.domain.errors import AppError
from faktura.pipeline import CorpusService, RunService


@pytest.fixture
def env(make_env):
    return make_env()


@pytest.fixture
def corpus(env, pdf_dir):
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    return c


def statuses(env, corpus_id):
    with env.uow() as uow:
        return {f.name: f for f in uow.files.list(corpus_id)}


def get_run(env, run_id):
    with env.uow() as uow:
        return uow.runs.get(run_id)


# --- start -----------------------------------------------------------------------------------------------------









def test_second_start_conflict(env, corpus):
    rs = RunService(env.services)
    rs.start(corpus.id, "full", user_id=env.user_id)
    with pytest.raises(AppError) as ei:
        rs.start(corpus.id, "full", user_id=env.user_id)
    assert ei.value.code == "conflict" and ei.value.http_status == 409












# --- execute: full ---------------------------------------------------------------------------------------------------

def test_execute_full_done(env, corpus):
    run = env.full_run(corpus.id)
    assert run.status == RunStatus.done and run.finished_at is not None
    assert (run.counts.done, run.counts.failed, run.counts.queued) == (2, 0, 0)
    assert {f.status for f in statuses(env, corpus.id).values()} == {CorpusFileStatus.done}














# --- submit / cancel / retry --------------------------------------------------------------------------------------------



















# --- виды прогонов -------------------------------------------------------------------------------------------------------------------













# --- start_or_append (служебный корпус «Загруженные вручную») ----------------------------------------------------------------------------







# --- keep_review ----------------------------------------------------------------------------------------------------------------------------

def _accept_first_result_fact(env, paper_id):
    with env.uow() as uow:
        rows = uow.facts.query(FactFilter(paper_ids=[paper_id]), [], 1000, None).data
        fact = next(r.fact for r in rows if r.fact.target["entity"] == "results")
        uow.facts.update_review(fact.id, review_status=ReviewStatus.accepted)
    return fact


def _facts(env, paper_id):
    with env.uow() as uow:
        return [r.fact for r in uow.facts.query(FactFilter(paper_ids=[paper_id]), [], 1000, None).data]






