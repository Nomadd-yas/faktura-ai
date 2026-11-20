"""Ответ `GET /checks/accuracy` (форма spec/04 §4.13, подсчёт по spec/14 §14.7 - api_contract У-20, К-3).

`linked` - доля с верным якорем (пересечение bbox ≥ 50 %) - считается, только если у эталона есть bbox;
у dev_gold его нет, поэтому `linked = None`.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..domain.ports import EvalReport
from ..domain.profile import metric_to_param
from ..domain.tables import EVAL_METRICS
from .core import SOURCE_KINDS

__all__ = ["accuracy_response", "report_markdown", "PARAM_LABELS", "SOURCE_LABELS", "ERROR_KIND_LABELS"]

PARAM_LABELS = {
    "PCE": "КПД", "Voc": "Voc", "Jsc": "Jsc", "FF": "FF", "Rs": "Rs", "Rsh": "Rsh", "HI": "Индекс гистерезиса",
    "Jsc_EQE": "Jsc по EQE", "stabilized_PCE": "Стабилизированный КПД", "T80": "T80",
    "retained_PCE": "Сохранённый КПД",
}
SOURCE_LABELS = {"table": "Таблица", "text": "Текст", "figure": "Рисунок", "abstract": "Аннотация",
                 "supplementary": "Дополнительные материалы"}
ERROR_KIND_LABELS = {"miss": "Пропуск", "extra": "Лишний факт", "value": "Неверное значение",
                     "unit": "Неверная нормализация", "link": "Неверная привязка"}


def _r(v: Any) -> float | None:
    return None if v is None else round(float(v), 3)


def _scores(gold: int, pred: int, tp: int) -> dict[str, Any]:
    p = tp / pred if pred else None
    r = tp / gold if gold else None
    f1 = (2 * p * r / (p + r) if (p + r) else 0.0) if p is not None and r is not None else None
    return {"gold": gold, "extracted": pred, "correct": tp, "p": _r(p), "r": _r(r), "f1": _r(f1)}


def accuracy_response(report: EvalReport, *, collection: str, model_version: str | None,
                      gold_dataset: str | None = "dev_gold", paper_ids: Mapping[str, str] | None = None,
                      fact_ids: Mapping[tuple[str, str], str] | None = None,
                      paper_short: Mapping[str, str] | None = None) -> dict:
    """Форма spec/04 §4.13; linked=None без bbox.

    `paper_ids` - publication_id -> paper_id (API подставляет id статей корпуса), `fact_ids` -
    (publication_id, result_id) -> fact_id (для ссылки из ошибки на факт), `paper_short` - подписи статей."""
    t = report.totals or {}
    paper_ids = paper_ids or {}
    fact_ids = fact_ids or {}
    order = [m for m in EVAL_METRICS if m in report.per_metric] + \
            [m for m in report.per_metric if m not in EVAL_METRICS]
    by_param = []
    for m in order:
        pm = report.per_metric[m]
        row = {"param": metric_to_param(m), "metric_name": m, "label": PARAM_LABELS.get(m, m),
               **_scores(int(pm.get("gold", 0)), int(pm.get("pred", 0)), int(pm.get("tp", 0)))}
        if pm.get("insufficient_data"):
            row.update(p=None if not pm.get("pred") else row["p"], r=None, f1=None, insufficient_data=True)
        by_param.append(row)
    by_source = []
    src = t.get("by_source") or {}
    for kind in (*SOURCE_KINDS, *[k for k in src if k not in SOURCE_KINDS]):
        if kind in src:
            e = src[kind]
            by_source.append({"kind": kind, "label": SOURCE_LABELS.get(kind, kind),
                              **_scores(int(e.get("gold", 0)), int(e.get("pred", 0)), int(e.get("tp", 0)))})
    by_paper = []
    for pid, e in (t.get("by_paper") or {}).items():
        by_paper.append({"paper_id": paper_ids.get(pid, pid), "publication_id": pid,
                         "short": (paper_short or {}).get(pid) or e.get("short") or pid,
                         **_scores(int(e.get("gold", 0)), int(e.get("pred", 0)), int(e.get("tp", 0)))})
    errors = []
    for e in t.get("errors") or []:
        pid = e.get("paper_id")
        rid = e.get("result_id")
        errors.append({"kind": e.get("kind"), "kind_label": ERROR_KIND_LABELS.get(e.get("kind"), e.get("kind")),
                       "paper_id": paper_ids.get(pid, pid), "publication_id": pid, "row_id": e.get("row_id"),
                       "param": metric_to_param(e.get("param") or ""), "fact_id": fact_ids.get((pid, rid)) if rid
                       else None, "result_id": rid, "gold": e.get("gold"), "got": e.get("got"),
                       "note": e.get("note")})
    total = _scores(int(t.get("gold", 0) or 0), int(t.get("pred", 0) or 0), int(t.get("tp", 0) or 0))
    return {
        "collection": collection,
        "gold_dataset": gold_dataset,
        "model_version": model_version,
        "total": total,
        "normalized": _r(t.get("normalization_accuracy")),
        "linked": None,
        "source_page": _r(t.get("source_page_accuracy")),
        "aggregation": _r(t.get("aggregation_accuracy")),
        "result_origin": _r(t.get("result_origin_accuracy")),
        "extraction_status": _r(t.get("extraction_status_accuracy")),
        "in_scope": _r(t.get("in_scope_accuracy")),
        "by_param": by_param,
        "by_source": by_source,
        "by_paper": by_paper,
        "errors": errors,
        "errors_total": int(t.get("errors_total", len(errors)) or 0),
        "warnings": list(report.warnings),
    }


def _pct(v: Any) -> str:
    return "—" if v is None else f"{float(v) * 100:.1f} %".replace(".", ",")


def report_markdown(report: EvalReport, issues: Any | None = None) -> str:
    """Отчёт оценки в Markdown (для `faktura eval` и людей): P/R/F1 по метрикам, точность атрибутов, замечания."""
    t = report.totals or {}
    lines = ["# Оценка извлечения по эталону", "",
             f"Всего: эталон {t.get('gold', 0)}, извлечено {t.get('pred', 0)}, верно {t.get('tp', 0)}; "
             f"P {_pct(t.get('precision'))}, R {_pct(t.get('recall'))}, F1 {_pct(t.get('f1'))}.", "",
             "| Метрика | Эталон | Извлечено | Верно | P | R | F1 |", "|---|---|---|---|---|---|---|"]
    for m, pm in report.per_metric.items():
        lines.append(f"| {m} | {pm.get('gold', 0)} | {pm.get('pred', 0)} | {pm.get('tp', 0)} | "
                     f"{_pct(pm.get('precision'))} | {_pct(pm.get('recall'))} | {_pct(pm.get('f1'))} |")
    lines += ["", "| Точность | Значение |", "|---|---|"]
    for k, label in (("source_page_accuracy", "source_page"), ("normalization_accuracy", "нормализация"),
                     ("aggregation_accuracy", "aggregation"), ("result_origin_accuracy", "result_origin"),
                     ("extraction_status_accuracy", "extraction_status"), ("in_scope_accuracy", "in_scope")):
        lines.append(f"| {label} | {_pct(t.get(k))} |")
    if issues is not None:
        lines += ["", f"Замечания: recall {_pct(issues.recall)}, precision {_pct(issues.precision)} "
                      f"(совпало {issues.tp}, пропущено {issues.fn}, лишних {issues.fp})."]
    if report.warnings:
        lines += ["", "Предупреждения:"] + [f"- {w}" for w in report.warnings]
    return "\n".join(lines) + "\n"
