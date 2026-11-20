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

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from pipeline import run_pipeline

app = FastAPI(title="pdf-dataset", version="1.0",
              description="PDF -> markdown (glmocr) -> dataset.json. Синхронный API без авторизации.")

OUT_DIR = Path(os.environ.get("OUT_DIR", "/out"))
# [faktura-patch] обычный def (FastAPI гонит его в пуле потоков) — /health отвечает во время разбора;
# сам конвейер — строго по одному запросу (glmocr тяжёлый).
_PIPELINE_LOCK = threading.Lock()


@app.get("/health")
def health():
    return {"status": "ok"}


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
            result = run_pipeline(pdf_path, out_dir=out_dir,
                                  publication_id=pub_id, no_strip=no_strip)
    except (FileNotFoundError, RuntimeError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "ok": result["ok"],
        "publication_id": result["publication_id"],
        "extract_error": result["extract_error"],
        "failed_tables": result["failed_tables"],
        "counts": result["meta"].get("counts"),
        "dataset": result["dataset"],
        "meta": result["meta"],
        "out_dir": result["out_dir"],
    }
