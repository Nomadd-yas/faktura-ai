"""Цикл агента: модель <-> инструменты, пока не будет текстового ответа, вопроса-уточнения, лимита или остановки."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..ask.client import AskUnavailable
from . import MAX_TOKENS
from .tools import TOOL_SPECS, Toolbox

log = logging.getLogger("faktura.agent")
MIN_CALL_S = 3.0  # меньше этого остатка времени новый вызов модели не начинаем


@dataclass
class Outcome:
    status: str  # done | stopped | failed
    text: str = ""
    clarify: bool = False
    options: list[str] = field(default_factory=list)
    reason: str = ""  # для failed: llm_unavailable | timeout | empty | ...
    meta: dict[str, Any] = field(default_factory=dict)
    llm_calls: int = 0


def _add_meta(meta: dict, part: dict | None) -> None:
    part = part or {}
    for k in ("tokens_in", "tokens_out"):
        meta[k] = int(meta.get(k) or 0) + int(part.get(k) or 0)
    meta["cost_usd"] = round(float(meta.get("cost_usd") or 0) + float(part.get("cost_usd") or 0), 6)
    for k in ("model", "provider"):
        if part.get(k):
            meta[k] = part[k]


def _args(raw: Any) -> tuple[dict, str | None]:
    if isinstance(raw, dict):
        return raw, None
    try:
        v = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}, "arguments are not valid JSON"
    return (v, None) if isinstance(v, dict) else ({}, "arguments must be a JSON object")


def run_loop(llm: Any, tb: Toolbox, messages: list[dict], *, emit: Callable[[str, dict], None],
             is_cancelled: Callable[[], bool], max_calls: int, timeout_s: float,
             clock: Callable[[], float] = time.monotonic) -> Outcome:
    t0 = clock()
    meta: dict[str, Any] = {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    out = Outcome("failed", meta=meta)
    calls = 0
    while True:
        if is_cancelled():
            out.status = "stopped"
            return out
        remaining = timeout_s - (clock() - t0)
        if remaining < MIN_CALL_S:
            out.reason = "timeout"
            return out
        final = calls >= max_calls  # лимит инструментов исчерпан: просим ответ без инструментов
        try:
            r = llm.chat(messages, None if final else TOOL_SPECS, max_tokens=MAX_TOKENS, timeout_s=min(remaining, 30.0))
        except AskUnavailable as e:
            log.warning("Модель агента недоступна: %s", e)
            out.reason = "llm_unavailable"
            return out
        out.llm_calls += 1
        _add_meta(meta, r.get("meta"))
        msg = r.get("message") or {}
        tcs = [] if final else (msg.get("tool_calls") or [])
        if not tcs:
            text = (msg.get("content") or "").strip()
            if not text:
                out.reason = "empty"
                return out
            out.status, out.text = "done", text
            return out
        messages.append({"role": "assistant", "content": msg.get("content") or None, "tool_calls": tcs})
        for tc in tcs:
            if is_cancelled():
                out.status = "stopped"
                return out
            fn = tc.get("function") or {}
            name, (args, bad) = fn.get("name") or "", _args(fn.get("arguments"))
            if name == "clarify" and not bad:
                q, opts = str(args.get("question") or "").strip(), [str(o).strip() for o in args.get("options") or [] if str(o).strip()]
                if q and 2 <= len(opts) <= 4:
                    out.status, out.text, out.clarify, out.options = "done", q, True, opts
                    return out
                res: dict = {"error": "clarify needs question and 2-4 options"}
            elif bad:
                res = {"error": bad}
            elif calls >= max_calls:
                res = {"error": "tool call limit reached: answer now from what you have"}
            else:
                calls += 1
                res, step = tb.call(name, args)
                emit("step", {"text": step})
            messages.append({"role": "tool", "tool_call_id": tc.get("id") or f"call_{calls}", "content": tb.dump(res)})
