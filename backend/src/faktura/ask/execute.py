"""Исполнитель: план -> CaseAnswer-карточки по строкам разбора. Никакого LLM: числа берутся только из строк."""
from __future__ import annotations

import json
from collections import Counter

from ..domain.registry import get_domain
from . import MAX_ROWS, rag, solar
from .builder import Builder, Executed, check_label, in_papers, n_issues, n_papers, n_values, of_papers, plural
from .context import RunCtx

NODATA = "В этом разборе пока нет данных: когда статьи будут разобраны, на вопросы можно будет отвечать."
HELP = ("Не удалось понять вопрос. Отвечаю по данным разбора: лучшее значение показателя, значения по условиям, "
        "замечания, строки и поля таблиц набора.")
_SKIP_COLS = {"source_fragment", "authors", "DOI", "title", "note", "description"}


def suggestions(ctx: RunCtx) -> list[str]:
    """Вопросы-подсказки по этому разбору: из реальных данных, без LLM (до 4)."""
    data = [p for p in ctx.papers if p.has_data and p.in_scope]
    if not data:
        return []
    out: list[str] = []
    stats = ctx.metric_stats()
    if ctx.domain == "solar":
        out.append("Лучший КПД" if any(m == "PCE" for m, _, _ in stats) else f"Лучший {stats[0][0]}" if stats else "")
        worst = max(data, key=lambda p: len(p.issues))
        if worst.issues:
            out.append(f"Замечания у {worst.pid}")
        cls = next((c for c in ctx.describe().get("classes", []) if c in ("perovskite", "CIGS", "CdTe", "kesterite")), "")
        if cls == "perovskite":
            out.append("Лучший КПД перовскитов")
        out.append("Сколько значений")
        out.append("Что в таблице devices")
    else:
        if stats:
            out.append(f"Лучший {stats[0][0]}")
        worst = max(data, key=lambda p: len(p.issues))
        if worst.issues:
            out.append(f"Замечания у {worst.pid}")
        out.append("Какие датасеты")
        out.append("Что в таблице configurations")
    return [s for s in dict.fromkeys(out) if s][:4]


def _help(ctx: RunCtx, text: str = HELP) -> Executed:
    return Executed("unknown", text)


def _issues(ctx: RunCtx, plan: dict, b: Builder) -> Executed:
    target = ctx.target(plan["pids"]) if plan["pids"] else [p for p in ctx.papers if p.has_data or p.issues]
    chk = plan["check"].lower()
    items = [(p, i) for p in target for i in p.issues
             if not chk or chk in (i.get("check_name") or "").lower() or chk in check_label(i.get("check_name") or "").lower()]
    if not items:
        who = f" у {', '.join(plan['pids'])}" if plan["pids"] else ""
        return b.done("issues", f"Открытых замечаний{who} нет.")
    by = Counter(i.get("check_name") or "" for _, i in items)
    parts = [f"{check_label(c)} — {n}" for c, n in by.most_common(3)]
    shown = items[:MAX_ROWS]
    b.cards.append({"kind": "issues", "title": "Замечания", "items": [{"pid": p.pid, "iid": i["issue_id"]} for p, i in shown],
                    **({"domain": "rag"} if ctx.domain == "rag" else {})})
    p0, i0 = items[0]
    mark = b.cite_issue(p0.pid, i0)
    papers = {p.pid for p, _ in items}
    who = f"У {plan['pids'][0]}" if len(plan["pids"]) == 1 else f"В {in_papers(len(papers))}"
    desc = (i0.get("description") or "").strip().rstrip(".")
    desc = desc[:140] + "…" if len(desc) > 140 else desc
    text = f"{who} {n_issues(len(items))} (открытых): {'; '.join(parts)}. Например, стр. {i0.get('source_page') or '—'}: {desc} {mark}."
    if len(items) > len(shown):
        text += f" Показаны первые {len(shown)} из {len(items)}."
    facts = {"total": len(items), "by_check": [[check_label(c), n] for c, n in by.most_common(5)],
             "example": {"cite": mark, "pid": p0.pid, "page": i0.get("source_page") or "", "description": desc}}
    return b.done("issues", text, facts, len(items), len(shown))


def _pick_cols(table: str, rows: list[dict], field: str, cols: list[str], key: str) -> list[str]:
    out = ["publication_id", key] if table != "publications" else ["publication_id", "year", "in_scope"]
    if field and field in cols and field not in out:
        out.append(field)
    for c in cols:
        if len(out) >= 6:
            break
        if c not in out and c not in _SKIP_COLS and any(r.get(c) for r in rows):
            out.append(c)
    return out


def _table_rows(ctx: RunCtx, plan: dict, b: Builder) -> Executed:
    spec = get_domain(ctx.domain)
    table = plan["table"] or "results"
    if table not in spec.tables:
        return Executed("table_rows", f"В наборе этой области нет таблицы {table}. Таблицы: {', '.join(spec.tables)}.")
    target = ctx.target(plan["pids"]) if plan["pids"] else [p for p in ctx.papers if p.has_data]
    rows = [(p, r) for p in target for r in p.tables.get(table) or []]
    if not rows:
        return b.done("table_rows", f"В таблице {table} строк нет.")
    field = plan["field"] if plan["field"] in spec.tables[table] else ""
    if field:  # «примеры поля»: сначала строки, где поле заполнено, по разным статьям
        rows.sort(key=lambda x: not x[1].get(field))
    limit = min(max(plan["limit"], 5), MAX_ROWS)
    shown = rows[:limit]
    cols = _pick_cols(table, [r for _, r in shown], field, list(spec.tables[table]), spec.keys[table])
    key = spec.keys[table]
    b.cards.append({"kind": "tableRows", "title": f"Строки таблицы {table}", "table": table, "cols": cols, "domain": ctx.domain,
                    "rows": [{"pid": p.pid, "id": r.get(key, ""), "cells": [r.get(c, "") for c in cols]} for p, r in shown]})
    marks = [b.cite_row(p.pid, table, r.get(key, ""), r) for p, r in shown[:3]]
    papers = len({p.pid for p, _ in rows})
    text = f"В таблице {table} {len(rows)} {plural(len(rows), 'строка', 'строки', 'строк')} из {of_papers(papers)}."
    text += f" Показаны первые {len(shown)}: " if len(rows) > len(shown) else " Строки: "
    text += "; ".join(f"{p.pid} · {r.get(key, '')} {m}" for m, (p, r) in zip(marks, shown[:3])) + "."
    return b.done("table_rows", text, {"table": table, "total": len(rows), "shown": len(shown)}, len(rows), len(shown))


def _examples(ctx: RunCtx, b: Builder, table: str, field: str, n: int = 3) -> str:
    """2-3 строки таблицы из разбора (по разным статьям, поле заполнено) как карточка tableRows; текст со сносками."""
    spec = get_domain(ctx.domain)
    cols_all, key = list(spec.tables[table]), spec.keys[table]
    picked, seen = [], set()
    for p in ctx.papers:
        for r in p.tables.get(table) or []:
            if (not field or r.get(field)) and p.pid not in seen:
                picked.append((p, r))
                seen.add(p.pid)
                break
        if len(picked) >= n:
            break
    if not picked:
        return ""
    cols = _pick_cols(table, [r for _, r in picked], field, cols_all, key)
    b.cards.append({"kind": "tableRows", "title": f"Примеры из таблицы {table}", "table": table, "cols": cols,
                    "domain": ctx.domain,
                    "rows": [{"pid": p.pid, "id": r.get(key, ""), "cells": [r.get(c, "") for c in cols]} for p, r in picked]})
    return "; ".join(f"{p.pid}: {table} · {r.get(key, '')} {b.cite_row(p.pid, table, r.get(key, ''), r)}" for p, r in picked)


def _schema(ctx: RunCtx, plan: dict, b: Builder) -> Executed:
    from ..passport.run_passport import _domain_content  # noqa: PLC0415

    ents = json.loads(_domain_content(ctx.domain)).get("entities") or []
    counts = {e["id"]: sum(len(p.tables.get(e["id"]) or []) for p in ctx.papers) for e in ents}
    if plan["field"] == "@required":  # «какие поля обязательны»: по таблице или по всем
        shown = [e for e in ents if not plan["table"] or e["id"] == plan["table"]]
        for e in shown:
            b.cards.append({"kind": "schema", "title": f"{e.get('title', e['id'])} ({e['id']})", "table": e["id"],
                            "fields": [{"name": f["name"], "ru": f["name"].replace("_", " "), "required": True,
                                        "desc": f.get("description") or "", **({"ref": f["ref"]} if f.get("ref") else {})}
                                       for f in e["fields"] if f.get("required")]})
        text = "Обязательные поля: " + "; ".join(
            f"{e['id']} — {', '.join(f['name'] for f in e['fields'] if f.get('required'))}" for e in shown)             + ". Остальные можно оставить пустыми: пустая ячейка значит, что в статье этого нет."
        return b.done("schema", text, {"tables": [e["id"] for e in shown]})
    if not plan["table"] and not plan["field"]:
        text = (f"Набор для сдачи — {len(ents)} таблиц, каждая отдельный CSV: "
                + ", ".join(f"{e['id']} — {counts[e['id']]}" for e in ents) + ".")
        return b.done("schema", text, {"tables": counts})
    ent = next((e for e in ents if e["id"] == plan["table"]), None)
    if ent is None and plan["field"]:  # поле без таблицы: ищем в results, затем в остальных
        ent = next((e for e in sorted(ents, key=lambda e: e["id"] != "results")
                    if any(f["name"] == plan["field"] for f in e["fields"])), None)
    if ent is None:
        return b.done("schema", "Такой таблицы или поля в наборе нет.")
    fields = ent["fields"]
    f0 = next((f for f in fields if f["name"] == plan["field"]), None)
    shown = [f0] if f0 else fields
    b.cards.append({"kind": "schema", "title": f"{'Поле' if f0 else 'Поля таблицы'} {f0['name'] if f0 else ent['id']}",
                    "table": ent["id"], "fields": [{"name": f["name"], "ru": f["name"].replace("_", " "),
                                                    "required": bool(f.get("required")), "desc": f.get("description") or "",
                                                    **({"ref": f["ref"]} if f.get("ref") else {})} for f in shown]})
    req = [f["name"] for f in fields if f.get("required")]
    if f0:
        vals = f0.get("values")
        text = (f"{f0['name']} — поле таблицы {ent['id']}. {(f0.get('description') or '').rstrip('.')}. "
                f"{'Обязательное' if f0.get('required') else 'Не обязательное: пусто — в статье этого нет'}."
                + (f" Допустимые значения: {', '.join(vals)}." if vals else ""))
        facts = {"table": ent["id"], "field": f0["name"], "required": bool(f0.get("required")), "values": vals or []}
    else:
        text = (f"Таблица {ent['id']} («{ent.get('title', ent['id'])}»): одна строка — {ent.get('row', '')}. Ключ: {ent.get('key')}; "
                f"полей {len(fields)}, обязательные: {', '.join(req)}. В этом разборе {counts[ent['id']]} "
                f"{plural(counts[ent['id']], 'строка', 'строки', 'строк')}.")
        facts = {"table": ent["id"], "fields": len(fields), "required": req, "rows": counts[ent["id"]]}
    ex = _examples(ctx, b, ent["id"], f0["name"] if f0 else "")
    if ex:
        text += " Примеры: " + ex + "."
    return b.done("schema", text, facts)


def _paper(ctx: RunCtx, plan: dict, b: Builder) -> Executed:
    target = [ctx.by_pid[p] for p in plan["pids"] if p in ctx.by_pid][:3]
    if not target:
        return Executed("paper", "Назовите статью разбора, например D001.")
    for p in target:
        b.cards.append({"kind": "ragPaper" if ctx.domain == "rag" else "paper", "pid": p.pid})
    lines = [f"{p.pid}: {p.title[:80] or 'без названия'}, {n_values(len(p.results))}, {n_issues(len(p.issues))}"
             + ("" if p.in_scope else ", не по теме") for p in target]
    return b.done("paper", "; ".join(lines) + ".", {"papers": [{"pid": p.pid, "results": len(p.results), "issues": len(p.issues)}
                                                              for p in target]})


def execute(ctx: RunCtx, plan: dict) -> Executed:
    """Исполняет нормализованный план. Для области без данных или неизвестных кодов - понятный текст."""
    b = Builder(ctx.domain)
    if not ctx.has_data:
        return Executed("unknown", NODATA)
    missing = plan.get("missing_pids") or []
    if missing and not plan["pids"]:
        who = ", ".join(missing)
        return Executed(plan["intent"], f"{'Статей' if len(missing) > 1 else 'Статьи'} {who} нет в этом разборе.")
    note = f" Статей {', '.join(missing)} в разборе нет." if missing else ""
    intent = plan["intent"]
    if intent in ("best", "results") and plan["unknown_metric"] and not plan["metrics"]:
        top = ", ".join(m for m, _, _ in ctx.metric_stats()[:5])
        ex = Executed(intent, f"Такой метрики в разборе нет: {plan['unknown_metric']}. Самые частые: {top}.")
    elif intent in ("best", "results"):
        ex = rag.run_values(ctx, plan, b) if ctx.domain == "rag" else solar.run(ctx, plan, b)
    elif intent == "rag_list":
        ex = rag.run_list(ctx, plan, b) if ctx.domain == "rag" else Executed("rag_list", "Списки датасетов и моделей есть только в области RAG.")
    elif intent == "issues":
        ex = _issues(ctx, plan, b)
    elif intent == "table_rows":
        ex = _table_rows(ctx, plan, b)
    elif intent == "schema":
        ex = _schema(ctx, plan, b)
    elif intent == "paper":
        ex = _paper(ctx, plan, b)
    elif intent == "count":
        target = ctx.target(plan["pids"])
        if ctx.domain == "rag":
            text, facts = rag.count_text(ctx, plan)
        else:
            rows = solar.select(ctx, plan, plan["metrics"][0] if plan["metrics"] else None) if (plan["metrics"] or plan["material_class"] or plan["layers"]) else []
            n = len(rows) if rows else sum(len(p.results) for p in target)
            text, facts = f"{n_papers(len(target))}: {n_values(n)}.", {"papers": len(target), "results": n}
        ex = b.done("count", text, facts)
    else:
        ex = _help(ctx)
    if note:
        ex.text += note
    return ex
