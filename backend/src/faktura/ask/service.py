"""Один вопрос: планировщик (LLM или правила) -> исполнитель -> автор текста (LLM или шаблон)."""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .client import AskUnavailable
from .context import RunCtx, llm_view
from .execute import NODATA, execute, suggestions
from .plan import normalize_plan
from .rules import keyword_plan

log = logging.getLogger("faktura.ask")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_MARK = re.compile(r"\[\d+\]")
MAX_TEXT = 900


def grounded(text: str, draft: str, facts: dict, question: str) -> bool:
    """Текст автора можно показать, если все его числа и сноски есть в шаблонном тексте, фактах или вопросе."""
    if not text or len(text) > MAX_TEXT:
        return False
    allowed = {m.replace(",", ".") for m in _NUM.findall(draft + " " + json.dumps(facts, ensure_ascii=False) + " " + question)}
    if any(m.replace(",", ".") not in allowed for m in _NUM.findall(text)):
        return False
    return set(_MARK.findall(text)) <= set(_MARK.findall(draft))


def _add_meta(meta: dict, part: dict | None) -> None:
    for k in ("tokens_in", "tokens_out"):
        meta[k] += int((part or {}).get(k) or 0)
    meta["cost_usd"] = round(meta["cost_usd"] + float((part or {}).get("cost_usd") or 0), 6)
    if (part or {}).get("model"):
        meta["model"] = part["model"]


def answer_question(run: RunCtx, question: str, client: Any = None) -> dict:
    """CaseAnswer + `meta` (planner: llm|rules, writer: llm|template, degraded: ML настроен, но не ответил)."""
    t0 = time.monotonic()
    meta: dict[str, Any] = {"planner": "rules", "writer": "template", "degraded": False, "intent": "unknown",
                            "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    if not run.has_data:
        return {"text": NODATA, "cards": [], "cites": [], "suggestions": [], "meta": meta}
    desc = run.describe()
    raw = None
    if client is not None:
        try:
            raw, pm = client.plan(question, llm_view(desc))
            meta["planner"] = "llm"
            _add_meta(meta, pm)
        except AskUnavailable as e:
            meta["degraded"] = True
            log.warning("Планировщик ML недоступен, отвечаю по правилам: %s", e)
    plan = normalize_plan(raw, desc) if raw is not None else None
    if plan is None or plan["intent"] == "unknown":  # правила - запасной путь и при «не понял» от LLM
        if plan is not None:
            meta["planner"] = "llm+rules"
        plan = normalize_plan(keyword_plan(question, desc), desc)
    ex = execute(run, plan)
    meta["intent"] = ex.intent
    text = ex.text
    if client is not None and not meta["degraded"] and ex.facts and ex.intent != "unknown":
        try:
            t, wm = client.write(question, ex.intent, ex.facts, ex.text)
            _add_meta(meta, wm)
            if grounded(t, ex.text, ex.facts, question):
                text, meta["writer"] = t, "llm"
        except AskUnavailable as e:
            meta["degraded"] = True
            log.warning("Автор текста ML недоступен, беру шаблон: %s", e)
    asked = question.strip().lower()
    sugg = [s for s in suggestions(run) if s.lower() != asked][:3]
    meta["seconds"] = round(time.monotonic() - t0, 3)
    meta["plan"] = {k: v for k, v in plan.items() if v not in ("", [], None, False)}
    if ex.total > ex.shown:
        meta["capped"] = {"total": ex.total, "shown": ex.shown}
    return {"text": text, "cards": ex.cards, "cites": ex.cites, "suggestions": sugg, "meta": meta}
