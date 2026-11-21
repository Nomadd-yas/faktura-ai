"""Вопросы внутри разбора: агент (CORE_API §4j, §15.9). Только владелец разбора; чата вне разбора нет.

GET/POST /runs/{id}/messages, GET /runs/{id}/messages/{mid}/stream, POST .../stop, PUT .../rating, GET /runs/{id}/suggestions.
"""
from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
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


class RatingBody(BaseModel):
    rating: str | None = None
    reason: str | None = None


def _messages(uow, corpus) -> list[dict]:
    """Системные files и run (синтезируются из разбора) + сохранённые вопросы и ответы."""
    files = [f for f in uow.files.list(corpus.id) if f.status != CorpusFileStatus.skipped_duplicate]
    ids = _file_ids(files)
    at = iso(corpus.created_at)
    head = [{"id": "m_files", "role": "user", "kind": "files", "files": [ids[f.id] for f in files], "at": at},
            {"id": "m_run", "role": "system", "kind": "run", "at": at}]
    return head + [store.message_view(m) for m in store.list_all(uow, corpus.id)]


@router.get("/runs/{run_id}/messages", summary="Сообщения ветки разбора")
def get_messages(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        return JSONResponse({"data": _messages(uow, get_corpus(uow, user, run_id))}, media_type=JSON_MEDIA_TYPE)


def check_pid(files, pid: str | None) -> str | None:
    """pid должен принадлежать разбору, иначе 422 (field=pid); пустой pid = нет контекста статьи."""
    pid = (pid or "").strip() or None
    if pid is not None and pid not in set(_file_ids(files).values()):
        raise AppError("validation_error", "Статья не из этого разбора", field="pid")
    return pid


def _start(request: Request, run_id: str, text: str, user: CurrentUser, services: Services,
           pid: str | None = None) -> tuple[str, str, Any]:
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
        busy = [m for m in store.list_all(uow, corpus.id) if m["role"] == "assistant" and m["status"] == "running"
                and auth.utcnow() - m["created_at"] < timedelta(seconds=STALE_S)]
        if busy:
            raise AppError("turn_busy", "Ответ ещё пишется — дождитесь или нажмите «Стоп»")
        for m in store.list_all(uow, corpus.id):  # брошенные «пишется» закрываем
            if m["role"] == "assistant" and m["status"] == "running":
                store.finish(uow, m["id"], status="error", answer=None, clarify=False, meta={"error": "stale"},
                             tool_log=[])
        q = store.add(uow, corpus.id, user.id, "user", "text", text=question)
        a = store.add(uow, corpus.id, user.id, "assistant", "answer", status="running", reply_to=q["id"])
        corpus_id = corpus.id
    stream = actx.agent_streams.create(a["id"])
    threading.Thread(target=service.answer_in_thread, args=(actx, corpus_id, a["id"], question, stream, pid), daemon=True,
                     name=f"agent-{a['id']}").start()
    return q["id"], a["id"], stream


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
    """`{text, pid?}` -> SSE (`Accept: text/event-stream`): step, text, card, cite, suggestions, done | error;
    иначе `202 {message_id, question_id, stream_url}`."""
    qid, mid, stream = _start(request, run_id, body.text, user, services, body.pid)
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(_frames(request, stream), media_type="text/event-stream", headers=SSE_HEADERS)
    return JSONResponse({"message_id": mid, "question_id": qid,
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


@router.get("/runs/{run_id}/suggestions", summary="Подсказки вопросов (не повторяют заданные)")
def get_suggestions(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        run = load_ctx(uow, corpus)
        asked = [m["text"] or "" for m in store.list_all(uow, corpus.id) if m["role"] == "user"]
    return JSONResponse({"data": service.fresh_suggestions(run, asked, 5)}, media_type=JSON_MEDIA_TYPE)


__all__ = ["router"]
