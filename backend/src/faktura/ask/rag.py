"""Область RAG: лучшие значения метрик только среди сопоставимых (задача, датасет, метрика, вариант, k, единица)
и списки датасетов, конфигураций, моделей, задач (карточка ragTable)."""
from __future__ import annotations

from . import MAX_ROWS
from .builder import Builder, Executed, fnum, in_papers, n_papers, n_values, place_of, plural, to_float, unit_ru
from .context import PaperData, RunCtx

COLS = ["Статья", "Значение", "Конфигурация", "Датасет", "Место в статье"]
MODEL_COLS = {"generators": "generator_model", "embeddings": "embedding_model", "retrievers": "retriever_model",
              "rerankers": "reranker_model"}
MODEL_RU = {"generators": "генераторы", "embeddings": "модели эмбеддингов", "retrievers": "ретриверы",
            "rerankers": "реранкеры"}


def value_of(row: dict) -> float | None:
    v = to_float(row.get("normalized_value"))
    return v if v is not None else to_float(row.get("metric_value"))


def direction(row: dict) -> str | None:
    d = (row.get("metric_direction") or "").lower()
    return "higher" if d.startswith("higher") else "lower" if d.startswith("lower") else None


def val_text(row: dict) -> str:
    v = value_of(row)
    if v is None:
        return "—"
    u = row.get("normalized_unit") or row.get("metric_unit") or ""
    return f"{fnum(v)} {unit_ru(u)}".strip() if u and u not in ("fraction", "score", "ratio") else fnum(v)


def config_name(p: PaperData, row: dict) -> str:
    c = p.configs.get(row.get("config_id") or "")
    if not c:
        return row.get("config_id") or "—"
    name = c.get("rag_variant") or " + ".join(x for x in (c.get("retrieval_method"), c.get("retriever_model"),
                                                          c.get("generator_model")) if x)
    return (name or c.get("config_id") or "—")[:80]


def dataset_name(p: PaperData, row: dict) -> str:
    d = p.datasets.get(row.get("dataset_id") or "")
    return (d.get("dataset_name") or d.get("dataset_id") or "") if d else ""


def task_name(p: PaperData, row: dict) -> str:
    t = p.tasks.get(row.get("task_id") or "")
    return (t.get("task_type") or "") if t else ""


def _label(p: PaperData, r: dict) -> str:
    return " · ".join(x for x in (p.pid, config_name(p, r), f"{r.get('metric_name')} {val_text(r)}",
                                  "results · " + r.get("result_id", ""), place_of(r)) if x)


def select(ctx: RunCtx, plan: dict, names: list[str]) -> list[tuple[PaperData, dict]]:
    out = []
    ds, task, cfg = plan["rag_dataset"].lower(), plan["rag_task"].lower(), plan["rag_config"].lower()
    for p in ctx.target(plan["pids"]):
        for r in p.results:
            if r.get("extraction_status") not in ("extracted", "", None) or r.get("duplicate_of") or value_of(r) is None:
                continue
            if (r.get("result_origin") or "primary") != plan["origin"]:
                continue
            if names and r.get("metric_name") not in names:
                continue
            if ds and ds not in dataset_name(p, r).lower():
                continue
            if task and task not in task_name(p, r).lower():
                continue
            if cfg and cfg not in config_name(p, r).lower():
                continue
            if plan["aggregation"] and r.get("aggregation") != plan["aggregation"]:
                continue
            v = value_of(r)
            if plan["min_value"] is not None and not v >= plan["min_value"]:
                continue
            if plan["max_value"] is not None and not v <= plan["max_value"]:
                continue
            out.append((p, r))
    return out


def _key(p: PaperData, r: dict) -> tuple:
    return (task_name(p, r), dataset_name(p, r), r.get("metric_name"), r.get("metric_variant") or "",
            r.get("metric_k") or "", r.get("normalized_unit") or r.get("metric_unit") or "")


def _cond(key: tuple) -> str:
    task, ds, _m, variant, k, _u = key
    parts = [f"датасет «{ds}»" if ds else "", f"задача «{task}»" if task else "",
             f"вариант метрики «{variant[:60]}»" if variant else "", f"k = {k}" if k else ""]
    return ", ".join(x for x in parts if x)


def run_values(ctx: RunCtx, plan: dict, b: Builder) -> Executed:
    best = plan["intent"] == "best"
    worst = plan["sort"] == "worst"
    names = plan["metrics"] or [m for m, _, _ in ctx.metric_stats()[:1]]
    if not names:
        return b.done(plan["intent"], "В этом разборе нет значений метрик.")
    rows = select(ctx, plan, names)
    mt = " / ".join(names[:3])
    if not rows:
        return b.done(plan["intent"], f"{mt}: подходящих значений в разборе нет.", {"metric": mt, "total": 0})
    buckets: dict[tuple, list[tuple[PaperData, dict]]] = {}
    for p, r in rows:
        buckets.setdefault(_key(p, r), []).append((p, r))
    # сравниваем только сопоставимое: берём набор условий, где больше всего статей, затем строк
    key = max(buckets, key=lambda k: (len({p.pid for p, _ in buckets[k]}), len(buckets[k])))
    main = buckets[key]
    d = direction(main[0][1])
    ordered = sorted(main, key=lambda x: (value_of(x[1]), x[0].pid, x[1].get("result_id", "")))
    if d != "lower":
        ordered.reverse()
    if worst:
        ordered.reverse()
    seen: set[tuple] = set()  # у одной конфигурации одной статьи - её лучшее значение, а не вся кривая
    cands = [x for x in ordered if not ((x[0].pid, x[1].get("config_id")) in seen or seen.add((x[0].pid, x[1].get("config_id"))))]
    if plan["per_paper"] and best and len({p.pid for p, _ in cands}) > 1:
        seen_p: set[str] = set()
        cands = [x for x in cands if not (x[0].pid in seen_p or seen_p.add(x[0].pid))]
    limit = min(plan["limit"] if best else max(plan["limit"], MAX_ROWS), MAX_ROWS)
    shown = cands[:limit]
    marks = [b.cite_result(p.pid, r, _label(p, r)) for p, r in shown]
    has_dir = d is not None and len({value_of(r) for _, r in shown}) > 1
    b.cards.append({
        "kind": "ragTable", "title": f"{key[2]}: {key[1]}" if key[1] else str(key[2]), "cols": COLS,
        "rows": [{"cells": [p.pid, val_text(r), config_name(p, r), dataset_name(p, r) or "—", place_of(r) or "—"],
                  "pid": p.pid, "rid": r["result_id"], "best": bool(best and has_dir and i == 0)}
                 for i, (p, r) in enumerate(shown)],
        **({"note": "Выше лучше" if d == "higher" else "Ниже лучше"} if has_dir else {})})
    other = len(buckets) - 1
    total = len(main)
    first_p, first_r = shown[0]
    cond = _cond(key)
    word = "Худшее" if worst else "Лучшее"
    if best and d is None:
        text = (f"У метрики {key[2]} в данных не указано, что считать лучшим, поэтому «лучшее» не выбираю. "
                f"Значения ({cond}): " + "; ".join(f"{val_text(r)} у {p.pid} {m}" for m, (p, r) in zip(marks[:3], shown[:3])) + ".")
    elif best:
        text = (f"{word} значение {key[2]}: {val_text(first_r)} у {first_p.pid}, конфигурация «{config_name(first_p, first_r)}» "
                f"{marks[0]}. Сравнивал только значения в одинаковых условиях: {cond}. "
                f"Значений: {total}, статей: {len({p.pid for p, _ in main})}.")
    else:
        text = (f"{key[2]}: {n_values(total)} в {in_papers(len({p.pid for p, _ in main}))} ({cond}). "
                f"Первое в списке: {val_text(first_r)} у {first_p.pid} {marks[0]}.")
        if len(cands) > len(shown):
            text += f" Показаны первые {len(shown)}."
    if other > 0:
        text += (f" Есть ещё {other} {plural(other, 'набор условий', 'набора условий', 'наборов условий')}: "
                 "значения из них не сравниваются, уточните датасет.")
    facts = {"kind": "best" if best else "list", "metric": key[2], "conditions": cond, "direction": d or "",
             "total": total, "shown": len(shown), "other_condition_sets": other,
             "rows": [{"cite": m, "pid": p.pid, "value": val_text(r), "config": config_name(p, r), "page": r.get("source_page") or ""}
                      for m, (p, r) in zip(marks[:8], shown[:8])]}
    return b.done(plan["intent"], text, facts, total, len(shown))


def run_list(ctx: RunCtx, plan: dict, b: Builder) -> Executed:
    kind = plan["rag_list"] or "datasets"
    target = ctx.target(plan["pids"])
    m: dict[str, set[str]] = {}
    for p in target:
        if kind == "datasets":
            vals = [d.get("dataset_name") or d.get("dataset_id") or "" for d in p.tables.get("datasets") or []]
        elif kind == "tasks":
            vals = [t.get("task_type") or "" for t in p.tables.get("tasks") or []]
        elif kind == "configs":
            vals = [c.get("rag_variant") or c.get("config_id") or "" for c in p.tables.get("configurations") or []]
        else:
            vals = [c.get(MODEL_COLS[kind]) or "" for c in p.tables.get("configurations") or []]
        for v in vals:
            if v.strip():
                m.setdefault(v.strip(), set()).add(p.pid)
    ru = {"datasets": "Датасеты", "tasks": "Задачи", "configs": "Конфигурации"}.get(kind) or MODEL_RU[kind].capitalize()
    if not m:
        return b.done("rag_list", f"{ru}: в этих статьях ничего не указано.")
    items = sorted(m.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    shown = items[:15]
    b.cards.append({"kind": "ragTable", "title": ru, "cols": ["Название", "Статей", "Какие"],
                    "rows": [{"cells": [name, str(len(ps)), ", ".join(sorted(ps)[:6]) + ("…" if len(ps) > 6 else "")],
                              "pid": sorted(ps)[0]} for name, ps in shown],
                    **({"note": f"Показаны 15 из {len(items)}"} if len(items) > 15 else {})})
    top = "; ".join(f"{n} ({n_papers(len(ps))})" for n, ps in items[:3])
    text = f"{ru}: {len(items)} в {in_papers(len(target))}. Чаще всего: {top}."
    return b.done("rag_list", text, {"kind": kind, "total": len(items), "top": [[n, len(ps)] for n, ps in items[:5]]},
                  len(items), len(shown))


def count_text(ctx: RunCtx, plan: dict) -> tuple[str, dict]:
    target = ctx.target(plan["pids"])
    n = sum(len(p.results) for p in target)
    conf = sum(len(p.tables.get("configurations") or []) for p in target)
    iss = sum(len(p.issues) for p in target)
    return (f"{n_papers(len(target))}: {n_values(n)}, {conf} {plural(conf, 'конфигурация', 'конфигурации', 'конфигураций')}, "
            f"{iss} {plural(iss, 'открытое замечание', 'открытых замечания', 'открытых замечаний')}.",
            {"papers": len(target), "results": n, "configurations": conf, "open_issues": iss})
