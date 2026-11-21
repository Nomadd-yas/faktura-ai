"""HTTP-клиент ML для вопросов: `POST /ask/plan` (вопрос -> план) и `POST /ask/write` (факты -> текст)."""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger("faktura.ask")


class AskUnavailable(Exception):
    """ML недоступен или ответил не по контракту: вызывающий переходит на правила и шаблонный текст."""


def ask_base_url(settings) -> str | None:
    """Адрес ML для вопросов: FAKTURA_ASK_URL, иначе адрес извлечения (без хвоста /extract), если извлечение идёт через ML."""
    if getattr(settings, "ask_mode", "auto") == "off":
        return None
    url = (settings.ask_url or "").strip()
    if not url and settings.extractor == "http" and (settings.extract_url or "").strip():
        url = re.sub(r"/(?:ml/)?extract/?$", "", settings.extract_url.strip())
    return url or None


class HttpAskClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0, http: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self._http = http

    def _client(self):
        if self._http is None:
            import httpx  # noqa: PLC0415

            self._http = httpx.Client(timeout=self.timeout_s)
        return self._http

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self._client().post(self.base_url + path, json=body, timeout=self.timeout_s)
        except Exception as e:  # noqa: BLE001 - таймаут, обрыв, нет сети
            raise AskUnavailable(f"ML недоступен: {type(e).__name__}") from e
        if r.status_code != 200:
            raise AskUnavailable(f"ML ответил {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise AskUnavailable("ML вернул не JSON") from e
        if not isinstance(data, dict):
            raise AskUnavailable("ML вернул не объект")
        return data

    def plan(self, question: str, run: dict) -> tuple[dict, dict]:
        data = self._post("/ask/plan", {"question": question, "run": run})
        plan = data.get("plan")
        if not isinstance(plan, dict):
            raise AskUnavailable("в ответе ML нет plan")
        return plan, data.get("meta") if isinstance(data.get("meta"), dict) else {}

    def write(self, question: str, intent: str, facts: dict, draft: str) -> tuple[str, dict]:
        data = self._post("/ask/write", {"question": question, "intent": intent, "facts": facts, "draft": draft})
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise AskUnavailable("в ответе ML нет text")
        return text.strip(), data.get("meta") if isinstance(data.get("meta"), dict) else {}


def build_ask_client(settings) -> HttpAskClient | None:
    base = ask_base_url(settings)
    return HttpAskClient(base, timeout_s=settings.ask_timeout_s) if base else None
