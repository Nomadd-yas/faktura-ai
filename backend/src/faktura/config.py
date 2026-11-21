"""Настройки приложения (план 4.2). Префикс переменных окружения FAKTURA_, поддержка .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

import platformdirs
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CASE_DIR = (BACKEND_DIR.parent / "data" / "caseB_kejs5").resolve()
DEFAULT_RAG_CASE_DIR = (BACKEND_DIR.parent / "data" / "caseA_drive").resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAKTURA_", env_file=".env", extra="ignore")

    # корень кейса (К-10)
    case_dir: Path | None = None
    # БД и файлы
    db_url: str = "sqlite:///./faktura.db"
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: Path = Path("./storage")
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str | None = None
    # ML-1
    extractor: Literal["mock", "http", "raw"] = "mock"
    extract_url: str | None = None
    extract_timeout_s: float = 330
    extract_concurrency: int = 2
    extract_retry_delays: str = "20,60"  # паузы между попытками: 2 паузы = 3 попытки
    extract_protocol: Literal["contract", "pdf_dataset"] = "contract"  # pdf_dataset - прототип ML-1 (POST /extract)
    extract_retry_on_timeout: bool = True
    # ndjson - просить у ML-1 ответ потоком NDJSON (откат на JSON при 406 / ответе без NDJSON); json - всегда обычный JSON
    extract_stream: Literal["ndjson", "json"] = "ndjson"
    ml_pause_poll_s: float = 30
    ml_pause_max_s: float = 300  # максимум паузы прогона при недоступной модели
    cache_dir: Path | None = None
    mock_issues: Literal["all", "ml_only"] = "all"
    mock_gold_dir: Path | None = None
    bbox_origin: Literal["top-left", "bottom-left"] = "top-left"
    autolink_duplicates: Literal["auto", "on", "off"] = "auto"
    norm_impl: Literal["local", "faktura_norm"] = "local"
    eval_impl: Literal["local", "faktura_eval"] = "local"
    # безопасность
    jwt_secret: str | None = None
    access_ttl_s: int = 900
    refresh_ttl_days: int = 30
    refresh_short_ttl_h: int = 12
    refresh_in_body: bool = False
    cookie_secure: bool = True
    admin_email: str | None = None
    admin_password: str | None = None
    allow_signup: bool = False
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:5200"])
    # лимиты
    max_file_mb: int = 50
    max_pages: int = 300
    max_upload_files: int = 200
    max_upload_mb: int = 1024
    max_zip_mb: int = 2048
    zip_max_unpacked_gb: int = 2
    zip_max_files: int = 500
    zip_max_depth: int = 10
    run_concurrency: int = 4
    file_timeout_s: float = 600  # предел разбора одного файла (10 мин)
    resume_runs_on_start: bool = True
    export_sync_limit: int = 50000
    check_script: Path | None = None
    # область rag: корень кейса, эталон для мока, скрипт проверки (выводятся из rag_case_dir)
    rag_case_dir: Path | None = None
    rag_mock_gold_dir: Path | None = None
    rag_check_script: Path | None = None
    check_timeout_s: int = 900
    # SSE
    sse_ping_s: int = 25
    sse_buffer: int = 1000
    sse_max_events: int | None = None
    sse_idle_timeout_s: float | None = None

    @model_validator(mode="after")
    def _derive_defaults(self) -> "Settings":
        if self.case_dir is None and DEFAULT_CASE_DIR.is_dir():
            self.case_dir = DEFAULT_CASE_DIR
        if self.cache_dir is None:
            self.cache_dir = Path(platformdirs.user_cache_dir("faktura")) / "ml_raw"
        if self.case_dir is not None:
            if self.mock_gold_dir is None:
                self.mock_gold_dir = self.case_dir / "dev_gold"
            if self.check_script is None:
                cand = self.case_dir / "tools" / "check_submission.py"
                if cand.is_file():
                    self.check_script = cand
        if self.rag_case_dir is None and DEFAULT_RAG_CASE_DIR.is_dir():
            self.rag_case_dir = DEFAULT_RAG_CASE_DIR
        if self.rag_case_dir is not None:
            if self.rag_mock_gold_dir is None:
                self.rag_mock_gold_dir = self.rag_case_dir / "dev_gold"
            if self.rag_check_script is None:
                cand = self.rag_case_dir / "tools" / "check_submission.py"
                if cand.is_file():
                    self.rag_check_script = cand
        return self

    @property
    def retry_delays(self) -> tuple[float, ...]:
        return tuple(float(x) for x in self.extract_retry_delays.split(",") if x.strip())


def domain_paths(settings: Settings, name: str) -> tuple[Path | None, Path | None, Path | None]:
    """(case_dir, mock_gold_dir, check_script) области."""
    if name == "solar":
        return settings.case_dir, settings.mock_gold_dir, settings.check_script
    if name == "rag":
        return settings.rag_case_dir, settings.rag_mock_gold_dir, settings.rag_check_script
    from .domain.errors import AppError

    raise AppError("domain_not_supported", f"Область не поддерживается: {name}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
