"""Вход: POST /auth/login|refresh|logout|register, GET /auth/me (BACKEND §2.1-2.2, api_contract §4.2)."""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...domain.security import hash_password, validate_password, verify_password
from . import auth
from .deps import JSON_MEDIA_TYPE, client_ip, current_user, get_context, get_services, optional_user
from .errors import app_error_response, validation_error_from
from .serializers import user_out

router = APIRouter(tags=["auth"])

COOKIE_PATH = "/api/v1/auth"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_EMAIL = 254
MAX_PASSWORD = 1024

_dummy_hash: str | None = None


def _dummy() -> str:
    """Хеш-пустышка: неизвестная почта проверяется так же долго, как известная."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("dummy-password-for-timing")
    return _dummy_hash


def check_email(email: Any, *, field: str = "email") -> str:
    if not isinstance(email, str) or not email.strip():
        raise AppError("validation_error", "Введите почту", field=field)
    e = email.strip().lower()
    if len(e) > MAX_EMAIL or not EMAIL_RE.match(e):
        raise AppError("validation_error", "Проверьте адрес почты", field=field)
    return e


async def read_json_object(request: Request) -> dict[str, Any]:
    """Тело запроса как JSON-объект (пустое тело -> {}); иначе 400 validation_error."""
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise AppError("validation_error", "Тело запроса — некорректный JSON") from None
    if not isinstance(data, dict):
        raise AppError("validation_error", "Тело запроса должно быть объектом JSON")
    return data


def parse_model(model: type[BaseModel], data: dict[str, Any]) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise validation_error_from([{"loc": ("body", *err["loc"]), "type": err["type"]} for err in e.errors()]) \
            from None


def _token_body(ctx: Any, tokens: auth.SessionTokens, *, with_user: bool) -> dict[str, Any]:
    body: dict[str, Any] = {"access_token": tokens.pair.access_token, "token_type": "Bearer",
                            "expires_in": tokens.pair.expires_in}
    if ctx.settings.refresh_in_body:
        body["refresh_token"] = tokens.pair.refresh_token
    if with_user:
        body["user"] = user_out(tokens.user)
    return body


def _set_refresh_cookie(resp: Response, ctx: Any, tokens: auth.SessionTokens) -> None:
    resp.set_cookie(auth.COOKIE_NAME, tokens.pair.refresh_token, max_age=tokens.max_age, path=COOKIE_PATH,
                    httponly=True, samesite="strict", secure=bool(ctx.settings.cookie_secure))


def _clear_refresh_cookie(resp: Response, ctx: Any) -> None:
    resp.delete_cookie(auth.COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="strict",
                       secure=bool(ctx.settings.cookie_secure))


def _token_response(ctx: Any, tokens: auth.SessionTokens, *, with_user: bool, status: int = 200) -> JSONResponse:
    resp = JSONResponse(_token_body(ctx, tokens, with_user=with_user), status_code=status,
                        media_type=JSON_MEDIA_TYPE, headers={"Cache-Control": "no-store"})
    _set_refresh_cookie(resp, ctx, tokens)
    return resp


async def _refresh_token_from(request: Request) -> str | None:
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        return token
    try:
        data = await read_json_object(request)
    except AppError:
        return None
    t = data.get("refresh_token")
    return t if isinstance(t, str) and t else None


# --- login -------------------------------------------------------------------------------------

class LoginIn(BaseModel):
    email: str
    password: str
    remember: bool = False


@router.post("/auth/login", summary="Вход по почте и паролю")
def login(body: LoginIn, request: Request) -> JSONResponse:
    ctx = get_context(request)
    if not body.email.strip():
        raise AppError("validation_error", "Введите почту", field="email")
    email = check_email(body.email)
    if not body.password:
        raise AppError("validation_error", "Введите пароль", field="password")
    if len(body.password) > MAX_PASSWORD:
        raise AppError("validation_error", "Слишком длинный пароль", field="password")
    ip = client_ip(request)
    now = auth.utcnow()
    lim = ctx.login_limiter
    lim.check(f"email:{email}", now=now)
    lim.check(f"ip:{ip}", now=now)

    services = get_services(request)
    outcome: AppError | auth.SessionTokens
    with services.db.uow() as uow:
        user = uow.users.get_by_email(email)
        stored_hash = user.password_hash if user is not None and user.password_hash else None
        ok = verify_password(body.password, stored_hash or _dummy())
        if user is None or stored_hash is None or not ok:
            uow.login_attempts.record(email=email, ip=ip, success=False)
            outcome = AppError("invalid_credentials")
        elif user.disabled:
            uow.login_attempts.record(email=email, ip=ip, success=False)
            outcome = AppError("account_disabled")
        else:
            uow.login_attempts.record(email=email, ip=ip, success=True)
            outcome = ctx.sessions.start(uow, user, remember=body.remember, now=now, ip=ip,
                                         user_agent=request.headers.get("user-agent"))
            try:
                uow.users.update(user.id, last_login_at=now)
            except TypeError:  # pragma: no cover - схема без last_login_at
                pass
    if isinstance(outcome, AppError):
        if outcome.code == "invalid_credentials":
            lim.fail(f"email:{email}", now=now)
            lim.fail(f"ip:{ip}", now=now)
        raise outcome
    lim.reset(f"email:{email}")
    return _token_response(ctx, outcome, with_user=True)


# --- refresh / logout --------------------------------------------------------------------------

@router.post("/auth/refresh", summary="Новая пара токенов по refresh (ротация)")
async def refresh(request: Request) -> JSONResponse:
    ctx = get_context(request)
    token = await _refresh_token_from(request)

    def _rotate() -> AppError | auth.SessionTokens:
        services = get_services(request)
        with services.db.uow() as uow:
            return ctx.sessions.rotate(uow, token, now=auth.utcnow(), ip=client_ip(request),
                                       user_agent=request.headers.get("user-agent"))

    outcome = await run_in_threadpool(_rotate)
    if isinstance(outcome, AppError):
        err = app_error_response(outcome, request)
        _clear_refresh_cookie(err, ctx)
        return err
    return _token_response(ctx, outcome, with_user=False)


@router.post("/auth/logout", status_code=204, summary="Выход: гасит текущую сессию (all=true - все)")
async def logout(request: Request) -> Response:
    ctx = get_context(request)
    token = request.cookies.get(auth.COOKIE_NAME)
    data: dict[str, Any] = {}
    try:
        data = await read_json_object(request)
    except AppError:
        data = {}
    if not token and isinstance(data.get("refresh_token"), str):
        token = data["refresh_token"]
    all_sessions = data.get("all") is True

    def _do() -> None:
        user = optional_user(request)
        services = get_services(request)
        with services.db.uow() as uow:
            owner = ctx.sessions.revoke(uow, token)
            if all_sessions:
                uid = user.id if user is not None else owner
                if uid:
                    uow.sessions.revoke_all_for_user(uid)

    await run_in_threadpool(_do)
    resp = Response(status_code=204)
    _clear_refresh_cookie(resp, ctx)
    return resp


# --- me ----------------------------------------------------------------------------------------

@router.get("/auth/me", summary="Текущий пользователь")
def auth_me(request: Request, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
    with get_services(request).db.uow() as uow:
        u = uow.users.get(user.id)
    if u is None:  # pragma: no cover - удалён между проверкой и чтением
        raise AppError("unauthorized", "Нужно войти")
    return user_out(u)


# --- register ----------------------------------------------------------------------------------

class RegisterIn(BaseModel):
    name: str | None = None
    email: Any = None
    password: Any = None


@router.post("/auth/register", status_code=201, summary="Регистрация (по умолчанию выключена)")
async def register(request: Request) -> JSONResponse:
    ctx = get_context(request)
    if not ctx.settings.allow_signup:
        raise AppError("registration_disabled")
    data = await read_json_object(request)
    body = parse_model(RegisterIn, data)
    email = check_email(body.email)
    if not isinstance(body.password, str):
        raise AppError("validation_error", "Введите пароль", field="password")
    validate_password(body.password)
    name = (body.name or "").strip() or email.split("@", 1)[0]
    if len(name) > 80:
        raise AppError("validation_error", "Имя длиннее 80 символов", field="name")

    pw_hash = await run_in_threadpool(hash_password, body.password)

    def _create() -> auth.SessionTokens:
        from ...storage import DEFAULT_WORKSPACE_ID  # noqa: PLC0415

        services = get_services(request)
        with services.db.uow() as uow:
            if uow.users.get_by_email(email) is not None:
                raise AppError("email_taken", field="email")
            user = uow.users.create(workspace_id=DEFAULT_WORKSPACE_ID, email=email, name=name,
                                    password_hash=pw_hash, is_admin=False, disabled=False)
            return ctx.sessions.start(uow, user, remember=bool(data.get("remember", False)), now=auth.utcnow(),
                                      ip=client_ip(request), user_agent=request.headers.get("user-agent"))

    tokens = await run_in_threadpool(_create)
    return _token_response(ctx, tokens, with_user=True, status=201)
