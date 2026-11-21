"""Сообщения чата разбора в БД (таблица run_messages) и потоки событий в памяти."""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any

import sqlalchemy as sa

from ..api.base import auth
from ..api.base.serializers import iso
from ..domain.ids import new_id
from ..storage import schema as S

T = S.run_messages
CURRENCY = "RUB"  # RouterAI присылает usage.cost в рублях
RATINGS = ("up", "down")
RATING_REASONS = ("Неверная цифра", "Не тот источник", "Неполный ответ")


def add(uow, corpus_id: str, user_id: str | None, role: str, kind: str, *, text: str | None = None,
        status: str = "done", reply_to: str | None = None) -> dict:
    mid = new_id("msg")
    uow.conn.execute(sa.insert(T).values(
        id=mid, corpus_id=corpus_id, user_id=user_id, role=role, kind=kind, text=text, status=status, clarify=False,
        reply_to=reply_to, created_at=auth.utcnow()))
    return get(uow, corpus_id, mid)


def _row(r) -> dict:
    return dict(r._mapping)


def get(uow, corpus_id: str, mid: str) -> dict | None:
    r = uow.conn.execute(sa.select(T).where(T.c.id == mid, T.c.corpus_id == corpus_id)).first()
    return _row(r) if r else None


def list_all(uow, corpus_id: str) -> list[dict]:
    rows = uow.conn.execute(sa.select(T).where(T.c.corpus_id == corpus_id).order_by(T.c.seq)).all()
    return [_row(r) for r in rows]


def status_of(uow, mid: str) -> str | None:
    return uow.conn.execute(sa.select(T.c.status).where(T.c.id == mid)).scalar()


def finish(uow, mid: str, *, status: str, answer: dict | None, clarify: bool, meta: dict, tool_log: list,
           cost: float = 0.0, cost_currency: str = CURRENCY) -> bool:
    """Записывает итог ответа, если он ещё running (остановленный пользователем остаётся stopped). True - записано."""
    res = uow.conn.execute(sa.update(T).where(T.c.id == mid, T.c.status == "running").values(
        status=status, answer=answer, clarify=clarify, meta=meta, tool_log=tool_log, cost=cost,
        cost_currency=cost_currency))
    return res.rowcount > 0


def stop(uow, corpus_id: str, mid: str, answer: dict) -> bool:
    res = uow.conn.execute(sa.update(T).where(T.c.id == mid, T.c.corpus_id == corpus_id, T.c.status == "running")
                           .values(status="stopped", answer=answer))
    return res.rowcount > 0


def set_rating(uow, corpus_id: str, mid: str, rating: str | None, reason: str | None) -> None:
    uow.conn.execute(sa.update(T).where(T.c.id == mid, T.c.corpus_id == corpus_id)
                     .values(rating=rating, rating_reason=reason))


def message_view(m: dict) -> dict:
    """Сообщение в форме §15.9."""
    out: dict[str, Any] = {"id": m["id"], "role": m["role"], "kind": m["kind"], "at": iso(m["created_at"])}
    if m["role"] == "user":
        out["text"] = m["text"] or ""
        return out
    out["status"] = m["status"]
    if m.get("answer") is not None:
        out["answer"] = m["answer"]
    if m.get("clarify"):
        out["clarify"] = True
    out["rating"], out["rating_reason"] = m.get("rating"), m.get("rating_reason")
    return out


def thread_qa(rows: list[dict], exclude: str | None = None) -> list[tuple[str, str]]:
    """Пары (вопрос, текст ответа) завершённых обменов ветки: для истории модели и фильтра подсказок."""
    out, q = [], None
    for m in rows:
        if m["id"] == exclude:
            continue
        if m["role"] == "user":
            q = m["text"] or ""
        elif m["role"] == "assistant" and m["status"] == "done" and q is not None and m.get("answer"):
            out.append((q, (m["answer"] or {}).get("text") or ""))
            q = None
    return out


# --- потоки событий (в памяти процесса) ---------------------------------------------------------------------------

def frame(event: str, data: dict, eid: int) -> str:
    return f"id: {eid}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Stream:
    """События одного ответа: пишет рабочий поток, читают любые соединения (повторное подключение с начала)."""

    def __init__(self) -> None:
        self.frames: list[str] = []
        self.done = False
        self._lock = threading.Lock()

    def emit(self, event: str, data: dict) -> None:
        with self._lock:
            self.frames.append(frame(event, data, len(self.frames) + 1))
            if event in ("done", "error"):
                self.done = True

    def read(self, start: int) -> list[str]:
        with self._lock:
            return self.frames[start:]


class Streams:
    def __init__(self, keep: int = 200) -> None:
        self._d: OrderedDict[str, Stream] = OrderedDict()
        self._lock = threading.Lock()
        self.keep = keep

    def create(self, mid: str) -> Stream:
        with self._lock:
            s = self._d[mid] = Stream()
            while len(self._d) > self.keep:
                self._d.popitem(last=False)
            return s

    def get(self, mid: str) -> Stream | None:
        with self._lock:
            return self._d.get(mid)
