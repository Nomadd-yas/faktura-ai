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
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import Settings, get_settings
from .adapters import SUPPORTED_DOMAINS, MockGoldExtractor
from .ndjson import NDJSON_TYPE, events_from_raw

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
            state["ext"] = MockGoldExtractor(Path(gold), s.mock_issues, rag_gold_dir=s.rag_mock_gold_dir)
        return state["ext"]

    @app.get("/health")
    def health() -> JSONResponse:
        h = extractor().health()
        if h.status != "ok":
            return JSONResponse(status_code=503, headers={"Retry-After": "30"},
                                content={"status": "down", "model_version": None, "router": "down"})
        return JSONResponse(content={"status": "ok", "model_version": h.model_version, "router": "ok"})

    @app.post("/ml/extract", response_model=None)
    async def extract(request: Request) -> JSONResponse | StreamingResponse:
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
            raw = extractor().build_raw(pid, domain, content)
        except Exception as e:  # noqa: BLE001 - нет эталона и т. п.
            return _error(503, "unavailable", f"Мок недоступен: {e}", retryable=True)
        # NDJSON: заголовок Accept или поле формы stream=ndjson (stream=json - принудительно обычный JSON);
        # по умолчанию - обычный JSON, как раньше
        stream = str(form.get("stream") or "").strip().lower()
        if stream == "ndjson" or (stream != "json" and NDJSON_TYPE in request.headers.get("accept", "").lower()):
            return StreamingResponse(events_from_raw(raw), media_type=NDJSON_TYPE)
        return JSONResponse(content=raw)

    @app.post("/ask/plan")
    async def ask_plan(request: Request) -> JSONResponse:
        """Мок планировщика вопросов: правиловый, детерминированный (тот же контракт, что у ML: {question, run})."""
        from ..ask.rules import keyword_plan

        try:
            body = await request.json()
            question, run = str(body["question"]), body["run"]
            if not isinstance(run, dict) or not question.strip() or len(question) > 500:
                raise ValueError
        except Exception:  # noqa: BLE001
            return _error(400, "bad_request", "Ожидается JSON {question (до 500 символов), run}")
        return JSONResponse(content={"plan": keyword_plan(question, run), "meta": {
            "model": "mock", "provider": "mock", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}})

    @app.post("/ask/write")
    async def ask_write(request: Request) -> JSONResponse:
        """Мок автора текста: возвращает шаблонный текст из запроса (draft) без изменений."""
        try:
            body = await request.json()
            draft = body["draft"]
            if not isinstance(draft, str) or not isinstance(body.get("facts"), dict):
                raise ValueError
        except Exception:  # noqa: BLE001
            return _error(400, "bad_request", "Ожидается JSON {question, intent, facts, draft}")
        return JSONResponse(content={"text": draft, "meta": {
            "model": "mock", "provider": "mock", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}})

    @app.post("/llm/chat")
    async def llm_chat(request: Request) -> JSONResponse:
        """Мок прокси LLM для агента: детерминированный. Первый ход - вызов filter по results (3 строки);
        после результата инструмента - ответ с первым значением и маркером [[fact:...]] (или «данных нет»)."""
        import json as _json

        try:
            body = await request.json()
            messages = body["messages"]
            if not isinstance(messages, list) or not messages:
                raise ValueError
        except Exception:  # noqa: BLE001
            return _error(400, "bad_request", "Ожидается JSON {messages, tools?}")
        meta = {"model": "mock", "provider": "mock", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        if not tool_msgs and body.get("tools"):
            call = {"id": "call_1", "type": "function", "function": {
                "name": "filter", "arguments": _json.dumps({"table": "results", "limit": 3})}}
            return JSONResponse(content={"message": {"role": "assistant", "content": "", "tool_calls": [call]},
                                         "finish_reason": "tool_calls", "meta": meta})
        text = "В разборе этого нет."
        try:
            rows = _json.loads(tool_msgs[-1]["content"]).get("rows") or []
        except Exception:  # noqa: BLE001
            rows = []
        if rows:
            r = rows[0]
            text = f"Первое значение {r.get('metric_name', '')}: {r.get('value')} [[fact:{r.get('fact_id')}]]."
        return JSONResponse(content={"message": {"role": "assistant", "content": text}, "finish_reason": "stop",
                                     "meta": meta})

    return app


app = create_mock_app()

if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("FAKTURA_ML_MOCK_PORT", "8081")))
