"""Словарь нормализации (CORE_API §4h): ``GET /dictionary`` и свои синонимы параметров, материалов и условий.

Свои синонимы хранятся по рабочей области (таблица ``dictionary_synonyms``), видны всем её пользователям.
Применяются к новым разборам и перезапускам (``pipeline/processor.py``), старые не пересчитываются.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...norm import catalog
from ...norm.user_dict import version_suffix
from .collection_config import COLLECTION_IDS, get_param
from .deps import current_user, get_services, mutation_response
from .routes_auth import read_json_object
from .serializers import iso

router = APIRouter()

MAX_TEXT = 200


def _collection(value: str | None) -> str:
    c = value or "solar"
    if c not in COLLECTION_IDS:
        raise AppError("validation_error", "Неизвестная коллекция", field="collection",
                       details={"allowed": list(COLLECTION_IDS)})
    return c


def _text(data: dict[str, Any]) -> str:
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise AppError("validation_error", "Введите написание", field="text")
    text = " ".join(text.split())
    if len(text) > MAX_TEXT:
        raise AppError("validation_error", f"Написание длиннее {MAX_TEXT} символов", field="text")
    return text


def _out(row: Any, **extra: Any) -> dict[str, Any]:
    return {"id": row.id, "text": row.text, "custom": True, "collection": row.collection, "param": row.param,
            **extra, "created_at": iso(row.created_at)}


def _record(uow: Any, user: CurrentUser, action: str, label: str, row: Any) -> Any:
    return uow.actions.record(user, action, label, [{"collection": row.collection, "param": row.param,
                                                     "text": row.text}], undoable=False)


def _add(request: Request, user: CurrentUser, collection: str, param: str, text: str, taken: set[str],
         label: str, **extra: Any) -> JSONResponse:
    with get_services(request).db.uow() as uow:
        mine = uow.synonyms.list(user.workspace_id, collection)
        if text.casefold() in taken or any(m.text.casefold() == text.casefold() for m in mine):
            raise AppError("synonym_exists", field="text")
        row = uow.synonyms.add(user.workspace_id, collection, param, text, created_by=user.id)
        action = _record(uow, user, "dictionary.synonym_add", f"{label}: добавлено «{text}»", row)
    return mutation_response({"synonym": _out(row, **extra), "renormalize": {"papers": 0, "job_id": None}},
                             action, status=201)


def _remove(request: Request, user: CurrentUser, collection: str, param: str, text: str, builtin: set[str],
            label: str) -> JSONResponse:
    low = text.strip().casefold()
    with get_services(request).db.uow() as uow:
        row = next((m for m in uow.synonyms.list(user.workspace_id, collection)
                    if m.param == param and m.text.casefold() == low), None)
        if row is None:
            if low in builtin:
                raise AppError("synonym_builtin")
            raise AppError("not_found", "Такого написания нет в словаре")
        uow.synonyms.remove(row.id)
        action = _record(uow, user, "dictionary.synonym_remove", f"{label}: удалено «{row.text}»", row)
    return mutation_response({"removed": {"id": row.id, "text": row.text, "param": param, "collection": collection},
                              "renormalize": {"papers": 0, "job_id": None}}, action)


@router.get("/dictionary", summary="Словарь нормализации коллекции")
def get_dictionary(request: Request, collection: str | None = None,
                   user: CurrentUser = Depends(current_user)) -> JSONResponse:
    coll = _collection(collection)
    services = get_services(request)
    with services.db.uow() as uow:
        custom = uow.synonyms.list(user.workspace_id)
    base = str(getattr(services.normalizer, "dictionary_version", "") or "")
    body = catalog.build_dictionary(services.normalizer, coll, custom, base + version_suffix(custom))
    return JSONResponse(body)


@router.post("/dictionary/params/{collection}/{param}/synonyms", summary="Добавить синоним параметра")
async def add_param_synonym(collection: str, param: str, request: Request,
                            user: CurrentUser = Depends(current_user)) -> JSONResponse:
    coll = _collection(collection)
    if get_param(coll, param) is None:
        raise AppError("not_found", "Параметр не найден")
    text = _text(await read_json_object(request))
    taken = catalog.param_taken(get_services(request).normalizer, coll)
    return _add(request, user, coll, param.lower(), text, taken, f"Словарь, параметр {param.lower()}")


@router.delete("/dictionary/params/{collection}/{param}/synonyms/{text}", summary="Удалить свой синоним параметра")
def delete_param_synonym(collection: str, param: str, text: str, request: Request,
                         user: CurrentUser = Depends(current_user)) -> JSONResponse:
    coll = _collection(collection)
    if get_param(coll, param) is None:
        raise AppError("not_found", "Параметр не найден")
    builtin = {s.casefold() for s in catalog.builtin_param_synonyms(get_services(request).normalizer, coll, param)}
    builtin.add(param.lower())
    return _remove(request, user, coll, param.lower(), text, builtin, f"Словарь, параметр {param.lower()}")


def _entry(entry_id: str) -> tuple[str, dict[str, Any], str]:
    found = catalog.find_entry(entry_id)
    if found is None:
        raise AppError("not_found", "Запись словаря не найдена")
    kind, entry = found
    return kind, entry, catalog.entry_param(kind, entry)


@router.post("/dictionary/entries/{entry_id}/synonyms", summary="Добавить синоним материала или условия")
async def add_entry_synonym(entry_id: str, request: Request,
                            user: CurrentUser = Depends(current_user)) -> JSONResponse:
    _, entry, param = _entry(entry_id)
    text = _text(await read_json_object(request))
    return _add(request, user, "solar", param, text, catalog.entry_taken(), f"Словарь, {entry['canon']}",
                entry_id=entry["id"])


@router.delete("/dictionary/entries/{entry_id}/synonyms/{text}", summary="Удалить свой синоним материала или условия")
def delete_entry_synonym(entry_id: str, text: str, request: Request,
                         user: CurrentUser = Depends(current_user)) -> JSONResponse:
    _, entry, param = _entry(entry_id)
    builtin = {entry["canon"].casefold(), *(s.casefold() for s in entry["synonyms"])}
    return _remove(request, user, "solar", param, text, builtin, f"Словарь, {entry['canon']}")
