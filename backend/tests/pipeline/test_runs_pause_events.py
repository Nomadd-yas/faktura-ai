"""Пауза прогона при недоступном ML и события прогона (REQ-065, план 5.6 P1; часы и sleep подменены)."""
from __future__ import annotations

import pytest

from faktura.domain.entities import CorpusFileStatus, RunStatus
from faktura.pipeline import RunService


def statuses(env, corpus_id):
    with env.uow() as uow:
        return {f.name: f for f in uow.files.list(corpus_id)}


def setup(env, pdf_dir):
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    return c


# --- пауза ML ----------------------------------------------------------------------------------------------------------

def test_pause_then_resume_all_done(make_env, pdf_dir, clock, event_log, settings):
    settings.ml_pause_poll_s = 5
    env = make_env(health_seq=["down", "down", "ok"])
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    rs = RunService(env.services)
    run = rs.start(c.id, "full", user_id=env.user_id)
    paused_seen = []
    rs.execute(run.id, on_progress=lambda r: paused_seen.append(r.paused))
    log.stop()
    with env.uow() as uow:
        final = uow.runs.get(run.id)
    names = log.names()
    assert "run.paused" in names and "run.resumed" in names
    assert names.index("run.paused") < names.index("run.resumed") < names.index("run.done")
    assert final.status == RunStatus.done and final.paused is False
    assert {f.status for f in statuses(env, c.id).values()} == {CorpusFileStatus.done}
    assert True in paused_seen  # на паузе on_progress видел paused=true
    assert clock.slept, "ожидание идёт через подменённый sleep, а не реальное время"


def test_pause_event_payload(make_env, pdf_dir, clock, event_log, settings):
    settings.ml_pause_poll_s = 7
    env = make_env(health_seq=["down", "ok"])
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    env.full_run(c.id)
    log.stop()
    ev = log.of("run.paused")[0]
    assert ev["reason"] == "ml_unavailable" and ev["corpus_id"] == c.id and "run_id" in ev
    assert ev.get("retry_in_s") in (7, None) or isinstance(ev["retry_in_s"], (int, float))
    assert log.of("run.resumed")[0]["corpus_id"] == c.id


def test_paused_run_keeps_status_running_and_files_queued(make_env, pdf_dir, clock, settings):
    settings.ml_pause_poll_s = 5
    settings.ml_pause_max_s = 20
    env = make_env(health_seq=["down"])
    c = setup(env, pdf_dir)
    rs = RunService(env.services)
    run = rs.start(c.id, "full", user_id=env.user_id)
    snapshots = []

    def on_progress(r):
        snapshots.append((r.status, r.paused, r.pause_reason))

    rs.execute(run.id, on_progress=on_progress)
    assert (RunStatus.running, True, "ml_unavailable") in snapshots  # статус на паузе - running, не новое значение


def test_pause_exceeds_max_fails_remaining_with_ml_unavailable(make_env, pdf_dir, clock, settings, event_log):
    settings.ml_pause_poll_s = 10
    settings.ml_pause_max_s = 30
    env = make_env(health_seq=["down"])
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    run = env.full_run(c.id)
    log.stop()
    fs = statuses(env, c.id)
    assert run.status == RunStatus.done_with_errors
    assert {f.status for f in fs.values()} == {CorpusFileStatus.failed}
    assert {f.error_code for f in fs.values()} == {"ml_unavailable"}
    assert sum(clock.slept) >= 30 and sum(clock.slept) < 30 + 2 * 10 + 1  # ждали ml_pause_max_s, не дольше
    assert "run.paused" in log.names() and "run.resumed" not in log.names()
    assert log.of("run.done")[-1]["status"] == "done_with_errors"


def test_recheck_does_not_need_ml_even_when_down(make_env, pdf_dir, clock, settings):
    settings.ml_pause_poll_s = 5
    settings.ml_pause_max_s = 10
    env = make_env(health_seq=["ok"])
    c = setup(env, pdf_dir)
    env.full_run(c.id)  # оба файла готовы
    env.spy.down_all = True
    run = env.full_run(c.id, kind="recheck")  # recheck не обращается к ML
    assert run.status == RunStatus.done


def test_unavailable_ml_before_start_pauses_instead_of_failing_immediately(make_env, pdf_dir, clock, settings):
    settings.ml_pause_poll_s = 5
    settings.ml_pause_max_s = 60
    env = make_env(health_seq=["down", "down", "down", "ok"])
    c = setup(env, pdf_dir)
    run = env.full_run(c.id)
    assert run.status == RunStatus.done and env.spy.health_calls >= 3


# --- события прогона ------------------------------------------------------------------------------------------------------------

def test_run_file_event_for_each_file_and_run_done_last(make_env, pdf_dir, clock, event_log):
    env = make_env()
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    run = env.full_run(c.id)
    log.stop()
    files = log.of("run.file")
    assert sorted(e["status"] for e in files if e["run_id"] == run.id) == ["done", "done"]
    assert {e["file_id"] for e in files} == {f.id for f in statuses(env, c.id).values()}
    assert all({"paper_id", "facts", "issues"} <= set(e) for e in files)
    done = log.of("run.done")
    assert len(done) == 1 and done[0]["status"] == "done" and done[0]["counts"]["done"] == 2
    assert log.names()[-1] == "run.done"


def test_run_file_failed_event_has_error(make_env, pdf_dir, clock, event_log):
    env = make_env()
    env.spy.fail_on["D003"] = "ml_error"
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    env.full_run(c.id)
    log.stop()
    failed = [e for e in log.of("run.file") if e["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["error"]


def test_paper_done_event_per_paper(make_env, pdf_dir, clock, event_log):
    env = make_env()
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    env.full_run(c.id)
    log.stop()
    assert len(log.of("paper.done")) == 2


def test_run_progress_not_more_than_once_per_second(make_env, pdf_dir, clock, event_log):
    env = make_env()
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    rs = RunService(env.services)
    run = rs.start(c.id, "full", user_id=env.user_id)
    rs.execute(run.id)  # часы стоят: всё происходит в одну «секунду»
    log.stop()
    assert len(log.of("run.progress")) <= 1


def test_run_progress_emitted_again_after_a_second(make_env, pdf_dir, clock, event_log):
    env = make_env()
    # на каждый вызов extract «проходит» 1.5 с
    orig = env.spy.extract

    def slow(req):
        clock.advance(1.5)
        return orig(req)

    env.spy.extract = slow
    c = setup(env, pdf_dir)
    log = event_log(env.services.bus, env.workspace_id)
    env.full_run(c.id)
    log.stop()
    assert len(log.of("run.progress")) >= 2
    pr = log.of("run.progress")[-1]
    assert {"run_id", "corpus_id", "progress", "counts"} <= set(pr)
    assert 0 <= pr["progress"] <= 1


def test_events_published_to_own_workspace_only(make_env, pdf_dir, clock, event_log):
    env = make_env()
    c = setup(env, pdf_dir)
    other = event_log(env.services.bus, "ws_other")
    env.full_run(c.id)
    other.stop()
    assert other.items == []
