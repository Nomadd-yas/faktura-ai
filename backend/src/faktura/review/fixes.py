"""Предложения исправлений для замечаний разбора (детерминированные, без LLM).

suggest(domain, issue, results, measurements) -> {"suggestions": [...], "alternatives": [...], "note": str|None,
"resolution": {"issue_state", "reason"}|None}.

Элемент suggestions (применяется вместе, одним действием):
  {rid, metric, current, suggested, unit, reason, method: "rule", confidence: "high"|"medium"}   - новое значение
  {rid, metric, current, action: "reject"|"note", reason, method: "rule", confidence}            - без числа
alternatives: [{title, items: [...], confidence}] - другие варианты исправления (когда неясно, какое число ошибочно).
resolution - когда числового исправления нет: предложить состояние замечания `ok` (проверено) с причиной.
`results` - строки results статьи с уже применёнными правками v2 (runs_v2._effective_results).
Неизвестные проверки дают пустой результат.
"""
from __future__ import annotations

import math
from typing import Any

from ..agent.calc import CalcError, safe_eval

METHOD = "rule"
_SHIFTS = (-3, -2, -1, 1, 2, 3)
# физические пределы показателей solar (как в checks.rules range): (min, max]
_LIMITS = {"PCE": (0, 100), "FF": (0, 100), "Voc": (0, 3), "Jsc": (0, 50)}
_RAG_UNIT_SCALE = {"Recall", "MRR", "nDCG", "EM", "F1", "Precision"}

# проверки без числового исправления: предлагаем «проверено» (ok) с причиной
_NO_NUMERIC: dict[str, str] = {
    "ambiguity": "Из статьи нельзя однозначно понять, к какому прибору/моменту относится значение; числа не меняем, "
                 "замечание можно закрыть как проверенное",
    "measurement_conditions": "Нестандартные условия измерения указаны в статье; значение верное, замечание "
                              "информационное",
    "missing_conditions": "Условия измерения в статье не указаны; восстанавливать их по догадке нельзя",
    "comparability": "Нестандартный источник света или образец; значение извлечено верно, но несопоставимо",
    "plausibility": "Значение близко к рекорду: если оно совпадает со статьёй, замечание можно закрыть",
    "rounding": "Расхождение объясняется округлением последнего знака, значения менять не нужно",
    "figure_only": "Чисел нет, результат виден только на графике",
    "figure_mismatch": "Расхождение с рисунком - точность чтения графика или разные данные; число не меняем",
    "voc_deficit_consistency": "Дефицит Voc указан в статье отдельно; проверьте по тексту и закройте замечание",
    "dedup_trap": "Одинаковые значения у разных приборов или в двух таблицах; проверьте по статье",
    "cited_in_table": "Строки из других работ в таблице; при необходимости отклоните их вручную",
    "metric_k_missing": "k у метрики @k в публикации не указан; подбирать его нельзя",
    "mrr": "MRR больше Recall@10 - проверьте конфигурации в статье; число автоматически не определить",
    "typo": "Опечатка в формуле или обозначении, не в числе",
    "possible_swap": "Возможна перестановка значений; проверьте по статье",
}


def _num(v: Any) -> float | None:
    try:
        f = float(str(v).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


def _val(r: dict) -> float | None:
    v = _num(r.get("normalized_value"))
    return v if v is not None else _num(r.get("metric_value"))


def _unit(r: dict) -> str:
    return r.get("normalized_unit") or r.get("metric_unit") or ""


def _fmt(x: float) -> str:
    return f"{x:.4g}"


def _round(x: float) -> float:
    return float(f"{x:.5g}")


def _item(r: dict, suggested: float, reason: str, confidence: str = "high") -> dict:
    return {"rid": r.get("result_id"), "metric": r.get("metric_name", ""), "current": _val(r),
            "suggested": _round(suggested), "unit": _unit(r), "reason": reason, "method": METHOD,
            "confidence": confidence}


def _action(r: dict, action: str, reason: str, confidence: str = "medium") -> dict:
    return {"rid": r.get("result_id"), "metric": r.get("metric_name", ""), "current": _val(r), "action": action,
            "reason": reason, "method": METHOD, "confidence": confidence}


def typo_score(cur: float, new: float) -> float:
    """Насколько new/cur похоже на сдвиг запятой (степень 10): 0 - точно, чем больше, тем дальше."""
    if not cur or not new or cur * new <= 0:
        return 9.0
    lg = math.log10(abs(new / cur))
    return min(abs(lg - n) for n in _SHIFTS)


def _plausible(metric: str, v: float) -> bool:
    lim = _LIMITS.get(metric)
    return True if lim is None else lim[0] < v <= lim[1]


def scale_fix(metric: str, v: float, domain: str) -> tuple[float, str, str] | None:
    """Исправление масштаба единицы: (новое значение, причина, уверенность) или None."""
    if domain == "rag" or metric in _RAG_UNIT_SCALE:
        if 1 < v <= 100:
            return v / 100, f"{metric} лежит в шкале 0-1, а значение {_fmt(v)} похоже на проценты", "high"
        return None
    if metric == "FF" and 0 < v <= 1:
        return v * 100, f"FF = {_fmt(v)} - это доля, в процентах {_fmt(v * 100)} (×100)", "high"
    if metric == "PCE" and 0 < v <= 1:
        return v * 100, f"КПД {_fmt(v)} похож на долю; в процентах {_fmt(v * 100)} (×100)", "medium"
    if metric == "PCE" and v > 100:
        for k in (10, 100):
            if 0 < v / k <= 50:
                return v / k, f"КПД {_fmt(v)} % выше физического предела; вероятен сдвиг запятой (÷{k})", "medium"
    if metric == "Voc" and v > 10:
        if v / 1000 <= 3:
            return v / 1000, f"Voc = {_fmt(v)} - это милливольты, в вольтах {_fmt(v / 1000)} (÷1000)", "high"
    if metric == "Jsc":
        if 0 < v < 0.1:
            return v * 1000, f"Jsc = {_fmt(v)} - вероятно А/см², в мА/см² {_fmt(v * 1000)} (×1000)", "high"
        if v >= 1000:
            return v / 1000, f"Jsc = {_fmt(v)} - вероятно мкА/см², в мА/см² {_fmt(v / 1000)} (÷1000)", "medium"
    return None


# ------------------------------------------------------------------ правила
def _by_metric(rows: list[dict], metric: str) -> list[dict]:
    return [r for r in rows if r.get("metric_name") == metric]


def _pin(measurements: list[dict], meas_id: str | None) -> float:
    """Мощность света, мВт/см²: из measurements (W/m² ×0.1, sun ×100), иначе 100."""
    m = next((x for x in measurements if x.get("measurement_id") == meas_id), None)
    v = _num((m or {}).get("light_intensity"))
    if not v or v <= 0:
        return 100.0
    u = str((m or {}).get("intensity_unit") or "").lower().replace(" ", "").replace("²", "2")
    if not u:
        return v * 0.1 if v > 200 else v
    if "w/m2" in u:
        return v * 0.1
    if "sun" in u:
        return v * 100
    return v


def _jv(rows: list[dict], meas: list[dict], domain: str) -> dict:
    pce = _by_metric(rows, "PCE")[:1]
    voc, jsc, ff = (_by_metric(rows, m)[:1] for m in ("Voc", "Jsc", "FF"))
    if not (pce and voc and jsc and ff):
        return {}
    p, v, j, f = pce[0], voc[0], jsc[0], ff[0]
    vals = {k: _val(r) for k, r in (("P", p), ("V", v), ("J", j), ("F", f))}
    if any(x is None or x <= 0 for x in vals.values()):
        return {}
    pin = _pin(meas, p.get("measurement_id"))
    vals["I"] = pin
    try:
        calc = safe_eval("V*J*F/I", vals)
        f_need = safe_eval("P*I/(V*J)", vals)
        j_need = safe_eval("P*I/(V*F)", vals)
        v_need = safe_eval("P*I/(J*F)", vals)
    except (CalcError, ZeroDivisionError):
        return {}
    pin_txt = "" if pin == 100 else f" при Pin = {_fmt(pin)} мВт/см²"
    base = f"Voc·Jsc·FF/Pin = {_fmt(vals['V'])}·{_fmt(vals['J'])}·{_fmt(vals['F'])}/{_fmt(pin)}{pin_txt}"
    cands = [(p, calc, f"КПД пересчитан: {base} = {_fmt(calc)} %"),
             (f, f_need, f"FF, согласующийся с КПД {_fmt(vals['P'])} %, Voc и Jsc: {_fmt(f_need)} %"),
             (j, j_need, f"Jsc, согласующийся с КПД, Voc и FF: {_fmt(j_need)} мА/см²"),
             (v, v_need, f"Voc, согласующийся с КПД, Jsc и FF: {_fmt(v_need)} В")]
    cands = [(r, n, why) for r, n, why in cands if n and math.isfinite(n) and _plausible(r["metric_name"], n)]
    if not cands:
        return {}
    scored = sorted(((typo_score(_val(r), n), i, r, n, why) for i, (r, n, why) in enumerate(cands)),
                    key=lambda t: (t[0], t[1]))
    top = scored[0]
    if top[0] < 0.03:
        conf = "high"
        why = top[4] + f"; текущее значение отличается сдвигом запятой (×{_fmt(top[3] / _val(top[2]))})"
    else:
        # нет опечатки по порядку: основная версия - пересчёт КПД (производная величина)
        top = next((t for t in scored if t[2] is p), top)
        conf, why = "medium", top[4]
    primary = _item(top[2], top[3], why, conf)
    alts = [{"title": f"Исправить {r['metric_name']}", "confidence": "medium",
             "items": [_item(r, n, w, "medium")]} for _s, _i, r, n, w in scored if r is not top[2]]
    return {"suggestions": [primary], "alternatives": alts}


def _champion(rows: list[dict]) -> dict:
    ch = next((r for r in rows if r.get("aggregation") == "champion"), None)
    mn = next((r for r in rows if r.get("aggregation") in ("mean", "median")), None)
    if not ch or not mn:
        return {}
    vc, vm = _val(ch), _val(mn)
    if vc is None or vm is None:
        return {}
    why = (f"Лучший прибор ({_fmt(vc)}) не может быть хуже среднего ({_fmt(vm)}): вероятно, значения "
           "перепутаны местами")
    swap = [_item(ch, vm, why, "medium"), _item(mn, vc, why, "medium")]
    return {"suggestions": swap,
            "alternatives": [{"title": "Строка «лучший» помечена неверно: отметить как проверенную",
                              "confidence": "medium",
                              "items": [_action(ch, "note", "Агрегация лучшего значения проверена вручную")]}]}


def _pair(rows: list[dict], what: str) -> dict:
    """Два значения одного показателя расходятся: либо исправить одно, либо другое (по похожести на опечатку)."""
    rows = [r for r in rows if _val(r) is not None]
    if len(rows) < 2:
        return {}
    a, b = rows[0], rows[1]
    va, vb = _val(a), _val(b)
    if va == vb:
        return {}
    opts = [(typo_score(va, vb), 0, a, vb), (typo_score(vb, va), 1, b, va)]
    opts.sort(key=lambda t: (t[0], t[1]))
    s0, _i, r0, n0 = opts[0]
    conf = "high" if s0 < 0.03 else "medium"
    why = f"{what}: {_fmt(va)} и {_fmt(vb)}; приведите значение в соответствие со вторым вхождением"
    if s0 < 0.03:
        why += f" (отличие - сдвиг запятой ×{_fmt(n0 / _val(r0))})"
    s1, _j, r1, n1 = opts[1]
    return {"suggestions": [_item(r0, n0, why, conf)],
            "alternatives": [{"title": f"Исправить {r1.get('metric_name', '')} {r1.get('result_id')}",
                              "confidence": "medium", "items": [_item(r1, n1, why, "medium")]}]}


def _scale_for_rows(rows: list[dict], domain: str, only_with_issue: bool = True) -> list[dict]:
    out = []
    for r in rows:
        v = _val(r)
        if v is None:
            continue
        fx = scale_fix(r.get("metric_name") or "", v, domain)
        if fx:
            out.append(_item(r, fx[0], fx[1], fx[2]))
    return out


def _recall_k(rows: list[dict]) -> dict:
    rows = [r for r in rows if _val(r) is not None]
    if len(rows) < 2:
        return {}
    a, b = rows[0], rows[1]
    why = "Recall@k не может убывать с ростом k; значения, вероятно, перепутаны местами"
    return {"suggestions": [_item(a, _val(b), why, "medium"), _item(b, _val(a), why, "medium")]}


def suggest(domain: str, issue: dict, results: list[dict], measurements: list[dict] | None = None) -> dict:
    """Предложения по одному замечанию. results - действующие строки статьи (с правками v2)."""
    check = (issue.get("check_name") or "").strip()
    rids = [x.strip() for x in (issue.get("record_ids") or "").split(";") if x.strip()]
    by_id = {r.get("result_id"): r for r in results}
    rows = [by_id[i] for i in rids if i in by_id]
    out: dict[str, Any] = {"suggestions": [], "alternatives": [], "note": None, "resolution": None}
    found: dict = {}
    try:
        if check in ("unit_inconsistency", "range", "metric_scale", "voc_limit"):
            items = _scale_for_rows(rows, domain)
            if items:
                found = {"suggestions": items}
        elif check == "jv_consistency":
            found = _jv(rows, measurements or [], domain)
        elif check == "champion_vs_mean":
            found = _champion(rows)
        elif check in ("contradiction", "eqe_consistency"):
            found = _pair(rows, "Расхождение значений" if check == "contradiction" else "Jsc по EQE и по ВАХ")
        elif check == "recall_k":
            found = _recall_k(rows)
        elif check == "derived_values":
            if rows:
                found = {"suggestions": [_action(r, "reject", "Это разность или относительный прирост из текста, "
                                                 "а не значение показателя: строку лучше отклонить", "high")
                                         for r in rows]}
        elif check == "hysteresis_index":
            out["note"] = "HI считается по КПД обратной и прямой развёрток; исправьте КПД, и замечание закроется"
    except Exception:  # noqa: BLE001 - предложение не должно ломать экран
        found = {}
    out["suggestions"] = found.get("suggestions", [])
    out["alternatives"] = found.get("alternatives", [])
    if not out["suggestions"] and check in _NO_NUMERIC:
        out["resolution"] = {"issue_state": "ok", "reason": _NO_NUMERIC[check]}
        out["note"] = _NO_NUMERIC[check]
    elif not out["suggestions"] and check == "hysteresis_index":
        out["resolution"] = {"issue_state": "ok", "reason": "Индекс гистерезиса проверен вручную"}
    elif not out["suggestions"] and check in ("jv_consistency", "champion_vs_mean", "contradiction",
                                              "eqe_consistency", "unit_inconsistency", "derived_values", "range",
                                              "metric_scale", "recall_k", "voc_limit"):
        out["note"] = "Числовое исправление определить не удалось; проверьте значения по статье вручную"
        out["resolution"] = {"issue_state": "ok", "reason": "Проверено вручную"}
    return out


def fix_available(domain: str, issue: dict, results: list[dict], measurements: list[dict] | None = None) -> bool:
    s = suggest(domain, issue, results, measurements)
    return bool(s["suggestions"] or s["resolution"])


def known_checks() -> set[str]:
    return set(_NO_NUMERIC) | {"unit_inconsistency", "range", "metric_scale", "voc_limit", "jv_consistency",
                               "champion_vs_mean", "contradiction", "eqe_consistency", "recall_k",
                               "derived_values", "hysteresis_index"}

