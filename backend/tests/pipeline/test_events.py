"""InlineExecutor, ThreadExecutor, InMemoryEventBus (план 5.6, P1)."""
from __future__ import annotations

import threading
import time

import pytest

from faktura.domain.ports import EventBus, Executor
from faktura.pipeline import InlineExecutor, InMemoryEventBus, ThreadExecutor


# --- executors -------------------------------------------------------------------------------------------------













# --- bus: публикация и подписка ---------------------------------------------------------------------------------------











def test_last_event_id_replays_missed(event_log):
    bus = InMemoryEventBus()
    ids = [bus.publish("ws", "run.file", {"i": i}) for i in range(5)]
    log = event_log(bus, "ws", ids[1])
    time.sleep(0.1)
    log.stop()
    assert [e[2]["i"] for e in log.items] == [2, 3, 4]














# --- run.progress не чаще раза в секунду ----------------------------------------------------------------------------------





