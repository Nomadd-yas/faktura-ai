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

def test_start_creates_queued_run_with_counts(env, corpus):
    run = RunService(env.services).start(corpus.id, "full", user_id=env.user_id)
    assert run.status == RunStatus.queued and run.kind == RunKind.full
    assert run.corpus_id == corpus.id and run.started_by == env.user_id
    assert run.counts.total == 2 and run.counts.queued == 2
    assert run.keep_review is True and run.concurrency == 4 and run.model_version == "latest"
    assert not run.paused


def test_start_accepts_string_and_enum_kind(env, corpus):
    rs = RunService(env.services)
    r = rs.start(corpus.id, RunKind.full, user_id=env.user_id)
    assert r.kind == RunKind.full


def test_start_unknown_kind_validation_error(env, corpus):
    with pytest.raises((AppError, ValueError)) as ei:
        RunService(env.services).start(corpus.id, "bogus", user_id=env.user_id)
    assert getattr(ei.value, "code", "validation_error") == "validation_error"


def test_start_unknown_corpus_not_found(env):
    with pytest.raises(AppError) as ei:
        RunService(env.services).start("crp_nope", "full", user_id=env.user_id)
    assert ei.value.code == "not_found"


def test_second_start_conflict(env, corpus):
    rs = RunService(env.services)
    rs.start(corpus.id, "full", user_id=env.user_id)
    with pytest.raises(AppError) as ei:
        rs.start(corpus.id, "full", user_id=env.user_id)
    assert ei.value.code == "conflict" and ei.value.http_status == 409


def test_start_allowed_after_previous_finished(env, corpus):
    env.full_run(corpus.id)
    run = RunService(env.services).start(corpus.id, "full", user_id=env.user_id)
    assert run.status == RunStatus.queued


def test_runs_in_different_corpora_do_not_conflict(env, pdf_dir, corpus):
    other = env.new_corpus("B")
    env.add_dir(pdf_dir, other.id)
    rs = RunService(env.services)
    rs.start(corpus.id, "full", user_id=env.user_id)
    assert rs.start(other.id, "full", user_id=env.user_id).status == RunStatus.queued


def test_selected_requires_file_ids_subset(env, corpus):
    fid = statuses(env, corpus.id)["D001.pdf"].id
    run = RunService(env.services).start(corpus.id, "selected", file_ids=[fid], user_id=env.user_id)
    assert run.counts.total == 1 and run.file_ids == [fid]


def test_selected_foreign_file_id_rejected(env, corpus):
    with pytest.raises(AppError) as ei:
        RunService(env.services).start(corpus.id, "selected", file_ids=["fil_foreign"], user_id=env.user_id)
    assert ei.value.code in ("validation_error", "not_found")


def test_profile_and_model_stored(env, corpus):
    run = RunService(env.services).start(corpus.id, "full", user_id=env.user_id, model_version="m-7", concurrency=2,
                                         keep_review=False, build_output=True)
    assert (run.model_version, run.concurrency, run.keep_review, run.build_output) == ("m-7", 2, False, True)


# --- execute: full ---------------------------------------------------------------------------------------------------

def test_execute_full_done(env, corpus):
    run = env.full_run(corpus.id)
    assert run.status == RunStatus.done and run.finished_at is not None
    assert (run.counts.done, run.counts.failed, run.counts.queued) == (2, 0, 0)
    assert {f.status for f in statuses(env, corpus.id).values()} == {CorpusFileStatus.done}


def test_execute_on_progress_called_with_monotonic_done(env, corpus):
    rs = RunService(env.services)
    run = rs.start(corpus.id, "full", user_id=env.user_id)
    seen = []
    rs.execute(run.id, on_progress=lambda r: seen.append(r.counts.done))
    assert seen and seen == sorted(seen) and seen[-1] == 2


def test_execute_twice_is_noop_or_conflict(env, corpus):
    rs = RunService(env.services)
    run = rs.start(corpus.id, "full", user_id=env.user_id)
    rs.execute(run.id)
    n = len(env.spy.calls)
    try:
        again = rs.execute(run.id)
        assert again.status == RunStatus.done
    except AppError as e:
        assert e.code == "conflict"
    assert len(env.spy.calls) == n


def test_failed_file_gives_done_with_errors(env, corpus):
    env.spy.fail_on["D003"] = "ml_error"
    run = env.full_run(corpus.id)
    fs = statuses(env, corpus.id)
    assert run.status == RunStatus.done_with_errors and run.counts.failed == 1 and run.counts.done == 1
    assert fs["D003.pdf"].status == CorpusFileStatus.failed and fs["D003.pdf"].error_code == "ml_error"
    assert fs["D001.pdf"].status == CorpusFileStatus.done


def test_broken_pdf_in_corpus_failed_run_done_with_errors(env, pdf_dir, tmp_path):
    (pdf_dir / "BROKEN.pdf").write_bytes(b"%PDF-1.4\ngarbage")
    c = env.new_corpus("cli")
    env.add_dir(pdf_dir, c.id, mode="cli")
    run = env.full_run(c.id)
    fs = statuses(env, c.id)
    assert fs["BROKEN.pdf"].status == CorpusFileStatus.failed and fs["BROKEN.pdf"].error_code == "pdf_broken"
    assert run.status == RunStatus.done_with_errors
    assert "BROKEN" not in env.spy.calls  # до ML битый файл не доходит


def test_fail_fast_stops_after_first_failure(make_env, pdf_dir):
    env = make_env()
    env.spy.fail_on["D001"] = "ml_error"
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    rs = RunService(env.services)
    run = rs.start(c.id, "full", user_id=env.user_id, concurrency=1)
    out = rs.execute(run.id, fail_fast=True)
    assert out.status in (RunStatus.failed, RunStatus.done_with_errors)
    assert env.spy.calls == ["D001"]
    assert statuses(env, c.id)["D003.pdf"].status in (CorpusFileStatus.cancelled, CorpusFileStatus.queued,
                                                    CorpusFileStatus.pending)


def test_cancel_token_stops_and_keeps_finished(env, corpus):
    class Tok:
        def __init__(self): self.n = 0
        def is_set(self):
            self.n += 1
            return self.n > 1  # после первого файла
    rs = RunService(env.services)
    run = rs.start(corpus.id, "full", user_id=env.user_id, concurrency=1)
    out = rs.execute(run.id, cancel=Tok())
    fs = statuses(env, corpus.id)
    assert out.status == RunStatus.cancelled
    assert sorted(f.status for f in fs.values()) == [CorpusFileStatus.cancelled, CorpusFileStatus.done]
    assert out.counts.done == 1


# --- submit / cancel / retry --------------------------------------------------------------------------------------------

def test_submit_inline_executes(env, corpus):
    rs = RunService(env.services)
    run = rs.start(corpus.id, "full", user_id=env.user_id)
    rs.submit(run.id)
    assert get_run(env, run.id).status == RunStatus.done


def test_submit_uses_services_executor(make_env, pdf_dir):
    submitted = []

    class Capture:
        def submit(self, fn):
            submitted.append(fn)

    env = make_env(executor=Capture())
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    rs = RunService(env.services)
    run = rs.start(c.id, "full", user_id=env.user_id)
    rs.submit(run.id)
    assert len(submitted) == 1 and get_run(env, run.id).status == RunStatus.queued
    submitted[0]()
    assert get_run(env, run.id).status == RunStatus.done


def test_cancel_before_start_marks_all_cancelled(env, corpus):
    rs = RunService(env.services)
    run = rs.start(corpus.id, "full", user_id=env.user_id)
    out = rs.cancel(run.id)
    assert out.status in (RunStatus.cancelled, RunStatus.cancelling)
    assert all(f.status in (CorpusFileStatus.cancelled, CorpusFileStatus.pending, CorpusFileStatus.queued)
               for f in statuses(env, corpus.id).values())
    assert env.spy.calls == []


def test_cancel_frees_corpus_for_new_run(env, corpus):
    rs = RunService(env.services)
    run = rs.start(corpus.id, "full", user_id=env.user_id)
    rs.cancel(run.id)
    rs.execute(run.id)  # доводит до cancelled, ML не зовёт
    assert get_run(env, run.id).status == RunStatus.cancelled
    assert rs.start(corpus.id, "full", user_id=env.user_id)


def test_cancel_finished_run_conflict_or_noop(env, corpus):
    run = env.full_run(corpus.id)
    try:
        out = RunService(env.services).cancel(run.id)
        assert out.status == RunStatus.done
    except AppError as e:
        assert e.code == "conflict"


def test_cancel_unknown_run(env):
    with pytest.raises(AppError) as ei:
        RunService(env.services).cancel("run_nope")
    assert ei.value.code == "not_found"


def test_retry_file_creates_run_for_that_file(env, corpus):
    env.spy.fail_on["D003"] = "ml_error"
    env.full_run(corpus.id)
    env.spy.fail_on.clear()
    fid = statuses(env, corpus.id)["D003.pdf"].id
    rs = RunService(env.services)
    run = rs.retry_file(corpus.id, fid)
    assert run.counts.total == 1 and run.file_ids == [fid]
    rs.execute(run.id)
    assert statuses(env, corpus.id)["D003.pdf"].status == CorpusFileStatus.done


def test_retry_file_of_other_corpus_not_found(env, corpus, pdf_dir):
    other = env.new_corpus("B")
    env.add_dir(pdf_dir, other.id)
    fid = statuses(env, other.id)["D001.pdf"].id
    with pytest.raises(AppError) as ei:
        RunService(env.services).retry_file(corpus.id, fid)
    assert ei.value.code == "not_found"


def test_retry_file_during_active_run_conflict(env, corpus):
    rs = RunService(env.services)
    rs.start(corpus.id, "full", user_id=env.user_id)
    with pytest.raises(AppError) as ei:
        rs.retry_file(corpus.id, statuses(env, corpus.id)["D001.pdf"].id)
    assert ei.value.code == "conflict"


# --- виды прогонов -------------------------------------------------------------------------------------------------------------------

def test_new_only_skips_processed(env, corpus, make_pdf, pdf_dir):
    env.full_run(corpus.id)
    n = len(env.spy.calls)
    make_pdf(["new"], name="D999.pdf", directory=pdf_dir)
    env.add_dir(pdf_dir, corpus.id)  # D001/D003 -> duplicate_name, D999 новый
    run = env.full_run(corpus.id, kind="new_only")
    assert env.spy.calls[n:] == ["D999"] and run.counts.total == 1


def test_full_reprocesses_everything(env, corpus):
    env.full_run(corpus.id)
    env.full_run(corpus.id)
    assert sorted(env.spy.calls) == ["D001", "D001", "D003", "D003"]


def test_failed_only_retries_just_failed(env, corpus):
    env.spy.fail_on["D003"] = "ml_error"
    env.full_run(corpus.id)
    env.spy.fail_on.clear()
    n = len(env.spy.calls)
    run = env.full_run(corpus.id, kind="failed_only")
    assert env.spy.calls[n:] == ["D003"] and run.status == RunStatus.done


def test_failed_only_with_nothing_failed_is_empty_done(env, corpus):
    env.full_run(corpus.id)
    run = env.full_run(corpus.id, kind="failed_only")
    assert run.counts.total == 0 and run.status == RunStatus.done


@pytest.mark.parametrize("kind", ["renormalize", "recheck"])
def test_renormalize_recheck_do_not_call_extractor(env, corpus, kind):
    env.full_run(corpus.id)
    n = len(env.spy.calls)
    run = env.full_run(corpus.id, kind=kind)
    assert run.status == RunStatus.done and len(env.spy.calls) == n and run.counts.done == 2


def test_selected_processes_only_chosen(env, corpus):
    fid = statuses(env, corpus.id)["D003.pdf"].id
    run = env.full_run(corpus.id, kind="selected", file_ids=[fid])
    assert env.spy.calls == ["D003"] and run.counts.total == 1
    assert statuses(env, corpus.id)["D001.pdf"].status != CorpusFileStatus.done


# --- start_or_append (служебный корпус «Загруженные вручную») ----------------------------------------------------------------------------

def test_start_or_append_starts_selected_run_when_idle(env, pdf_dir, collect):
    manual = CorpusService(env.services).manual_corpus(env.workspace_id)
    r = CorpusService(env.services).add_inputs(manual.id, collect(pdf_dir, ["D001.pdf"]), mode="api")
    run = RunService(env.services).start_or_append(manual.id, [r.added[0].id], user_id=env.user_id)
    assert run.kind == RunKind.selected and run.counts.total == 1


def test_start_or_append_adds_to_active_run_without_409(env, pdf_dir, collect):
    manual = CorpusService(env.services).manual_corpus(env.workspace_id)
    cs = CorpusService(env.services)
    f1 = cs.add_inputs(manual.id, collect(pdf_dir, ["D001.pdf"]), mode="api").added[0]
    rs = RunService(env.services)
    first = rs.start_or_append(manual.id, [f1.id], user_id=env.user_id)
    f2 = cs.add_inputs(manual.id, collect(pdf_dir, ["D003.pdf"]), mode="api").added[0]
    second = rs.start_or_append(manual.id, [f2.id], user_id=env.user_id)
    assert second.id == first.id and second.counts.total == 2
    rs.execute(first.id)
    assert {f.status for f in statuses(env, manual.id).values()} == {CorpusFileStatus.done}


def test_start_or_append_wrong_corpus_kind_rejected(env, corpus):
    with pytest.raises(AppError) as ei:
        RunService(env.services).start_or_append(corpus.id, [statuses(env, corpus.id)["D001.pdf"].id],
                                                 user_id=env.user_id)
    assert ei.value.code in ("validation_error", "conflict")


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


def test_keep_review_preserves_accepted(env, corpus):
    env.full_run(corpus.id)
    pid = statuses(env, corpus.id)["D001.pdf"].paper_id
    fact = _accept_first_result_fact(env, pid)
    env.full_run(corpus.id, keep_review=True)
    same = [f for f in _facts(env, pid) if f.target == fact.target]
    assert same and same[0].review_status == ReviewStatus.accepted


def test_keep_review_false_resets_to_new(env, corpus):
    env.full_run(corpus.id)
    pid = statuses(env, corpus.id)["D001.pdf"].paper_id
    fact = _accept_first_result_fact(env, pid)
    env.full_run(corpus.id, keep_review=False)
    same = [f for f in _facts(env, pid) if f.target == fact.target]
    assert same and same[0].review_status == ReviewStatus.new


def test_keep_review_mismatched_edit_becomes_manual_fact(make_env, pdf_dir, fake_inner, fake_norm):
    class Drifting(fake_inner):
        def extract(self, req):
            out = super().extract(req)
            if len(self.calls) > 2:  # второй прогон: значение R2 ушло далеко (>0,5 %)
                for r in out.bundle.results:
                    if r.result_id == "R2":
                        r.raw_value = "55.5"
            return out

    env = make_env(extractor=Drifting(), normalizer=fake_norm)
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    env.full_run(c.id)
    pid = statuses(env, c.id)["D001.pdf"].paper_id
    with env.uow() as uow:
        r2 = next(r.fact for r in uow.facts.query(FactFilter(paper_ids=[pid]), [], 1000, None).data
                  if r.fact.target.get("id") == "R2")
        uow.facts.update_review(r2.id, review_status=ReviewStatus.accepted)
    env.full_run(c.id, keep_review=True)
    with env.uow() as uow:
        manual = uow.facts.manual_for_paper(pid)
    assert len(manual) == 1 and manual[0].normalized_value is not None
    assert str(manual[0].normalized_value) == "12.89"  # прежнее значение сохранено как ручной факт
