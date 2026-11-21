"""Заглушки P2 без другого владельца (api_contract §1.8, §4.14, У-15).

Форма ответа - как в контракте, данные пустые или нули, заголовок ``X-Faktura-Stub: 1``. Изменяющие заглушки
ничего не сохраняют (отдают переданные поля, ``id`` и ``action_id``). Объекты по id не находятся -> 404 (тоже со
``X-Faktura-Stub: 1``). Группы: ``/me/stats``, ``/collections/{id}/stats``, ``/views*``, ``/graph*``, ``/entities/*``,
``/search``, ``/projects*``, ``/chats*``, ``/turns*``, ``POST /dictionary/units`` (синонимы словаря - routes_dictionary.py).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...domain.ids import new_id
from .. import API_PREFIX
from . import auth
from .collection_config import COLLECTION_IDS, get_collection, get_param
from .deps import current_user, get_context, get_services, stub_response
from .errors import error_response
from .routes_auth import read_json_object
from .serializers import iso

router = APIRouter()

STUB_HEADER = {"X-Faktura-Stub": "1"}
GRAPH_CSV_HEADER = "source,target,type,source_label,target_label"
MAX_QUESTION = 2000
STUB_OUTRO = "Ответы в чате появятся позже"


# --- помощники ---------------------------------------------------------------------------------

def stub_not_found(request: Request, message: str) -> JSONResponse:
    return error_response("not_found", message, request=request, headers=dict(STUB_HEADER))


def stub_mutation(payload: dict[str, Any], *, status: int = 201) -> JSONResponse:
    action_id = new_id("act")
    return stub_response({**payload, "action_id": action_id}, status=status, headers={"X-Action-Id": action_id})


def _text(data: dict[str, Any], key: str, *, required: bool = False, default: str | None = None,
          limit: int = 500) -> str | None:
    v = data.get(key, default)
    if v is None:
        if required:
            raise AppError("validation_error", f"Поле «{key}» обязательно", field=key)
        return None
    if not isinstance(v, str):
        raise AppError("validation_error", f"Поле «{key}» должно быть строкой", field=key)
    if len(v) > limit:
        raise AppError("validation_error", f"Поле «{key}» длиннее {limit} символов", field=key)
    return v


def _collection(data: dict[str, Any], *, default: str | None = "solar") -> str | None:
    c = data.get("collection", default)
    if c is None:
        return None
    if not isinstance(c, str) or c not in COLLECTION_IDS:
        raise AppError("validation_error", "Неизвестная коллекция", field="collection",
                       details={"allowed": list(COLLECTION_IDS)})
    return c


def _now_iso() -> str | None:
    return iso(auth.utcnow())


# --- профиль и коллекции -----------------------------------------------------------------------

@router.get("/me/stats", tags=["stub"], summary="Сводка профиля (P2, заглушка)")
def me_stats(user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_response({"papers": 0, "mine": 0, "facts": 0, "accepted": 0, "edited": 0, "rejected": 0,
                          "issues": 0, "projects": 0, "questions": 0})


@router.get("/collections/{collection_id}/stats", tags=["stub"], summary="Сводка коллекции (P2, заглушка)")
def collection_stats(collection_id: str, request: Request, project_id: str | None = None,
                     user: CurrentUser = Depends(current_user)) -> JSONResponse:
    if get_collection(collection_id) is None:
        return stub_not_found(request, "Коллекция не найдена")
    return stub_response({"papers": 0, "facts": 0, "processing": 0, "open_issues": 0})


# --- сохранённые виды --------------------------------------------------------------------------

@router.get("/views", tags=["stub"], summary="Сохранённые виды (P2, заглушка)")
def list_views(collection: str | None = None, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_response({"data": []})


@router.post("/views", tags=["stub"], summary="Сохранить вид (P2, заглушка)")
async def create_view(request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    data = await read_json_object(request)
    name = _text(data, "name", required=True, limit=200)
    if not name or not name.strip():
        raise AppError("validation_error", "Поле «name» не может быть пустым", field="name")
    out: dict[str, Any] = {"name": name.strip()}
    if "query" in data:
        out["query"] = _text(data, "query", limit=2000)
    if "collection" in data:
        out["collection"] = _collection(data)
    out["id"] = new_id("view")
    return stub_mutation(out)


@router.patch("/views/{view_id}", tags=["stub"])
@router.delete("/views/{view_id}", tags=["stub"])
@router.post("/views/{view_id}/restore", tags=["stub"])
def view_by_id(view_id: str, request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_not_found(request, "Вид не найден")


# --- граф и сущности ---------------------------------------------------------------------------

@router.get("/graph", tags=["stub"], summary="Граф связей (P2, заглушка)")
def graph(collection: str | None = None, root: str | None = None, project_id: str | None = None,
          user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_response({"nodes": [], "edges": [], "default_root": None})


@router.get("/graph/edges.csv", tags=["stub"], summary="Рёбра графа в CSV (P2, заглушка)")
def graph_edges_csv(collection: str | None = None, root: str | None = None, project_id: str | None = None,
                    user: CurrentUser = Depends(current_user)) -> Response:
    return Response(GRAPH_CSV_HEADER + "\r\n", media_type="text/csv; charset=utf-8",
                    headers={**STUB_HEADER, "Content-Disposition": 'attachment; filename="graph_edges.csv"'})


def _material_entry(request: Request, entry_id: str) -> dict[str, Any] | None:
    """Материал словаря нормализатора по ``entry_id`` (ключ псевдонима или формула без регистра)."""
    try:
        snap = get_services(request).normalizer.dictionary()
    except Exception:  # noqa: BLE001 - у нормализатора может не быть словаря
        return None
    low = entry_id.strip().lower()
    for m in getattr(snap, "materials", []) or []:
        alias = str(m.get("alias") or "").lower()
        formula = str(m.get("formula") or "")
        if low and (low == alias or low == formula.lower()):
            return {"entry_id": entry_id, "canon": formula or alias, "name": formula or alias,
                    "synonyms": [s for s in (m.get("alias"), formula) if s]}
    return None


@router.get("/entities/{entry_id}/papers", tags=["stub"], summary="Где используется сущность (P2, заглушка)")
def entity_papers(entry_id: str, request: Request, project_id: str | None = None,
                  user: CurrentUser = Depends(current_user)) -> JSONResponse:
    entry = _material_entry(request, entry_id)
    if entry is None:
        return stub_not_found(request, "Сущность не найдена в словаре")
    return stub_response({"entity": {k: entry[k] for k in ("entry_id", "canon", "name")}, "data": []})


# --- поиск -------------------------------------------------------------------------------------

@router.get("/search", tags=["stub"], summary="Глобальный поиск (P2, заглушка)")
def search(q: str = "", scope: str = "all", limit: int | None = None, project_id: str | None = None,
           user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_response({"q": q, "layout_fixed": None, "groups": []})


# --- проекты -----------------------------------------------------------------------------------

@router.get("/projects", tags=["stub"], summary="Мои проекты (P2, заглушка)")
def list_projects(user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_response({"data": []})


@router.post("/projects", tags=["stub"], summary="Создать проект (P2, заглушка)")
async def create_project(request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    data = await read_json_object(request)
    name = (_text(data, "name", default="", limit=200) or "").strip() or "Новый проект"
    collection = _collection(data)
    note = _text(data, "note", default="", limit=2000) or ""
    now = _now_iso()
    return stub_mutation({"id": new_id("pr"), "name": name, "collection": collection, "note": note,
                          "pinned": False, "created_at": now, "updated_at": now, "chats_count": 0,
                          "papers_count": 0, "open_issues": 0})


@router.get("/projects/{project_id}", tags=["stub"])
@router.patch("/projects/{project_id}", tags=["stub"])
@router.delete("/projects/{project_id}", tags=["stub"])
@router.post("/projects/{project_id}/restore", tags=["stub"])
@router.get("/projects/{project_id}/suggestions", tags=["stub"])
def project_by_id(project_id: str, request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_not_found(request, "Проект не найден")


# --- чаты и реплики ----------------------------------------------------------------------------

@router.get("/chats", tags=["stub"], summary="Чаты (P2, заглушка)")
def list_chats(project_id: str | None = None, kind: str | None = None, pinned: bool | None = None,
               q: str | None = None, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_response({"data": [], "total": 0, "next_cursor": None})


def _sse(events: list[tuple[str, dict[str, Any]]]) -> Iterator[str]:
    for n, (event, data) in enumerate(events, start=1):
        yield f"event: {event}\nid: {n}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _turn_events(turn: dict[str, Any], *, with_chat: bool = False) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    if with_chat:
        events.append(("chat", turn["chat"]))
    events += [
        ("turn", {"turn_id": turn["turn_id"], "version": 1, "question": turn["question"],
                  "steps": ["Разбираю вопрос"]}),
        ("step", {"step": 1}),
        ("outro", {"text": STUB_OUTRO}),
        ("done", {"status": "done", "explore": "", "followups": []}),
    ]
    return events


def _stream(events: list[tuple[str, dict[str, Any]]], status: int = 200) -> StreamingResponse:
    return StreamingResponse(_sse(events), status_code=status, media_type="text/event-stream",
                             headers={**STUB_HEADER, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/chats/ask", tags=["stub"], summary="Новый вопрос (P2, заглушка)")
async def chats_ask(request: Request, user: CurrentUser = Depends(current_user)) -> Response:
    data = await read_json_object(request)
    question = data.get("question")
    if not isinstance(question, str) or not question.strip():
        raise AppError("validation_error", "Введите вопрос", field="question")
    if len(question) > MAX_QUESTION:
        raise AppError("validation_error", f"Вопрос длиннее {MAX_QUESTION} символов", field="question")
    collection = _collection(data)
    project_id = data.get("project_id")
    if project_id is not None:
        return stub_not_found(request, "Проект не найден")
    ctx = get_context(request)
    ctx.chat_limiter.hit(f"chat:{user.id}", now=auth.utcnow())
    now = _now_iso()
    chat = {"id": new_id("ch"), "kind": "ask", "project_id": None, "paper_id": None, "collection": collection,
            "title": question.strip()[:200], "pinned": False, "created_at": now, "updated_at": now,
            "turns_count": 1, "paper_status": None}
    turn_id = new_id("trn")
    turn = {"turn_id": turn_id, "question": question.strip(), "chat": chat, "user_id": user.id}
    ctx.remember_turn(turn_id, turn)
    if "text/event-stream" in request.headers.get("accept", ""):
        return _stream(_turn_events(turn, with_chat=True))
    return stub_response({"chat": chat, "turn_id": turn_id, "stream_url": f"{API_PREFIX}/turns/{turn_id}/stream",
                          "existing": False}, status=201)


@router.get("/chats/{chat_id}", tags=["stub"])
@router.patch("/chats/{chat_id}", tags=["stub"])
@router.delete("/chats/{chat_id}", tags=["stub"])
@router.post("/chats/{chat_id}/restore", tags=["stub"])
@router.get("/chats/{chat_id}/turns", tags=["stub"])
@router.post("/chats/{chat_id}/turns", tags=["stub"])
def chat_by_id(chat_id: str, request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    return stub_not_found(request, "Чат не найден")


def _known_turn(request: Request, turn_id: str, user: CurrentUser) -> dict[str, Any] | None:
    turn = get_context(request).stub_turns.get(turn_id)
    if turn is None or turn.get("user_id") != user.id:
        return None
    return turn


def _turn_version(turn: dict[str, Any]) -> dict[str, Any]:
    return {"version": 1, "question": turn["question"], "status": "done", "steps": ["Разбираю вопрос"], "step": 1,
            "generated_by": "stub", "answer": {"intro": "", "items": [], "outro": STUB_OUTRO, "cites": [],
                                               "card": None, "explore": "", "followups": []}}


@router.get("/turns/{turn_id}/stream", tags=["stub"], summary="Поток ответа (P2, заглушка)")
def turn_stream(turn_id: str, request: Request, user: CurrentUser = Depends(current_user)) -> Response:
    turn = _known_turn(request, turn_id, user)
    if turn is None:
        return stub_not_found(request, "Реплика не найдена")
    return _stream(_turn_events(turn))


@router.post("/turns/{turn_id}/stop", tags=["stub"])
def turn_stop(turn_id: str, request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    turn = _known_turn(request, turn_id, user)
    if turn is None:
        return stub_not_found(request, "Реплика не найдена")
    return stub_response(_turn_version(turn))


@router.post("/turns/{turn_id}/versions", tags=["stub"])
def turn_versions(turn_id: str, request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    if _known_turn(request, turn_id, user) is None:
        return stub_not_found(request, "Реплика не найдена")
    raise AppError("version_conflict", "Изменение вопроса появится вместе с чатом")


@router.put("/turns/{turn_id}/active-version", tags=["stub"])
@router.put("/turns/{turn_id}/rating", tags=["stub"])
def turn_update(turn_id: str, request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    turn = _known_turn(request, turn_id, user)
    if turn is None:
        return stub_not_found(request, "Реплика не найдена")
    return stub_response({"id": turn_id, "seq": 1, "active_version": 1, "rating": None, "rating_reason": None,
                          "versions": [_turn_version(turn)]})


# --- правка словаря ----------------------------------------------------------------------------

@router.post("/dictionary/units", tags=["stub"], summary="Своя единица (появится)")
async def add_unit(request: Request, user: CurrentUser = Depends(current_user)) -> JSONResponse:
    data = await read_json_object(request)
    collection = _collection(data)
    param = _text(data, "param", required=True, limit=100) or ""
    if get_param(collection or "solar", param) is None:
        raise AppError("validation_error", "Неизвестный параметр", field="param")
    unit = _text(data, "unit", required=True, limit=50)
    factor = data.get("factor")
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or factor <= 0:
        raise AppError("validation_error", "Множитель должен быть положительным числом", field="factor")
    return stub_mutation({"unit": {"collection": collection, "param": param.lower(), "unit": unit, "factor": factor,
                                   "custom": True}, "renormalize": {"papers": 0, "job_id": None}})
