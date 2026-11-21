"""Состояние экземпляра приложения: настройки, ленивые Services, лимитеры, сессии (план 5.7).

Хранится в ``app.state.faktura``. У каждого ``create_app`` свой контекст: лимитеры не общие между приложениями.
"""
from __future__ import annotations

import logging
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from ...config import Settings
from ...domain.errors import AppError
from ...domain.services import Services
from .auth import LoginLimiter, RateLimiter, SessionService

log = logging.getLogger("faktura.api")

UPLOADS_PER_HOUR = 60
CHAT_QUESTIONS_PER_MIN = 30
REQUESTS_PER_MIN = 600


@dataclass
class AppContext:
    settings: Settings
    services: Services | None
    jwt_secret: str
    sessions: SessionService
    login_limiter: LoginLimiter = field(default_factory=LoginLimiter)
    rate_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(REQUESTS_PER_MIN, 60))
    upload_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(
        UPLOADS_PER_HOUR, 3600, message="Слишком много загрузок: не больше 60 в час"))
    chat_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(
        CHAT_QUESTIONS_PER_MIN, 60, message="Слишком много вопросов: не больше 30 в минуту"))
    # клиент ML для «Вопросов по разбору»: "auto" - собрать из настроек при первом вопросе, None - без ML (правила)
    ask_client: Any = "auto"
    # клиент ML агента чата разбора (POST /llm/chat): "auto" - из настроек при первом вопросе, None - без агента (база)
    llm_client: Any = "auto"
    agent_streams: Any = field(default_factory=lambda: __import__("faktura.agent.store", fromlist=["Streams"]).Streams())
    stub_turns: "OrderedDict[str, dict[str, Any]]" = field(default_factory=OrderedDict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get_services(self) -> Services:
        """Services, переданные в create_app, или ленивая сборка ``faktura.pipeline.build_services`` (один раз)."""
        if self.services is not None:
            return self.services
        with self._lock:
            if self.services is None:
                import faktura.pipeline as pipeline  # noqa: PLC0415 - ленивый импорт (нет циклов, быстрый старт)

                try:
                    sv = pipeline.build_services(self.settings)
                    # первый запуск сервера: схема БД и администратор из FAKTURA_ADMIN_EMAIL/PASSWORD
                    sv.db.create_all()
                    from ...storage import seed_admin  # noqa: PLC0415

                    seed_admin(sv.db, self.settings)
                    self.services = sv
                except AppError:
                    raise
                except Exception as e:  # pragma: no cover - зависит от окружения
                    log.exception("Не удалось собрать Services")
                    raise AppError("internal_error", "Сервис не готов: не удалось подключить хранилище") from e
        return self.services

    def resume_runs(self) -> list[str]:
        """Продолжает прогоны, оставшиеся `queued`/`running` после перезапуска (повторный вызов безопасен).
        Ошибки не роняют запуск сервера."""
        if not self.settings.resume_runs_on_start:
            return []
        try:
            from ...pipeline.runs import RunService  # noqa: PLC0415

            ids = RunService(self.get_services()).resume_active()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось продолжить незавершённые прогоны")
            return []
        if ids:
            log.info("Продолжены прогоны после перезапуска: %s", ", ".join(ids))
        return ids

    def remember_turn(self, turn_id: str, data: dict[str, Any], cap: int = 500) -> None:
        with self._lock:
            self.stub_turns[turn_id] = data
            while len(self.stub_turns) > cap:
                self.stub_turns.popitem(last=False)


def make_context(settings: Settings, services: Services | None) -> AppContext:
    secret = settings.jwt_secret
    if not secret:
        log.warning("FAKTURA_JWT_SECRET не задан: токены подписываются случайным ключом и не переживут перезапуск")
        secret = secrets.token_urlsafe(48)
    sessions = SessionService(secret=secret, access_ttl_s=settings.access_ttl_s,
                              refresh_ttl_days=settings.refresh_ttl_days,
                              refresh_short_ttl_h=settings.refresh_short_ttl_h)
    return AppContext(settings=settings, services=services, jwt_secret=secret, sessions=sessions)
