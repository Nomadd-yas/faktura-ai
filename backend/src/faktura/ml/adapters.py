"""Реализации ExtractAdapter (план 5.2, Б-1): мок на dev_gold, HTTP к контейнеру ML-1, сырой каталог, кеш.

Конвейер работает только через протокол `domain.ports.ExtractAdapter`; реальный контейнер ML-1
подключается настройками (`FAKTURA_EXTRACTOR=http`, `FAKTURA_EXTRACT_URL`, таймаут, ретраи,
параллельность) без правок конвейера.
"""
from __future__ import annotations

import email.utils
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal

from ..config import Settings
from ..domain.errors import AppError, ExtractError
from ..domain.extraction import ExtractOutcome, ExtractRequest, MlHealth
from ..domain.io import parse_cell, read_tables
from ..domain.ports import ExtractAdapter, FileStore
from ..domain.tables import INTEGER_FIELDS, TABLES
from .response import BACKEND_RESULT_FIELDS, ROW_TABLES, parse_extract_response

log = logging.getLogger(__name__)

# правила по числам, которые ML может не делать (spec/07 §7.0.1) - их считает бэк (К-15);
# режим мока ml_only убирает их из ответа
NUMERIC_CHECKS: frozenset[str] = frozenset({
    "jv_consistency", "voc_limit", "range", "eqe_consistency", "champion_vs_mean", "hysteresis_index",
})
PDF_ERROR_CODES = frozenset({"pdf_no_text", "pdf_encrypted", "pdf_broken"})
RETRY_STATUSES = frozenset({429, 502, 503, 504})
MAX_RETRY_AFTER_S = 600.0
SUPPORTED_DOMAINS = ("solar",)


# ---------------------------------------------------------------------------------------------
# мок на dev_gold
# ---------------------------------------------------------------------------------------------

def _json_value(table: str, col: str, text: str | None) -> Any:
    """Ячейка эталона -> значение JSON §7.0.1: числа - числами (если float передаёт их точно), списки - массивами."""
    v = parse_cell(table, col, text)
    if isinstance(v, Decimal):
        if (table, col) in INTEGER_FIELDS and v == v.to_integral_value():
            return int(v)
        f = float(v)
        return f if Decimal(repr(f)) == v else format(v, "f")
    return v


class MockGoldExtractor:
    """Строит ответ §7.0.1 из dev_gold через domain.io и пропускает через parse_extract_response.
    Замечания эталона - computed_by=ml, confidence=1.0; неизвестный publication_id - ответ только с publication.
    model_version = 'mock-gold-<sha8 эталона>'. Ответы мока не кешируются."""
    name = "mock"

    def __init__(self, gold_dir: Path, issues_mode: Literal["all", "ml_only"] = "all") -> None:
        if issues_mode not in ("all", "ml_only"):
            raise ValueError(f"issues_mode: all | ml_only, получено {issues_mode!r}")
        self.gold_dir = Path(gold_dir) if gold_dir is not None else None
        self.issues_mode = issues_mode
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, list[dict[str, str]]]] | None = None
        self._version: str | None = None

    # --- загрузка эталона ---
    def _load(self) -> None:
        if self._rows is not None:
            return
        with self._lock:
            if self._rows is not None:
                return
            gd = self.gold_dir
            if gd is None or not gd.is_dir() or not (gd / "publications.csv").is_file():
                raise ExtractError("ml_error", f"Нет эталона для мока: {gd}", retryable=False)
            import hashlib

            h = hashlib.sha256()
            for t in TABLES:
                p = gd / f"{t}.csv"
                h.update(t.encode())
                h.update(p.read_bytes() if p.is_file() else b"")
            tables = read_tables(gd)
            rows: dict[str, dict[str, list[dict[str, str]]]] = {}
            for t in TABLES:
                for r in tables.get(t, []):
                    pid = (r.get("publication_id") or "").strip()
                    if pid:
                        rows.setdefault(pid, {}).setdefault(t, []).append(r)
            self._version = f"mock-gold-{h.hexdigest()[:8]}"
            self._rows = rows

    @property
    def model_version(self) -> str:
        self._load()
        assert self._version is not None
        return self._version

    def publication_ids(self) -> list[str]:
        self._load()
        return list(self._rows or {})

    def _row_json(self, table: str, row: dict[str, str]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for col in TABLES[table]:
            if table == "results" and col in BACKEND_RESULT_FIELDS:
                continue  # ML их не заполняет (§7.0.1)
            out[col] = _json_value(table, col, row.get(col))
        for k, v in row.items():  # колонки вне шаблона - как есть
            if k is not None and k not in TABLES[table]:
                out[k] = v
        return out

    def build_raw(self, publication_id: str) -> dict[str, Any]:
        """Ответ §7.0.1 для статьи; его же отдаёт mock_server."""
        self._load()
        paper = (self._rows or {}).get(publication_id)
        raw: dict[str, Any] = {"model_version": self._version}
        if not paper:
            raw["publication"] = {"publication_id": publication_id}
            for t in ROW_TABLES:
                raw[t] = []
            return raw
        pubs = paper.get("publications") or [{"publication_id": publication_id}]
        raw["publication"] = self._row_json("publications", pubs[0])
        raw["publication"]["publication_id"] = publication_id
        for t in ROW_TABLES:
            recs = []
            for r in paper.get(t, []):
                if t == "issues" and self.issues_mode == "ml_only" and (r.get("check_name") or "").strip() in NUMERIC_CHECKS:
                    continue
                rec = self._row_json(t, r)
                if t == "issues":
                    rec["_confidence"] = 1.0
                recs.append(rec)
            raw[t] = recs
        return raw

    def extract(self, req: ExtractRequest) -> ExtractOutcome:
        return parse_extract_response(self.build_raw(req.publication_id), req.publication_id)

    def health(self) -> MlHealth:
        try:
            return MlHealth(status="ok", model_version=self.model_version)
        except (ExtractError, OSError):
            return MlHealth(status="down", model_version=None)


# ---------------------------------------------------------------------------------------------
# HTTP к контейнеру ML-1
# ---------------------------------------------------------------------------------------------

def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    v = value.strip()
    try:
        s = float(v)
        return s if s >= 0 else None
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((dt - datetime.now(timezone.utc)).total_seconds(), 0.0)


def _error_body(resp) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - тело ошибки может быть не JSON
        return {}
    return body if isinstance(body, dict) else {}


class HttpExtractor:
    """multipart: file, publication_id, domain, fields (JSON), request_id.
    Ретраи: 429 (Retry-After), 502, 503, 504, обрыв соединения; 400/413/422 без ретраев.
    Коды ExtractError: ml_unavailable (retryable), ml_timeout, ml_schema_mismatch,
    pdf_no_text|pdf_encrypted|pdf_broken (из 422), ml_error."""
    name = "http"

    def __init__(self, base_url: str, timeout_s: float = 330, retry_delays: tuple[float, ...] = (30, 120, 600),
                 concurrency: int = 2, sleep: Callable[[float], None] = time.sleep, transport: Any = None,
                 health_timeout_s: float = 10.0) -> None:
        if not base_url or not str(base_url).strip():
            raise AppError("validation_error", "Не задан адрес ML-1 (FAKTURA_EXTRACT_URL / --extract-url)")
        self.base_url = str(base_url).strip()
        self.timeout_s = float(timeout_s)
        self.retry_delays = tuple(float(d) for d in retry_delays)
        self.concurrency = max(int(concurrency), 1)
        self.sleep = sleep
        self.transport = transport
        self.health_timeout_s = float(health_timeout_s)
        self._sem = threading.BoundedSemaphore(self.concurrency)
        self._client = None
        self._client_lock = threading.Lock()

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def _get_client(self):
        import httpx

        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    kw: dict[str, Any] = {"timeout": httpx.Timeout(self.timeout_s)}
                    if self.transport is not None:
                        kw["transport"] = self.transport
                    self._client = httpx.Client(**kw)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- извлечение ---
    def _form(self, req: ExtractRequest, request_id: str) -> dict[str, str]:
        data = {"publication_id": req.publication_id, "domain": req.domain or "solar", "request_id": request_id}
        if req.fields:
            data["fields"] = json.dumps([f.model_dump(mode="json") for f in req.fields], ensure_ascii=False)
        return data

    def extract(self, req: ExtractRequest) -> ExtractOutcome:
        import httpx

        pdf_path = Path(req.pdf_path)
        try:
            content = pdf_path.read_bytes()
        except OSError as e:
            raise ExtractError("ml_error", f"Не прочитан файл {pdf_path.name}: {e}") from e
        request_id = req.request_id or f"{req.publication_id}-{uuid.uuid4().hex[:12]}"
        form = self._form(req, request_id)
        filename = f"{req.publication_id}.pdf"
        client = self._get_client()
        attempts = len(self.retry_delays) + 1
        last: ExtractError | None = None
        for attempt in range(attempts):
            retry_after: float | None = None
            try:
                with self._sem:
                    resp = client.post(self._url("/ml/extract"), data=form,
                                       files={"file": (filename, content, "application/pdf")})
            except httpx.TimeoutException as e:
                last = ExtractError("ml_timeout", f"ML-1 не ответил за {self.timeout_s:g} с", retryable=True)
                log.warning("ML-1 %s: таймаут (попытка %d/%d)", req.publication_id, attempt + 1, attempts)
                last.__cause__ = e
            except httpx.TransportError as e:  # ConnectError, ReadError, RemoteProtocolError ...
                last = ExtractError("ml_unavailable", f"Нет связи с ML-1: {type(e).__name__}", retryable=True)
                log.warning("ML-1 %s: %s (попытка %d/%d)", req.publication_id, type(e).__name__, attempt + 1, attempts)
                last.__cause__ = e
            else:
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                    except Exception as e:  # noqa: BLE001
                        raise ExtractError("ml_schema_mismatch", "Ответ ML-1 не JSON") from e
                    return parse_extract_response(data, req.publication_id)
                body = _error_body(resp)
                code = str(body.get("code") or "")
                message = str(body.get("message") or "") or f"HTTP {resp.status_code}"
                details = {"status": resp.status_code, "body": body or None, "request_id": request_id}
                if resp.status_code in RETRY_STATUSES:
                    if resp.status_code == 504:
                        last = ExtractError("ml_timeout", f"ML-1: таймаут ({message})", retryable=True,
                                            details=details)
                    else:
                        last = ExtractError("ml_unavailable", f"ML-1 недоступен: {message}", retryable=True,
                                            details=details)
                    if resp.status_code in (429, 503):
                        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                    log.warning("ML-1 %s: HTTP %d (попытка %d/%d)", req.publication_id, resp.status_code,
                                attempt + 1, attempts)
                elif resp.status_code == 422:
                    c = code if code in PDF_ERROR_CODES else "ml_error"
                    raise ExtractError(c, f"ML-1: {message}", retryable=False, details=details)
                elif resp.status_code in (400, 413):
                    raise ExtractError("ml_error", f"ML-1 отклонил запрос (HTTP {resp.status_code}): {message}",
                                       retryable=False, details=details)
                else:
                    raise ExtractError("ml_error", f"ML-1: HTTP {resp.status_code}: {message}",
                                       retryable=bool(body.get("retryable", False)), details=details)
            if attempt < attempts - 1:
                delay = self.retry_delays[attempt]
                if retry_after is not None:
                    delay = min(retry_after, max(MAX_RETRY_AFTER_S, *self.retry_delays))
                self.sleep(delay)
        assert last is not None
        raise last

    # --- health ---
    def health(self) -> MlHealth:
        import httpx

        try:
            resp = self._get_client().get(self._url("/health"), timeout=min(self.health_timeout_s, self.timeout_s))
        except httpx.HTTPError:
            return MlHealth(status="down", model_version=None)
        if resp.status_code != 200:
            ra = _parse_retry_after(resp.headers.get("Retry-After"))
            body = _error_body(resp)
            mv = body.get("model_version") if isinstance(body.get("model_version"), str) else None
            return MlHealth(status="down", model_version=mv, retry_after_s=int(ra) if ra is not None else None)
        body = _error_body(resp)
        if not body:
            return MlHealth(status="down", model_version=None)
        status = body.get("status")
        mv = body.get("model_version")
        mv = str(mv) if isinstance(mv, (str, int, float)) and not isinstance(mv, bool) and str(mv) else None
        if status not in ("ok", "degraded", "down"):
            status = "degraded"
        return MlHealth(status=status, model_version=mv)


# ---------------------------------------------------------------------------------------------
# сырой каталог (import-raw)
# ---------------------------------------------------------------------------------------------

class RawDirExtractor:
    """<raw_dir>/<publication_id>.json; health всегда ok."""
    name = "raw"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = Path(raw_dir)

    def extract(self, req: ExtractRequest) -> ExtractOutcome:
        p = self.raw_dir / f"{req.publication_id}.json"
        if not p.is_file():
            raise ExtractError("ml_error", f"Нет файла ответа ML: {p.name} в {self.raw_dir}", retryable=False)
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            raise ExtractError("ml_schema_mismatch", f"{p.name}: не JSON ({e})") from e
        return parse_extract_response(data, req.publication_id)

    def health(self) -> MlHealth:
        return MlHealth(status="ok", model_version=None)


# ---------------------------------------------------------------------------------------------
# кеш сырых ответов
# ---------------------------------------------------------------------------------------------

_SAFE_VERSION = re.compile(r"[^A-Za-z0-9._+-]+")
_SAFE_SHA = re.compile(r"^[0-9a-fA-F]{8,128}$")
LAST_FILE = "_last.json"
STORE_PREFIX = "ml_raw/"


def _version_file(version: str) -> str:
    v = _SAFE_VERSION.sub("_", str(version)).strip("._") or "unknown"
    if v == LAST_FILE[:-5]:
        v = "v" + v
    return f"{v}.json"


class MlCache:
    """Кеш сырых ответов: {sha256}/{model_version}.json и {sha256}/_last.json (REQ-066).

    `store` - каталог на диске (CLI, по умолчанию `settings.cache_dir`, вне `--out`) или FileStore
    (API: ключи `ml_raw/{sha256}/...`). `_last.json` хранит последнюю версию для этого sha256 и нужен
    для работы без ML (возобновление офлайн).
    """

    def __init__(self, store: FileStore | Path) -> None:
        self.store = store
        self._lock = threading.Lock()

    @property
    def _is_dir(self) -> bool:
        return isinstance(self.store, (str, os.PathLike))

    @staticmethod
    def _check_sha(sha256: str) -> str:
        if not isinstance(sha256, str) or not _SAFE_SHA.match(sha256):
            raise ValueError(f"Некорректный sha256: {sha256!r}")
        return sha256.lower()

    def _read(self, sha256: str, fname: str) -> bytes | None:
        if self._is_dir:
            p = Path(self.store) / sha256 / fname
            try:
                return p.read_bytes()
            except OSError:
                return None
        key = f"{STORE_PREFIX}{sha256}/{fname}"
        try:
            if not self.store.exists(key):
                return None
            return self.store.get_bytes(key)
        except (OSError, KeyError):
            return None

    def _write(self, sha256: str, fname: str, data: bytes) -> None:
        if self._is_dir:
            d = Path(self.store) / sha256
            d.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, d / fname)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
            return
        self.store.put_bytes(f"{STORE_PREFIX}{sha256}/{fname}", data, "application/json")

    def get(self, sha256: str, version: str) -> dict | None:
        sha = self._check_sha(sha256)
        b = self._read(sha, _version_file(version))
        if b is None:
            return None
        try:
            data = json.loads(b.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            log.warning("Кеш ML: битый файл %s/%s - пропущен", sha, version)
            return None
        return data if isinstance(data, dict) else None

    def put(self, sha256: str, version: str, raw: dict) -> None:
        sha = self._check_sha(sha256)
        body = json.dumps(raw, ensure_ascii=False, default=str).encode("utf-8")
        last = json.dumps({"model_version": str(version), "file": _version_file(version),
                           "saved_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False).encode("utf-8")
        with self._lock:
            self._write(sha, _version_file(version), body)
            self._write(sha, LAST_FILE, last)

    def last_version(self, sha256: str) -> str | None:
        sha = self._check_sha(sha256)
        b = self._read(sha, LAST_FILE)
        if b is None:
            return None
        try:
            data = json.loads(b.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        v = data.get("model_version") if isinstance(data, dict) else None
        return v if isinstance(v, str) and v else None

    def get_last(self, sha256: str) -> tuple[str, dict] | None:
        v = self.last_version(sha256)
        if v is None:
            return None
        raw = self.get(sha256, v)
        return (v, raw) if raw is not None else None


class CachedExtractor:
    """Второй вызов с тем же sha256 и версией не обращается к inner; при недоступном ML берётся _last.json.

    Версия `latest` разрешается через `inner.health()` (конвейер обычно разрешает её один раз на прогон
    и передаёт конкретную). Неретраимые ошибки (pdf_encrypted и т. п.) кешем не маскируются и не кешируются.
    """

    def __init__(self, inner: ExtractAdapter, cache: MlCache) -> None:
        self.inner = inner
        self.cache = cache
        self.name = inner.name

    def _from_cache(self, req: ExtractRequest, version: str, raw: dict, note: str | None = None) -> ExtractOutcome:
        out = parse_extract_response(raw, req.publication_id)
        out.from_cache = True
        if note:
            out.warnings.append(note)
        if out.model_version != version and out.model_version == "unknown":
            out.model_version = version
        return out

    def _offline(self, req: ExtractRequest, err: ExtractError) -> ExtractOutcome:
        hit = self.cache.get_last(req.sha256)
        if hit is None:
            raise err
        v, raw = hit
        return self._from_cache(req, v, raw, f"ML недоступен: взят сохранённый ответ версии {v}")

    def extract(self, req: ExtractRequest) -> ExtractOutcome:
        version = req.model_version
        if not version or version == "latest":
            h = self.inner.health()
            if h.status == "down":
                return self._offline(req, ExtractError(
                    "ml_unavailable", "Сервис извлечения недоступен, сохранённого ответа нет", retryable=True))
            version = h.model_version
        if version:
            raw = self.cache.get(req.sha256, version)
            if raw is not None:
                return self._from_cache(req, version, raw)
        try:
            out = self.inner.extract(req.model_copy(update={"model_version": version or req.model_version}))
        except ExtractError as e:
            if e.retryable and e.code in ("ml_unavailable", "ml_timeout"):
                return self._offline(req, e)
            raise
        if out.raw:
            try:
                self.cache.put(req.sha256, out.model_version, out.raw)
            except (OSError, TypeError, ValueError) as e:  # кеш - оптимизация: сбой записи не роняет статью
                log.warning("Кеш ML: не записан ответ %s: %s", req.publication_id, e)
        return out

    def health(self) -> MlHealth:
        return self.inner.health()


def resolve_model_version(requested: str, extractor: ExtractAdapter) -> str | None:
    """'latest' -> health().model_version; None, если ML недоступен. Конкретная версия возвращается как есть.

    Если сервис жив, но версию не сообщает (raw), возвращается имя адаптера.
    """
    if requested and requested != "latest":
        return requested
    h = extractor.health()
    if h.status == "down":
        return None
    return h.model_version or getattr(extractor, "name", None) or "unknown"


def build_extractor(settings: Settings, *, kind: str | None = None, url: str | None = None,
                    raw_dir: Path | None = None, retry_delays: tuple[float, ...] | None = None,
                    cache: MlCache | None = None) -> ExtractAdapter:
    """kind mock|http|raw (по умолчанию http, если задан url, иначе settings.extractor); кеш - для http/raw."""
    k = kind or ("http" if url else settings.extractor)
    if k == "mock":
        if settings.mock_gold_dir is None:
            raise AppError("validation_error", "Для мока нужен эталон: FAKTURA_MOCK_GOLD_DIR / --gold-dir")
        return MockGoldExtractor(Path(settings.mock_gold_dir), settings.mock_issues)
    if k == "http":
        base = url or settings.extract_url
        if not base:
            raise AppError("validation_error", "Для извлечения через ML-1 нужен адрес: FAKTURA_EXTRACT_URL / --extract-url")
        ext: ExtractAdapter = HttpExtractor(
            base, timeout_s=settings.extract_timeout_s,
            retry_delays=tuple(retry_delays) if retry_delays is not None else settings.retry_delays,
            concurrency=settings.extract_concurrency)
    elif k == "raw":
        if raw_dir is None:
            raise AppError("validation_error", "Для import-raw нужен каталог ответов ML: --raw-dir / --in")
        ext = RawDirExtractor(Path(raw_dir))
    else:
        raise AppError("validation_error", f"Неизвестный экстрактор: {k} (mock | http | raw)")
    return CachedExtractor(ext, cache) if cache is not None else ext
