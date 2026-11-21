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

import json
import os
import re
import shutil
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

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


def _child_run(pdf_path, out_dir, pub_id, no_strip, domain="solar"):
    """[faktura-patch] Выполняется в ОТДЕЛЬНОМ процессе: torch/glmocr на CPU не отдают память системе,
    поэтому после каждой статьи процесс завершается и память освобождается целиком.
    Исключения возвращаются словарём (PipelineError не всегда сериализуется pickle)."""
    try:
        return {"result": run_pipeline(pdf_path, out_dir=out_dir, publication_id=pub_id, no_strip=no_strip, domain=domain)}
    except PipelineError as e:
        return {"perr": {"code": e.code, "message": e.message, "retryable": e.retryable, "status": e.status}}
    except Exception as e:  # noqa: BLE001
        return {"err": f"{type(e).__name__}: {e}"}


# [faktura-patch] прогретые воркеры: один пул на сервис, парсер glmocr грузится один раз на воркер;
# воркер перезапускается каждые ML_TASKS_PER_CHILD статей (утечки памяти torch на CPU).
_POOL = None
_POOL_LOCK = threading.Lock()


def _worker_init():
    from pipeline import warm_parser
    warm_parser()


def _get_pool():
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            _POOL = ProcessPoolExecutor(
                max_workers=max(1, int(os.environ.get("ML_CONCURRENCY", "1"))),
                mp_context=mp.get_context("spawn"),
                max_tasks_per_child=max(1, int(os.environ.get("ML_TASKS_PER_CHILD", "8"))),
                initializer=_worker_init)
        return _POOL


def _reset_pool(broken):
    global _POOL
    with _POOL_LOCK:
        if _POOL is broken:
            _POOL = None
    try:
        broken.shutdown(wait=False, cancel_futures=True)
    except Exception:  # noqa: BLE001
        pass


def _run_isolated(pdf_path, out_dir, pub_id, no_strip, domain="solar"):
    from concurrent.futures.process import BrokenProcessPool
    pool = _get_pool()
    try:
        return pool.submit(_child_run, str(pdf_path), str(out_dir), pub_id, no_strip, domain).result()
    except BrokenProcessPool:  # процесс убит (OOM по лимиту контейнера) — пул пересоздаётся для следующего запроса
        _reset_pool(pool)
        return {"perr": {"code": "ml_error", "retryable": False, "status": 500,
                         "message": "разбор прерван: процесс извлечения завершён (вероятно, не хватило памяти)"}}


@app.on_event("startup")
def _warm_pool():
    """[faktura-patch] Прогрев в фоне: по no-op на воркер, чтобы initializer загрузил layout-модель; /health не блокируется."""
    def go():
        try:
            pool = _get_pool()
            n = max(1, int(os.environ.get("ML_CONCURRENCY", "1")))
            for f in [pool.submit(int, 0) for _ in range(n)]:
                f.result()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=go, daemon=True).start()


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


NDJSON_TYPE = "application/x-ndjson"
NDJSON_ROWS = max(1, int(os.environ.get("ML_NDJSON_ROWS", "1000")))  # строк таблицы в одной строке потока


def wants_ndjson(accept: str | None, stream: str | None) -> bool:
    """[faktura-patch] §10: поток NDJSON — по заголовку `Accept: application/x-ndjson` или полю формы stream=ndjson.
    Поле формы сильнее заголовка (stream=json принудительно возвращает обычный JSON). По умолчанию — JSON."""
    s = (stream or "").strip().lower()
    if s in ("ndjson", "json"):
        return s == "ndjson"
    return NDJSON_TYPE in (accept or "").lower()


def _line(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def ndjson_events(body: dict, rows_per_line: int = NDJSON_ROWS):
    """[faktura-patch] Тело обычного ответа /extract -> строки NDJSON (bytes):
      {"type":"header","protocol":"ndjson/1","ok","model_version","publication_id","extract_error","failed_tables",
       "has_dataset","out_dir","meta"}
      {"type":"table","table":"results","rows":[... до rows_per_line строк ...]}   (сколько нужно, по порядку)
      {"type":"end","counts":{"таблица": строк},"ok":true}
    Сбой после первой строки -> {"type":"error","code","message","retryable","detail"} и поток закрывается без end."""
    try:
        ds = body.get("dataset")
        yield _line({"type": "header", "protocol": "ndjson/1", "ok": body.get("ok"),
                     "model_version": body.get("model_version"), "publication_id": body.get("publication_id"),
                     "extract_error": body.get("extract_error"), "failed_tables": body.get("failed_tables"),
                     "has_dataset": isinstance(ds, dict), "out_dir": body.get("out_dir"), "meta": body.get("meta")})
        counts = {}
        for table, rows in (ds.items() if isinstance(ds, dict) else ()):
            rows = rows if isinstance(rows, list) else []
            counts[table] = len(rows)
            for i in range(0, len(rows), rows_per_line):
                yield _line({"type": "table", "table": table, "rows": rows[i:i + rows_per_line]})
        yield _line({"type": "end", "counts": counts, "ok": body.get("ok")})
    except Exception as e:  # noqa: BLE001 - заголовок ответа уже отправлен: ошибка идёт в потоке
        msg = f"{type(e).__name__}: {e}"
        yield _line({"type": "error", "code": "ml_error", "message": msg, "retryable": False, "detail": msg})


@app.post("/extract")
def extract_pdf(request: Request,
                      file: UploadFile = File(...),
                      publication_id: str | None = Form(None),
                      no_strip: bool = Form(False),
                      domain: str = Form("solar"),  # [faktura-patch] solar | rag
                      stream: str | None = Form(None)):  # [faktura-patch] ndjson | json (иначе по Accept)
    """Загружает PDF, прогоняет конвейер, возвращает датасет и метаданные.

    Результат также сохраняется в файлы: /out/<id>/<id>.dataset.json и .meta.json.
    Повторная загрузка с тем же именем файла перезаписывает предыдущий результат.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="нет имени файла")
    domain = (domain or "solar").strip().lower()
    if domain not in ("solar", "rag"):  # [faktura-patch]
        return _error(422, "domain_not_supported", f"область {domain!r} не поддерживается (solar | rag)")

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
            out = _run_isolated(pdf_path, out_dir, pub_id, no_strip, domain)
    except Exception as e:  # noqa: BLE001 - сбой запуска процесса -> 500 ml_error
        return _error(500, "ml_error", f"{type(e).__name__}: {e}")
    if "perr" in out:
        e = out["perr"]
        headers = {"Retry-After": "60"} if e["code"] == "ocr_unavailable" else None
        return _error(e["status"], e["code"], e["message"], retryable=e["retryable"], headers=headers)
    if "err" in out:
        return _error(500, "ml_error", out["err"])
    result = out["result"]

    if wants_ndjson(request.headers.get("accept"), stream):  # [faktura-patch] §10
        body = {"ok": result["ok"], "model_version": result.get("model_version") or model_version(),
                "publication_id": result["publication_id"], "extract_error": result["extract_error"],
                "failed_tables": result["failed_tables"], "dataset": result["dataset"], "meta": result["meta"],
                "out_dir": result["out_dir"]}
        return StreamingResponse(ndjson_events(body), media_type=NDJSON_TYPE)

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
