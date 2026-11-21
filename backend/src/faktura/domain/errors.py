"""Ошибки и каталог кодов (api_contract.md §1.5)."""
from __future__ import annotations

import logging
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
    "too_large": (413, "Слишком большая загрузка"),
    "unsupported_file": (415, "Неподдерживаемый тип файла"),
    "unsupported_media_type": (415, "Нужны PDF-файлы или один ZIP-архив"),
    "rate_limited": (429, "Слишком много запросов"),
    "internal_error": (500, "Внутренняя ошибка"),
    "not_implemented": (501, "Не реализовано"),
    "duplicate_name": (409, "Файл с таким именем уже есть"),
    "file_too_large": (413, "Файл больше допустимого размера"),
    "too_many_pages": (422, "Слишком много страниц в PDF"),
    "pdf_encrypted": (422, "PDF защищён паролем"),
    "pdf_broken": (422, "PDF повреждён"),
    "pdf_no_text": (422, "В PDF нет текстового слоя"),
    "zip_bomb": (413, "Архив слишком велик после распаковки"),
    "zip_too_large": (413, "Архив слишком велик"),
    "encrypted_pdf": (422, "PDF защищён паролем"),
    "extractor_unavailable": (503, "Сервис разбора временно недоступен"),
    "domain_not_supported": (422, "Область не поддерживается"),
    "issue_auto_fixed": (409, "Замечание уже исправлено автоматически"),
    # внутренние коды извлечения: наружу в API уходят как extractor_unavailable, тексты - публичные
    "ml_unavailable": (503, "Сервис разбора временно недоступен"),
    "ml_timeout": (503, "Сервис разбора временно недоступен"),
    "ml_schema_mismatch": (502, "Модель вернула ответ в неверном формате"),
    "ml_error": (502, "Модель не смогла разобрать статью"),
    "ml_invalid_data": (502, "Модель вернула некорректные данные"),
}

# Публичные тексты причин по внутренним кодам ошибок (RunFile.reason, error_message): без «ML», «бэк».
PUBLIC_REASONS: dict[str, str] = {
    "ml_unavailable": "Сервис разбора временно недоступен",
    "ml_timeout": "Сервис разбора временно недоступен",
    "ml_schema_mismatch": "Модель вернула ответ в неверном формате",
    "ml_error": "Модель не смогла разобрать статью",
    "pdf_broken": "PDF повреждён или не читается",
    "pdf_encrypted": "PDF защищён паролем",
    "pdf_no_text": "В PDF нет текстового слоя",
    "too_many_pages": "В PDF слишком много страниц",
    "file_too_large": "Файл больше допустимого размера",
    "ml_quota": "Исчерпан лимит сервиса разбора",
    "ml_invalid_data": "Модель вернула некорректные данные",
    "file_timeout": "Превышено время разбора файла (10 мин)",
}
# коды, у которых в публичный текст входит пояснение из сообщения («Модель вернула некорректные данные: ...»)
DETAILED_CODES: frozenset[str] = frozenset({"ml_invalid_data", "file_timeout"})
INTERNAL_REASON = "Внутренняя ошибка сервиса разбора"


def public_reason(code: str | None, message: str | None = None) -> str:
    """Публичная причина сбоя по коду; неизвестные коды - общий текст (технический текст только в логи)."""
    if code in DETAILED_CODES and message:
        return message
    if code in PUBLIC_REASONS:
        return PUBLIC_REASONS[code]
    return INTERNAL_REASON


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


def extract_error(code: str, technical: str, *, retryable: bool = False, details: Any = None) -> ExtractError:
    """ExtractError с публичным текстом из каталога; технические детали - только в лог и в `details`."""
    logging.getLogger("faktura.extractor").warning("extractor %s: %s", code, technical)
    d = dict(details) if isinstance(details, dict) else ({"value": details} if details is not None else {})
    d.setdefault("technical", technical)
    return ExtractError(code, None, retryable=retryable, details=d)
