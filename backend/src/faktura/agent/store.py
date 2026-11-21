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
TH = S.run_threads
TITLE_MAX = 80
CURRENCY = "RUB"  # RouterAI присылает usage.cost в рублях
RATINGS = ("up", "down")
RATING_REASONS = ("Неверная цифра", "Не тот источник", "Неполный ответ")


def add(uow, corpus_id: str, user_id: str | None, role: str, kind: str, *, text: str | None = None,
        status: str = "done", reply_to: str | None = None, thread_id: str | None = None,
        source: str = "messages") -> dict:
    mid = new_id("msg")
    now = auth.utcnow()
    uow.conn.execute(sa.insert(T).values(
        id=mid, corpus_id=corpus_id, user_id=user_id, role=role, kind=kind, text=text, status=status, clarify=False,
        reply_to=reply_to, created_at=now, thread_id=thread_id, source=source))
    if thread_id is not None:
        touch_thread(uow, thread_id, now, question=text if role == "user" else None)
    return get(uow, corpus_id, mid)


def _row(r) -> dict:
    return dict(r._mapping)


def get(uow, corpus_id: str, mid: str) -> dict | None:
    r = uow.conn.execute(sa.select(T).where(T.c.id == mid, T.c.corpus_id == corpus_id)).first()
    return _row(r) if r else None


def list_all(uow, corpus_id: str, thread_id: str | None = None) -> list[dict]:
    """Сообщения разбора; с `thread_id` - только этого чата."""
    q = sa.select(T).where(T.c.corpus_id == corpus_id)
    if thread_id is not None:
        q = q.where(T.c.thread_id == thread_id)
    return [_row(r) for r in uow.conn.execute(q.order_by(T.c.seq)).all()]


# --- чаты (история; к любому можно вернуться) -----------------------------------------------------------------

def auto_title(question: str) -> str:
    t = " ".join((question or "").split())
    return t if len(t) <= TITLE_MAX else t[:TITLE_MAX - 1].rstrip() + "…"


def create_thread(uow, corpus_id: str, user_id: str | None, title: str | None = None, *, at=None) -> dict:
    tid = new_id("thr")
    now = at or auth.utcnow()
    title = (title or "").strip() or None
    uow.conn.execute(sa.insert(TH).values(id=tid, corpus_id=corpus_id, user_id=user_id, title=title,
                                          title_auto=title is None, created_at=now, updated_at=now))
    return get_thread(uow, corpus_id, tid)


def get_thread(uow, corpus_id: str, tid: str) -> dict | None:
    r = uow.conn.execute(sa.select(TH).where(TH.c.id == tid, TH.c.corpus_id == corpus_id)).first()
    return _row(r) if r else None


def touch_thread(uow, tid: str, at, *, question: str | None = None) -> None:
    """Время последнего сообщения; первый вопрос даёт имя чату, если пользователь его не задал."""
    uow.conn.execute(sa.update(TH).where(TH.c.id == tid).values(updated_at=at))
    if question:
        uow.conn.execute(sa.update(TH).where(TH.c.id == tid, TH.c.title_auto.is_(True), TH.c.title.is_(None))
                         .values(title=auto_title(question)))


def rename_thread(uow, corpus_id: str, tid: str, title: str) -> None:
    uow.conn.execute(sa.update(TH).where(TH.c.id == tid, TH.c.corpus_id == corpus_id)
                     .values(title=title, title_auto=False))


def delete_thread(uow, corpus_id: str, tid: str) -> None:
    uow.conn.execute(sa.delete(T).where(T.c.corpus_id == corpus_id, T.c.thread_id == tid))
    uow.conn.execute(sa.delete(TH).where(TH.c.id == tid, TH.c.corpus_id == corpus_id))


def adopt_legacy(uow, corpus_id: str, user_id: str | None) -> None:
    """Сообщения, записанные до появления чатов (thread_id NULL), собираются в один чат. Идемпотентно."""
    rows = [r for r in list_all(uow, corpus_id) if r.get("thread_id") is None]
    if not rows:
        return
    t = create_thread(uow, corpus_id, user_id or rows[0].get("user_id"), at=rows[0]["created_at"])
    uow.conn.execute(sa.update(T).where(T.c.corpus_id == corpus_id, T.c.thread_id.is_(None)).values(thread_id=t["id"]))
    first_q = next((r["text"] for r in rows if r["role"] == "user" and r.get("text")), None)
    uow.conn.execute(sa.update(TH).where(TH.c.id == t["id"]).values(
        updated_at=rows[-1]["created_at"], title=auto_title(first_q) if first_q else None))


def list_threads(uow, corpus_id: str, user_id: str | None = None) -> list[dict]:
    """Чаты разбора, свежие сверху (по последнему сообщению)."""
    adopt_legacy(uow, corpus_id, user_id)
    rows = uow.conn.execute(sa.select(TH).where(TH.c.corpus_id == corpus_id)
                            .order_by(TH.c.updated_at.desc(), TH.c.seq.desc())).all()
    return [_row(r) for r in rows]


def current_thread(uow, corpus_id: str, user_id: str | None) -> dict:
    """Текущий чат = последний по времени; нет ни одного - создаётся."""
    ts = list_threads(uow, corpus_id, user_id)
    return ts[0] if ts else create_thread(uow, corpus_id, user_id)


def resolve_thread(uow, corpus_id: str, user_id: str | None, thread_id: str | None) -> dict:
    """None - текущий чат, "new" - новый, иначе чат по id (чужой разбор / нет такого -> None у вызывающего)."""
    if thread_id is None or not str(thread_id).strip():
        return current_thread(uow, corpus_id, user_id)
    if thread_id == "new":
        return create_thread(uow, corpus_id, user_id)
    adopt_legacy(uow, corpus_id, user_id)
    return get_thread(uow, corpus_id, thread_id)


def running_in(rows: list[dict]) -> dict | None:
    return next((m for m in reversed(rows) if m["role"] == "assistant" and m["status"] == "running"), None)


def thread_view(t: dict, rows: list[dict]) -> dict:
    """Чат в списке истории: имя, даты, число вопросов, последнее сообщение, идёт ли ответ."""
    last = rows[-1] if rows else None
    preview = None
    if last is not None:
        txt = last["text"] if last["role"] == "user" else ((last.get("answer") or {}).get("text") or "")
        preview = {"role": last["role"], "text": auto_title(txt), "at": iso(last["created_at"])}
    run = running_in(rows)
    return {"id": t["id"], "title": t["title"] or "Новый чат", "created_at": iso(t["created_at"]),
            "updated_at": iso(t["updated_at"]), "questions": sum(1 for m in rows if m["role"] == "user"),
            "last_message": preview, "running_message_id": run["id"] if run else None}


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
    out: dict[str, Any] = {"id": m["id"], "role": m["role"], "kind": m["kind"], "at": iso(m["created_at"]),
                           "thread_id": m.get("thread_id")}
    if m["role"] == "user":
        out["text"] = m["text"] or ""
        return out
    out["status"] = m["status"]
    out["source"] = m.get("source") or "messages"  # ask: answer в форме CaseAnswer (/ask), messages - §15.9
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
