#!/usr/bin/env python3
# Самый простой HTTP API поверх конвейера:  POST /extract (multipart PDF) → ответ сразу.
#
# Без авторизации — только для внутренней сети / VPN / закрытого контура.
# Ответы синхронные: статья обрабатывается минуты, клиент должен держать соединение
# и иметь большой timeout (например 10 минут). Одновременно — один запрос.
#
# Запуск:
#   uvicorn api:app --host 0.0.0.0 --port 8080
# (в образе: docker run ... pdf-dataset serve)

import os
import re
import shutil
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from pipeline import (PipelineError, config_path, model_version, ocr_reachable,
                      run_pipeline)

app = FastAPI(title="pdf-dataset", version="1.0",
              description="PDF -> markdown (glmocr) -> dataset.json. Синхронный API без авторизации.")

OUT_DIR = Path(os.environ.get("OUT_DIR", "/out"))
# [faktura-patch] обычный def (FastAPI гонит его в пуле потоков) — /health отвечает во время разбора;
# сам конвейер — строго по одному запросу (glmocr тяжёлый).
# [faktura-patch] одновременно не больше ML_CONCURRENCY статей (каждая — в своём процессе)
_PIPELINE_LOCK = threading.BoundedSemaphore(max(1, int(os.environ.get("ML_CONCURRENCY", "1"))))


def _child_run(pdf_path, out_dir, pub_id, no_strip):
    """[faktura-patch] Выполняется в ОТДЕЛЬНОМ процессе: torch/glmocr на CPU не отдают память системе,
    поэтому после каждой статьи процесс завершается и память освобождается целиком.
    Исключения возвращаются словарём (PipelineError не всегда сериализуется pickle)."""
    try:
        return {"result": run_pipeline(pdf_path, out_dir=out_dir, publication_id=pub_id, no_strip=no_strip)}
    except PipelineError as e:
        return {"perr": {"code": e.code, "message": e.message, "retryable": e.retryable, "status": e.status}}
    except Exception as e:  # noqa: BLE001
        return {"err": f"{type(e).__name__}: {e}"}


def _run_isolated(pdf_path, out_dir, pub_id, no_strip):
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    from concurrent.futures.process import BrokenProcessPool
    with ProcessPoolExecutor(max_workers=1, mp_context=mp.get_context("spawn")) as ex:
        try:
            return ex.submit(_child_run, str(pdf_path), str(out_dir), pub_id, no_strip).result()
        except BrokenProcessPool:  # процесс убит (OOM по лимиту контейнера)
            return {"perr": {"code": "ml_error", "retryable": False, "status": 500,
                             "message": "разбор прерван: процесс извлечения завершён (вероятно, не хватило памяти)"}}


def _error(status, code, message, *, retryable=False, headers=None):
    """[faktura-patch] Единый формат ошибки; detail = message — для совместимости со старыми клиентами."""
    return JSONResponse(status_code=status, headers=headers,
                        content={"code": code, "message": message, "retryable": retryable,
                                 "detail": message})


@app.exception_handler(HTTPException)
async def _http_exc(request, exc):
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return _error(exc.status_code, "bad_request" if exc.status_code < 500 else "ml_error", msg)


@app.get("/health")
def health():
    # [faktura-patch] status=down, если нет ключа RouterAI или недоступен OCR-сервер
    key = bool(os.environ.get("ROUTERAI_API_KEY"))
    ocr = ocr_reachable(config_path(), timeout=2.0)
    body = {"status": "ok" if key and ocr else "down", "model_version": model_version(),
            "ocr": "ok" if ocr else "down", "router_key": key}
    return JSONResponse(status_code=200, content=body)


@app.post("/extract")
def extract_pdf(file: UploadFile = File(...),
                      publication_id: str | None = Form(None),
                      no_strip: bool = Form(False)):
    """Загружает PDF, прогоняет конвейер, возвращает датасет и метаданные.

    Результат также сохраняется в файлы: /out/<id>/<id>.dataset.json и .meta.json.
    Повторная загрузка с тем же именем файла перезаписывает предыдущий результат.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="нет имени файла")

    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(file.filename).stem).strip("_") or "paper"
    pub_id = (publication_id or safe).strip()
    out_dir = OUT_DIR / safe
    uploads = OUT_DIR / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    pdf_path = uploads / f"{safe}.pdf"

    with pdf_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        with _PIPELINE_LOCK:
            out = _run_isolated(pdf_path, out_dir, pub_id, no_strip)
    except Exception as e:  # noqa: BLE001 - сбой запуска процесса -> 500 ml_error
        return _error(500, "ml_error", f"{type(e).__name__}: {e}")
    if "perr" in out:
        e = out["perr"]
        headers = {"Retry-After": "60"} if e["code"] == "ocr_unavailable" else None
        return _error(e["status"], e["code"], e["message"], retryable=e["retryable"], headers=headers)
    if "err" in out:
        return _error(500, "ml_error", out["err"])
    result = out["result"]

    return {
        "ok": result["ok"],
        "model_version": result.get("model_version") or model_version(),
        "publication_id": result["publication_id"],
        "extract_error": result["extract_error"],
        "failed_tables": result["failed_tables"],
        "counts": result["meta"].get("counts"),
        "dataset": result["dataset"],
        "meta": result["meta"],
        "out_dir": result["out_dir"],
    }
