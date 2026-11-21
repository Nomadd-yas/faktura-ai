"""Профиль, коллекции, пользователи (админ), health (api_contract §4.1, §4.3, §4.15; BACKEND §2.2)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...domain.security import generate_password, hash_password, validate_password
from .collection_config import collections_payload
from .deps import JSON_MEDIA_TYPE, admin_user, current_user, get_context, get_services
from .routes_auth import check_email, read_json_object
from .serializers import user_out

log = logging.getLogger("faktura.api")
router = APIRouter()

COLORS = ("sapphire", "violet", "emerald", "amber", "rose", "graphite")
PROFILE_LIMITS = {"role": 80, "team": 80, "focus": 300}
HEALTH_RETRY_AFTER_S = 30


# --- health ------------------------------------------------------------------------------------

def _health_sync(request: Request) -> tuple[int, dict[str, Any]]:
    ctx = get_context(request)
    from ... import __version__

    body: dict[str, Any] = {"status": "ok", "version": __version__, "model_version": None, "db": "ok",
                            "storage": "ok", "ml": "ok", "ml_model_version": None}
    try:
        services = ctx.get_services()
    except Exception:  # noqa: BLE001 - health не падает
        log.exception("health: Services недоступны")
        body.update(status="error", db="error", storage="error", ml="degraded")
        return 503, body
    try:
        services.db.ping()
    except Exception as e:  # noqa: BLE001
        log.warning("health: БД недоступна: %s", type(e).__name__)
        body["db"] = "error"
    try:
        services.store.exists("_health/ping")
    except Exception as e:  # noqa: BLE001
        log.warning("health: хранилище недоступно: %s", type(e).__name__)
        body["storage"] = "error"
    try:
        h = services.extractor_factory().health()
        body["ml"] = "ok" if getattr(h, "status", None) == "ok" else "degraded"
        body["ml_model_version"] = body["model_version"] = getattr(h, "model_version", None)
    except Exception as e:  # noqa: BLE001
        log.warning("health: сервис извлечения недоступен: %s", type(e).__name__)
        body["ml"] = "degraded"
    if body["db"] == "error" or body["storage"] == "error":
        body["status"] = "error"
        return 503, body
    return 200, body


@router.get("/health", tags=["service"], summary="Состояние сервиса (без авторизации)")
async def health(request: Request) -> JSONResponse:
    status, body = await run_in_threadpool(_health_sync, request)
    headers = {"Cache-Control": "no-store"}
    if status == 503:
        headers["Retry-After"] = str(HEALTH_RETRY_AFTER_S)
    return JSONResponse(body, status_code=status, headers=headers, media_type=JSON_MEDIA_TYPE)


# --- /me ---------------------------------------------------------------------------------------

def _get_user(request: Request, user_id: str) -> Any:
    with get_services(request).db.uow() as uow:
        u = uow.users.get(user_id)
    if u is None:
        raise AppError("unauthorized", "Нужно войти")
    return u


@router.get("/me", tags=["profile"], summary="Профиль текущего пользователя")
def get_me(request: Request, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    return user_out(_get_user(request, user.id))


def _opt_text(data: dict[str, Any], key: str, limit: int) -> str | None:
    v = data[key]
    if v is None:
        return None
    if not isinstance(v, str):
        raise AppError("validation_error", f"Поле «{key}» должно быть строкой", field=key)
    v = v.strip()
    if len(v) > limit:
        raise AppError("validation_error", f"Поле «{key}» длиннее {limit} символов", field=key)
    return v or None


def _name(v: Any, *, field: str = "name") -> str:
    if not isinstance(v, str) or not v.strip():
        raise AppError("validation_error", "Поле «name» не может быть пустым", field=field)
    v = v.strip()
    if len(v) > 80:
        raise AppError("validation_error", "Имя длиннее 80 символов", field=field)
    return v


def profile_changes(data: dict[str, Any]) -> dict[str, Any]:
    """Проверка полей профиля (spec/03 User): name 1..80, role/team 0..80, focus 0..300, color - enum."""
    changes: dict[str, Any] = {}
    if "name" in data:
        changes["name"] = _name(data["name"])
    for key, limit in PROFILE_LIMITS.items():
        if key in data:
            changes[key] = _opt_text(data, key, limit)
    if "color" in data:
        c = data["color"]
        if c is None:
            c = "graphite"
        if not isinstance(c, str) or c not in COLORS:
            raise AppError("validation_error", "Цвет должен быть одним из: " + ", ".join(COLORS), field="color",
                           details={"allowed": list(COLORS)})
        changes["color"] = c
    return changes


@router.patch("/me", tags=["profile"], summary="Изменить свой профиль")
async def patch_me(request: Request, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    data = await read_json_object(request)
    if "email" in data:
        raise AppError("validation_error", "Почту изменить нельзя — обратитесь к администратору", field="email")
    if "is_admin" in data or "disabled" in data:
        field = "is_admin" if "is_admin" in data else "disabled"
        raise AppError("validation_error", "Права меняет только администратор", field=field)
    changes = profile_changes(data)

    def _apply() -> Any:
        with get_services(request).db.uow() as uow:
            if changes:
                return uow.users.update(user.id, **changes)
            return uow.users.get(user.id)

    return user_out(await run_in_threadpool(_apply))


# --- /collections ------------------------------------------------------------------------------

@router.get("/collections", tags=["profile"], summary="Конфигурация коллекций")
def get_collections(user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    return collections_payload()


# --- /admin/users ------------------------------------------------------------------------------

@router.get("/admin/users", tags=["admin"], summary="Пользователи рабочего пространства")
def list_users(request: Request, admin: CurrentUser = Depends(admin_user)) -> dict[str, Any]:
    with get_services(request).db.uow() as uow:
        users = uow.users.list(admin.workspace_id)
    return {"data": [user_out(u, admin=True) for u in users]}


def _bool(data: dict[str, Any], key: str) -> bool:
    v = data[key]
    if not isinstance(v, bool):
        raise AppError("validation_error", f"Поле «{key}» должно быть true или false", field=key)
    return v


@router.post("/admin/users", status_code=201, tags=["admin"], summary="Создать пользователя")
async def create_user(request: Request, admin: CurrentUser = Depends(admin_user)) -> JSONResponse:
    data = await read_json_object(request)
    email = check_email(data.get("email"))
    generate = data.get("generate") is True
    if generate:
        password = generate_password()
    else:
        password = data.get("password")
        if password is None:
            raise AppError("validation_error", "Задайте пароль или generate: true", field="password")
        if not isinstance(password, str):
            raise AppError("validation_error", "Пароль должен быть строкой", field="password")
        validate_password(password)
    fields: dict[str, Any] = {}
    if data.get("name") is not None:
        fields["name"] = _name(data["name"])
    else:
        fields["name"] = email.split("@", 1)[0]
    fields.update(profile_changes({k: data[k] for k in ("role", "team", "focus", "color") if k in data}))
    is_admin = _bool(data, "is_admin") if "is_admin" in data else False
    pw_hash = await run_in_threadpool(hash_password, password)

    def _create() -> Any:
        with get_services(request).db.uow() as uow:
            if uow.users.get_by_email(email) is not None:
                raise AppError("email_taken", field="email")
            return uow.users.create(workspace_id=admin.workspace_id, email=email, password_hash=pw_hash,
                                    is_admin=is_admin, disabled=False, **fields)

    created = await run_in_threadpool(_create)
    body: dict[str, Any] = {"user": user_out(created, admin=True)}
    if generate:
        body["password"] = password  # показывается один раз, нигде не хранится
    return JSONResponse(body, status_code=201, media_type=JSON_MEDIA_TYPE, headers={"Cache-Control": "no-store"})


@router.patch("/admin/users/{user_id}", tags=["admin"], summary="Изменить пользователя")
async def patch_user(user_id: str, request: Request, admin: CurrentUser = Depends(admin_user)) -> dict[str, Any]:
    data = await read_json_object(request)
    if "email" in data:
        raise AppError("validation_error", "Почту изменить нельзя", field="email")
    changes: dict[str, Any] = {}
    if "name" in data:
        changes["name"] = _name(data["name"])
    changes.update(profile_changes({k: data[k] for k in ("role", "team", "focus", "color") if k in data}))
    for key in ("disabled", "is_admin"):
        if key in data:
            changes[key] = _bool(data, key)
    password = data.get("password") if "password" in data else None
    if "password" in data:
        if not isinstance(password, str):
            raise AppError("validation_error", "Пароль должен быть строкой", field="password")
        validate_password(password)
        changes["password_hash"] = await run_in_threadpool(hash_password, password)

    def _update() -> Any:
        with get_services(request).db.uow() as uow:
            target = uow.users.get(user_id)
            if target is None or target.workspace_id != admin.workspace_id:
                raise AppError("not_found", "Пользователь не найден")
            loses_admin = target.is_admin and not target.disabled and (
                changes.get("disabled") is True or changes.get("is_admin") is False)
            if loses_admin and uow.users.count_admins(admin.workspace_id) <= 1:
                raise AppError("conflict", "Нельзя отключить или разжаловать последнего администратора")
            updated = uow.users.update(user_id, **changes) if changes else target
            if changes.get("disabled") is True or "password_hash" in changes:
                uow.sessions.revoke_all_for_user(user_id)
            return updated

    return user_out(await run_in_threadpool(_update), admin=True)
