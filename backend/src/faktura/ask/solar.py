"""Значения показателей по статьям солнечных элементов: «лучшее» и списки значений (карточка results)."""
from __future__ import annotations

import re

from . import MAX_ROWS
from .builder import (CLASS_RU, LOWER_BETTER, Builder, Executed, fnum, in_papers, metric_label, n_values, place_of,
                      to_float, unit_ru)
from .context import PaperData, RunCtx


def value_of(row: dict) -> float | None:
    v = to_float(row.get("normalized_value"))
    return v if v is not None else to_float(row.get("metric_value"))


def unit_of(row: dict) -> str:
    return row.get("normalized_unit") or row.get("metric_unit") or ""


def val_text(row: dict) -> str:
    v = value_of(row)
    u = unit_ru(unit_of(row))
    return f"{fnum(v)} {u}".strip() if v is not None else "—"


def class_of(p: PaperData, row: dict) -> str:
    m = p.materials.get(row.get("material_id") or "")
    if m and m.get("material_class"):
        return m["material_class"]
    d = p.devices.get(row.get("device_id") or "")
    dm = d and p.materials.get(d.get("absorber_material_id") or "")
    if dm and dm.get("material_class"):
        return dm["material_class"]
    cnt: dict[str, float] = {}
    for mm in p.tables.get("materials") or []:
        c = mm.get("material_class")
        if c:
            cnt[c] = cnt.get(c, 0) + (0.1 if c == "other" else 1)
    return max(cnt, key=cnt.get) if cnt else ""


def device_name(p: PaperData, row: dict) -> str:
    d = p.devices.get(row.get("device_id") or "")
    return (d.get("device_label") or d.get("device_id") or "") if d else ""


def select(ctx: RunCtx, plan: dict, metric: str | None) -> list[tuple[PaperData, dict]]:
    out: list[tuple[PaperData, dict]] = []
    layer_rx = [re.compile(rf"(?<![a-z]){re.escape(t.lower())}x?(?![a-z])") for t in plan["layers"]]
    dev = plan["device"].lower()
    for p in ctx.target(plan["pids"]):
        layer_devices: set[str] | None = None
        if layer_rx:
            layer_devices = {d for d, ls in p.layers_of.items()
                             if any(rx.search((x.get("layer_material") or "").lower()) for rx in layer_rx for x in ls)}
        for r in p.results:
            if r.get("extraction_status") != "extracted" or r.get("duplicate_of") or value_of(r) is None:
                continue
            if (r.get("result_origin") or "primary") != plan["origin"]:
                continue
            if metric and r.get("metric_name") != metric:
                continue
            if plan["aggregation"] and r.get("aggregation") != plan["aggregation"]:
                continue
            if plan["material_class"] and class_of(p, r) not in plan["material_class"]:
                continue
            if layer_devices is not None and r.get("device_id") not in layer_devices:
                continue
            if dev:
                d = p.devices.get(r.get("device_id") or "") or {}
                if dev not in " ".join(str(d.get(k, "")) for k in ("device_label", "device_type", "architecture",
                                                                   "device_role")).lower():
                    continue
            v = value_of(r)
            if plan["min_value"] is not None and not v >= plan["min_value"]:
                continue
            if plan["max_value"] is not None and not v <= plan["max_value"]:
                continue
            out.append((p, r))
    return out


def _sorted(rows: list[tuple[PaperData, dict]], metric: str, worst: bool) -> list[tuple[PaperData, dict]]:
    low = (metric in LOWER_BETTER) != worst  # True: меньшее первым
    return sorted(rows, key=lambda x: (value_of(x[1]) if low else -value_of(x[1]), x[0].pid, x[1].get("result_id", "")))


def scope_text(plan: dict, metric: str | None) -> str:
    parts = []
    if plan["material_class"]:
        parts.append(" и ".join(CLASS_RU.get(c, c) for c in plan["material_class"]))
    if plan["layers"]:
        parts.append("слой " + ", ".join(plan["layers"]))
    u = {"PCE": "%", "FF": "%", "Voc": "В", "Jsc": "мА/см²"}.get(metric or "", "")
    u = f" {u}" if u else ""
    lo, hi = plan["min_value"], plan["max_value"]
    if lo is not None and hi is not None:
        parts.append(f"от {fnum(lo)} до {fnum(hi)}{u}")
    elif lo is not None:
        parts.append(f"не ниже {fnum(lo)}{u}")
    elif hi is not None:
        parts.append(f"не выше {fnum(hi)}{u}")
    if plan["aggregation"]:
        parts.append({"champion": "лучшие приборы", "mean": "средние", "median": "медианы"}[plan["aggregation"]])
    if plan["origin"] == "cited":
        parts.append("цитируемые")
    return ", ".join(parts)


def _label(p: PaperData, r: dict) -> str:
    dn = device_name(p, r)
    return " · ".join(x for x in (p.pid, dn, f"{metric_label(r['metric_name'])} {val_text(r)}",
                                  "results · " + r.get("result_id", ""), place_of(r)) if x)


def default_metric(ctx: RunCtx, plan: dict) -> str | None:
    if plan["metrics"]:
        return plan["metrics"][0]
    names = [m for m, _, _ in ctx.metric_stats()]
    return "PCE" if "PCE" in names else (names[0] if names else None)


def run(ctx: RunCtx, plan: dict, b: Builder) -> Executed:
    best = plan["intent"] == "best"
    worst = plan["sort"] == "worst"
    metric = default_metric(ctx, plan)
    if not metric:
        return b.done(plan["intent"], "В этом разборе нет значений показателей.")
    rows = select(ctx, plan, metric)
    scope = scope_text(plan, metric)
    scope_s = f" ({scope})" if scope else ""
    mt = metric_label(metric)
    if not rows:
        return b.done(plan["intent"], f"{mt[0].upper() + mt[1:]}{scope_s}: подходящих значений в разборе нет.",
                      {"metric": mt, "scope": scope, "total": 0})
    pool = rows
    if best and not plan["aggregation"]:
        champ = [x for x in rows if x[1].get("aggregation") == "champion"]
        pool = champ or rows  # champion важнее среднего (как «лучшее значение» разбора)
    ordered = _sorted(pool, metric, worst)
    if plan["per_paper"] and best:
        seen: set[str] = set()
        ordered = [x for x in ordered if not (x[0].pid in seen or seen.add(x[0].pid))]
    limit = min(plan["limit"] if best else max(plan["limit"], MAX_ROWS), MAX_ROWS)
    shown = ordered[:limit]
    title = ("Худшее: " if worst else "Лучшее: ") + mt if best else mt[0].upper() + mt[1:]
    b.cards.append({"kind": "results", "title": title, "rows": [{"pid": p.pid, "rid": r["result_id"]} for p, r in shown]})
    marks = [b.cite_result(p.pid, r, _label(p, r)) for p, r in shown]
    facts_rows = [{"cite": m, "pid": p.pid, "value": val_text(r), "device": device_name(p, r),
                   "aggregation": r.get("aggregation") or "", "page": r.get("source_page") or ""}
                  for m, (p, r) in zip(marks, shown)]
    papers_n = len({p.pid for p, _ in rows})
    total = len(rows)
    first_p, first_r = shown[0]
    dn = device_name(first_p, first_r)
    if best:
        head = f"{'Худший' if worst else 'Лучший'} {mt}{scope_s}: {val_text(first_r)} у {first_p.pid}" \
               f"{f' ({dn})' if dn else ''} {marks[0]}."
        if len(shown) > 1:
            head += " Следом: " + "; ".join(f"{val_text(r)} у {p.pid} {m}" for m, (p, r) in zip(marks[1:4], shown[1:4])) + "."
        head += f" Подходящих значений: {total} в {in_papers(papers_n)}."
        if pool is not rows:
            head += " Среди значений лучших приборов (champion)."
    else:
        head = f"{mt[0].upper() + mt[1:]}{scope_s}: {n_values(total)} в {in_papers(papers_n)}. " \
               f"Первое в списке: {val_text(first_r)} у {first_p.pid} {marks[0]}."
        if total > len(shown):
            head += f" Показаны первые {len(shown)} из {total}."
    if any(r.get("result_id") in p.corrected for p, r in shown):
        head += " Учтены ручные исправления значений."
    facts = {"kind": "best" if best else "list", "metric": mt, "scope": scope, "total": total, "papers": papers_n,
             "shown": len(shown), "rows": facts_rows[:8], "worst": worst}
    return b.done(plan["intent"], head, facts, total, len(shown))
