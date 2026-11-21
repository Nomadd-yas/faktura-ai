"""Сборка контейнера зависимостей (план 5.6). Единственное место, где выбираются реализации портов.

`build_services(settings, **overrides)` - для API и CLI; `make_extractor(...)` - выбор адаптера ML-1 по настройкам
(мок на dev_gold, HTTP к контейнеру ML-1 с кешем ответов, сырой каталог). Реальный контейнер ML-1 подключается
настройками (`FAKTURA_EXTRACTOR=http`, `FAKTURA_EXTRACT_URL`, таймаут, ретраи, параллельность) без правок конвейера.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..domain.errors import AppError
from ..domain.extraction import ExtractOutcome, ExtractRequest
from ..domain.ports import ExtractAdapter
from ..domain.services import Services

log = logging.getLogger(__name__)

_SERVICE_FIELDS = frozenset(f.name for f in dataclasses.fields(Services))


def _sleep(seconds: float) -> None:
    """Позднее связывание time.sleep: тесты подменяют его, и ретраи HTTP-адаптера не ждут реального времени."""
    time.sleep(seconds)


def _versioned_cache_class():
    from ..ml.adapters import CachedExtractor

    class VersionedCachedExtractor(CachedExtractor):
        """Кеш ответов ML-1, который находит ответ и по запрошенной версии.

        Прогон спрашивает версию у `/health` (например, `extract-0.3`) и ищет ответ в кеше по ней; сам ответ
        может нести другую строку `model_version`. Поэтому сырой ответ сохраняется ещё и под запрошенной версией:
        повторный запуск и второй файл с тем же содержимым не вызывают ML (REQ-066).
        """

        def extract(self, req: ExtractRequest) -> ExtractOutcome:
            out = super().extract(req)
            v = req.model_version
            if not out.from_cache and out.raw and v and v != "latest" and v != out.model_version:
                try:
                    self.cache.put(req.sha256, v, out.raw)
                except (OSError, TypeError, ValueError) as e:  # кеш - оптимизация
                    log.warning("Кеш ML: ответ %s не сохранён под версией %s: %s", req.publication_id, v, e)
            return out

    return VersionedCachedExtractor


def make_extractor(settings: Settings, *, kind: str | None = None, url: str | None = None,
                   raw_dir: Path | None = None, retry_delays: tuple[float, ...] | None = None,
                   use_cache: bool = True) -> ExtractAdapter:
    """mock | http | raw. Кешируются только ответы HTTP (мок читает эталон, raw - файлы на диске).

    Ошибка конфигурации (нет эталона, адреса ML-1 или каталога ответов) - AppError('validation_error')."""
    from ..ml.adapters import HttpExtractor, MlCache, MockGoldExtractor, RawDirExtractor

    k = kind or settings.extractor
    if k == "mock":
        gold = settings.mock_gold_dir
        rag = settings.rag_mock_gold_dir
        if (gold is None or not Path(gold).is_dir()) and (rag is None or not Path(rag).is_dir()):
            raise AppError("validation_error",
                           f"Для мока ML-1 нужен каталог эталона (FAKTURA_MOCK_GOLD_DIR / --gold-dir): {gold}")
        return MockGoldExtractor(Path(gold) if gold is not None else None, settings.mock_issues, rag_gold_dir=rag)
    if k == "http":
        base = url or settings.extract_url
        if not base:
            raise AppError("validation_error",
                           "Для извлечения через ML-1 нужен адрес: FAKTURA_EXTRACT_URL / --extract-url")
        delays = tuple(retry_delays) if retry_delays is not None else settings.retry_delays
        ext: ExtractAdapter = HttpExtractor(base, timeout_s=settings.extract_timeout_s, retry_delays=delays,
                                            concurrency=settings.extract_concurrency, sleep=_sleep,
                                            protocol=settings.extract_protocol,
                                            retry_on_timeout=settings.extract_retry_on_timeout,
                                            stream=settings.extract_stream)
        if use_cache and settings.cache_dir is not None:
            return _versioned_cache_class()(ext, MlCache(Path(settings.cache_dir)))
        return ext
    if k == "raw":
        if raw_dir is None:
            raise AppError("validation_error", "Для import-raw нужен каталог ответов ML-1: --raw-dir")
        return RawDirExtractor(Path(raw_dir))
    raise AppError("validation_error", f"Неизвестный экстрактор: {k} (mock | http | raw)")


def _default_evaluator(settings: Settings):
    from ..evaluation import FakturaEvalAdapter, LocalEval

    if settings.eval_impl == "faktura_eval":
        return FakturaEvalAdapter()
    return LocalEval()


def build_services(settings: Settings, **overrides: Any) -> Services:
    """Выбор реализаций по настройкам: БД, FileStore, ParseAdapter, Normalizer, EventBus, Executor,
    extractor_factory, EvalAdapter. overrides подменяют любое поле Services (тесты); неизвестное поле - TypeError.

    `extractor_factory()` создаёт адаптер ML-1 лениво (ошибка конфигурации - при первом вызове) и затем отдаёт
    тот же экземпляр: у HTTP-адаптера общий пул соединений и ограничение параллельности."""
    unknown = set(overrides) - _SERVICE_FIELDS
    if unknown:
        raise TypeError(f"build_services: неизвестные поля {sorted(unknown)}")

    def lazy(name: str, factory: Callable[[], Any]) -> Any:
        return overrides[name] if name in overrides else factory()

    from ..norm import get_normalizer
    from ..pdfio import LocalParseAdapter
    from ..storage import Database, build_file_store
    from .events import InMemoryEventBus, ThreadExecutor

    db = lazy("db", lambda: Database(settings.db_url))
    store = lazy("store", lambda: build_file_store(settings))
    parser = lazy("parser", LocalParseAdapter)
    normalizer = lazy("normalizer", lambda: get_normalizer(settings))
    bus = lazy("bus", lambda: InMemoryEventBus(buffer=settings.sse_buffer))
    executor = lazy("executor", lambda: ThreadExecutor(settings.run_concurrency))
    evaluator = lazy("evaluator", lambda: _default_evaluator(settings))
    if "extractor_factory" in overrides:
        factory = overrides["extractor_factory"]
    else:
        holder: dict[str, ExtractAdapter] = {}
        lock = threading.Lock()

        def factory() -> ExtractAdapter:
            with lock:
                if "x" not in holder:
                    holder["x"] = make_extractor(settings)
                return holder["x"]

    return Services(settings=settings, db=db, store=store, parser=parser, normalizer=normalizer, bus=bus,
                    executor=executor, extractor_factory=factory, evaluator=evaluator)
