"""Единый формат ошибок API (api_contract §1.5, BACKEND §8.3) и обработчики исключений FastAPI.

Тело любой ошибки: ``{"error": {"code", "message", "field", "details", "request_id"}}``.
``request_id`` заполняется у всех ошибок (§11), заголовок ``X-Request-Id`` есть всегда.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...domain.errors import ERROR_CATALOG, AppError

log = logging.getLogger("faktura.api")

JSON_MEDIA_TYPE = "application/json; charset=utf-8"

# Коды контракта, которых нет в общем каталоге domain.errors (api_contract §1.5).
# setdefault: если координатор перенесёт их в domain/errors.py, значения оттуда главнее.
API_ERROR_CODES: dict[str, tuple[int, str]] = {
    "invalid_credentials": (401, "Неверная почта или пароль"),
    "refresh_invalid": (401, "Сессия истекла, войдите снова"),
    "registration_disabled": (403, "Регистрация закрыта — учётную запись выдаёт администратор"),
    "account_disabled": (403, "Учётная запись отключена. Обратитесь к администратору"),
    "email_taken": (409, "Почта уже используется"),
    "too_many_attempts": (429, "Слишком много попыток. Попробуйте позже"),
    "too_many_requests": (429, "Слишком много запросов, подождите минуту"),
    "method_not_allowed": (405, "Метод не поддерживается для этого адреса"),
    "query_unparsed": (400, "Не понял запрос: попробуйте «КПД выше 18»"),
    "run_not_finished": (409, "Прогон ещё идёт — выходная папка будет готова после завершения"),
    "already_resolved": (409, "Замечание уже разобрано"),
    "already_undone": (409, "Уже отменено"),
    "synonym_exists": (409, "Такое написание уже есть в словаре"),
    "turn_busy": (409, "Ответ ещё пишется — дождитесь или нажмите «Стоп»"),
    "version_conflict": (409, "Изменить можно только последний вопрос"),
    "fact_value_unrecognized": (422, "Не удалось распознать значение"),
    "nothing_to_export": (422, "Нечего экспортировать — выберите статьи или снимите «только принятые»"),
    "paper_not_ready": (422, "Статья ещё разбирается"),
    "submission_invalid": (422, "Набор не прошёл проверку"),
    # ядро v1 (CORE_API.md)
    "domain_not_supported": (422, "Эта область пока не поддерживается: доступна только solar"),
    "no_files": (400, "Не выбрано ни одного файла"),
    "too_large": (413, "Слишком большая загрузка"),
    "unsupported_media_type": (415, "Нужны PDF-файлы или один ZIP-архив"),
    "extractor_unavailable": (503, "Сервис разбора временно недоступен"),
    "issue_auto_fixed": (409, "Замечание уже исправлено автоматически"),
    "run_in_progress": (409, "Разбор ещё идёт — выгрузка будет готова после завершения"),
}
for _code, _val in API_ERROR_CODES.items():
    ERROR_CATALOG.setdefault(_code, _val)

# Русские сообщения для кодов, у которых в каталоге нейтральный текст (контракт §1.5).
_FRIENDLY = {
    "unauthorized": "Нужно войти",
    "token_expired": "Сессия истекла",
    "forbidden": "Нет доступа",
    "internal_error": "Что-то пошло не так. Мы уже разбираемся",
}

_HTTP_CODES = {
    400: "validation_error", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    409: "conflict", 410: "gone", 413: "too_large", 415: "unsupported_media_type", 429: "too_many_requests",
    501: "not_implemented", 502: "extractor_unavailable", 503: "extractor_unavailable",
}

_EXTRACTOR_CODES = {"ml_unavailable", "ml_timeout", "ml_schema_mismatch", "ml_error"}

RETRY_AFTER_CODES = {"too_many_attempts", "too_many_requests", "rate_limited"}


def request_id_of(request: Request | None) -> str | None:
    if request is None:
        return None
    state = request.scope.get("state")
    if isinstance(state, dict):
        return state.get("request_id")
    return getattr(state, "request_id", None)


def error_payload(code: str, message: str | None = None, *, field: str | None = None, details: Any = None,
                  status: int | None = None, request_id: str | None = None) -> dict[str, Any]:
    st = status or ERROR_CATALOG.get(code, (500, code))[0]
    msg = message or _FRIENDLY.get(code) or ERROR_CATALOG.get(code, (500, "Ошибка"))[1]
    return {"error": {"code": code, "message": msg, "field": field, "details": details,
                      "request_id": request_id}}


def error_response(code: str, message: str | None = None, *, field: str | None = None, details: Any = None,
                   status: int | None = None, request: Request | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    """Готовый JSONResponse ошибки. Для 429 с ``details.retry_after`` ставит ``Retry-After``."""
    st = status or ERROR_CATALOG.get(code, (500, code))[0]
    rid = request_id_of(request)
    hdrs = dict(headers or {})
    if isinstance(details, dict) and details.get("retry_after") is not None and (st in (429, 503)):
        hdrs.setdefault("Retry-After", str(int(details["retry_after"])))
    if st == 401:
        hdrs.setdefault("WWW-Authenticate", "Bearer")
    if rid:
        hdrs.setdefault("X-Request-Id", rid)
    body = error_payload(code, message, field=field, details=details, status=st, request_id=rid)
    return JSONResponse(body, status_code=st, headers=hdrs, media_type=JSON_MEDIA_TYPE)


def app_error_response(exc: AppError, request: Request | None = None,
                       headers: dict[str, str] | None = None) -> JSONResponse:
    message = exc.message
    if exc.code in _EXTRACTOR_CODES:  # внутренние коды ML наружу не выходят (§11)
        return error_response("extractor_unavailable", message, field=exc.field, details=None,
                              status=exc.http_status if exc.http_status in (502, 503) else 503,
                              request=request, headers=headers)
    # сообщение по умолчанию из общего каталога - заменить на дружелюбное
    if exc.code in _FRIENDLY and message == ERROR_CATALOG.get(exc.code, (0, None))[1]:
        message = _FRIENDLY[exc.code]
    return error_response(exc.code, message, field=exc.field, details=exc.details, status=exc.http_status,
                          request=request, headers=headers)


# --- ошибки валидации pydantic -> 400 validation_error ------------------------------------------

_LOC_ROOTS = {"body", "query", "path", "header", "cookie"}


def _field_of(loc: Any) -> str | None:
    parts = list(loc or ())
    if parts and parts[0] in _LOC_ROOTS:
        parts = parts[1:]
    names = [str(p) for p in parts]
    if not names or not isinstance(parts[0], str):
        return None
    return ".".join(names)


def _message_for(err: dict[str, Any], field: str | None) -> str:
    t = str(err.get("type", ""))
    f = f"«{field}»" if field else ""
    if t == "json_invalid":
        return "Тело запроса — некорректный JSON"
    if field is None:
        if t == "missing":
            return "Нужно тело запроса в формате JSON"
        return "Тело запроса должно быть объектом JSON"
    if t == "missing":
        return f"Не заполнено поле {f}"
    if t in ("int_parsing", "int_type", "int_from_float"):
        return f"Поле {f} должно быть целым числом"
    if t in ("float_parsing", "float_type", "decimal_parsing", "decimal_type"):
        return f"Поле {f} должно быть числом"
    if t in ("bool_parsing", "bool_type"):
        return f"Поле {f} должно быть true или false"
    if t in ("string_type", "string_unicode"):
        return f"Поле {f} должно быть строкой"
    if t in ("list_type", "tuple_type", "set_type"):
        return f"Поле {f} должно быть списком"
    if t in ("dict_type", "model_type", "model_attributes_type"):
        return f"Поле {f} должно быть объектом"
    if t == "string_too_short":
        return f"Поле {f} не может быть пустым"
    if t == "string_too_long":
        return f"Поле {f} слишком длинное"
    if t in ("greater_than", "greater_than_equal", "less_than", "less_than_equal"):
        return f"Значение поля {f} вне допустимых пределов"
    if t in ("enum", "literal_error"):
        return f"Недопустимое значение поля {f}"
    return f"Некорректное значение поля {f}"


def validation_error_from(errors: list[dict[str, Any]]) -> AppError:
    """Список ошибок pydantic -> AppError(validation_error) с полем первой ошибки и русским сообщением."""
    if not errors:
        return AppError("validation_error", "Некорректный запрос")
    first = errors[0]
    field = _field_of(first.get("loc"))
    details = {"errors": [{"field": _field_of(e.get("loc")), "type": str(e.get("type", ""))} for e in errors[:20]]}
    return AppError("validation_error", _message_for(first, field), field=field, details=details)


# --- обработчики --------------------------------------------------------------------------------

async def _on_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.http_status >= 500:
        log.error("AppError %s: %s [request_id=%s]", exc.code, exc.message, request_id_of(request))
    return app_error_response(exc, request)


async def _on_validation(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return app_error_response(validation_error_from(list(exc.errors())), request)


async def _on_http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "internal_error" if exc.status_code >= 500 else "validation_error")
    message = None
    if code == "not_found":
        message = "Не найдено"
    elif isinstance(exc.detail, str) and exc.detail and exc.status_code not in (404, 405) and \
            any("а" <= ch.lower() <= "я" for ch in exc.detail):
        message = exc.detail
    return error_response(code, message, status=exc.status_code, request=request,
                          headers=dict(exc.headers or {}))


def install_error_handlers(app: Any) -> None:
    app.add_exception_handler(AppError, _on_app_error)
    app.add_exception_handler(RequestValidationError, _on_validation)
    app.add_exception_handler(StarletteHTTPException, _on_http)
