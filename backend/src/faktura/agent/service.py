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
from ..sources.openalex import build_client as build_sources_client
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
    for f in v.used_calc_facts:
        rids.append((f.split("/", 1)[0], f.split("/", 1)[1]))
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
            cards.append({"kind": "compare", "domain": run.domain, "title": "Сравнение", "pids": c["pids"],
                          "metric": c["metric"], "metrics": [c["metric"]]})
    if tb.fixes:  # предложения правок: ничего не применяется, фронт применяет выбранные через v2 review (corrected_value)
        cards.append({"kind": "fix", "domain": run.domain, "title": "Предлагаемые правки", "items": tb.fixes})
    return cards


def _is_cancelled(services, mid: str) -> Callable[[], bool]:
    def check() -> bool:
        try:
            with services.db.uow() as uow:
                return store.status_of(uow, mid) == "stopped"
        except Exception:  # noqa: BLE001 - не смогли прочитать флаг: не останавливаем
            return False
    return check


def answer_in_thread(actx: Any, corpus_id: str, mid: str, question: str, stream: store.Stream,
                     pid: str | None = None, thread_id: str | None = None) -> None:
    """Рабочий поток ответа: всё, что случилось, - в БД и в `stream`; исключения наружу не выходят."""
    emit = stream.emit
    try:
        _answer(actx, corpus_id, mid, question, emit, pid, thread_id)
    except Exception as e:  # noqa: BLE001
        log.exception("Агент разбора упал")
        try:
            with actx.get_services().db.uow() as uow:
                store.finish(uow, mid, status="error", answer=None, clarify=False, meta={"error": type(e).__name__},
                             tool_log=[])
        except Exception:  # noqa: BLE001
            log.exception("Не удалось записать ошибку ответа")
        emit("error", {"code": "agent_error", "message": "Не удалось ответить на вопрос", "retryable": True})


def _llm(actx) -> Any:
    from .llm import build_llm_client  # noqa: PLC0415

    if isinstance(actx.llm_client, str):  # "auto": один раз из настроек
        actx.llm_client = build_llm_client(actx.settings)
    return actx.llm_client


def _sources(actx) -> Any:
    """Клиент OpenAlex: из настроек один раз; None - отключён. Тесты подставляют свой в `actx.sources_client`."""
    if isinstance(actx.sources_client, str):
        actx.sources_client = build_sources_client(actx.settings)
    return actx.sources_client


def answer_sources(tb: Toolbox, v: Verified) -> list[dict]:
    """Работы ответа: процитированные [[src:Sn]] по порядку первого упоминания; нет ссылок - всё, что нашёл инструмент."""
    ids = v.used_srcs or list(tb.sources)
    return [{"id": i, **tb.sources[i]} for i in ids][:20]


def _ask_client(actx) -> Any:
    if isinstance(actx.ask_client, str):
        actx.ask_client = ask_client_mod.build_ask_client(actx.settings)
    return actx.ask_client



def run_agent(actx, corpus_id: str, question: str, *, pid: str | None = None, only: set[str] | None = None,
              mid: str | None = None, thread_id: str | None = None,
              emit: Callable[[str, dict], None] = lambda *_: None,
              is_cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Агент -> проверка -> (запасной базовый вариант). -> {status, answer, clarify, meta, tool_log}.
    История - прошлые обмены чата `thread_id` (у ответа `mid` - его чат); без обоих - один вопрос без истории."""
    services = actx.get_services()
    t0 = time.monotonic()
    emit("step", {"text": "Разбираю вопрос"})
    with services.db.uow() as uow:
        corpus = uow.corpora.get(corpus_id)
        run = load_ctx(uow, corpus, only)
        if thread_id is None and mid:
            m = store.get(uow, corpus_id, mid)
            thread_id = m.get("thread_id") if m else None
        rows = store.list_all(uow, corpus_id, thread_id) if (mid or thread_id) else []
        partial = _partial(uow, corpus)
    qa = store.thread_qa(rows, exclude=mid)
    asked = [q for q, _ in qa] + [question]
    meta: dict[str, Any] = {"mode": "agent", "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "cost_currency": store.CURRENCY}
    tool_log: list[dict] = []
    clarify = False
    fallback = None
    answer: dict | None = None
    status = "done"
    dropped = 0
    tb = Toolbox(run, services, corpus_id, _sources(actx))
    llm = _llm(actx)
    if not run.has_data:
        fallback = "nodata"
    elif llm is None:
        fallback = "no_llm"
    else:
        max_calls = int(getattr(actx.settings, "agent_max_tool_calls", MAX_TOOL_CALLS))
        messages = [{"role": "system", "content": system_prompt(run, max_calls, pid)}, *history_messages(qa),
                    {"role": "user", "content": question}]
        out: Outcome = run_loop(llm, tb, messages, emit=emit, is_cancelled=is_cancelled, max_calls=max_calls,
                                timeout_s=float(getattr(actx.settings, "agent_timeout_s", TOTAL_TIMEOUT_S)))
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
            dropped = v.items_dropped
            tool_log.append({"verification": {"items": v.items_total, "dropped": v.items_dropped, "items_dropped": v.dropped,
                                              "raw_text": out.text[:4000]}})
            if v.too_many_dropped or not v.text.strip():
                fallback = "verification"
            else:
                answer = {"text": v.text, "cards": _cards(run, tb, v), "cites": v.cites,
                          "suggestions": fresh_suggestions(run, asked), "sources": answer_sources(tb, v)}
                if v.unverified:
                    answer["unverified"] = v.unverified
    meta["planner"] = "agent"
    if status == "done" and answer is None:  # запасной путь: базовый вариант (/ask)
        meta["mode"], meta["planner"] = "base", "base"
        meta["fallback"] = fallback
        emit("step", {"text": "Отвечаю по шаблонам"})
        base = answer_question(run, question, _ask_client(actx))
        bm = base.get("meta") or {}
        for k in ("tokens_in", "tokens_out"):
            meta[k] = int(meta[k] + (bm.get(k) or 0))
        meta["cost"] = round(meta["cost"] + float(bm.get("cost", bm.get("cost_usd")) or 0), 6)
        meta["model"] = bm.get("model") or meta.get("model")
        text = base["text"]
        if fallback in NOTES:
            text += "\n\n" + NOTES[fallback]
        answer = {"text": text, "cards": base["cards"], "cites": _spec_cites(run, base["cites"]),
                  "suggestions": fresh_suggestions(run, asked), "sources": []}
        if fallback in NOTES:
            answer["fallback"] = fallback
    if answer is not None and partial:
        answer["text"] += f"\n\nРазбор ещё идёт: ответ по {partial['done']} из {partial['total']} готовых статей."
        answer["partial"] = partial
    meta["seconds"] = round(time.monotonic() - t0, 3)
    meta["dropped"] = dropped
    if pid:
        meta["pid"] = pid
    if answer is not None:
        answer["meta"] = {"model": meta.get("model"), "provider": meta.get("provider"), "planner": meta["planner"],
                          "tool_calls": meta.get("tool_calls", 0), "tokens_in": meta["tokens_in"],
                          "tokens_out": meta["tokens_out"], "cost": meta["cost"], "cost_currency": store.CURRENCY,
                          "seconds": meta["seconds"], "dropped": dropped,
                          **({"fallback": fallback} if fallback else {})}
    return {"status": status, "answer": answer, "clarify": clarify, "meta": meta, "tool_log": tool_log}


def _answer(actx, corpus_id: str, mid: str, question: str, emit: Callable[[str, dict], None],
            pid: str | None = None, thread_id: str | None = None) -> None:
    services = actx.get_services()
    r = run_agent(actx, corpus_id, question, pid=pid, mid=mid, thread_id=thread_id, emit=emit,
                  is_cancelled=_is_cancelled(services, mid))
    status, answer, clarify = r["status"], r["answer"], r["clarify"]
    with services.db.uow() as uow:
        saved = store.finish(uow, mid, status=status, answer=answer, clarify=clarify, meta=r["meta"],
                             tool_log=r["tool_log"], cost=float(r["meta"]["cost"]), cost_currency=store.CURRENCY)
    if not saved or status == "stopped":  # остановлен пользователем: ответ записал stop
        emit("done", {"message_id": mid, "status": "stopped", "answer": STOPPED})
        return
    emit_answer(emit, mid, answer, clarify)


# --- /ask через агента: ответ в форме CaseAnswer фронта --------------------------------------------------------------

def _cite_label(c: dict) -> str:
    pid, page = c.get("pid") or "", c.get("page")
    pg = f"стр. {page}" if page else ""
    frag = (c.get("fragment") or "").strip()
    if c.get("src_id"):
        return "Источник: " + frag
    if c.get("calc_id"):
        return "Расчёт: " + frag
    if c.get("agg_id"):
        return c.get("text") or "Агрегат"
    if c.get("table") or c.get("row_id"):
        return " · ".join(x for x in (c.get("table"), c.get("row_id") or c.get("row"), pg) if x)
    if c.get("issue_id") or c.get("iid"):
        return " · ".join(x for x in (pid, f"замечание {c.get('issue_id') or c.get('iid')}", pg) if x)
    if c.get("chunk_id") or (frag and not (c.get("result_id") or c.get("rid"))):
        return " · ".join(x for x in (pid, pg, f"«{frag[:90]}»" if frag else "") if x)
    return " · ".join(x for x in (pid, f"строка {c.get('result_id') or c.get('rid')}", pg) if x)


def ask_cite(c: dict, run: RunCtx) -> dict:
    """Сноска агента/базового ответа -> CaseCite фронта: {pid, rid?, iid?, table?, row?, page?, label, domain?} (+ n, fragment)."""
    pid = c.get("pid") or ""
    rid = c.get("rid") or c.get("result_id")
    if not pid and c.get("result_ids"):  # агрегат: ведём на первую строку
        pid, _, rid = str(c["result_ids"][0]).partition("/")
    out: dict[str, Any] = {"n": c.get("n"), "pid": pid, "label": c.get("label") or _cite_label(c)}
    if rid:
        out["rid"] = rid
    iid = c.get("iid") or c.get("issue_id")
    if iid:
        out["iid"] = iid
    row = c.get("row") or c.get("row_id")
    if c.get("table") and row:
        out["table"], out["row"] = c["table"], row
    if c.get("page") not in (None, ""):
        out["page"] = str(c["page"])
    if run.domain == "rag":
        out["domain"] = "rag"
    for k in ("fragment", "chunk_id", "calc_id", "expr", "src_id", "openalex_id", "url"):
        if c.get(k):
            out[k] = c[k]
    return {k: v for k, v in out.items() if v is not None}


def ask_card(card: dict) -> dict:
    """Карточка агента -> карточка фронта (compare: metrics[], title)."""
    if card.get("kind") == "compare":
        return {"title": "Сравнение", **card, "metrics": card.get("metrics") or [card.get("metric")]}
    return card


def ask_via_agent(actx, corpus_id: str, question: str, pid: str | None, only: set[str] | None,
                  thread_id: str | None = None) -> dict | None:
    """Ответ /ask агентом: CaseAnswer {text, cards, cites, suggestions, meta[, clarify]}; None - агент недоступен.
    С `thread_id` модель видит прошлые обмены этого чата."""
    if _llm(actx) is None:
        return None
    services = actx.get_services()
    with services.db.uow() as uow:
        run = load_ctx(uow, uow.corpora.get(corpus_id), only)
    r = run_agent(actx, corpus_id, question, pid=pid, only=only, thread_id=thread_id)
    a = r["answer"]
    if a is None:
        return None
    out = {"text": a["text"], "cards": [ask_card(c) for c in a.get("cards") or []],
           "cites": [ask_cite(c, run) for c in a.get("cites") or []], "suggestions": a.get("suggestions") or [],
           "sources": a.get("sources") or [], "meta": a["meta"]}
    for k in ("clarify", "fallback", "partial", "unverified"):
        if a.get(k):
            out[k] = a[k]
    out["_tool_log"] = r["tool_log"]  # для истории (run_messages.tool_log); /ask наружу его не отдаёт
    return out


def emit_answer(emit: Callable[[str, dict], None], mid: str, answer: dict, clarify: bool = False) -> None:
    """text -> card -> cite -> [sources] -> suggestions -> done (порядок событий §15.9)."""
    for line in answer["text"].splitlines(keepends=True):
        emit("text", {"text": line})
    for c in answer.get("cards") or []:
        emit("card", {"card": c})
    for c in answer.get("cites") or []:
        emit("cite", {"cite": c})
    if answer.get("sources"):  # внешние работы (OpenAlex): фронт рисует блок и ведёт на страницу «Источники»
        emit("sources", {"sources": answer["sources"]})
    emit("suggestions", {"suggestions": answer.get("suggestions") or []})
    emit("done", {"message_id": mid, "status": "done", "answer": answer, **({"clarify": True} if clarify else {})})
