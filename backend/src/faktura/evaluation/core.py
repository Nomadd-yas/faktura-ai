"""Сравнение выгрузки с эталоном по spec/14 §14.7 (REQ-116, REQ-117; решение К-3).

Учитываются только `metric_name` из `metrics` (по умолчанию 11 метрик ТЗ). Запись сопоставляется по ключу
`publication_id + metric_name + прибор + aggregation + scan_direction (через measurement_id) + time_point`:
- прибор предсказания переводится в `device_id` эталона по `device_label` (casefold, схлопнутые пробелы);
  без метки - по `device_id`;
- значение - `normalized_value` с допуском на последний знак (единица последнего разряда более грубого числа);
  пустое значение (`present_not_extractable`) совпадает только с пустым;
- строки-повторы (`duplicate_of`) сопоставляются отдельно и засчитываются, только если найдена их основная
  запись эталона.
Внутри ключа пары подбираются жадно по возрастанию расхождения; `result_id` значения не имеют.

Отдельно считаются точность `source_page`, нормализации (`normalized_value` при совпавшем `raw_value`),
`aggregation`, `result_origin`, `extraction_status`, `in_scope`. Замечания - по
`(publication_id, check_name, frozenset(record_ids))`.

`EvalReport.per_metric[m]` и `EvalReport.totals`: `gold, pred, tp, fp, fn, precision, recall, f1`;
у метрики без строк в эталоне `insufficient_data = True`, P/R/F1 = None и предупреждение «мало данных».
`totals` также несёт разбивки для экрана точности: `by_paper`, `by_source`, `errors` (см. accuracy_response).
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Hashable, Sequence

from ..domain.io import parse_cell, read_tables
from ..domain.ports import EvalReport, IssueEvalReport
from ..domain.tables import EVAL_METRICS

__all__ = ["evaluate", "evaluate_tables", "evaluate_issues", "evaluate_issues_tables", "source_kind",
           "MAX_ERRORS", "SOURCE_KINDS"]

MAX_ERRORS = 1000
SOURCE_KINDS = ("table", "text", "figure", "abstract", "supplementary")
_SUPP_RE = re.compile(r"^\s*(supplementary|supporting|suppl\.?|(table|figure|fig\.?)\s*s\d)", re.I)


# --- чтение -------------------------------------------------------------------------------------

def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _num(table: str, col: str, text: Any) -> Decimal | None:
    v = parse_cell(table, col, _s(text))
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, Decimal):
        return v
    if isinstance(v, str):
        from ..export.sanitize import parse_number_text

        return parse_number_text(v)
    return None


def _page(text: Any) -> int | None:
    d = _num("results", "source_page", text)
    return int(d) if d is not None and d == d.to_integral_value() else None


def _label(text: Any) -> str:
    return " ".join(_s(text).casefold().split())


def source_kind(row: dict) -> str:
    """Вид источника значения: table | text | figure | abstract | supplementary."""
    frag, table, fig = _s(row.get("source_fragment")), _s(row.get("source_table")), _s(row.get("source_figure"))
    section = _s(row.get("source_section")).lower()
    if frag.lower().startswith("table"):
        return "table"
    if _SUPP_RE.match(frag):
        return "supplementary"
    if section.startswith("abstract") or section.startswith("аннотац"):
        return "abstract"
    if frag:
        return "text"
    if table:
        return "supplementary" if _SUPP_RE.match(table) else "table"
    if fig:
        return "supplementary" if _SUPP_RE.match(fig) else "figure"
    return "text"


@dataclass
class _Row:
    idx: int
    pid: str
    rid: str
    metric: str
    device_id: str
    device_key: Hashable
    row_label: str
    agg: str
    scan: str
    time_point: Decimal | None
    value: Decimal | None
    unit: str
    raw: str
    page: int | None
    origin: str
    status: str
    dup_of: str
    kind: str


@dataclass
class _Side:
    rows: list[_Row] = field(default_factory=list)
    pubs: dict[str, dict] = field(default_factory=dict)


def _index(tables: dict[str, list[dict]]) -> tuple[dict, dict, dict]:
    devices = {(_s(r.get("publication_id")), _s(r.get("device_id"))): r for r in tables.get("devices", [])}
    scans = {(_s(r.get("publication_id")), _s(r.get("measurement_id"))): _s(r.get("scan_direction"))
             for r in tables.get("measurements", [])}
    pubs = {}
    for r in tables.get("publications", []):
        pubs.setdefault(_s(r.get("publication_id")), r)
    return devices, scans, pubs


def _load(tables: dict[str, list[dict]], metrics: set[str],
          device_key: Callable[[str, str, str], Hashable]) -> _Side:
    devices, scans, pubs = _index(tables)
    side = _Side(pubs=pubs)
    for i, r in enumerate(tables.get("results", [])):
        metric = _s(r.get("metric_name"))
        if metric not in metrics:
            continue
        pid, did, mid = _s(r.get("publication_id")), _s(r.get("device_id")), _s(r.get("measurement_id"))
        dev = devices.get((pid, did))
        label = _label(dev.get("device_label")) if dev else ""
        side.rows.append(_Row(
            idx=i, pid=pid, rid=_s(r.get("result_id")), metric=metric, device_id=did,
            device_key=device_key(pid, did, label) if did else None,
            row_label=_s(dev.get("device_label")) if dev and _s(dev.get("device_label")) else did,
            agg=_s(r.get("aggregation")), scan=scans.get((pid, mid), "") if mid else "",
            time_point=_num("results", "time_point", r.get("time_point")),
            value=_num("results", "normalized_value", r.get("normalized_value")),
            unit=_s(r.get("normalized_unit")) or _s(r.get("metric_unit")),
            raw=_s(r.get("raw_value")), page=_page(r.get("source_page")), origin=_s(r.get("result_origin")),
            status=_s(r.get("extraction_status")), dup_of=_s(r.get("duplicate_of")), kind=source_kind(r)))
    return side


def _load_pair(gold_t: dict, pred_t: dict, metrics: set[str]) -> tuple[_Side, _Side]:
    gold = _load(gold_t, metrics, lambda pid, did, label: did)
    gdev, _, _ = _index(gold_t)
    by_label: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (pid, did), r in gdev.items():
        lab = _label(r.get("device_label"))
        if lab:
            by_label[(pid, lab)].append(did)

    def pred_key(pid: str, did: str, label: str) -> Hashable:
        if not label:
            return did
        cands = by_label.get((pid, label))
        if not cands:
            return ("label", label)
        return did if did in cands else cands[0]

    return gold, _load(pred_t, metrics, pred_key)


# --- сопоставление ------------------------------------------------------------------------------

def _ulp(d: Decimal) -> Decimal:
    exp = d.as_tuple().exponent
    return Decimal(1).scaleb(exp) if isinstance(exp, int) else Decimal(0)


def _diff(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """Расхождение, если значения совпадают с допуском на последний знак, иначе None."""
    if a is None or b is None:
        return Decimal(0) if a is None and b is None else None
    d = abs(a - b)
    return d if d <= max(_ulp(a), _ulp(b)) else None


def _key(r: _Row, with_agg: bool = True) -> tuple:
    return (r.pid, r.metric, r.device_key, r.agg if with_agg else None, r.scan, r.time_point)


def _greedy(gold: list[_Row], pred: list[_Row], keyfn: Callable[[_Row], Hashable],
            score: Callable[[_Row, _Row], Decimal | None]) -> list[tuple[_Row, _Row]]:
    gk: dict[Hashable, list[_Row]] = defaultdict(list)
    pk: dict[Hashable, list[_Row]] = defaultdict(list)
    for g in gold:
        gk[keyfn(g)].append(g)
    for p in pred:
        pk[keyfn(p)].append(p)
    pairs: list[tuple[_Row, _Row]] = []
    for k, gs in gk.items():
        ps = pk.get(k)
        if not ps:
            continue
        cands = []
        for gi, g in enumerate(gs):
            for pi, p in enumerate(ps):
                d = score(g, p)
                if d is not None:
                    cands.append((d, gi, pi))
        cands.sort()
        used_g: set[int] = set()
        used_p: set[int] = set()
        for _, gi, pi in cands:
            if gi in used_g or pi in used_p:
                continue
            used_g.add(gi)
            used_p.add(pi)
            pairs.append((gs[gi], ps[pi]))
    return pairs


def _value_score(g: _Row, p: _Row) -> Decimal | None:
    return _diff(g.value, p.value)


def _raw_norm(s: str) -> str:
    return "".join(s.split()).casefold()


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _prf(gold: int, pred: int, tp: int) -> dict[str, Any]:
    p, r = _rate(tp, pred), _rate(tp, gold)
    f1 = (2 * p * r / (p + r) if (p + r) else 0.0) if p is not None and r is not None else None
    return {"gold": gold, "pred": pred, "tp": tp, "fp": pred - tp, "fn": gold - tp, "precision": p, "recall": r,
            "f1": f1}


def _num_text(d: Decimal | None, unit: str) -> str | None:
    if d is None:
        return None
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    s = s.replace(".", ",")
    return f"{s} {unit}".strip()


def _short(pub: dict | None, pid: str) -> str:
    if not pub:
        return pid
    authors = _s(pub.get("authors"))
    first = authors.split(";")[0].strip() if authors else ""
    surname = first.split()[0].rstrip(",") if first else ""
    year = _s(pub.get("year"))
    if surname and year:
        return f"{surname} ({year})"
    title = _s(pub.get("title"))
    if title:
        return (title[:40] + "…") if len(title) > 40 else title
    return pid


def evaluate_tables(gold_t: dict[str, list[dict]], pred_t: dict[str, list[dict]], *,
                    metrics: Sequence[str] = EVAL_METRICS) -> EvalReport:
    """Ядро `evaluate`: таблицы в виде `read_tables` (строки как словари ячеек)."""
    mset = list(dict.fromkeys(metrics))
    gold, pred = _load_pair(gold_t, pred_t, set(mset))

    g_main = [r for r in gold.rows if not r.dup_of]
    g_dup = [r for r in gold.rows if r.dup_of]
    p_main = [r for r in pred.rows if not r.dup_of]
    p_dup = [r for r in pred.rows if r.dup_of]
    main_pairs = _greedy(g_main, p_main, _key, _value_score)
    matched_main = {(g.pid, g.rid) for g, _ in main_pairs}
    dup_pairs = [(g, p) for g, p in _greedy(g_dup, p_dup, _key, _value_score) if (g.pid, g.dup_of) in matched_main]
    pairs = main_pairs + dup_pairs
    g_ok = {id(g) for g, _ in pairs}
    p_ok = {id(p) for _, p in pairs}

    # счётчики по метрикам
    per_metric: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for m in mset:
        gm = [r for r in gold.rows if r.metric == m]
        pm = [r for r in pred.rows if r.metric == m]
        tp = sum(1 for g in gm if id(g) in g_ok)
        d = _prf(len(gm), len(pm), tp)
        d["insufficient_data"] = len(gm) == 0
        if not gm:
            d["precision"] = d["recall"] = d["f1"] = None
            warnings.append(f"{m}: мало данных — в эталоне нет строк этой метрики, P/R/F1 не считаются")
        d["duplicates"] = {"gold": sum(1 for r in gm if r.dup_of), "pred": sum(1 for r in pm if r.dup_of),
                           "tp": sum(1 for g in gm if g.dup_of and id(g) in g_ok)}
        per_metric[m] = d
    totals = _prf(len(gold.rows), len(pred.rows), len(pairs))

    # точность атрибутов
    totals["source_page_accuracy"] = _rate(sum(1 for g, p in pairs if g.page == p.page), len(pairs))
    totals["result_origin_accuracy"] = _rate(sum(1 for g, p in pairs if g.origin == p.origin), len(pairs))
    totals["extraction_status_accuracy"] = _rate(sum(1 for g, p in pairs if g.status == p.status), len(pairs))
    agg_pairs = _greedy(gold.rows, pred.rows, lambda r: _key(r, with_agg=False), _value_score)
    totals["aggregation_accuracy"] = _rate(sum(1 for g, p in agg_pairs if g.agg == p.agg), len(agg_pairs))
    raw_pairs = _greedy([r for r in gold.rows if r.raw], [r for r in pred.rows if r.raw], _key,
                        lambda g, p: Decimal(0) if _raw_norm(g.raw) == _raw_norm(p.raw) else None)
    totals["normalization_accuracy"] = _rate(sum(1 for g, p in raw_pairs if _diff(g.value, p.value) is not None),
                                             len(raw_pairs))
    in_scope_ok = in_scope_n = 0
    for pid, gpub in gold.pubs.items():
        gv = parse_cell("publications", "in_scope", _s(gpub.get("in_scope")))
        if not isinstance(gv, bool):
            continue
        in_scope_n += 1
        ppub = pred.pubs.get(pid)
        pv = parse_cell("publications", "in_scope", _s(ppub.get("in_scope"))) if ppub else None
        in_scope_ok += int(pv is gv)
    totals["in_scope_accuracy"] = _rate(in_scope_ok, in_scope_n)

    # разбивки для экрана точности
    by_paper: dict[str, dict[str, Any]] = {}
    for r in gold.rows:
        e = by_paper.setdefault(r.pid, {"gold": 0, "pred": 0, "tp": 0})
        e["gold"] += 1
        e["tp"] += int(id(r) in g_ok)
    for r in pred.rows:
        by_paper.setdefault(r.pid, {"gold": 0, "pred": 0, "tp": 0})["pred"] += 1
    for pid, e in by_paper.items():
        e["short"] = _short(gold.pubs.get(pid) or pred.pubs.get(pid), pid)
    by_source: dict[str, dict[str, int]] = {}
    for r in gold.rows:
        e = by_source.setdefault(r.kind, {"gold": 0, "pred": 0, "tp": 0})
        e["gold"] += 1
        e["tp"] += int(id(r) in g_ok)
    for r in pred.rows:
        by_source.setdefault(r.kind, {"gold": 0, "pred": 0, "tp": 0})["pred"] += 1
    errors = _errors(gold.rows, pred.rows, g_ok, p_ok)
    totals["papers"] = len({r.pid for r in gold.rows} | set(gold.pubs))
    totals["by_paper"] = dict(sorted(by_paper.items()))
    totals["by_source"] = {k: by_source[k] for k in (*SOURCE_KINDS, *sorted(set(by_source) - set(SOURCE_KINDS)))
                           if k in by_source}
    totals["errors_total"] = len(errors)
    totals["errors"] = errors[:MAX_ERRORS]
    return EvalReport(per_metric=per_metric, totals=totals, warnings=warnings)


def _err(kind: str, g: _Row | None, p: _Row | None, note: str | None = None) -> dict[str, Any]:
    r = g or p
    assert r is not None
    return {"kind": kind, "paper_id": r.pid, "row_id": (g.row_label if g else p.row_label) or None,
            "param": r.metric, "result_id": p.rid if p else None, "gold_result_id": g.rid if g else None,
            "gold": _num_text(g.value, g.unit) if g else None, "got": _num_text(p.value, p.unit) if p else None,
            "gold_raw": g.raw if g else None, "got_raw": p.raw if p else None, "note": note}


def _errors(gold: list[_Row], pred: list[_Row], g_ok: set[int], p_ok: set[int]) -> list[dict[str, Any]]:
    """Несовпадения: value (то же место, другое значение), unit (тот же raw, другая нормализация), miss, extra."""
    pk: dict[tuple, list[_Row]] = defaultdict(list)
    for p in pred:
        if id(p) not in p_ok:
            pk[_key(p)].append(p)
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for g in gold:
        if id(g) in g_ok:
            continue
        cands = [p for p in pk.get(_key(g), []) if id(p) not in used]
        if not cands:
            out.append(_err("miss", g, None))
            continue
        p = next((c for c in cands if bool(c.dup_of) == bool(g.dup_of)), cands[0])
        used.add(id(p))
        if _diff(g.value, p.value) is not None:
            out.append(_err("miss", g, None, "повтор засчитывается, только если найдена основная запись"))
        elif g.raw and _raw_norm(g.raw) == _raw_norm(p.raw):
            out.append(_err("unit", g, p, f"raw «{p.raw} {p.unit}» нормализовано иначе"))
        else:
            out.append(_err("value", g, p))
    for p in pred:
        if id(p) not in p_ok and id(p) not in used:
            out.append(_err("extra", None, p))
    return out


def evaluate(gold_dir: Path, pred_dir: Path, *, metrics: Sequence[str] = EVAL_METRICS) -> EvalReport:
    """Оценка выгрузки `pred_dir` против эталона `gold_dir` (папки с 7 CSV). Нет папки или файла - пустая таблица."""
    return evaluate_tables(read_tables(Path(gold_dir)), read_tables(Path(pred_dir)), metrics=metrics)


# --- замечания ----------------------------------------------------------------------------------

def _issue_keys(rows: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in rows:
        ids = frozenset(x.strip() for x in _s(r.get("record_ids")).split(";") if x.strip())
        c[(_s(r.get("publication_id")), _s(r.get("check_name")), ids)] += 1
    return c


def evaluate_issues_tables(gold_rows: list[dict], pred_rows: list[dict]) -> IssueEvalReport:
    g, p = _issue_keys(gold_rows), _issue_keys(pred_rows)
    tp_c = g & p
    tp, n_g, n_p = sum(tp_c.values()), sum(g.values()), sum(p.values())
    by_check: dict[str, dict[str, Any]] = {}
    for (_, check, _), n in g.items():
        by_check.setdefault(check, {"gold": 0, "pred": 0, "tp": 0})["gold"] += n
    for (_, check, _), n in p.items():
        by_check.setdefault(check, {"gold": 0, "pred": 0, "tp": 0})["pred"] += n
    for (_, check, _), n in tp_c.items():
        by_check[check]["tp"] += n
    for e in by_check.values():
        e["fp"], e["fn"] = e["pred"] - e["tp"], e["gold"] - e["tp"]
        e["recall"], e["precision"] = _rate(e["tp"], e["gold"]), _rate(e["tp"], e["pred"])
    return IssueEvalReport(recall=_rate(tp, n_g), precision=_rate(tp, n_p), tp=tp, fp=n_p - tp, fn=n_g - tp,
                           by_check=dict(sorted(by_check.items())))


def evaluate_issues(gold_dir: Path, pred_dir: Path) -> IssueEvalReport:
    """recall/precision по ключу (publication_id, check_name, frozenset(record_ids))."""
    return evaluate_issues_tables(read_tables(Path(gold_dir))["issues"], read_tables(Path(pred_dir))["issues"])
