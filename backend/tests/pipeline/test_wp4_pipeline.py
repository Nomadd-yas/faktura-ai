"""WP4: устойчивость конвейера - строгая проверка ответа, таймаут файла, параллельность, этапы, возобновление."""
from __future__ import annotations

import copy
import threading
import time

import pytest

from faktura.domain.entities import CorpusFileStatus, RunStatus
from faktura.ml.response import parse_extract_response


class _Wrap:
    """Обёртка над экстрактором с хуками; name=mock."""
    name = "mock"

    def __init__(self, inner, on_extract=None):
        self.inner = inner
        self.on_extract = on_extract

    def extract(self, req):
        if self.on_extract is not None:
            return self.on_extract(req, self.inner)
        return self.inner.extract(req)

    def health(self):
        return self.inner.health()


def _files(env, corpus_id):
    with env.uow() as uow:
        return {f.name: f for f in uow.files.list(corpus_id)}


def test_duplicate_ids_from_model_fail_file_with_public_reason(make_env, pdf_dir, mini_gold_dir, settings):
    from faktura.ml.adapters import MockGoldExtractor

    gold = MockGoldExtractor(mini_gold_dir, "all")

    def bad(req, inner):
        raw = copy.deepcopy(inner.build_raw(req.publication_id))
        if raw["results"]:
            raw["results"].append(copy.deepcopy(raw["results"][0]))
        return parse_extract_response(raw, req.publication_id)

    env = make_env(extractor=_Wrap(gold, bad))
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    env.full_run(c.id)
    f = _files(env, c.id)["D001.pdf"]
    assert f.status == CorpusFileStatus.failed
    assert f.error_message.startswith("Модель вернула некорректные данные: ")
    assert "ML" not in f.error_message
    assert f.progress == 100 and f.stage is None


def test_file_timeout_marks_error_and_run_goes_on(make_env, pdf_dir, mini_gold_dir, settings):
    from faktura.ml.adapters import MockGoldExtractor

    settings.file_timeout_s = 0.4
    release = threading.Event()
    gold = MockGoldExtractor(mini_gold_dir, "all")

    def slow(req, inner):
        if req.publication_id == "D001":
            release.wait(10)
        return inner.extract(req)

    env = make_env(extractor=_Wrap(gold, slow))
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    try:
        t0 = time.monotonic()
        run = env.full_run(c.id)
        files = _files(env, c.id)
    finally:
        release.set()
    assert files["D001.pdf"].status == CorpusFileStatus.failed
    assert files["D001.pdf"].error_message.startswith("Превышено время разбора файла")
    assert files["D003.pdf"].status == CorpusFileStatus.done
    assert run.status == RunStatus.done_with_errors


def test_files_processed_in_parallel_for_mock(make_env, pdf_dir, mini_gold_dir):
    from faktura.ml.adapters import MockGoldExtractor

    gold = MockGoldExtractor(mini_gold_dir, "all")
    state = {"now": 0, "max": 0}
    lock = threading.Lock()
    gate = threading.Event()

    def slow(req, inner):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            if state["now"] >= 2:
                gate.set()
        gate.wait(3)  # оба файла должны дойти сюда одновременно
        with lock:
            state["now"] -= 1
        return inner.extract(req)

    env = make_env(extractor=_Wrap(gold, slow))
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    run = env.full_run(c.id)
    assert state["max"] >= 2
    assert run.status == RunStatus.done and run.concurrency == 4


def test_stage_and_progress_persisted(make_env, pdf_dir, mini_gold_dir):
    from faktura.ml.adapters import MockGoldExtractor

    gold = MockGoldExtractor(mini_gold_dir, "all")
    seen = {}

    def peek(req, inner):
        env_ = holder["env"]
        with env_.uow() as uow:
            f = [x for x in uow.files.list(holder["cid"]) if x.publication_id == req.publication_id][0]
        seen[req.publication_id] = (f.status, f.stage, f.progress)
        return inner.extract(req)

    holder: dict = {}
    env = make_env(extractor=_Wrap(gold, peek))
    c = env.new_corpus()
    holder.update(env=env, cid=c.id)
    env.add_dir(pdf_dir, c.id)
    env.full_run(c.id)
    assert seen["D001"] == (CorpusFileStatus.processing, "Извлечение", 10)
    for f in _files(env, c.id).values():
        assert f.status == CorpusFileStatus.done and f.progress == 100 and f.stage is None


def test_processor_reports_stages(make_env, pdf_dir):
    from faktura.domain.profile import builtin_solar_profile
    from faktura.pipeline.processor import PaperProcessor

    env = make_env()
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    with env.uow() as uow:
        pid = [f.paper_id for f in uow.files.list(c.id) if f.name == "D001.pdf"][0]
    stages: list[tuple[str, int]] = []
    out = PaperProcessor(env.services).process(pid, profile=builtin_solar_profile(), mode="full",
                                               model_version="latest",
                                               on_stage=lambda n, p: stages.append((n, p)))
    assert out.status.value == "done"
    assert stages == [("Нормализация", 60), ("Проверка", 85)]


def test_resume_active_continues_and_is_idempotent(make_env, pdf_dir):
    from faktura.api.base.context import make_context
    from faktura.pipeline import RunService

    env = make_env()
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    rs = RunService(env.services)
    run = rs.start(c.id, "full", user_id=env.user_id)
    # имитация падения сервера посреди разбора: прогон running, файл processing
    with env.uow() as uow:
        uow.runs.update(run.id, status=RunStatus.running)
        fid = uow.runs.get(run.id).file_ids[0]
        uow.files.update_status(fid, CorpusFileStatus.processing, stage="Проверка", progress=85)
    ctx = make_context(env.services.settings, env.services)
    assert ctx.resume_runs() == [run.id]
    with env.uow() as uow:
        assert uow.runs.get(run.id).status in (RunStatus.done, RunStatus.done_with_errors)
        assert all(f.status == CorpusFileStatus.done for f in uow.files.list(c.id))
    assert ctx.resume_runs() == []  # повторный вызов ничего не делает


def test_resume_skips_run_already_in_flight(make_env, pdf_dir):
    from faktura.pipeline import RunService
    from faktura.pipeline import runs as runs_mod

    env = make_env()
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    run = RunService(env.services).start(c.id, "full", user_id=env.user_id)
    with runs_mod._INFLIGHT_LOCK:
        runs_mod._INFLIGHT.add(run.id)
    try:
        assert RunService(env.services).resume_active() == []
    finally:
        with runs_mod._INFLIGHT_LOCK:
            runs_mod._INFLIGHT.discard(run.id)


def test_app_startup_resumes_runs(make_env, pdf_dir):
    from fastapi.testclient import TestClient

    from faktura.api.base import create_app
    from faktura.pipeline import RunService

    env = make_env()
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    run = RunService(env.services).start(c.id, "full", user_id=env.user_id)
    with TestClient(create_app(env.services.settings, env.services)):
        pass
    with env.uow() as uow:
        assert uow.runs.get(run.id).status in (RunStatus.done, RunStatus.done_with_errors)


def test_late_columns_added_to_existing_db(tmp_path):
    """Существующая БД без stage/progress получает колонки при create_all (идемпотентно)."""
    import sqlalchemy as sa

    from faktura.config import Settings
    from faktura.pipeline import build_services

    s = Settings(_env_file=None, db_url=f"sqlite:///{(tmp_path / 'old.db').as_posix()}", storage_dir=tmp_path / "st",
                 extractor="mock", jwt_secret="x" * 40)
    sv = build_services(s)
    sv.db.create_all()
    with sv.db.engine.begin() as c:
        c.execute(sa.text("ALTER TABLE corpus_files DROP COLUMN stage"))
        c.execute(sa.text("ALTER TABLE corpus_files DROP COLUMN progress"))
    sv.db.create_all()
    sv.db.create_all()
    cols = {c["name"] for c in sa.inspect(sv.db.engine).get_columns("corpus_files")}
    assert {"stage", "progress"} <= cols
