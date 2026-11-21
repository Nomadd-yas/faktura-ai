"""Ответ на вопрос внутри разбора: агент -> проверка -> (запасной базовый вариант) -> сохранение -> события."""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from ..ask import client as ask_client_mod
from ..ask.context import RunCtx, load_ctx
from ..ask.execute import suggestions
from ..ask.service import answer_question
from ..domain.entities import CorpusFileStatus
from . import MAX_TOOL_CALLS, TOTAL_TIMEOUT_S, store
from .loop import Outcome, run_loop
from .prompt import history_messages, system_prompt
from .tools import Toolbox
from .verify import Verified, Verifier

log = logging.getLogger("faktura.agent")
ACTIVE = (CorpusFileStatus.pending, CorpusFileStatus.downloading, CorpusFileStatus.queued, CorpusFileStatus.processing)
NOTES = {
    "verification": "Ответ собран по шаблонам: агент не смог подтвердить большую часть своего текста данными разбора.",
    "llm_unavailable": "Ответ собран по шаблонам: модель сейчас недоступна.",
    "timeout": "Ответ собран по шаблонам: агент не уложился во время.",
    "empty": "Ответ собран по шаблонам: агент не вернул ответа.",
    "error": "Ответ собран по шаблонам: у агента произошла ошибка.",
}
STOPPED = {"text": "Ответ остановлен.", "cards": [], "cites": [], "suggestions": []}


def norm_question(q: str) -> tuple:
    """Ключ «того же вопроса»: набор статей + слова без порядка («сравни D003 и D001» = «сравни D001 и D003»)."""
    pids = tuple(sorted(set(re.findall(r"\bD\d+\b", q.upper()))))
    words = sorted(set(re.findall(r"[^\W\d_]+|\d+", re.sub(r"\bD\d+\b", " ", q, flags=re.I).lower())) - {"и", "с", "а"})
    return pids, tuple(words)


def fresh_suggestions(run: RunCtx, asked: list[str], n: int = 3) -> list[str]:
    seen = {norm_question(q) for q in asked}
    return [s for s in suggestions(run) if norm_question(s) not in seen][:n]


def _partial(uow, corpus) -> dict | None:
    files = [f for f in uow.files.list(corpus.id) if f.status != CorpusFileStatus.skipped_duplicate]
    if any(f.status in ACTIVE for f in files):
        return {"done": sum(f.status == CorpusFileStatus.done for f in files), "total": len(files)}
    return None


def _spec_cites(run: RunCtx, base_cites: list[dict]) -> list[dict]:
    """Сноски базового ответа -> формат §15.9 (n, result_id, issue_id, page, fragment); исходные поля сохраняются."""
    out = []
    for i, c in enumerate(base_cites, 1):
        p = run.by_pid.get(c.get("pid") or "")
        frag = ""
        if p and c.get("rid"):
            r = next((x for x in p.results if x.get("result_id") == c["rid"]), None)
            frag = (r or {}).get("source_fragment", "")
        elif p and c.get("iid"):
            r = next((x for x in p.issues if x.get("issue_id") == c["iid"]), None)
            frag = (r or {}).get("source_fragment") or (r or {}).get("description", "")
        out.append({"n": i, "domain": c.get("domain") or run.domain, "pid": c.get("pid"), "result_id": c.get("rid"),
                    "issue_id": c.get("iid"), "page": c.get("page"), "fragment": (frag or "")[:300],
                    **{k: c[k] for k in ("label", "table", "row") if k in c}})
    return out


def _cards(run: RunCtx, tb: Toolbox, v: Verified) -> list[dict]:
    cards: list[dict] = []
    rids = [(f.split("/", 1)[0], f.split("/", 1)[1]) for f in v.used_facts]
    for a in v.used_aggs:  # строки, из которых посчитан цитируемый агрегат
        for i in tb.aggs[a]["ids"]:
            if tb.aggs[a]["table"] == "results" and "/" in i:
                rids.append((i.split("/", 1)[0], i.split("/", 1)[1]))
    rids = list(dict.fromkeys(rids))[:50]
    if rids:
        cards.append({"kind": "results", "title": "Значения из ответа", "domain": run.domain,
                      "rows": [{"pid": p, "rid": r} for p, r in rids]})
    if v.used_issues:
        cards.append({"kind": "issues", "title": "Замечания", "domain": run.domain,
                      "items": [{"pid": i.split("/", 1)[0], "iid": i.split("/", 1)[1]} for i in v.used_issues[:50]]})
    for c in tb.compared[-1:]:
        if len(c["pids"]) >= 2 and c["metric"] and set(c["pids"]) & {p for p, _ in rids}:
            cards.append({"kind": "compare", "domain": run.domain, "pids": c["pids"], "metric": c["metric"]})
    return cards


def _is_cancelled(services, mid: str) -> Callable[[], bool]:
    def check() -> bool:
        try:
            with services.db.uow() as uow:
                return store.status_of(uow, mid) == "stopped"
        except Exception:  # noqa: BLE001 - не смогли прочитать флаг: не останавливаем
            return False
    return check


def answer_in_thread(actx: Any, corpus_id: str, mid: str, question: str, stream: store.Stream) -> None:
    """Рабочий поток ответа: всё, что случилось, - в БД и в `stream`; исключения наружу не выходят."""
    emit = stream.emit
    try:
        _answer(actx, corpus_id, mid, question, emit)
    except Exception as e:  # noqa: BLE001
        log.exception("Агент разбора упал")
        try:
            with actx.get_services().db.uow() as uow:
                store.finish(uow, mid, status="error", answer=None, clarify=False, meta={"error": type(e).__name__},
                             tool_log=[], cost_usd=0.0)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось записать ошибку ответа")
        emit("error", {"code": "agent_error", "message": "Не удалось ответить на вопрос", "retryable": True})


def _llm(actx) -> Any:
    from .llm import build_llm_client  # noqa: PLC0415

    if isinstance(actx.llm_client, str):  # "auto": один раз из настроек
        actx.llm_client = build_llm_client(actx.settings)
    return actx.llm_client


def _ask_client(actx) -> Any:
    if isinstance(actx.ask_client, str):
        actx.ask_client = ask_client_mod.build_ask_client(actx.settings)
    return actx.ask_client


def _answer(actx, corpus_id: str, mid: str, question: str, emit: Callable[[str, dict], None]) -> None:
    services = actx.get_services()
    t0 = time.monotonic()
    emit("step", {"text": "Разбираю вопрос"})
    with services.db.uow() as uow:
        corpus = uow.corpora.get(corpus_id)
        run = load_ctx(uow, corpus)
        rows = store.list_all(uow, corpus_id)
        partial = _partial(uow, corpus)
    qa = store.thread_qa(rows, exclude=mid)
    asked = [q for q, _ in qa] + [question]
    meta: dict[str, Any] = {"mode": "agent", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    tool_log: list[dict] = []
    clarify = False
    fallback = None
    answer: dict | None = None
    status = "done"
    tb = Toolbox(run, services, corpus_id)
    llm = _llm(actx)
    if not run.has_data:
        fallback = "nodata"
    elif llm is None:
        fallback = "no_llm"
    else:
        max_calls = int(getattr(actx.settings, "agent_max_tool_calls", MAX_TOOL_CALLS))
        messages = [{"role": "system", "content": system_prompt(run, max_calls)}, *history_messages(qa),
                    {"role": "user", "content": question}]
        out: Outcome = run_loop(llm, tb, messages, emit=emit, is_cancelled=_is_cancelled(services, mid),
                                max_calls=max_calls, timeout_s=float(getattr(actx.settings, "agent_timeout_s", TOTAL_TIMEOUT_S)))
        meta.update(out.meta)
        meta["llm_calls"], meta["tool_calls"] = out.llm_calls, len(tb.calls)
        tool_log = list(tb.calls)
        if out.status == "stopped":
            status = "stopped"
        elif out.status == "failed":
            fallback = out.reason or "error"
        elif out.clarify:
            clarify = True
            answer = {"text": out.text, "cards": [], "cites": [], "suggestions": out.options, "clarify": True}
        else:
            emit("step", {"text": "Сверяю с источниками"})
            v = Verifier(tb, question, getattr(actx.settings, "rag_unverified", "drop")).run(out.text)
            tool_log.append({"verification": {"items": v.items_total, "dropped": v.items_dropped, "items_dropped": v.dropped,
                                              "raw_text": out.text[:4000]}})
            if v.too_many_dropped or not v.text.strip():
                fallback = "verification"
            else:
                answer = {"text": v.text, "cards": _cards(run, tb, v), "cites": v.cites,
                          "suggestions": fresh_suggestions(run, asked)}
                if v.unverified:
                    answer["unverified"] = v.unverified
    if status == "done" and answer is None:  # запасной путь: базовый вариант (/ask)
        meta["mode"] = "base"
        meta["fallback"] = fallback
        emit("step", {"text": "Отвечаю по шаблонам"})
        base = answer_question(run, question, _ask_client(actx))
        for k in ("tokens_in", "tokens_out", "cost_usd"):
            meta[k] = round(meta[k] + (base.get("meta") or {}).get(k, 0), 6)
        text = base["text"]
        if fallback in NOTES:
            text += "\n\n" + NOTES[fallback]
        answer = {"text": text, "cards": base["cards"], "cites": _spec_cites(run, base["cites"]),
                  "suggestions": fresh_suggestions(run, asked)}
        if fallback in NOTES:
            answer["fallback"] = fallback
    if answer is not None and partial:
        answer["text"] += f"\n\nРазбор ещё идёт: ответ по {partial['done']} из {partial['total']} готовых статей."
        answer["partial"] = partial
    meta["seconds"] = round(time.monotonic() - t0, 3)
    with services.db.uow() as uow:
        saved = store.finish(uow, mid, status=status, answer=answer, clarify=clarify, meta=meta, tool_log=tool_log,
                             cost_usd=float(meta["cost_usd"]))
    if not saved or status == "stopped":  # остановлен пользователем: ответ записал stop
        emit("done", {"message_id": mid, "status": "stopped", "answer": STOPPED})
        return
    emit_answer(emit, mid, answer, clarify)


def emit_answer(emit: Callable[[str, dict], None], mid: str, answer: dict, clarify: bool = False) -> None:
    """text -> card -> cite -> suggestions -> done (порядок событий §15.9)."""
    for line in answer["text"].splitlines(keepends=True):
        emit("text", {"text": line})
    for c in answer.get("cards") or []:
        emit("card", {"card": c})
    for c in answer.get("cites") or []:
        emit("cite", {"cite": c})
    emit("suggestions", {"suggestions": answer.get("suggestions") or []})
    emit("done", {"message_id": mid, "status": "done", "answer": answer, **({"clarify": True} if clarify else {})})
