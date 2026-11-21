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

import ask  # [faktura-patch] вопросы по разбору
from pipeline import (PipelineError, check_pdf, config_path, model_version, ocr_reachable,
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


# [faktura-patch] «Вопросы по разбору»: два небольших вызова LLM (см. ask.py), не занимают очередь разбора статей
@app.post("/ask/plan")
async def ask_plan(request: Request):
    return await _ask_call(request, lambda b: ask.plan(b.get("question"), b.get("run")))


@app.post("/ask/write")
async def ask_write(request: Request):
    return await _ask_call(request, lambda b: ask.write(b.get("question"), b.get("intent"), b.get("facts"), b.get("draft")))


# [faktura-patch] прокси LLM для агента чата разбора: OpenAI-сообщения и tools -> сообщение ассистента (+ meta)
@app.post("/llm/chat")
async def llm_chat(request: Request):
    return await _ask_call(request, lambda b: ask.chat(b.get("messages"), b.get("tools"), b.get("max_tokens"),
                                                       b.get("temperature", 0.0), b.get("reasoning", False),
                                                       b.get("tool_choice")))


async def _ask_call(request: Request, fn):
    from starlette.concurrency import run_in_threadpool
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except Exception:  # noqa: BLE001
        return _error(400, "bad_request", "Ожидается JSON-объект")
    try:
        return JSONResponse(content=await run_in_threadpool(fn, body))
    except ask.AskError as e:
        return _error(e.status, e.code, e.message, retryable=e.retryable)
    except Exception as e:  # noqa: BLE001
        return _error(500, "ml_error", f"{type(e).__name__}: {e}")


# ---------- [faktura-patch] POST /ocr: блочный OCR (glmocr) постранично, с дисковым кешем ----------
# Отдельный маленький пул (OCR_CONCURRENCY, по умолчанию 2): парсер glmocr прогрет в воркере, память ограничена
# (воркер перезапускается каждые ML_TASKS_PER_CHILD запросов), очередь /extract не занимается.
_OCR_POOL = None


def _ocr_concurrency() -> int:
    return max(1, int(os.environ.get("OCR_CONCURRENCY", "2")))


def _get_ocr_pool():
    global _OCR_POOL
    with _POOL_LOCK:
        if _OCR_POOL is None:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            _OCR_POOL = ProcessPoolExecutor(
                max_workers=_ocr_concurrency(), mp_context=mp.get_context("spawn"),
                max_tasks_per_child=max(1, int(os.environ.get("ML_TASKS_PER_CHILD", "8"))),
                initializer=_worker_init)
        return _OCR_POOL


def _run_ocr_isolated(pdf_path, pages, work):
    """-> результат ocr_service.ocr_blocks_child; сломанный пул пересоздаётся."""
    global _OCR_POOL
    from concurrent.futures.process import BrokenProcessPool
    import ocr_service
    pool = _get_ocr_pool()
    try:
        return pool.submit(ocr_service.ocr_blocks_child, str(pdf_path), list(pages), str(work)).result()
    except BrokenProcessPool:
        with _POOL_LOCK:
            if _OCR_POOL is pool:
                _OCR_POOL = None
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass
        return {"perr": {"code": "ml_error", "retryable": False, "status": 500,
                         "message": "OCR прерван: процесс завершён (вероятно, не хватило памяти)"}}


@app.post("/ocr")
def ocr_pdf(file: UploadFile = File(...),
            pages: str | None = Form(None),     # "1,3,5-7"; по умолчанию все страницы
            format: str = Form("both")):        # markdown | blocks | both
    """Блочный OCR выбранных страниц PDF. Ответ: {pages:[{page, markdown?, blocks?, confidence?, seconds, page_size_pt}],
    model, seconds, cached, bbox_format}. bbox блока — нормализованные 0..1000 [x1,y1,x2,y2] от левого верхнего угла."""
    import hashlib
    import time
    import ocr_service as osv
    import pymupdf
    t0 = time.time()
    fmt = (format or "both").strip().lower()
    if fmt not in osv.FORMATS:
        return _error(400, "bad_request", f"format: {fmt!r} не из {osv.FORMATS}")
    data = file.file.read()
    sha = hashlib.sha256(data).hexdigest()
    work = osv.work_dir(OUT_DIR)
    try:
        pdf_path = work / "in.pdf"
        pdf_path.write_bytes(data)
        try:
            n = check_pdf(pdf_path)
        except PipelineError as e:
            return _error(e.status, e.code, e.message, retryable=e.retryable)
        try:
            plist = osv.parse_pages(pages, n)
        except osv.OcrRequestError as e:
            return _error(e.status, e.code, e.message)
        if len(plist) > osv.max_pages():
            return _error(413, "too_many_pages", f"страниц {len(plist)} > OCR_MAX_PAGES={osv.max_pages()}")
        try:
            cfg = config_path()
        except PipelineError as e:
            return _error(e.status, e.code, e.message)
        chash = osv.config_hash(cfg)
        cache = osv.OcrCache(OUT_DIR / "ocr_cache")
        entries = {p: cache.get(sha, p, chash) for p in plist}
        missing = [p for p in plist if entries[p] is None]
        if missing:
            if not ocr_reachable(cfg, timeout=2.0):
                return _error(503, "ocr_unavailable", "OCR-сервер (vLLM) недоступен", retryable=True,
                              headers={"Retry-After": "60"})
            try:
                out = _run_ocr_isolated(pdf_path, missing, work)
            except Exception as e:  # noqa: BLE001
                return _error(500, "ml_error", f"{type(e).__name__}: {e}")
            if "perr" in out:
                e = out["perr"]
                return _error(e["status"], e["code"], e["message"], retryable=e["retryable"],
                              headers={"Retry-After": "60"} if e["code"] == "ocr_unavailable" else None)
            if "err" in out:
                return _error(500, "ml_error", out["err"])
            for p in missing:
                entries[p] = out["pages"][p]
                cache.put(sha, p, chash, entries[p])
        sizes = {}
        try:
            doc = pymupdf.open(str(pdf_path))
            sizes = {p: [round(doc[p - 1].rect.width, 2), round(doc[p - 1].rect.height, 2)] for p in plist}
            doc.close()
        except Exception:  # noqa: BLE001
            pass
        return osv.build_response(entries, plist, fmt, model=osv.ocr_model_name(cfg), seconds=time.time() - t0,
                                  cached=not missing, sizes=sizes)
    finally:
        osv.cleanup(work)


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
    uploads = OUT_DIR / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    # [faktura-patch] рабочее имя = имя + область + хеш содержимого: P001 солнечного и P001 RAG-кейса - разные статьи;
    # раньше файл писался в uploads/<имя>.pdf до блокировки конвейера, и параллельный запрос мог его подменить
    import hashlib
    import uuid
    tmp = uploads / f".{safe}.{uuid.uuid4().hex}.part"
    h = hashlib.sha256()
    with tmp.open("wb") as f:
        while chunk := file.file.read(1 << 20):
            h.update(chunk)
            f.write(chunk)
    key = f"{safe}-{domain}-{h.hexdigest()[:10]}"
    pdf_path = uploads / f"{key}.pdf"
    os.replace(tmp, pdf_path)
    out_dir = OUT_DIR / key

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
