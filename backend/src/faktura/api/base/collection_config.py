"""Конфигурация коллекций для GET /collections (spec/04 §4.3, api_contract §4.3, У-6, У-21).

``solar`` - рабочая коллекция волны 1: параметры дополнены ``rs, rsh, hi, jsc_eqe, stabilized_pce, t80,
retained_pce, area`` (У-6). ``rag`` - только задел (``available: false``). Единицы - ASCII (BACKEND §8.5).
"""
from __future__ import annotations

import copy
from typing import Any

SOLAR_PARAMS: list[dict[str, Any]] = [
    {"key": "pce", "label": "КПД", "unit": "%", "units": {"%": 1, "fraction": 100}, "digits": 1, "text": False,
     "direction": "higher", "target": {"entity": "result", "metric_name": "PCE"}, "scale": {"min": 0, "max": 100}},
    {"key": "voc", "label": "Voc", "unit": "V", "units": {"V": 1, "mV": 0.001}, "digits": 2, "text": False,
     "direction": "higher", "target": {"entity": "result", "metric_name": "Voc"}},
    {"key": "jsc", "label": "Jsc", "unit": "mA/cm2", "units": {"mA/cm2": 1, "A/m2": 0.1}, "digits": 1, "text": False,
     "direction": "higher", "target": {"entity": "result", "metric_name": "Jsc"}},
    {"key": "ff", "label": "FF", "unit": "%", "units": {"%": 1, "fraction": 100}, "digits": 1, "text": False,
     "direction": "higher", "target": {"entity": "result", "metric_name": "FF"}, "scale": {"min": 0, "max": 100}},
    {"key": "rs", "label": "Rs", "unit": "Ohm*cm2", "units": {"Ohm*cm2": 1, "mOhm*cm2": 0.001}, "digits": 2,
     "text": False, "direction": "lower", "target": {"entity": "result", "metric_name": "Rs"}},
    {"key": "rsh", "label": "Rsh", "unit": "Ohm*cm2", "units": {"Ohm*cm2": 1, "kOhm*cm2": 1000}, "digits": 0,
     "text": False, "direction": "higher", "target": {"entity": "result", "metric_name": "Rsh"}},
    {"key": "hi", "label": "Индекс гистерезиса", "unit": "%", "units": {"%": 1, "fraction": 100}, "digits": 1,
     "text": False, "direction": "lower", "target": {"entity": "result", "metric_name": "HI"}},
    {"key": "jsc_eqe", "label": "Jsc по EQE", "unit": "mA/cm2", "units": {"mA/cm2": 1, "A/m2": 0.1}, "digits": 1,
     "text": False, "direction": "higher", "target": {"entity": "result", "metric_name": "Jsc_EQE"}},
    {"key": "stabilized_pce", "label": "Стабилизированный КПД", "unit": "%", "units": {"%": 1, "fraction": 100},
     "digits": 1, "text": False, "direction": "higher", "target": {"entity": "result", "metric_name": "stabilized_PCE"},
     "scale": {"min": 0, "max": 100}},
    {"key": "t80", "label": "T80", "unit": "h", "units": {"h": 1, "min": 0.0166667, "d": 24}, "digits": 0,
     "text": False, "direction": "higher", "target": {"entity": "result", "metric_name": "T80"}},
    {"key": "retained_pce", "label": "Сохранённый КПД", "unit": "%", "units": {"%": 1, "fraction": 100},
     "digits": 1, "text": False, "direction": "higher", "target": {"entity": "result", "metric_name": "retained_PCE"},
     "scale": {"min": 0, "max": 100}},
    {"key": "bandgap", "label": "Ширина зоны", "unit": "eV", "units": {"eV": 1}, "digits": 2, "text": False,
     "direction": "none", "target": {"entity": "material", "field": "bandgap_value"}},
    {"key": "thickness", "label": "Толщина поглотителя", "unit": "nm", "units": {"nm": 1, "um": 1000}, "digits": 0,
     "text": False, "direction": "none",
     "target": {"entity": "layer", "field": "layer_thickness", "layer_function": "absorber"}},
    {"key": "area", "label": "Активная площадь", "unit": "cm2", "units": {"cm2": 1, "mm2": 0.01}, "digits": 3,
     "text": False, "direction": "none", "target": {"entity": "device", "field": "active_area"}},
    {"key": "conditions", "label": "Условия измерения", "unit": "", "units": {}, "digits": 0, "text": True,
     "direction": "none", "target": {"entity": "measurement"}},
]

RAG_PARAMS: list[dict[str, Any]] = [
    {"key": "r5", "label": "Recall@5", "unit": "", "units": {}, "digits": 3, "text": False, "direction": "higher",
     "target": {"entity": "result", "metric_name": "recall@5"}, "scale": {"min": 0, "max": 1}},
    {"key": "r10", "label": "Recall@10", "unit": "", "units": {}, "digits": 3, "text": False, "direction": "higher",
     "target": {"entity": "result", "metric_name": "recall@10"}, "scale": {"min": 0, "max": 1}},
    {"key": "mrr", "label": "MRR", "unit": "", "units": {}, "digits": 3, "text": False, "direction": "higher",
     "target": {"entity": "result", "metric_name": "mrr"}, "scale": {"min": 0, "max": 1}},
    {"key": "chunk", "label": "Размер фрагмента", "unit": "tokens", "units": {"tokens": 1}, "digits": 0,
     "text": False, "direction": "none", "target": {"entity": "configuration", "field": "chunk_size"}},
    {"key": "retriever", "label": "Поисковик", "unit": "", "units": {}, "digits": 0, "text": True,
     "direction": "none", "target": {"entity": "configuration", "field": "retriever"}},
]

COLLECTIONS: list[dict[str, Any]] = [
    {
        "id": "solar", "name": "Солнечные элементы", "available": True,
        "hint": "Тонкоплёночные приборы: слои, КПД, условия измерения", "icon": "sun",
        "row_word": {"one": "образец", "gen": "образца", "cap": "Образец", "all": "все образцы",
                     "allCap": "Все образцы", "byRows": "По образцам"},
        "params": SOLAR_PARAMS,
        "table": ["voc", "jsc", "ff", "pce"], "best": "pce", "quality": ["pce", "voc", "jsc", "ff"],
        "plot": {"x": "voc", "y": "pce"},
        "topics": [{"id": "perovskite", "name": "перовскит"}, {"id": "cigs", "name": "CIGS"},
                   {"id": "cdte", "name": "CdTe"}, {"id": "tandem", "name": "тандем"},
                   {"id": "leadfree", "name": "бессвинцовые"}],
        "scope_note": "Тонкоплёночные однопереходные приборы: перовскиты, CIGS, CdTe, кестериты, Sb₂Se₃, "
                      "тонкоплёночный Si; без тандемов; только экспериментальные работы",
    },
    {
        "id": "rag", "name": "Поиск по документам (RAG)", "available": False,
        "hint": "Конфигурации RAG: поисковик, размер фрагмента, качество поиска — появится позже", "icon": "search",
        "row_word": {"one": "конфигурация", "gen": "конфигурации", "cap": "Конфигурация", "all": "все конфигурации",
                     "allCap": "Все конфигурации", "byRows": "По конфигурациям"},
        "params": RAG_PARAMS,
        "table": ["r5", "r10", "mrr"], "best": "mrr", "quality": ["r5", "r10", "mrr"],
        "plot": {"x": "r5", "y": "mrr"},
        "topics": [],
        "scope_note": "Задел модели данных: в волне 1 коллекция не обрабатывается",
    },
]

COLLECTION_IDS = tuple(c["id"] for c in COLLECTIONS)
WORKING_COLLECTIONS = ("solar",)


def collections_payload() -> dict[str, Any]:
    return {"data": copy.deepcopy(COLLECTIONS)}


def get_collection(collection_id: str) -> dict[str, Any] | None:
    for c in COLLECTIONS:
        if c["id"] == collection_id:
            return c
    return None


def get_param(collection_id: str, key: str) -> dict[str, Any] | None:
    c = get_collection(collection_id)
    if c is None:
        return None
    low = (key or "").lower()
    return next((p for p in c["params"] if p["key"] == low), None)


def param_label(key: str | None, collection_id: str = "solar", metric_name: str | None = None) -> str | None:
    if key:
        p = get_param(collection_id, key)
        if p is not None:
            return p["label"]
    return metric_name or key


TEXT_PARAMS = frozenset(p["key"] for c in COLLECTIONS for p in c["params"] if p["text"])
