"""Каталог словаря нормализации для экрана «Словарь» (CORE_API §4h): параметры, единицы, материалы, условия.

Встроенное - из norm/data/*.json, collection_config (параметры, единицы) и словаря нормализатора; свои синонимы
рабочей области - из таблицы dictionary_synonyms. Цель своего синонима (`param` в таблице):
  * ключ параметра коллекции (``voc``) - метрика результата, если у параметра есть ``target.metric_name``;
  * ``material:<id>`` - материал (формула из materials.json);
  * ``condition:<id>`` - условие измерения (значение спектра из spectra.json).
"""
from __future__ import annotations

import re
from typing import Any

from .local import _load

MATERIAL_PREFIX = "material:"
CONDITION_PREFIX = "condition:"

# Читаемые написания спектров: в spectra.json лежат регулярные выражения, показывать их человеку нельзя.
_SPECTRUM_SAMPLES: dict[str, list[str]] = {
    "AM0": ["AM0", "AM 0"],
    "AM1.5D": ["AM1.5D", "AM 1.5D"],
    "AM1.5G": ["AM1.5G", "AM 1.5G", "AM1.5", "AM 1.5", "1.5 AM"],
}
_SPECTRUM_NAMES = {"AM0": "Внеатмосферный спектр AM0", "AM1.5D": "Прямое излучение AM1.5D",
                   "AM1.5G": "Стандартный спектр AM1.5G (глобальное излучение)"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _dedupe(items: list[str], skip: set[str] | None = None) -> list[str]:
    seen = set(skip or ())
    out: list[str] = []
    for t in items:
        k = (t or "").strip().casefold()
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out


# --- параметры ---------------------------------------------------------------------------------

def builtin_param_synonyms(normalizer: Any, collection: str, key: str) -> list[str]:
    """Встроенные написания параметра по порядку: подпись, metric_name, синонимы профиля и словаря нормализатора."""
    from ..api.base.collection_config import get_param  # noqa: PLC0415 - без цикла импортов

    p = get_param(collection, key)
    if p is None:
        return []
    names: list[str] = [p["label"]]
    metric = (p.get("target") or {}).get("metric_name")
    if metric:
        names.append(metric)
        try:
            from ..domain.profile import builtin_solar_profile  # noqa: PLC0415

            for f in builtin_solar_profile().fields:
                if f.key == metric:
                    names.extend(f.synonyms)
        except Exception:  # noqa: BLE001
            pass
        try:
            for m in getattr(normalizer.dictionary(), "metrics", []) or []:
                if m.get("metric_name") == metric:
                    names.extend(m.get("synonyms") or [])
        except Exception:  # noqa: BLE001 - у нормализатора может не быть словаря
            pass
    return _dedupe(names)


def param_taken(normalizer: Any, collection: str) -> set[str]:
    """Все встроенные написания параметров коллекции (в нижнем регистре), включая ключи."""
    from ..api.base.collection_config import get_collection  # noqa: PLC0415

    taken: set[str] = set()
    for p in (get_collection(collection) or {}).get("params", []):
        taken.add(p["key"].casefold())
        taken.update(s.casefold() for s in builtin_param_synonyms(normalizer, collection, p["key"]))
    return taken


def metric_of_param(collection: str, key: str) -> str | None:
    from ..api.base.collection_config import get_param  # noqa: PLC0415

    p = get_param(collection, key)
    t = (p or {}).get("target") or {}
    return t.get("metric_name") if t.get("entity") == "result" else None


# --- материалы и условия -----------------------------------------------------------------------

def material_entries() -> list[dict[str, Any]]:
    """Материалы solar: по одной записи на формулу из materials.json (псевдонимы + известные формулы)."""
    mat = _load("materials")
    by_formula: dict[str, list[str]] = {}
    for alias, formula in mat["aliases"].items():
        by_formula.setdefault(formula, []).append(alias)
    for formula in mat["known"]:
        by_formula.setdefault(formula, [])
    out = []
    for formula in sorted(by_formula, key=str.casefold):
        out.append({"id": _slug(formula), "canon": formula, "name": formula,
                    "synonyms": _dedupe(by_formula[formula], {formula.casefold()}), "target": formula})
    return out


def condition_entries() -> list[dict[str, Any]]:
    """Условия измерения solar: спектры освещения из spectra.json (написания - читаемые образцы)."""
    out = []
    for s in _load("spectra")["spectra"]:
        v = s["value"]
        samples = _SPECTRUM_SAMPLES.get(v, [])
        out.append({"id": _slug(v), "canon": v, "name": _SPECTRUM_NAMES.get(v, f"Спектр {v}"),
                    "synonyms": _dedupe(samples, {v.casefold()}), "target": v})
    return out


def find_entry(entry_id: str) -> tuple[str, dict[str, Any]] | None:
    """-> ('material'|'condition', запись) по id или None."""
    low = (entry_id or "").strip().casefold()
    for e in material_entries():
        if e["id"] == low:
            return "material", e
    for e in condition_entries():
        if e["id"] == low:
            return "condition", e
    return None


def entry_param(kind: str, entry: dict[str, Any]) -> str:
    return (MATERIAL_PREFIX if kind == "material" else CONDITION_PREFIX) + entry["id"]


def entry_taken() -> set[str]:
    """Все встроенные написания и канонические записи материалов и условий (нижний регистр)."""
    taken: set[str] = set()
    for e in [*material_entries(), *condition_entries()]:
        taken.add(e["canon"].casefold())
        taken.update(s.casefold() for s in e["synonyms"])
    return taken


# --- единицы -----------------------------------------------------------------------------------

def unit_rows(collection: str) -> list[dict[str, Any]]:
    """Строки таблицы единиц: как в статье -> каноническая -> множитель (только встроенные)."""
    from ..api.base.collection_config import get_collection  # noqa: PLC0415

    rows = []
    for p in (get_collection(collection) or {}).get("params", []):
        if p.get("text"):
            continue
        for u, k in p["units"].items():
            rows.append({"param": p["key"], "label": p["label"], "unit": u, "canon": p["unit"], "factor": k,
                         "is_canon": u == p["unit"] or k == 1, "custom": False})
    return rows


# --- GET /dictionary ---------------------------------------------------------------------------

def _syn(text: str, row: Any = None) -> dict[str, Any]:
    if row is None:
        return {"text": text, "custom": False}
    return {"id": row.id, "text": row.text, "custom": True}


def build_dictionary(normalizer: Any, collection: str, custom: list[Any], version: str) -> dict[str, Any]:
    """Словарь коллекции в форме dictionary.ts фронта. `custom` - строки dictionary_synonyms рабочей области."""
    from ..api.base.collection_config import get_collection  # noqa: PLC0415

    mine = [c for c in custom if c.collection == collection]
    params: dict[str, list[dict[str, Any]]] = {}
    for p in (get_collection(collection) or {}).get("params", []):
        key = p["key"]
        params[key] = [_syn(t) for t in builtin_param_synonyms(normalizer, collection, key)] + \
                      [_syn("", c) for c in mine if c.param == key]
    materials: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    if collection == "solar":
        for e in material_entries():
            own = [_syn("", c) for c in mine if c.param == entry_param("material", e)]
            materials.append({"id": e["id"], "collection": collection, "canon": e["canon"], "name": e["name"],
                              "synonyms": [_syn(t) for t in e["synonyms"]] + own})
        for e in condition_entries():
            own = [_syn("", c) for c in mine if c.param == entry_param("condition", e)]
            conditions.append({"id": e["id"], "canon": e["canon"], "name": e["name"],
                               "synonyms": [_syn(t) for t in e["synonyms"]] + own})
    return {"collection": collection, "version": version, "params": params, "units": unit_rows(collection),
            "materials": materials, "conditions": conditions}
