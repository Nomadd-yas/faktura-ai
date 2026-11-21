"""Вопросы по разбору (CORE_API §4i): POST /runs/{id}/ask, GET /runs/{id}/ask/suggestions. Только владелец разбора."""
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
from ...domain.entities import CorpusFileStatus, CurrentUser
from ...domain.errors import AppError
from ...domain.services import Services
from ..base import auth
from ..base.deps import JSON_MEDIA_TYPE, current_user, get_context, get_services
from .routes_messages import check_pid
from .views import get_corpus

log = logging.getLogger("faktura.api")
router = APIRouter(tags=["runs"])
MAX_PIDS = 500


class AskBody(BaseModel):
    question: str = ""
    pids: list[str] | None = None
    pid: str | None = None  # статья, которую открыл пользователь (контекст «эта статья»); не из разбора -> 422


def _client(request: Request):
    ctx = get_context(request)
    if isinstance(ctx.ask_client, str):  # "auto": один раз из настроек
        ctx.ask_client = build_ask_client(ctx.settings)
    return ctx.ask_client


@router.post("/runs/{run_id}/ask", summary="Вопрос по разбору")
def ask_run(run_id: str, body: AskBody, request: Request, user: CurrentUser = Depends(current_user),
            services: Services = Depends(get_services)):
    """`{question, pids?}` -> CaseAnswer `{text, cards, cites, suggestions, meta}`. Без ML отвечает по правилам."""
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
    actx = get_context(request)
    # агент включён (как для /messages) -> ответ агентом в форме CaseAnswer; ветка сообщений не ведётся и не сохраняется
    try:
        res = agent_service.ask_via_agent(actx, corpus_id, question, pid, only)
    except Exception:  # noqa: BLE001 - любой сбой агента: запасной базовый ответ
        log.exception("Агент /ask упал, отвечаю базовым вариантом")
        res = None
    if res is not None:
        return JSONResponse(res, media_type=JSON_MEDIA_TYPE)
    with services.db.uow() as uow:
        run = load_ctx(uow, get_corpus(uow, user, run_id), only)
    # БД освобождена: вызовы ML (до ~30 с каждый) идут без открытой транзакции
    return JSONResponse(answer_question(run, question, _client(request)), media_type=JSON_MEDIA_TYPE)


@router.get("/runs/{run_id}/ask/suggestions", summary="Подсказки вопросов по разбору")
def ask_suggestions(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        run = load_ctx(uow, get_corpus(uow, user, run_id))
    return JSONResponse(suggestions(run), media_type=JSON_MEDIA_TYPE)
