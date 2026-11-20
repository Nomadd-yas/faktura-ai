"""Удалённый режим CLI (P2-R, api_contract У-31): `faktura extract --server URL`.

Шаги через API: POST /auth/login (почта и пароль из FAKTURA_EMAIL/FAKTURA_PASSWORD) -> POST /corpora ->
POST /corpora/{id}/files (пачками) -> POST /corpora/{id}/runs -> опрос GET /runs/{id} -> POST /exports
{format: submission} -> опрос GET /exports/{id} -> GET файла -> распаковка ZIP в --out.
Ключей API в волне 1 нет. Коды выхода - как у локального extract: 0, 1, 2, 3.
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse

from .extract import EXIT_CHECK, EXIT_OK, EXIT_PARTIAL, ExtractOptions, UsageProblem

API_PREFIX = "/api/v1"
POLL_S = 2.0
MAX_WAIT_S = 6 * 3600
BATCH_FILES = 200
BATCH_BYTES = 900 * 1024 * 1024
FINAL_RUN = ("done", "done_with_errors", "cancelled", "failed")
API_KEY_MESSAGE = "Ключи API появятся позже; задайте FAKTURA_EMAIL и FAKTURA_PASSWORD"


class RemoteError(Exception):
    """Сбой удалённого режима: код 1, без трассировки."""


def api_base(server: str) -> str:
    s = (server or "").strip()
    u = urlparse(s)
    if u.scheme not in ("http", "https") or not u.netloc or " " in s:
        raise UsageProblem(f"--server: нужен адрес вида https://host[:port], получено «{server}»")
    base = s.rstrip("/")
    if not base.endswith(API_PREFIX):
        base += API_PREFIX
    return base


def _sleep(s: float) -> None:
    time.sleep(s)


def _error_text(resp) -> str:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return f"HTTP {resp.status_code}"
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"{err.get('code') or resp.status_code}: {err.get('message') or ''}".strip()
    return f"HTTP {resp.status_code}"


def _id(body: dict, *keys: str) -> str:
    for k in keys:
        v = body.get(k)
        if isinstance(v, str) and v:
            return v
    raise RemoteError(f"в ответе сервера нет идентификатора ({', '.join(keys)})")


def _safe_extract(data: bytes, out: Path) -> list[str]:
    names: list[str] = []
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if not parts or any(p in ("..", "") or ":" in p for p in parts) or parts[0].startswith("/"):
                continue
            dest = out.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info))
            names.append("/".join(parts))
    return names


def run_remote(o: ExtractOptions, server: str, err: Callable[[str], None]) -> int:
    import httpx

    from ..pdfio.inputs import collect_inputs

    base = api_base(server)
    email = (os.environ.get("FAKTURA_EMAIL") or "").strip()
    password = os.environ.get("FAKTURA_PASSWORD") or ""
    if not email or not password:
        raise UsageProblem("Для --server задайте FAKTURA_EMAIL и FAKTURA_PASSWORD")
    collected = collect_inputs(Path(o.in_path))
    if not collected.files:
        raise UsageProblem(f"Во входе нет PDF: {o.in_path}")

    def url(path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith(API_PREFIX):
            return base[: -len(API_PREFIX)] + path
        return base + path

    try:
        with httpx.Client(timeout=httpx.Timeout(300.0)) as client:
            r = client.post(url("/auth/login"), json={"email": email, "password": password, "remember": False})
            if r.status_code != 200:
                raise RemoteError(f"Вход не выполнен ({_error_text(r)})")
            token = r.json().get("access_token")
            if not token:
                raise RemoteError("Сервер не выдал токен доступа")
            h = {"Authorization": f"Bearer {token}"}

            def call(method: str, path: str, ok: tuple[int, ...] = (200, 201, 202), **kw: Any):
                resp = client.request(method, url(path), headers=h, **kw)
                if resp.status_code not in ok:
                    raise RemoteError(f"{method} {path}: {_error_text(resp)}")
                return resp

            name = f"CLI {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            body: dict[str, Any] = {"name": name, "collection": o.domain}
            if not o.profile.builtin:
                body["profile_id"] = o.profile.profile_id
            corpus = call("POST", "/corpora", json=body).json()
            cid = _id(corpus, "corpus_id", "id")
            err(f"Корпус {cid}: загрузка {len(collected.files)} PDF")

            batch: list[tuple[str, tuple[str, bytes, str]]] = []
            size = 0
            for f in collected.files:
                data = Path(f.path).read_bytes()
                if batch and (len(batch) >= BATCH_FILES or size + len(data) > BATCH_BYTES):
                    call("POST", f"/corpora/{cid}/files", files=batch)
                    batch, size = [], 0
                batch.append(("file", (f.name, data, "application/pdf")))
                size += len(data)
            if batch:
                call("POST", f"/corpora/{cid}/files", files=batch)

            run = call("POST", f"/corpora/{cid}/runs",
                       json={"kind": "full", "model_version": o.model or "latest", "concurrency": o.concurrency,
                             "keep_review": True}).json()
            rid = _id(run, "run_id", "id")
            waited = 0.0
            while True:
                st = call("GET", f"/runs/{rid}").json()
                status = str(st.get("status") or "")
                if status in FINAL_RUN:
                    break
                prog = st.get("progress")
                if st.get("paused"):
                    err("ML-1 недоступен: прогон на паузе, ждём")
                elif prog is not None:
                    err(f"Прогон {rid}: {status} {prog}")
                if waited > MAX_WAIT_S:
                    raise RemoteError(f"Прогон {rid} не завершился за {MAX_WAIT_S // 3600} ч")
                _sleep(POLL_S)
                waited += POLL_S
            err(f"Прогон {rid}: {status}")

            exp = call("POST", "/exports", json={"format": "submission", "scope": {"corpus_id": cid},
                                                 "validate": True}).json()
            eid = _id(exp, "export_id", "id")
            waited = 0.0
            while True:
                e = call("GET", f"/exports/{eid}").json()
                est = str(e.get("status") or "")
                if est in ("done", "failed"):
                    break
                if waited > MAX_WAIT_S:
                    raise RemoteError(f"Выгрузка {eid} не готова за {MAX_WAIT_S // 3600} ч")
                _sleep(POLL_S)
                waited += POLL_S
            file_url = e.get("url") or f"/exports/{eid}/file"
            fr = call("GET", file_url)
            names = _safe_extract(fr.content, Path(o.out_dir))
            err(f"Набор сдачи распакован в {o.out_dir} ({len(names)} файлов)")
            check = (e.get("check") or {}).get("status") or fr.headers.get("X-Check-Status")
            if est == "failed" or check == "failed":
                msg = (e.get("error") or {}).get("message") if isinstance(e.get("error"), dict) else None
                err(f"Проверка сдачи не пройдена: {msg or 'см. check_report.txt'}")
                return EXIT_CHECK
            if status != "done":
                err(f"Прогон завершился со статусом {status}: часть файлов не разобрана")
                return EXIT_PARTIAL
            return EXIT_OK
    except httpx.HTTPError as ex:
        raise RemoteError(f"Сервер {server} недоступен: {type(ex).__name__}: {ex}") from ex
