"""Структурированный запрос (план) и его проверка. Схема - та же, что у ML (`POST /ask/plan`, строгий JSON-schema)."""
from __future__ import annotations

from typing import Any

from . import MAX_ROWS

INTENTS = ("results", "best", "issues", "table_rows", "schema", "paper", "rag_list", "count", "unknown")
RAG_LISTS = ("", "datasets", "configs", "tasks", "generators", "embeddings", "retrievers", "rerankers")
TABLES = ("publications", "materials", "devices", "layers", "measurements", "results", "issues",
          "configurations", "datasets", "tasks", "experiments")
AGGREGATIONS = ("", "champion", "mean", "median")
MATERIAL_CLASSES = ("perovskite", "CIGS", "CdTe", "kesterite", "Sb2Se3", "thin-film Si", "other")
DEFAULT_LIMIT = 5


def _str_arr() -> dict:
    return {"type": "array", "items": {"type": "string"}}


def plan_schema() -> dict:
    """JSON-schema плана (strict: все поля обязательны, пустая строка / пустой список / null = «не задано»)."""
    props: dict[str, Any] = {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "metrics": _str_arr(),
        "pids": _str_arr(),
        "material_class": {"type": "array", "items": {"type": "string", "enum": list(MATERIAL_CLASSES)}},
        "layers": _str_arr(),
        "device": {"type": "string"},
        "aggregation": {"type": "string", "enum": list(AGGREGATIONS)},
        "origin": {"type": "string", "enum": ["primary", "cited"]},
        "min_value": {"type": ["number", "null"]},
        "max_value": {"type": ["number", "null"]},
        "rag_config": {"type": "string"},
        "rag_dataset": {"type": "string"},
        "rag_task": {"type": "string"},
        "rag_list": {"type": "string", "enum": list(RAG_LISTS)},
        "table": {"type": "string", "enum": ["", *TABLES]},
        "field": {"type": "string"},
        "check": {"type": "string"},
        "sort": {"type": "string", "enum": ["best", "worst"]},
        "limit": {"type": "integer"},
        "per_paper": {"type": "boolean"},
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def empty_plan() -> dict:
    return {"intent": "unknown", "metrics": [], "pids": [], "material_class": [], "layers": [], "device": "",
            "aggregation": "", "origin": "primary", "min_value": None, "max_value": None, "rag_config": "",
            "rag_dataset": "", "rag_task": "", "rag_list": "", "table": "", "field": "", "check": "", "sort": "best",
            "limit": DEFAULT_LIMIT, "per_paper": True, "unknown_metric": ""}


def _strs(v: Any, n: int = 12) -> list[str]:
    return [str(x).strip() for x in v if str(x).strip()][:n] if isinstance(v, list) else []


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if v == v and abs(v) != float("inf") else None


def normalize_plan(raw: Any, desc: dict) -> dict:
    """Приводит ответ планировщика к безопасному виду: чужие коды и неизвестные метрики отбрасываются (первые
    уходят в `missing_pids`), числа и лимит ограничены. Ничего не бросает: непонятное -> intent=unknown."""
    p = empty_plan()
    if not isinstance(raw, dict):
        return p
    intent = raw.get("intent")
    p["intent"] = intent if intent in INTENTS else "unknown"
    known = {str(x).upper(): str(x) for x in desc.get("pids") or []}
    pids, missing = [], []
    for x in _strs(raw.get("pids"), 20):
        (pids if x.upper() in known else missing).append(known.get(x.upper(), x))
    p["pids"], p["missing_pids"] = list(dict.fromkeys(pids)), list(dict.fromkeys(missing))
    names = {str(m["name"]).lower(): str(m["name"]) for m in desc.get("metrics") or []}
    metrics = []
    p["unknown_metric"] = str(raw.get("unknown_metric") or "")[:60]  # не из схемы LLM: ставит правиловый планировщик
    for m in _strs(raw.get("metrics"), 6):
        hit = names.get(m.lower())
        if hit is None:  # «Recall» -> все «Recall@k», «F1» -> единственное «Answer F1» и т. п.: только однозначные совпадения
            cand = [v for k, v in names.items() if k.split("@")[0].strip() == m.lower().split("@")[0].strip()]
            hit = cand[0] if len(cand) == 1 else None
            if hit is None and cand:
                metrics.extend(c for c in cand if c not in metrics)
                continue
        if hit and hit not in metrics:
            metrics.append(hit)
        elif hit is None and not p["unknown_metric"]:
            p["unknown_metric"] = m[:60]
    p["metrics"] = metrics
    p["material_class"] = [c for c in _strs(raw.get("material_class")) if c in MATERIAL_CLASSES]
    p["layers"] = _strs(raw.get("layers"), 6)
    for k in ("device", "rag_config", "rag_dataset", "rag_task", "field", "check"):
        v = raw.get(k)
        p[k] = v.strip()[:80] if isinstance(v, str) else ""
    if p["field"].startswith("@") and p["field"] != "@required":
        p["field"] = ""
    if raw.get("aggregation") in AGGREGATIONS:
        p["aggregation"] = raw["aggregation"]
    if raw.get("origin") in ("primary", "cited"):
        p["origin"] = raw["origin"]
    p["min_value"], p["max_value"] = _num(raw.get("min_value")), _num(raw.get("max_value"))
    if raw.get("rag_list") in RAG_LISTS:
        p["rag_list"] = raw["rag_list"]
    if raw.get("table") in TABLES:
        p["table"] = raw["table"]
    p["sort"] = "worst" if raw.get("sort") == "worst" else "best"
    lim = raw.get("limit")
    p["limit"] = max(1, min(int(lim), MAX_ROWS)) if isinstance(lim, int) and not isinstance(lim, bool) else DEFAULT_LIMIT
    p["per_paper"] = raw.get("per_paper") is not False
    return p
