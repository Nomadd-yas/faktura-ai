"""SSE (GET /events) и одноразовые токены для EventSource (план 5.8)."""
from __future__ import annotations

from typing import AsyncIterator

from ...domain.entities import CurrentUser
from ...domain.ports import EventBus


async def sse_stream(bus: EventBus, workspace_id: str, last_event_id: str | None, *, ping_s: float = 25,
                     max_events: int | None = None, idle_timeout_s: float | None = None) -> AsyncIterator[str]:
    raise NotImplementedError("api.corpus.sse_stream")
    yield ""  # pragma: no cover


class EventTokens:
    def issue(self, user: CurrentUser) -> str:
        raise NotImplementedError("api.corpus.EventTokens.issue")

    def consume(self, token: str) -> CurrentUser | None:
        raise NotImplementedError("api.corpus.EventTokens.consume")
