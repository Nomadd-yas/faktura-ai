"""Ошибки и каталог кодов (api_contract.md §1.5)."""
from __future__ import annotations

from typing import Any

# code -> (http_status, сообщение по-русски)
ERROR_CATALOG: dict[str, tuple[int, str]] = {
    "validation_error": (400, "Некорректный запрос"),
    "unauthorized": (401, "Требуется вход"),
    "token_expired": (401, "Срок действия токена истёк"),
    "forbidden": (403, "Недостаточно прав"),
    "not_found": (404, "Не найдено"),
    "conflict": (409, "Конфликт состояния"),
    "gone": (410, "Ресурс удалён"),
    "payload_too_large": (413, "Слишком большой запрос"),
    "unsupported_file": (415, "Неподдерживаемый тип файла"),
    "rate_limited": (429, "Слишком много запросов"),
    "internal_error": (500, "Внутренняя ошибка"),
    "not_implemented": (501, "Не реализовано"),
    "duplicate_name": (409, "Файл с таким именем уже есть"),
    "file_too_large": (413, "Файл больше допустимого"),
    "too_many_pages": (413, "Слишком много страниц"),
    "pdf_encrypted": (422, "PDF защищён паролем"),
    "pdf_broken": (422, "PDF повреждён"),
    "pdf_no_text": (422, "В PDF нет текстового слоя"),
    "zip_bomb": (413, "Архив слишком велик после распаковки"),
    "zip_too_large": (413, "Архив слишком велик"),
    "ml_unavailable": (503, "Сервис извлечения недоступен"),
    "ml_timeout": (504, "Сервис извлечения не ответил вовремя"),
    "ml_schema_mismatch": (502, "Ответ сервиса извлечения не соответствует контракту"),
    "ml_error": (502, "Ошибка сервиса извлечения"),
}


class AppError(Exception):
    def __init__(self, code: str, message: str | None = None, http_status: int | None = None,
                 field: str | None = None, details: Any = None):
        status, default_msg = ERROR_CATALOG.get(code, (500, code))
        self.code = code
        self.message = message or default_msg
        self.http_status = http_status or status
        self.field = field
        self.details = details
        super().__init__(f"{code}: {self.message}")


class ExtractError(AppError):
    def __init__(self, code: str, message: str | None = None, *, retryable: bool = False, details: Any = None):
        super().__init__(code, message, details=details)
        self.retryable = retryable


class PdfError(AppError):
    def __init__(self, code: str, message: str | None = None, details: Any = None):
        super().__init__(code, message, details=details)
