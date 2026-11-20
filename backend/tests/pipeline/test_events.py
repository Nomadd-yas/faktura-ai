"""InlineExecutor, ThreadExecutor, InMemoryEventBus (план 5.6, P1)."""
from __future__ import annotations

import threading
import time

import pytest

from faktura.domain.ports import EventBus, Executor
from faktura.pipeline import InlineExecutor, InMemoryEventBus, ThreadExecutor


# --- executors -------------------------------------------------------------------------------------------------

def test_executors_satisfy_protocol():
    assert isinstance(InlineExecutor(), Executor)
    assert isinstance(ThreadExecutor(2), Executor)


def test_inline_runs_synchronously_in_caller_thread():
    seen = []
    InlineExecutor().submit(lambda: seen.append(threading.get_ident()))
    assert seen == [threading.get_ident()]


def test_inline_executor_usable_after_failing_task():
    ex, seen = InlineExecutor(), []
    try:
        ex.submit(lambda: (_ for _ in ()).throw(RuntimeError("x")))
    except RuntimeError:
        pass
    ex.submit(lambda: seen.append(1))
    assert seen == [1]


def test_thread_executor_runs_in_other_thread_and_returns_at_once():
    ex = ThreadExecutor(2)
    gate, done, tid = threading.Event(), threading.Event(), []

    def task():
        tid.append(threading.get_ident())
        gate.wait(5)
        done.set()

    t0 = time.perf_counter()
    ex.submit(task)
    assert time.perf_counter() - t0 < 1.0 and not done.is_set()
    gate.set()
    assert done.wait(5) and tid[0] != threading.get_ident()


def test_thread_executor_respects_max_workers():
    ex = ThreadExecutor(2)
    lock, state, finished = threading.Lock(), {"cur": 0, "max": 0}, threading.Semaphore(0)

    def task():
        with lock:
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
        time.sleep(0.05)
        with lock:
            state["cur"] -= 1
        finished.release()

    for _ in range(6):
        ex.submit(task)
    for _ in range(6):
        assert finished.acquire(timeout=5)
    assert 1 <= state["max"] <= 2


def test_thread_executor_survives_exception_in_task():
    ex, ok = ThreadExecutor(1), threading.Event()
    ex.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    ex.submit(ok.set)
    assert ok.wait(5)


# --- bus: публикация и подписка ---------------------------------------------------------------------------------------

def test_bus_satisfies_protocol():
    assert isinstance(InMemoryEventBus(), EventBus)


def test_publish_returns_increasing_ids():
    bus = InMemoryEventBus()
    ids = [bus.publish("ws", "run.file", {"i": i}) for i in range(5)]
    assert all(isinstance(i, str) for i in ids)
    nums = [int(i) for i in ids]
    assert nums == sorted(nums) and len(set(nums)) == 5


def test_subscriber_gets_events_in_order(event_log):
    bus = InMemoryEventBus()
    log = event_log(bus, "ws")
    for i in range(3):
        bus.publish("ws", "run.file", {"i": i})
    time.sleep(0.1)
    log.stop()
    assert [e[1] for e in log.items] == ["run.file"] * 3
    assert [e[2]["i"] for e in log.items] == [0, 1, 2]
    ids = [int(e[0]) for e in log.items]
    assert ids == sorted(ids)


def test_event_triple_shape(event_log):
    bus = InMemoryEventBus()
    log = event_log(bus, "ws")
    eid = bus.publish("ws", "paper.done", {"paper_id": "p"})
    time.sleep(0.1)
    log.stop()
    assert log.items == [(eid, "paper.done", {"paper_id": "p"})]


def test_workspaces_isolated(event_log):
    bus = InMemoryEventBus()
    a, b = event_log(bus, "A"), event_log(bus, "B")
    bus.publish("A", "run.file", {})
    time.sleep(0.1)
    a.stop(), b.stop()
    assert len(a.items) == 1 and b.items == []


def test_last_event_id_replays_missed(event_log):
    bus = InMemoryEventBus()
    ids = [bus.publish("ws", "run.file", {"i": i}) for i in range(5)]
    log = event_log(bus, "ws", ids[1])
    time.sleep(0.1)
    log.stop()
    assert [e[2]["i"] for e in log.items] == [2, 3, 4]


def test_last_event_id_of_latest_gives_nothing_old(event_log):
    bus = InMemoryEventBus()
    ids = [bus.publish("ws", "run.file", {"i": i}) for i in range(3)]
    log = event_log(bus, "ws", ids[-1])
    time.sleep(0.1)
    log.stop()
    assert log.items == []


def test_overflow_sends_stream_reset_first(event_log):
    bus = InMemoryEventBus(buffer=3)
    ids = [bus.publish("ws", "run.file", {"i": i}) for i in range(10)]
    log = event_log(bus, "ws", ids[0])  # id давно вытеснен
    time.sleep(0.1)
    log.stop()
    assert log.items and log.items[0][1] == "stream.reset"
    assert log.items[0][2] == {"reason": "history_lost"}


def test_no_reset_when_history_is_intact(event_log):
    bus = InMemoryEventBus(buffer=3)
    ids = [bus.publish("ws", "run.file", {"i": i}) for i in range(5)]
    log = event_log(bus, "ws", ids[-2])  # последние 3 в буфере
    time.sleep(0.1)
    log.stop()
    assert "stream.reset" not in log.names()
    assert [e[2]["i"] for e in log.items] == [4]


def test_garbage_last_event_id_does_not_crash(event_log):
    bus = InMemoryEventBus()
    bus.publish("ws", "run.file", {})
    log = event_log(bus, "ws", "not-a-number")
    time.sleep(0.1)
    log.stop()
    assert all(isinstance(e[1], str) for e in log.items)


def test_close_stops_iteration():
    bus = InMemoryEventBus()
    sub = bus.subscribe("ws", None)
    done = threading.Event()

    def run():
        for _ in sub:
            pass
        done.set()

    threading.Thread(target=run, daemon=True).start()
    sub.close()
    assert done.wait(3)


def test_ids_unique_under_concurrent_publish():
    bus = InMemoryEventBus(buffer=10_000)
    out, lock = [], threading.Lock()

    def worker(n):
        for i in range(50):
            eid = bus.publish("ws", "run.file", {"n": n, "i": i})
            with lock:
                out.append(eid)

    ts = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 400 and len(set(out)) == 400


# --- run.progress не чаще раза в секунду ----------------------------------------------------------------------------------

def test_progress_throttled_to_one_per_second(clock, event_log):
    bus = InMemoryEventBus()
    log = event_log(bus, "ws")
    for i in range(10):
        bus.publish("ws", "run.progress", {"run_id": "r1", "progress": i / 10})
    clock.advance(1.01)
    bus.publish("ws", "run.progress", {"run_id": "r1", "progress": 1.0})
    time.sleep(0.1)
    log.stop()
    assert len(log.of("run.progress")) == 2
    assert log.of("run.progress")[0]["progress"] == 0.0  # первое событие проходит сразу


def test_throttle_is_per_run(clock, event_log):
    bus = InMemoryEventBus()
    log = event_log(bus, "ws")
    bus.publish("ws", "run.progress", {"run_id": "r1", "progress": 0.1})
    bus.publish("ws", "run.progress", {"run_id": "r2", "progress": 0.1})
    time.sleep(0.1)
    log.stop()
    assert len(log.of("run.progress")) == 2


def test_other_events_never_throttled(clock, event_log):
    bus = InMemoryEventBus()
    log = event_log(bus, "ws")
    for i in range(20):
        bus.publish("ws", "run.file", {"run_id": "r1", "i": i})
    bus.publish("ws", "run.done", {"run_id": "r1"})
    time.sleep(0.1)
    log.stop()
    assert len(log.of("run.file")) == 20 and len(log.of("run.done")) == 1
