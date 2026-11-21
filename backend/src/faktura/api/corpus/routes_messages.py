"""Вопросы внутри разбора: агент (CORE_API §4j, §15.9) и история чатов (§4k). Только владелец разбора.

GET/POST /runs/{id}/messages (?thread= / thread_id), GET /runs/{id}/messages/{mid}/stream, POST .../stop,
PUT .../rating, GET /runs/{id}/suggestions; чаты: GET/POST /runs/{id}/threads, GET/PATCH/DELETE /runs/{id}/threads/{tid}.
Без thread_id вопрос идёт в текущий чат (последний), "new" - в новый.
"""
from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from ...agent import service, store
from ...ask import MAX_QUESTION
from ...ask.context import load_ctx
from ...domain.entities import CorpusFileStatus, CurrentUser
from ...domain.errors import AppError
from ...domain.services import Services
from .. import API_PREFIX
from ..base import auth
from ..base.deps import JSON_MEDIA_TYPE, current_user, get_context, get_services, sse_user
from ..base.serializers import iso
from .run_sse import HEADERS as SSE_HEADERS
from .views import _file_ids, get_corpus

router = APIRouter(tags=["runs"])
STALE_S = 180  # «пишется» дольше - считаем брошенным (рабочий поток умер вместе с процессом)
PING_S = 15.0


class MessageBody(BaseModel):
    text: str = ""
    pid: str | None = None  # статья, которую открыл пользователь: «эта статья» = она
    thread_id: str | None = None  # чат: None - текущий, "new" - новый


class ThreadBody(BaseModel):
    title: str | None = None


THREAD_TITLE_MAX = 200


def _title(raw: str | None, *, required: bool) -> str | None:
    t = " ".join((raw or "").split())
    if not t:
        if required:
            raise AppError("validation_error", "Введите название чата", field="title")
        return None
    if len(t) > THREAD_TITLE_MAX:
        raise AppError("validation_error", f"Название длиннее {THREAD_TITLE_MAX} символов", field="title")
    return t


def resolve_thread(uow, corpus, user: CurrentUser, thread_id: str | None) -> dict:
    """Чат разбора для вопроса: None - текущий, "new" - новый, иначе по id (нет в этом разборе -> 404)."""
    t = store.resolve_thread(uow, corpus.id, user.id, thread_id)
    if t is None:
        raise AppError("not_found", "Чат не найден")
    return t


def _existing_thread(uow, corpus, user: CurrentUser, thread_id: str | None) -> dict | None:
    """Для чтения: чат по id (404) или текущий без создания нового (None - чатов ещё нет)."""
    if thread_id:
        store.adopt_legacy(uow, corpus.id, user.id)
        t = store.get_thread(uow, corpus.id, thread_id)
        if t is None:
            raise AppError("not_found", "Чат не найден")
        return t
    ts = store.list_threads(uow, corpus.id, user.id)
    return ts[0] if ts else None


class RatingBody(BaseModel):
    rating: str | None = None
    reason: str | None = None


def _messages(uow, corpus, thread: dict | None) -> list[dict]:
    """Системные files и run (синтезируются из разбора) + вопросы и ответы чата."""
    files = [f for f in uow.files.list(corpus.id) if f.status != CorpusFileStatus.skipped_duplicate]
    ids = _file_ids(files)
    at = iso(corpus.created_at)
    head = [{"id": "m_files", "role": "user", "kind": "files", "files": [ids[f.id] for f in files], "at": at},
            {"id": "m_run", "role": "system", "kind": "run", "at": at}]
    if thread is None:
        return head
    return head + [store.message_view(m) for m in store.list_all(uow, corpus.id, thread["id"])]


@router.get("/runs/{run_id}/messages", summary="Сообщения чата разбора (?thread=, по умолчанию текущий)")
def get_messages(run_id: str, thread: str | None = None, user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        t = _existing_thread(uow, corpus, user, thread)
        return JSONResponse({"data": _messages(uow, corpus, t), "thread_id": t["id"] if t else None},
                            media_type=JSON_MEDIA_TYPE)


def check_pid(files, pid: str | None) -> str | None:
    """pid должен принадлежать разбору, иначе 422 (field=pid); пустой pid = нет контекста статьи."""
    pid = (pid or "").strip() or None
    if pid is not None and pid not in set(_file_ids(files).values()):
        raise AppError("validation_error", "Статья не из этого разбора", field="pid")
    return pid


def _start(request: Request, run_id: str, text: str, user: CurrentUser, services: Services,
           pid: str | None = None, thread_id: str | None = None) -> tuple[str, str, Any, str]:
    question = (text or "").strip()
    if not question:
        raise AppError("validation_error", "Введите вопрос", field="text")
    if len(question) > MAX_QUESTION:
        raise AppError("validation_error", f"Вопрос длиннее {MAX_QUESTION} символов", field="text")
    actx = get_context(request)
    actx.chat_limiter.hit(f"ask:{user.id}", now=auth.utcnow())
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        pid = check_pid([f for f in uow.files.list(corpus.id) if f.status != CorpusFileStatus.skipped_duplicate], pid)
        tid = resolve_thread(uow, corpus, user, thread_id)["id"]
        check_idle(uow, corpus.id, tid)
        q = store.add(uow, corpus.id, user.id, "user", "text", text=question, thread_id=tid)
        a = store.add(uow, corpus.id, user.id, "assistant", "answer", status="running", reply_to=q["id"], thread_id=tid)
        corpus_id = corpus.id
    stream = actx.agent_streams.create(a["id"])
    threading.Thread(target=service.answer_in_thread, args=(actx, corpus_id, a["id"], question, stream, pid, tid),
                     daemon=True, name=f"agent-{a['id']}").start()
    return q["id"], a["id"], stream, tid


def check_idle(uow, corpus_id: str, tid: str) -> None:
    """В чате уже пишется ответ -> 409 turn_busy; брошенные «пишется» (процесс умер) закрываются ошибкой."""
    rows = store.list_all(uow, corpus_id, tid)
    busy = [m for m in rows if m["role"] == "assistant" and m["status"] == "running"
            and auth.utcnow() - m["created_at"] < timedelta(seconds=STALE_S)]
    if busy:
        raise AppError("turn_busy", "Ответ ещё пишется — дождитесь или нажмите «Стоп»")
    for m in rows:
        if m["role"] == "assistant" and m["status"] == "running":
            store.finish(uow, m["id"], status="error", answer=None, clarify=False, meta={"error": "stale"}, tool_log=[])


async def _frames(request: Request, stream: store.Stream) -> AsyncIterator[str]:
    idx, idle = 0, 0.0
    while True:
        new = stream.read(idx)
        for fr in new:
            yield fr
        idx += len(new)
        if new:
            idle = 0.0
        if stream.done and not stream.read(idx):
            return
        await anyio.sleep(0.05)
        idle += 0.05
        if await request.is_disconnected():
            return  # агент дописывает ответ и без слушателя; остановка - только через /stop
        if idle >= PING_S:
            idle = 0.0
            yield ": ping\n\n"


@router.post("/runs/{run_id}/messages", summary="Вопрос по разбору (SSE или 202)")
def post_message(run_id: str, body: MessageBody, request: Request, user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    """`{text, pid?, thread_id?}` -> SSE (`Accept: text/event-stream`, чат - в заголовке X-Thread-Id): step, text, card,
    cite, suggestions, done | error; иначе `202 {message_id, question_id, thread_id, stream_url}`."""
    qid, mid, stream, tid = _start(request, run_id, body.text, user, services, body.pid, body.thread_id)
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(_frames(request, stream), media_type="text/event-stream",
                                 headers={**SSE_HEADERS, "X-Thread-Id": tid})
    return JSONResponse({"message_id": mid, "question_id": qid, "thread_id": tid,
                         "stream_url": f"{API_PREFIX}/runs/{run_id}/messages/{mid}/stream"}, status_code=202,
                        media_type=JSON_MEDIA_TYPE)


def _replay(m: dict) -> store.Stream:
    """Поток из сохранённого ответа (после рестарта или вытеснения из памяти)."""
    s = store.Stream()
    if m["status"] == "done" and m.get("answer"):
        service.emit_answer(s.emit, m["id"], m["answer"], bool(m.get("clarify")))
    elif m["status"] == "stopped":
        s.emit("done", {"message_id": m["id"], "status": "stopped", "answer": m.get("answer") or service.STOPPED})
    else:
        s.emit("error", {"code": "agent_error", "message": "Ответ не получен", "retryable": True})
    return s


@router.get("/runs/{run_id}/messages/{message_id}/stream", summary="SSE ответа (для 202)")
async def message_stream(run_id: str, message_id: str, request: Request, user: CurrentUser = Depends(sse_user),
                         services: Services = Depends(get_services)):
    """Токен - Bearer или ?access_token=. Поток воспроизводится с начала (id событий с 1)."""
    def load() -> dict:
        with services.db.uow() as uow:
            corpus = get_corpus(uow, user, run_id)
            m = store.get(uow, corpus.id, message_id)
            if m is None or m["role"] != "assistant":
                raise AppError("not_found", "Сообщение не найдено")
            return m

    m = await anyio.to_thread.run_sync(load)
    stream = get_context(request).agent_streams.get(message_id)
    if stream is None:
        stream = _replay(m)
    return StreamingResponse(_frames(request, stream), media_type="text/event-stream", headers=SSE_HEADERS)


def _assistant(uow, corpus, message_id: str) -> dict:
    m = store.get(uow, corpus.id, message_id)
    if m is None or m["role"] != "assistant":
        raise AppError("not_found", "Сообщение не найдено")
    return m


@router.post("/runs/{run_id}/messages/{message_id}/stop", summary="Остановить ответ")
def stop_message(run_id: str, message_id: str, user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    """Ответ в работе -> `stopped` (агент проверяет флаг между шагами). Уже завершённый - без изменений."""
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        _assistant(uow, corpus, message_id)
        store.stop(uow, corpus.id, message_id, service.STOPPED)
        return JSONResponse(store.message_view(_assistant(uow, corpus, message_id)), media_type=JSON_MEDIA_TYPE)


@router.put("/runs/{run_id}/messages/{message_id}/rating", summary="Оценка ответа")
def rate_message(run_id: str, message_id: str, body: RatingBody, user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    """`{rating: "up"|"down", reason?}`; `{rating: null}` снимает. Причина - только при `down`."""
    if body.rating is not None and body.rating not in store.RATINGS:
        raise AppError("validation_error", "rating: up, down или null", field="rating")
    if body.reason is not None and (body.rating != "down" or body.reason not in store.RATING_REASONS):
        raise AppError("validation_error", f"reason допустима только при down: {', '.join(store.RATING_REASONS)}",
                       field="reason")
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        _assistant(uow, corpus, message_id)
        store.set_rating(uow, corpus.id, message_id, body.rating, body.reason if body.rating else None)
        return JSONResponse(store.message_view(_assistant(uow, corpus, message_id)), media_type=JSON_MEDIA_TYPE)


@router.get("/runs/{run_id}/suggestions", summary="Подсказки вопросов (не повторяют заданные в чате)")
def get_suggestions(run_id: str, thread: str | None = None, user: CurrentUser = Depends(current_user),
                    services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        run = load_ctx(uow, corpus)
        t = _existing_thread(uow, corpus, user, thread)
        rows = store.list_all(uow, corpus.id, t["id"]) if t else []
        asked = [m["text"] or "" for m in rows if m["role"] == "user"]
    return JSONResponse({"data": service.fresh_suggestions(run, asked, 5)}, media_type=JSON_MEDIA_TYPE)


# --- история чатов (§4k) ------------------------------------------------------------------------------------

def _thread_full(uow, corpus, run_id: str, t: dict) -> dict:
    rows = store.list_all(uow, corpus.id, t["id"])
    out = store.thread_view(t, rows)
    run = store.running_in(rows)
    out["stream_url"] = f"{API_PREFIX}/runs/{run_id}/messages/{run['id']}/stream" if run else None
    out["messages"] = _messages(uow, corpus, t)
    return out


@router.get("/runs/{run_id}/threads", summary="История чатов разбора (свежие сверху)")
def list_threads(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        data = [store.thread_view(t, store.list_all(uow, corpus.id, t["id"]))
                for t in store.list_threads(uow, corpus.id, user.id)]
        return JSONResponse({"data": data}, media_type=JSON_MEDIA_TYPE)


@router.post("/runs/{run_id}/threads", status_code=201, summary="Новый чат")
def create_thread(run_id: str, body: ThreadBody | None = None, user: CurrentUser = Depends(current_user),
                  services: Services = Depends(get_services)):
    """`{title?}` -> чат; без названия его даст первый вопрос. Новый чат становится текущим."""
    title = _title(body.title if body else None, required=False)
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        t = store.create_thread(uow, corpus.id, user.id, title)
        return JSONResponse(store.thread_view(t, []), status_code=201, media_type=JSON_MEDIA_TYPE)


@router.get("/runs/{run_id}/threads/{thread_id}", summary="Чат целиком: сообщения и идущий ответ (возобновление)")
def get_thread(run_id: str, thread_id: str, user: CurrentUser = Depends(current_user),
               services: Services = Depends(get_services)):
    """Если ответ ещё пишется - `running_message_id` и `stream_url`: подключиться к потоку и дочитать его."""
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        t = _existing_thread(uow, corpus, user, thread_id)
        return JSONResponse(_thread_full(uow, corpus, run_id, t), media_type=JSON_MEDIA_TYPE)


@router.patch("/runs/{run_id}/threads/{thread_id}", summary="Переименовать чат")
def rename_thread(run_id: str, thread_id: str, body: ThreadBody, user: CurrentUser = Depends(current_user),
                  services: Services = Depends(get_services)):
    title = _title(body.title, required=True)
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        t = _existing_thread(uow, corpus, user, thread_id)
        store.rename_thread(uow, corpus.id, t["id"], title)
        t = store.get_thread(uow, corpus.id, t["id"])
        return JSONResponse(store.thread_view(t, store.list_all(uow, corpus.id, t["id"])), media_type=JSON_MEDIA_TYPE)


@router.delete("/runs/{run_id}/threads/{thread_id}", status_code=204, summary="Удалить чат")
def delete_thread(run_id: str, thread_id: str, user: CurrentUser = Depends(current_user),
                  services: Services = Depends(get_services)):
    """Идущий ответ останавливается, сообщения чата удаляются. Повторное удаление -> 404."""
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        t = _existing_thread(uow, corpus, user, thread_id)
        for m in store.list_all(uow, corpus.id, t["id"]):
            if m["role"] == "assistant" and m["status"] == "running":
                store.stop(uow, corpus.id, m["id"], service.STOPPED)
        store.delete_thread(uow, corpus.id, t["id"])
    return Response(status_code=204)


__all__ = ["router"]
