"""Исполнители и шина событий (план 5.6, этап P1; api_contract §4.13, У-23).

* `InlineExecutor` - выполняет задачу сразу в вызывающем потоке (CLI, тесты).
* `ThreadExecutor` - пул фоновых потоков (API); исключение задачи пишется в лог и не роняет пул.
* `InMemoryEventBus` - кольцевой буфер событий на рабочее пространство; id - сквозной счётчик по возрастанию;
  `run.progress` - не чаще раза в секунду на прогон (лишние отбрасываются); подписка с `Last-Event-ID`
  догоняет пропущенное, а если история уже вытеснена из буфера - первым приходит `stream.reset`.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterator

log = logging.getLogger(__name__)

PROGRESS_EVENT = "run.progress"
RESET_EVENT = "stream.reset"


class InlineExecutor:
    """Выполняет сразу, синхронно (CLI, тесты). Исключение задачи уходит вызывающему."""

    def submit(self, fn: Callable[[], None]) -> None:
        fn()


class ThreadExecutor:
    """Фоновые потоки (API): не больше max_workers задач одновременно, остальные ждут в очереди."""

    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max(1, int(max_workers))
        self._pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="faktura-run")

    def submit(self, fn: Callable[[], None]) -> None:
        def task() -> None:
            try:
                fn()
            except Exception:  # noqa: BLE001 - фоновая задача не должна ронять пул
                log.exception("Фоновая задача завершилась с ошибкой")

        self._pool.submit(task)

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


def _parse_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


class _Space:
    """Буфер одного рабочего пространства."""

    __slots__ = ("events", "evicted_upto")

    def __init__(self, size: int) -> None:
        self.events: deque[tuple[int, str, dict]] = deque()
        self.evicted_upto = 0  # id последнего вытесненного события (0 - ничего не терялось)


class EventSubscription:
    """Подписка на события рабочего пространства: итератор троек (event_id, event, data).

    Без `last_event_id` приходят только новые события. С `last_event_id` - сначала пропущенные из буфера;
    если часть истории вытеснена, первым идёт `stream.reset {"reason": "history_lost"}`.
    `get(timeout)` - неблокирующее чтение для SSE с пингами; `close()` завершает итерацию из любого потока.
    """

    def __init__(self, bus: "InMemoryEventBus", workspace_id: str, last_event_id: str | None) -> None:
        self._bus = bus
        self._ws = workspace_id
        self._closed = False
        self._pending: deque[tuple[str, str, dict]] = deque()
        with bus._cond:
            sp = bus._space(workspace_id)
            last = _parse_id(last_event_id)
            if last is None:
                self._cursor = bus._seq
            else:
                if last < sp.evicted_upto:
                    self._pending.append((str(sp.evicted_upto), RESET_EVENT, {"reason": "history_lost"}))
                self._cursor = last

    @property
    def closed(self) -> bool:
        return self._closed

    def _pull(self) -> None:
        sp = self._bus._space(self._ws)
        for eid, name, data in sp.events:
            if eid > self._cursor:
                self._pending.append((str(eid), name, data))
                self._cursor = eid

    def get(self, timeout: float | None = None) -> tuple[str, str, dict] | None:
        """Следующее событие; None - истёк timeout или подписка закрыта."""
        cond = self._bus._cond
        with cond:
            while True:
                if self._pending:
                    return self._pending.popleft()
                if self._closed:
                    return None
                self._pull()
                if self._pending:
                    continue
                if not cond.wait(timeout):
                    self._pull()
                    return self._pending.popleft() if self._pending and not self._closed else None

    def __iter__(self) -> Iterator[tuple[str, str, dict]]:
        while not self._closed:
            ev = self.get(timeout=0.5)
            if ev is not None:
                yield ev

    def close(self) -> None:
        with self._bus._cond:
            self._closed = True
            self._bus._cond.notify_all()


class InMemoryEventBus:
    """Кольцевой буфер на рабочее пространство, id по возрастанию; run.progress не чаще 1/с на прогон."""

    def __init__(self, buffer: int = 1000, progress_interval_s: float = 1.0) -> None:
        self.buffer = max(1, int(buffer))
        self.progress_interval_s = float(progress_interval_s)
        self._cond = threading.Condition(threading.Lock())
        self._seq = 0
        self._spaces: dict[str, _Space] = {}
        self._last_progress: dict[tuple[str, str], float] = {}

    def _space(self, workspace_id: str) -> _Space:
        sp = self._spaces.get(workspace_id)
        if sp is None:
            sp = self._spaces[workspace_id] = _Space(self.buffer)
        return sp

    def publish(self, workspace_id: str, event: str, data: dict) -> str:
        """Возвращает id события. Отброшенный по частоте `run.progress` получает id последнего события."""
        payload = dict(data or {})
        with self._cond:
            if event == PROGRESS_EVENT:
                key = (workspace_id, str(payload.get("run_id")))
                now = time.monotonic()
                last = self._last_progress.get(key)
                if last is not None and now - last < self.progress_interval_s:
                    return str(self._seq)
                self._last_progress[key] = now
            self._seq += 1
            eid = self._seq
            sp = self._space(workspace_id)
            sp.events.append((eid, event, payload))
            while len(sp.events) > self.buffer:
                sp.evicted_upto = sp.events.popleft()[0]
            self._cond.notify_all()
            return str(eid)

    def subscribe(self, workspace_id: str, last_event_id: str | None) -> EventSubscription:
        return EventSubscription(self, workspace_id, last_event_id)

    def last_id(self) -> str:
        with self._cond:
            return str(self._seq)
