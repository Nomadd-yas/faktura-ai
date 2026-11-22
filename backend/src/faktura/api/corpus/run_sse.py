"""SSE прогресса разбора: GET /runs/{id}/events (CORE_API §4e).

Поток строится опросом БД раз в `POLL_S` секунд (работает и между процессами). Полезная нагрузка - те же словари,
что отдаёт GET /runs/{id} (`run_view`), поэтому значения совпадают с ответом GET в тот же момент.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anyio
from fastapi import Request

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...domain.services import Services
from .views import get_corpus, run_view

POLL_S = 2.0
PING_S = 15.0
FINAL_RUN = ("done", "error")
FINAL_FILE = ("done", "out", "nodata", "ocr", "error")
FILE_KEYS = ("id", "name", "title", "status", "status_label", "stage", "progress", "eta_seconds", "pages", "started_at",
             "has_data", "results", "issues", "reason")
RUN_KEYS = ("id", "status", "progress", "eta_seconds", "started_at", "updated_at", "counts", "results", "issues")
HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
           "Referrer-Policy": "no-referrer"}


def snapshot(services: Services, user: CurrentUser, run_id: str, cap: int | None) -> dict[str, Any]:
    """Синхронно: полное представление разбора (404 для чужого/неизвестного)."""
    with services.db.uow() as uow:
        return run_view(uow, get_corpus(uow, user, run_id), cap=cap)


def frame(event: str, data: dict, eid: int) -> str:
    return f"id: {eid}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _run_payload(view: dict) -> dict:
    return {k: view.get(k) for k in RUN_KEYS}


def _file_payload(f: dict) -> dict:
    return {k: f.get(k) for k in FILE_KEYS}


async def run_events(request: Request, services: Services, user: CurrentUser, run_id: str, first: dict,
                     cap: int | None) -> AsyncIterator[str]:
    eid = 0
    view = first
    seen_files: dict[str, dict] = {}
    seen_run: dict | None = None
    done_files: set[str] = set()
    idle = 0.0
    snap = True
    while True:
        out: list[str] = []
        for f in view["files"]:
            p = _file_payload(f)
            fin = p["status"] in FINAL_FILE
            if snap or seen_files.get(p["id"]) != p:
                eid += 1
                out.append(frame("file.progress", p, eid))
            if fin and p["id"] not in done_files:
                done_files.add(p["id"])
                if not snap:  # в снимке финальные файлы - обычный file.progress
                    eid += 1
                    out.append(frame("file.done", p, eid))
            seen_files[p["id"]] = p
        rp = _run_payload(view)
        if snap or rp != seen_run:
            eid += 1
            out.append(frame("run.progress", rp, eid))
        seen_run = rp
        snap = False
        final = rp["status"] in FINAL_RUN
        if final:
            eid += 1
            out.append(frame("run.done", rp, eid))
        for chunk in out:
            yield chunk
        if out:
            idle = 0.0
        if final:
            return
        # ждём следующего опроса, по пути проверяя разрыв и отправляя keep-alive
        waited = 0.0
        while waited < POLL_S:
            step = min(0.25, POLL_S - waited)
            await anyio.sleep(step)
            waited += step
            idle += step
            if await request.is_disconnected():
                return
            if idle >= PING_S:
                idle = 0.0
                yield ": ping\n\n"
        try:
            view = await anyio.to_thread.run_sync(snapshot, services, user, run_id, cap)
        except AppError:  # разбор удалён во время потока
            return
