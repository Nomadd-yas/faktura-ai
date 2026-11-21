"""Ход разбора и ETA: чистые функции `pipeline/progress.py`, скорости этапов в БД."""
from __future__ import annotations

import pytest

from faktura.pipeline import progress as P
from faktura.pipeline.progress import EXTRACT, NORMALIZE, VALIDATE, FileState

SPEEDS = {EXTRACT: 10.0, NORMALIZE: 0.0, VALIDATE: 0.0}


def test_stage_ranges_and_cap():
    # внутри этапа: прошло / ожидаемое, не выше 95 % этапа
    assert P.file_progress(EXTRACT, 0, 10, SPEEDS) == 0
    assert P.file_progress(EXTRACT, 50, 10, SPEEDS) == pytest.approx(35.0)
    assert P.file_progress(EXTRACT, 10_000, 10, SPEEDS) == pytest.approx(66.5)
    assert P.file_progress(NORMALIZE, 0, 10, {**SPEEDS, NORMALIZE: 1.0}) == 70
    assert 85 <= P.file_progress(VALIDATE, 5, 10, {**SPEEDS, VALIDATE: 1.0}) < 100
    assert P.file_progress(None, 0, None, SPEEDS) == 0  # этапа/страниц нет - не падает


def test_progress_never_decreases_and_final_is_100():
    sm = P.Smoother()
    assert [sm.progress("f", v) for v in (10, 40, 30, 5, 60)] == [10, 40, 40, 40, 60]
    assert sm.progress("f", 150) == 99  # до конечного статуса не выше 99
    assert sm.progress("f", 10, final=True) == 100


def test_estimate_final_states():
    files = [FileState("a", "final", 5), FileState("b", "final", 7)]
    e = P.estimate(files, SPEEDS, 2)
    assert e.run_progress == 100 and e.run_eta is None and e.eta == {"a": None, "b": None}
    assert e.progress == {"a": 100, "b": 100}


def test_eta_null_without_pages():
    files = [FileState("a", "running", None, EXTRACT, 3), FileState("b", "queued", None)]
    e = P.estimate(files, SPEEDS, 2)
    assert e.run_eta is None and e.eta == {"a": None, "b": None}
    assert 0 <= e.run_progress < 100  # ход считается и без страниц


def test_file_eta_remaining_stages():
    sp = {EXTRACT: 10.0, NORMALIZE: 1.0, VALIDATE: 2.0}
    # 10 стр: извлечение 100 с, прошло 40 -> 60 + 10 + 20
    assert P.file_remaining(EXTRACT, 40, 10, sp) == pytest.approx(90)
    # перерасход этапа: остаётся 10 % ожидаемого + будущие этапы
    assert P.file_remaining(EXTRACT, 500, 10, sp) == pytest.approx(10 + 30)
    assert P.file_remaining(VALIDATE, 5, 10, sp) == pytest.approx(15)
    assert P.file_duration(10, sp) == pytest.approx(130)


def test_run_eta_with_parallelism():
    files = [FileState("r1", "running", 10, EXTRACT, 0),  # 100 с
             FileState("q1", "queued", 10), FileState("q2", "queued", 10), FileState("q3", "queued", 10)]
    e = P.estimate(files, SPEEDS, 2)
    assert e.eta["r1"] == pytest.approx(100)
    # второй рабочий свободен: q1 -> 100, q2 ждёт r1 -> 200, q3 -> 200
    assert e.eta["q1"] == pytest.approx(100) and e.eta["q2"] == pytest.approx(200)
    assert e.eta["q3"] == pytest.approx(200)
    assert e.run_eta == pytest.approx(200)  # 400 с работы / 2 рабочих
    assert P.estimate(files, SPEEDS, 1).run_eta == pytest.approx(400)
    # не меньше ETA самого долгого файла
    lone = P.estimate([FileState("r", "running", 10, EXTRACT, 0)], SPEEDS, 8)
    assert lone.run_eta == pytest.approx(100)


def test_run_progress_weighted_by_pages():
    e = P.estimate([FileState("big", "final", 90), FileState("small", "queued", 10)], SPEEDS, 2)
    assert e.run_progress == 90  # среднее по файлам дало бы 50


def test_smoothing_and_rounding():
    assert P.smooth_eta(100, 300) == pytest.approx(130)
    assert P.smooth_eta(100, 10) == pytest.approx(70)
    assert P.smooth_eta(100, 110) == 110
    assert P.smooth_eta(None, 55) == 55 and P.smooth_eta(100, None) is None
    assert P.round_eta(371) == 370 and P.round_eta(373) == 375 and P.round_eta(None) is None
    assert P.round_eta(1) == 5  # идущая работа не показывает 0
    sm = P.Smoother()
    assert sm.eta("r", 200) == 200
    assert sm.eta("r", 20) == 140  # 200 * 0.7
    assert sm.eta("r", None) is None


def test_speeds_default_and_learned():
    assert P.speeds_for("rag", None)[EXTRACT] == 15.0 and P.speeds_for("solar", None)[EXTRACT] == 10.0
    assert P.speeds_for("solar", {EXTRACT: 4.0})[EXTRACT] == 4.0
    assert P.speeds_for("unknown", {})[EXTRACT] > 0


def test_speed_repo_moving_average_per_domain(tmp_path):
    from faktura.storage import Database

    db = Database(f"sqlite:///{tmp_path / 's.db'}")
    db.create_all()
    with db.uow() as uow:
        assert uow.speeds.averages("solar") == {}
        for _ in range(60):
            uow.speeds.add("solar", EXTRACT, 100.0)  # старые замеры вытесняются окном 50
        for _ in range(50):
            uow.speeds.add("solar", EXTRACT, 2.0)
        uow.speeds.add("rag", EXTRACT, 7.0)
        uow.speeds.add("solar", EXTRACT, 0)  # мусор игнорируется
        uow.speeds.add("solar", EXTRACT, float("nan"))
    with db.uow() as uow:
        assert uow.speeds.averages("solar") == {EXTRACT: pytest.approx(2.0)}
        assert uow.speeds.averages("rag") == {EXTRACT: pytest.approx(7.0)}
