"""Вопросы по разбору (CORE_API §4i, история §4k): POST /runs/{id}/ask, GET /runs/{id}/ask/suggestions.
Только владелец разбора. Вопрос и ответ записываются в чат (по умолчанию текущий), модель видит его прошлые обмены."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...ask import MAX_QUESTION
from ...ask.client import build_ask_client
from ...ask.context import load_ctx
from ...ask.execute import suggestions
from ...ask.service import answer_question
from ...agent import service as agent_service
from ...agent import store
from ...domain.entities import CorpusFileStatus, CurrentUser
from ...domain.errors import AppError
from ...domain.services import Services
from ..base import auth
from ..base.deps import JSON_MEDIA_TYPE, current_user, get_context, get_services
from .routes_messages import check_idle, check_pid, resolve_thread
from .views import get_corpus

log = logging.getLogger("faktura.api")
router = APIRouter(tags=["runs"])
MAX_PIDS = 500


class AskBody(BaseModel):
    question: str = ""
    pids: list[str] | None = None
    pid: str | None = None  # статья, которую открыл пользователь (контекст «эта статья»); не из разбора -> 422
    thread_id: str | None = None  # чат: None - текущий, "new" - новый, иначе id (§4k)


def _client(request: Request):
    ctx = get_context(request)
    if isinstance(ctx.ask_client, str):  # "auto": один раз из настроек
        ctx.ask_client = build_ask_client(ctx.settings)
    return ctx.ask_client


@router.post("/runs/{run_id}/ask", summary="Вопрос по разбору")
def ask_run(run_id: str, body: AskBody, request: Request, user: CurrentUser = Depends(current_user),
            services: Services = Depends(get_services)):
    """`{question, pids?, pid?, thread_id?}` -> CaseAnswer `{text, cards, cites, suggestions, meta, thread_id, message_id,
    question_id}`. Без ML отвечает по правилам. Вопрос и ответ сохраняются в чате (история, возобновление)."""
    question = (body.question or "").strip()
    if not question:
        raise AppError("validation_error", "Введите вопрос", field="question")
    if len(question) > MAX_QUESTION:
        raise AppError("validation_error", f"Вопрос длиннее {MAX_QUESTION} символов", field="question")
    if body.pids is not None and len(body.pids) > MAX_PIDS:
        raise AppError("validation_error", "Слишком много статей в pids", field="pids")
    get_context(request).chat_limiter.hit(f"ask:{user.id}", now=auth.utcnow())
    only = {str(p) for p in body.pids} if body.pids else None
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        pid = check_pid([f for f in uow.files.list(corpus.id) if f.status != CorpusFileStatus.skipped_duplicate], body.pid)
        corpus_id = corpus.id
        tid = resolve_thread(uow, corpus, user, body.thread_id)["id"]
        check_idle(uow, corpus_id, tid)
    actx = get_context(request)
    # агент включён (как для /messages) -> ответ агентом в форме CaseAnswer, с историей чата
    try:
        res = agent_service.ask_via_agent(actx, corpus_id, question, pid, only, thread_id=tid)
    except Exception:  # noqa: BLE001 - любой сбой агента: запасной базовый ответ
        log.exception("Агент /ask упал, отвечаю базовым вариантом")
        res = None
    if res is None:
        with services.db.uow() as uow:
            run = load_ctx(uow, get_corpus(uow, user, run_id), only)
        # БД освобождена: вызовы ML (до ~30 с каждый) идут без открытой транзакции
        res = answer_question(run, question, _client(request))
    tool_log = res.pop("_tool_log", None) or []
    with services.db.uow() as uow:  # в историю чата: вопрос и ответ (как его показал фронт)
        q = store.add(uow, corpus_id, user.id, "user", "text", text=question, thread_id=tid, source="ask")
        a = store.add(uow, corpus_id, user.id, "assistant", "answer", status="running", reply_to=q["id"], thread_id=tid,
                      source="ask")
        meta = res.get("meta") or {}
        store.finish(uow, a["id"], status="done", answer=res, clarify=bool(res.get("clarify")), meta=meta, tool_log=tool_log,
                     cost=float(meta.get("cost") or 0.0), cost_currency=meta.get("cost_currency") or store.CURRENCY)
    return JSONResponse({**res, "thread_id": tid, "message_id": a["id"], "question_id": q["id"]},
                        media_type=JSON_MEDIA_TYPE)


@router.get("/runs/{run_id}/ask/suggestions", summary="Подсказки вопросов по разбору")
def ask_suggestions(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        run = load_ctx(uow, get_corpus(uow, user, run_id))
    return JSONResponse(suggestions(run), media_type=JSON_MEDIA_TYPE)
