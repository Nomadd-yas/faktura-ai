"""Мок-сервер ML-1: FastAPI GET /health и POST /ml/extract поверх MockGoldExtractor (план 5.2, Б-1).

Повторяет контракт spec/07 §7.0.1: multipart `file`, `publication_id`, `domain`, `fields`, `document`,
`request_id`; ответ - строки 7 таблиц dev_gold для известной статьи, только `publication` для остальных.
Ошибки - тело `{"code", "message", "retryable"}`: 400 (нет файла, неверный domain), 413 (> 50 МБ),
422 `pdf_broken` (нет сигнатуры %PDF-), 503 на /health, если эталона нет.

Запуск: python -m faktura.ml.mock_server  (порт - FAKTURA_ML_MOCK_PORT, по умолчанию 8081)
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings
from .adapters import SUPPORTED_DOMAINS, MockGoldExtractor

MAX_FILE_MB = 50


def _error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message, "retryable": retryable})


def create_mock_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="faktura ML-1 mock", version="0.3-mock")
    state: dict[str, MockGoldExtractor] = {}

    def extractor() -> MockGoldExtractor:
        if "ext" not in state:
            s = settings or get_settings()
            gold = s.mock_gold_dir or Path("dev_gold")
            state["ext"] = MockGoldExtractor(Path(gold), s.mock_issues)
        return state["ext"]

    @app.get("/health")
    def health() -> JSONResponse:
        h = extractor().health()
        if h.status != "ok":
            return JSONResponse(status_code=503, headers={"Retry-After": "30"},
                                content={"status": "down", "model_version": None, "router": "down"})
        return JSONResponse(content={"status": "ok", "model_version": h.model_version, "router": "ok"})

    @app.post("/ml/extract")
    async def extract(request: Request) -> JSONResponse:
        ctype = request.headers.get("content-type", "")
        if not ctype.startswith("multipart/form-data"):
            return _error(400, "bad_request", "Ожидается multipart/form-data")
        try:
            form = await request.form()
        except Exception:  # noqa: BLE001 - битое тело запроса
            return _error(400, "bad_request", "Не удалось разобрать multipart")
        upload = form.get("file")
        if upload is None or isinstance(upload, str) or not hasattr(upload, "read"):
            return _error(400, "bad_request", "Нет файла (поле file)")
        domain = str(form.get("domain") or "").strip()
        if domain not in SUPPORTED_DOMAINS:
            return _error(400, "bad_request", f"Неверный domain: {domain!r}; поддерживается: {', '.join(SUPPORTED_DOMAINS)}")
        content = await upload.read()
        if len(content) > MAX_FILE_MB * 1024 * 1024:
            return _error(413, "file_too_large", f"Файл больше {MAX_FILE_MB} МБ")
        if b"%PDF-" not in content[:1024]:
            return _error(422, "pdf_broken", "Файл не PDF (нет сигнатуры %PDF-)")
        pid = str(form.get("publication_id") or "").strip()
        if not pid:
            pid = Path(getattr(upload, "filename", "") or "").stem
        if not pid:
            return _error(400, "bad_request", "Нет publication_id")
        try:
            raw = extractor().build_raw(pid)
        except Exception as e:  # noqa: BLE001 - нет эталона и т. п.
            return _error(503, "unavailable", f"Мок недоступен: {e}", retryable=True)
        return JSONResponse(content=raw)

    return app


app = create_mock_app()

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("FAKTURA_ML_MOCK_PORT", "8081")))
