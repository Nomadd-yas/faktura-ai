"""Клиент модели агента: `POST /llm/chat` ML-сервиса (ключ RouterAI и закрепление провайдера - только там)."""
from __future__ import annotations

import logging
from typing import Any

from ..ask.client import AskUnavailable, ask_base_url

log = logging.getLogger("faktura.agent")


class HttpLlmClient:
    def __init__(self, base_url: str, timeout_s: float = 30.0, http: Any = None) -> None:
        self.base_url, self.timeout_s, self._http = base_url.rstrip("/"), float(timeout_s), http

    def _client(self):
        if self._http is None:
            import httpx  # noqa: PLC0415

            self._http = httpx.Client(timeout=self.timeout_s)
        return self._http

    def chat(self, messages: list[dict], tools: list[dict] | None = None, *, max_tokens: int = 1200,
             tool_choice: str | None = None, timeout_s: float | None = None) -> dict:
        """{message: {role, content, tool_calls?}, finish_reason, meta}; сбой ML -> AskUnavailable."""
        body: dict[str, Any] = {"messages": messages, "max_tokens": max_tokens, "temperature": 0.0}
        if tools:
            body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
        try:
            r = self._client().post(self.base_url + "/llm/chat", json=body, timeout=timeout_s or self.timeout_s)
        except Exception as e:  # noqa: BLE001 - таймаут, обрыв, нет сети
            raise AskUnavailable(f"ML недоступен: {type(e).__name__}") from e
        if r.status_code != 200:
            raise AskUnavailable(f"ML ответил {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise AskUnavailable("ML вернул не JSON") from e
        if not isinstance(data, dict) or not isinstance(data.get("message"), dict):
            raise AskUnavailable("в ответе ML нет message")
        return data


def build_llm_client(settings) -> HttpLlmClient | None:
    if getattr(settings, "agent_mode", "auto") == "off":
        return None
    base = ask_base_url(settings)
    return HttpLlmClient(base, timeout_s=settings.ask_timeout_s) if base else None
