"""Зависимости FastAPI, общие для всех API-модулей (план 5.7).

Как пользоваться в модулях api.corpus|papers|review::

    from faktura.api.base.deps import current_user, get_services, get_uow, page_params, mutation_response

    @router.post("/corpora", status_code=201)
    def create(body: CorpusIn, user=Depends(current_user), services=Depends(get_services)):
        with services.db.uow() as uow:
            ...
        return mutation_response(corpus_out(c), action, status=201)

* ``current_user`` - проверка ``Authorization: Bearer``, пользователь читается из БД (права и блокировка
  действуют сразу), лимит 600 запросов в минуту на пользователя (429 ``too_many_requests``).
* ``get_uow`` - один UnitOfWork на запрос: commit при успехе, rollback при исключении. **SQLite:** пока он открыт,
  не открывайте в этом же запросе ``services.db.uow()`` (блокировка единственного писателя) - работайте через него.
* ``mutation_response`` - ``action_id`` в теле и ``X-Action-Id``; ``stub_response`` - ``X-Faktura-Stub: 1``.
* ``idempotent`` / ``remember_idempotent`` - ``Idempotency-Key`` (24 ч, ключ в пределах пользователя).
"""
from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ...domain.entities import Action, CurrentUser, Page
from ...domain.errors import AppError
from ...domain.services import Services
from . import auth
from .context import AppContext
from .errors import JSON_MEDIA_TYPE

__all__ = [
    "JSON_MEDIA_TYPE", "PageParams", "IdempotencyContext", "get_context", "get_services", "get_uow", "request_uow",
    "bearer_token", "sse_user", "optional_user", "current_user", "admin_user", "page_params", "make_page_params",
    "page_response", "mutation_response", "stub_response", "idempotent", "remember_idempotent", "replay_response",
    "upload_rate_limit", "client_ip", "STATE_UOW", "STATE_USER", "DEFAULT_LIMIT", "MAX_LIMIT",
]

DEFAULT_LIMIT = 50
MAX_LIMIT = 500
STATE_UOW = "faktura_uow"
STATE_USER = "faktura_user"
IDEMPOTENCY_TTL_H = 24
MAX_IDEMPOTENCY_KEY = 200


class PageParams(BaseModel):
    limit: int = DEFAULT_LIMIT
    cursor: str | None = None
    sort: str | None = None


class IdempotencyContext(BaseModel):
    key: str | None = None
    replay: dict[str, Any] | None = None


# --- контекст и Services -----------------------------------------------------------------------

def get_context(request: Request) -> AppContext:
    ctx = getattr(request.app.state, "faktura", None)
    if ctx is None:  # pragma: no cover - приложение собрано не через create_app
        raise AppError("internal_error", "Приложение не инициализировано")
    return ctx


def get_services(request: Request) -> Services:
    return get_context(request).get_services()


def _state(request: Request) -> Any:
    return request.state


@contextlib.contextmanager
def request_uow(request: Request) -> Iterator[Any]:
    """UoW запроса, если его открыл ``get_uow``, иначе свой короткий ``services.db.uow()``."""
    existing = getattr(_state(request), STATE_UOW, None)
    if existing is not None:
        yield existing
        return
    with get_services(request).db.uow() as uow:
        yield uow


async def get_uow(request: Request) -> AsyncIterator[Any]:
    """Один UnitOfWork на запрос: commit при успехе, rollback при исключении.

    Открывается и закрывается в потоке цикла событий (у SQLite блокировка привязана к потоку)."""
    uow = get_services(request).db.uow()
    uow.__enter__()
    setattr(_state(request), STATE_UOW, uow)
    try:
        yield uow
    except BaseException as e:
        setattr(_state(request), STATE_UOW, None)
        uow.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        setattr(_state(request), STATE_UOW, None)
        uow.__exit__(None, None, None)


# --- пользователь ------------------------------------------------------------------------------

def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def bearer_token(request: Request) -> str | None:
    """Токен из ``Authorization: Bearer <token>`` или None (заголовка нет / схема не Bearer / пусто)."""
    header = request.headers.get("authorization")
    if not header:
        return None
    parts = header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def _load_user(request: Request, token: str) -> CurrentUser:
    ctx = get_context(request)
    claims = auth.decode_access(token, now=auth.utcnow(), secret=ctx.jwt_secret)
    with request_uow(request) as uow:
        user = uow.users.get(claims.sub)
    if user is None:
        raise AppError("unauthorized", "Нужно войти")
    if user.disabled:
        raise AppError("account_disabled")
    cu = CurrentUser(id=user.id, workspace_id=user.workspace_id, email=user.email, is_admin=bool(user.is_admin))
    setattr(_state(request), STATE_USER, cu)
    return cu


def current_user(request: Request) -> CurrentUser:
    """401 ``unauthorized`` без токена или с неверным, 401 ``token_expired`` для истёкшего, 403 ``account_disabled``
    для отключённого; 429 ``too_many_requests`` сверх 600 запросов в минуту."""
    cached = getattr(_state(request), STATE_USER, None)
    if isinstance(cached, CurrentUser):
        return cached
    token = bearer_token(request)
    if token is None:
        raise AppError("unauthorized", "Нужно войти")
    user = _load_user(request, token)
    get_context(request).rate_limiter.hit(f"user:{user.id}", now=auth.utcnow())
    return user


def sse_user(request: Request, access_token: str | None = None) -> CurrentUser:
    """Как ``current_user``, но для SSE: если нет заголовка Bearer, токен берётся из ``?access_token=``
    (EventSource не умеет заголовки). Подключать только к SSE-маршрутам; токен проверяется так же (подпись, срок)."""
    if bearer_token(request) is not None:
        return current_user(request)
    token = (access_token or request.query_params.get("access_token") or "").strip()
    if not token:
        raise AppError("unauthorized", "Нужно войти")
    user = _load_user(request, token)
    get_context(request).rate_limiter.hit(f"user:{user.id}", now=auth.utcnow())
    return user


def optional_user(request: Request) -> CurrentUser | None:
    """Пользователь, если передан действующий токен; иначе None (без ошибок и без учёта в лимите)."""
    token = bearer_token(request)
    if token is None:
        return None
    try:
        return _load_user(request, token)
    except AppError:
        return None


def admin_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """403 ``forbidden`` для не-админа (права читаются из БД при каждом запросе)."""
    if not user.is_admin:
        raise AppError("forbidden", "Нет доступа: нужны права администратора")
    return user


def upload_rate_limit(request: Request, user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """60 загрузок в час на пользователя (BACKEND §8.3) - для POST /papers и POST /corpora/{id}/files."""
    get_context(request).upload_limiter.hit(f"upload:{user.id}", now=auth.utcnow())
    return user


# --- пагинация ---------------------------------------------------------------------------------

def _clamp_limit(limit: int, default: int, maximum: int) -> int:
    if limit is None:
        return default
    if limit < 1:
        raise AppError("validation_error", "Параметр «limit» должен быть не меньше 1", field="limit")
    return min(int(limit), maximum)


def page_params(limit: int = DEFAULT_LIMIT, cursor: str | None = None, sort: str | None = None) -> PageParams:
    """``limit`` 1…500 (больше - урезается до 500, меньше 1 - 400 ``validation_error``)."""
    return PageParams(limit=_clamp_limit(limit, DEFAULT_LIMIT, MAX_LIMIT), cursor=cursor or None, sort=sort or None)


def make_page_params(default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT):
    """Зависимость пагинации со своими пределами (``/facts/table`` - 100 и 1000, ``/history`` - 100)."""

    def _dep(limit: int = default, cursor: str | None = None, sort: str | None = None) -> PageParams:
        return PageParams(limit=_clamp_limit(limit, default, maximum), cursor=cursor or None, sort=sort or None)

    return _dep


def _jsonable(item: Any) -> Any:
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    return item


def page_response(page: Page) -> dict:
    """``{data, total, next_cursor}``: ``total`` и ``next_cursor`` присутствуют всегда (могут быть null)."""
    return {"data": [_jsonable(x) for x in page.data], "total": page.total, "next_cursor": page.next_cursor}


# --- ответы ------------------------------------------------------------------------------------

def mutation_response(payload: dict, action: Action, *, status: int = 200,
                      headers: dict[str, str] | None = None) -> JSONResponse:
    """Тело мутации + ``action_id`` в теле и ``X-Action-Id`` (api_contract §1.6). Входной словарь не меняется."""
    body = {**payload, "action_id": action.id}
    hdrs = {**(headers or {}), "X-Action-Id": action.id}
    return JSONResponse(body, status_code=status, headers=hdrs, media_type=JSON_MEDIA_TYPE)


def stub_response(payload: dict, *, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    """Заглушка P2: верная форма ответа и заголовок ``X-Faktura-Stub: 1`` (У-15)."""
    hdrs = {**(headers or {}), "X-Faktura-Stub": "1"}
    return JSONResponse(payload, status_code=status, headers=hdrs, media_type=JSON_MEDIA_TYPE)


# --- идемпотентность ---------------------------------------------------------------------------

def _idem_key(request: Request) -> str | None:
    key = request.headers.get("idempotency-key")
    if key is None:
        return None
    key = key.strip()
    if not key:
        return None
    if len(key) > MAX_IDEMPOTENCY_KEY:
        raise AppError("validation_error", "Слишком длинный Idempotency-Key", field="Idempotency-Key")
    return key


def idempotent(request: Request, user: CurrentUser) -> IdempotencyContext:
    """Ключ ``Idempotency-Key`` и сохранённый первый ответ (``{"status", "body", "headers"?}``), если он есть."""
    key = _idem_key(request)
    if key is None:
        return IdempotencyContext()
    with request_uow(request) as uow:
        stored = uow.idempotency.get(user.id, key)
    return IdempotencyContext(key=key, replay=stored)


def remember_idempotent(request: Request, user: CurrentUser, ctx: IdempotencyContext, response: JSONResponse | dict,
                        *, status: int = 200) -> None:
    """Сохранить первый ответ на 24 ч (без ключа - ничего). Принимает JSONResponse или тело-словарь."""
    if not ctx.key:
        return
    if isinstance(response, JSONResponse):
        body = json.loads(bytes(response.body).decode("utf-8") or "null")
        keep = {k: v for k, v in response.headers.items() if k.lower() in ("x-action-id", "x-faktura-stub")}
        stored = {"status": response.status_code, "body": body, "headers": keep}
    else:
        stored = {"status": status, "body": response}
    with request_uow(request) as uow:
        uow.idempotency.put(user.id, ctx.key, stored, ttl_h=IDEMPOTENCY_TTL_H)


def replay_response(ctx: IdempotencyContext) -> JSONResponse | None:
    """Готовый ответ-повтор, если ключ уже использовался."""
    if not ctx.replay:
        return None
    hdrs = {str(k): str(v) for k, v in (ctx.replay.get("headers") or {}).items()}
    hdrs["Idempotent-Replayed"] = "true"
    return JSONResponse(ctx.replay.get("body"), status_code=int(ctx.replay.get("status", 200)), headers=hdrs,
                        media_type=JSON_MEDIA_TYPE)
