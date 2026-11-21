#!/usr/bin/env python3
# Извлечение датасета статьи (markdown) в ОДИН JSON — по одной таблице за вызов:
#   publications -> materials -> devices -> layers -> measurements -> results -> issues
#
# Почему так: заполнить всё одним ответом модели тяжело и дорого.
# Каждый вызов заполняет ТОЛЬКО свою таблицу (маленький ответ), а уже
# заполненные таблицы передаются вперёд как компактный контекст,
# чтобы results ссылались на реальные device_id / measurement_id / material_id.
# issues — последний, короткий проход.
#
# Если статья не по теме (in_scope=false в publications) — остальные проходы пропускаются.
#
# Использование:
#   1) как CLI:   ROUTERAI_API_KEY=sk-... python extract_dataset.py D001.md [--out D001.dataset.json]
#   2) как библиотека (так вызывает его pipeline.py — без подпроцесса):
#        from extract_dataset import extract
#        result = extract("D001.md", out="D001.dataset.json", meta_path="D001.meta.json")
#
# CLI-флаги:
#   --no-issues     не делать issues-проход
#   --issues-with-text  в issues-проход добавить текст статьи (дороже)
#   --no-strict     json_object вместо json_schema
#   --max-tokens    лимит на main-вызовы (по умолчанию 16384)

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from openai import OpenAI

BASE_URL = "https://routerai.ru/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"

MATERIAL_CLASSES = ["perovskite", "CIGS", "CdTe", "kesterite", "Sb2Se3", "thin-film Si", "other"]
DIMENSIONALITY = ["3D", "2D", "2D-3D"]
BANDGAP_METHODS = ["Tauc", "EQE", "PL", "cited"]
BANDGAP_UNITS = ["eV"]
DEVICE_ROLES = ["control", "target", "record"]
DEVICE_TYPES = ["cell", "minimodule", "module"]
ARCHITECTURES = ["n-i-p", "p-i-n", "substrate", "superstrate"]
SUBSTRATE_TYPES = ["rigid", "flexible"]
AREA_DEFINITIONS = ["designated illumination", "aperture", "total"]
AREA_UNITS = ["cm2"]
LAYER_FUNCTIONS = ["front_contact", "ETL", "absorber", "passivation", "HTL",
                   "interlayer", "recombination", "back_contact"]
MEASUREMENT_TYPES = ["J-V", "MPP tracking", "EQE", "stability"]
SCAN_DIRECTIONS = ["reverse", "forward", "average"]
STABILITY_PROTOCOLS = ["ISOS-D", "ISOS-L", "ISOS-T", "other"]
AGGREGATIONS = ["champion", "mean", "median"]
RESULT_ORIGINS = ["primary", "cited"]
EXTRACTION_STATUS = ["extracted", "present_not_extractable", "absent"]
# [faktura-patch] check_name из README (раздел issues) + виды замечаний из эталона dev_gold/issues.csv
CHECK_NAMES = ["jv_consistency", "voc_limit", "range", "eqe_consistency", "champion_vs_mean",
               "hysteresis_index", "contradiction", "unit_inconsistency", "possible_swap",
               "figure_mismatch", "figure_only", "cited_in_table", "dedup_trap", "rounding",
               "rounding_inconsistency", "plausibility", "missing_conditions",
               "measurement_conditions", "comparability", "ambiguity", "derived_values",
               "typo", "text_mix", "voc_deficit_consistency"]
KNOWN_METRICS = {"PCE", "Voc", "Jsc", "FF", "Rs", "Rsh", "HI", "Jsc_EQE",
                 "stabilized_PCE", "T80", "retained_PCE"}


def S(enum=None):
    base = {"type": "string"}
    if enum:
        base["enum"] = enum
    return {"anyOf": [base, {"type": "null"}]}


def N():
    return {"anyOf": [{"type": "number"}, {"type": "null"}]}


def I():
    return {"anyOf": [{"type": "integer"}, {"type": "null"}]}


def B():
    return {"type": "boolean"}


def BN():
    return {"anyOf": [{"type": "boolean"}, {"type": "null"}]}


def NS():
    # [faktura-patch] число ИЛИ текст (диапазон «63-68») — README допускает диапазон толщины
    return {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}]}


def STR():
    return {"type": "string"}


def obj(props):
    return {"type": "object",
            "properties": props,
            "required": list(props),          # strict: все ключи обязательны
            "additionalProperties": False}


# [faktura-patch] Колонки и порядок — 1:1 с submission_template/README.md (и backend TABLES).
PUB = obj({
    "publication_id": STR(),
    "title": S(),
    "year": {"anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]},
    "DOI": S(),
    "authors": S(),
    "in_scope": B(),
    "exclusion_reason": S(),
})

MATERIAL = obj({
    "publication_id": STR(),
    "material_id": STR(),
    "material_name_raw": S(),
    "formula_normalized": S(),
    "material_class": S(MATERIAL_CLASSES),
    "dimensionality": S(DIMENSIONALITY),
    "additives": S(),                     # через «; »
    "bandgap_value": N(),
    "bandgap_unit": S(BANDGAP_UNITS),
    "bandgap_method": S(BANDGAP_METHODS),
    "note": S(),
})

DEVICE = obj({
    "publication_id": STR(),
    "device_id": STR(),
    "absorber_material_id": S(),
    "device_label": S(),
    "device_role": S(DEVICE_ROLES),
    "device_type": S(DEVICE_TYPES),
    "architecture": S(ARCHITECTURES),
    "substrate_type": S(SUBSTRATE_TYPES),
    "active_area": N(),
    "area_unit": S(AREA_UNITS),
    "area_definition": S(AREA_DEFINITIONS),
    "encapsulation": S(),
    "note": S(),
})

LAYER = obj({
    "publication_id": STR(),
    "layer_id": STR(),
    "device_id": STR(),
    "layer_order": I(),
    "layer_function": S(LAYER_FUNCTIONS),
    "layer_material": S(),
    "layer_thickness": NS(),              # число или диапазон-текст «63-68»
    "thickness_unit": S(["nm"]),
    "deposition_method": S(),
    "annealing_temperature": S(),         # текст с единицей: «500 °C»
    "annealing_time": S(),                # текст с единицей: «10 min»
    "atmosphere": S(),
    "note": S(),
})

MEASUREMENT = obj({
    "publication_id": STR(),
    "measurement_id": STR(),
    "device_id": STR(),                   # один device_id или несколько через «;» (одни условия)
    "measurement_type": S(MEASUREMENT_TYPES),
    "spectrum": S(),
    "light_intensity": N(),
    "intensity_unit": S(),
    "scan_direction": S(SCAN_DIRECTIONS),
    "scan_rate": S(),
    "mask_used": BN(),
    "certified": BN(),
    "certification_lab": S(),
    "temperature": S(),
    "stability_protocol": S(STABILITY_PROTOCOLS),
    "stress_conditions": S(),
    "note": S(),
})

RESULT = obj({
    "publication_id": STR(),
    "result_id": STR(),
    "device_id": S(),                     # null для cited
    "measurement_id": S(),                # null для cited
    "material_id": S(),
    "metric_name": STR(),
    "metric_value": N(),                  # == normalized_value
    "metric_unit": S(),                   # == normalized_unit
    "aggregation": S(AGGREGATIONS),
    "spread_value": N(),
    "n_devices": I(),
    "time_point": N(),
    "time_unit": S(),
    "raw_value": {"anyOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}]},
    "raw_unit": S(),
    "normalized_value": N(),
    "normalized_unit": S(),
    "result_origin": S(RESULT_ORIGINS),
    "cited_publication_id": S(),
    "source_page": I(),
    "source_section": S(),
    "source_table": S(),
    "source_figure": S(),
    "source_fragment": S(),
    "extraction_status": S(EXTRACTION_STATUS),
    "duplicate_of": S(),                  # result_id основной записи
    "note": S(),
})

ISSUE = obj({
    "publication_id": STR(),
    "issue_id": STR(),
    "record_ids": S(),
    "check_name": S(),  # [faktura-patch] открытый список (README: «например»); CHECK_NAMES — подсказки в промпте
    "description": S(),
    "source_page": I(),
    "source_fragment": S(),
})

TABLES = {
    "publications": PUB,
    "materials": MATERIAL,
    "devices": DEVICE,
    "layers": LAYER,
    "measurements": MEASUREMENT,
    "results": RESULT,
    "issues": ISSUE,
}

def table_schema(name: str) -> dict:
    return {"type": "object",
            "properties": {name: {"type": "array", "items": TABLES[name]}},
            "required": [name],
            "additionalProperties": False}


COMMON_RULES = """Ты — экстрактор данных из научных статей (тонкоплёночные фотоэлементы).
Извлекай ТОЛЬКО то, что прямо написано в тексте статьи. Ничего не додумывай.
Ключевые правила:
- Верхний ключ JSON — ТОЛЬКО указанный в задании. Значение — массив объектов.
- publication_id везде равен заданному в задании.
- Пустая ячейка = null (не "", не "N/A", не "-").
- СТРОГО: ячейка заполняется, только если значение ЯВНО указано в тексте именно для этой
  записи; иначе null. Не выводи значения из «типичной практики», не подставляй значения по
  умолчанию, не копируй значение с одного прибора на другой, если в статье это не сказано.
  Лучше пустая ячейка, чем догадка.
- ID (material_id, device_id, layer_id, measurement_id, result_id, issue_id) —
  короткие (M1, DV1, L1, MS1, R001, I1...), уникальные внутри публикации.
  Если передаётся «Уже извлечено» — используй те же id, не переименовывай.
- Числа — JSON-числа, без единиц и множителей (исключение — поля, где явно сказано «текст с единицей»).
- Страницы: в тексте статьи перед содержимым каждой PDF-страницы стоит маркер
  <!-- page N -->. source_page = N ближайшего ПРЕДШЕСТВУЮЩЕГО маркера (страница PDF, с 1;
  не номер страницы журнала). source_fragment — короткая (до ~120 симв.) ДОСЛОВНАЯ
  цитата из текста этой страницы: символ в символ, без перефразирования, без
  переформатирования LaTeX/единиц; не склеивай разные места. Для значения из таблицы
  достаточно source_table и метки строки (в source_fragment можно ту же метку).
- Выводи ТОЛЬКО JSON-объект, без пояснений и markdown-ограждений.
"""

STEP_PROMPTS = {
    "publications": """Заполни таблицу publications РОВНО ОДНОЙ записью:
- title, year, DOI, authors (через «; »);
- in_scope (true/false) — относится ли статья к предметной области «тонкоплёночные
  солнечные элементы». in_scope=true ТОЛЬКО если одновременно:
  (а) поглотитель — тонкоплёночный: perovskite, CIGS, CdTe, kesterite (CZTS), Sb2Se3, thin-film Si (a-Si:H и т.п.);
  (б) в работе ИЗГОТОВЛЕНЫ приборы (ячейки/мини-модули/модули) и ИЗМЕРЕНЫ их характеристики
      (ВАХ, EQE, стабильность) — собственные экспериментальные результаты авторов.
  in_scope=false, если: чисто расчётная/симуляционная/модельная работа (DFT, ab initio, молекулярная
  динамика, SCAPS/численное моделирование, оптические расчёты) без изготовленных приборов
  и измеренных характеристик; обзор без собственного эксперимента; классический объёмный
  кремний; органические/DSSC/квантово-точечные и прочие не тонкоплёночные технологии;
  работа не про фотоэлементы. Мерило — не наличие слова «solar cell» в названии,
  а наличие экспериментального прибора с измерениями.
- exclusion_reason — только при in_scope=false (иначе null). Одно–два предложения по-русски
  в таком стиле: «Только численное моделирование (одномерная диффузионная модель в MATLAB и Maple);
  изготовленных образцов и измеренных характеристик нет. По разделу 3.1 расчётные работы без
  изготовленных образцов к предметной области не относятся.» или «Только расчёт: ab initio
  неадиабатическая молекулярная динамика и TDDFT для α-CsPbI3; изготовленных приборов и измеренных
  характеристик нет.» Назови, что именно сделано в работе, и почему это не подходит.""",

    "materials": """Заполни materials — материалы-поглотители из статьи (по одному на каждый
различающийся состав поглотителя: контроль, легированный и т.д.):
- material_name_raw — как материал назван в статье («0.001 M SrCl2–doped CsPbI2Br»);
- formula_normalized — для перовскита вид A-B-X с раскрытыми MA/FA/Cs (например FA0.95Cs0.05PbI3),
  для прочих — формула как в статье, если дана;
- material_class ∈ perovskite, CIGS, CdTe, kesterite, Sb2Se3, thin-film Si, other;
- dimensionality ∈ 3D, 2D, 2D-3D — ТОЛЬКО если размерность названа/очевидна из текста
  (2D/3D гетероструктура, 2D-перовскит); иначе null;
- additives — добавки/допанты/модификаторы поглотителя с концентрацией, через «; » (иначе null);
- bandgap_value (число) + bandgap_unit (eV) + bandgap_method: только если ширина зоны прямо
  указана. Tauc — из Tauc-plot/UV-Vis, EQE — из края EQE, PL — из ФЛ, cited — значение
  взято из литературы. Если метод не ясен — bandgap_method null;
- note — исходная формулировка, если она не укладывается в словарь.""",

    "devices": """Заполни devices — все приборы, отличные друг от друга
(состав абсорбера, архитектура, подложка, площадь, режим). Каждый прибор — своя строка:
- device_label — метка прибора как в статье («pristine», «B4», «0.005 M SrCl2»);
- device_role: control (контрольный/референс), target (целевой/модифицированный), record
  (рекордный) — только если роль ясна из текста;
- device_type: cell/minimodule/module; architecture: n-i-p/p-i-n/substrate/superstrate —
  только если указано или прямо следует из описанной структуры (порядок слоёв);
  нестандартную формулировку — в note, в поле ближайшее допустимое значение или null;
- substrate_type: rigid/flexible — только если назван (стекло — rigid, фольга/PET — flexible);
- active_area (число) + area_unit (cm2) — площадь, если указана; area_definition:
  designated illumination (площадь освещения, заданная маской/апертурой для измерения ВАХ),
  aperture (апертурная площадь), total (полная площадь) — только если
  статья говорит, какая это площадь;
- encapsulation — способ инкапсуляции, если описан;
- absorber_material_id — material_id из «Уже извлечено».""",

    "layers": """Заполни layers — слои каждого прибора ОТ ПОДЛОЖКИ (1 — контакт на подложке;
подложку (стекло/фольгу) НЕ записывать). Слои только из текста о структуре/изготовлении:
- device_id — из «Уже извлечено» (у каждого прибора свой набор слоёв; если слои общие
  для приборов и в статье описаны один раз — запиши набор слоёв для каждого такого прибора);
- layer_order: 1, 2, ...
- layer_function ∈ front_contact, ETL, absorber, passivation, HTL, interlayer,
  recombination, back_contact. Неизвестное приводи к ближайшему: CdS и аналогичные
  n-слои -> ETL; металлическая сетка -> front_contact; просветляющее и прочие
  промежуточные -> interlayer. Исходное название — в note;
- layer_material — материал слоя как в статье («compact TiO2», «Spiro-OMeTAD», «Au»);
- layer_thickness — только если указана, В НМ (мкм × 1000); число, либо текст-диапазон «63-68»;
  thickness_unit = nm, если толщина указана;
- deposition_method — метод нанесения («spin-coating», «thermal evaporation», «sputtering»), если описан;
- annealing_temperature — текст с единицей в °C («500 °C»); annealing_time — текст с единицей
  («10 min»); только если отжиг/обработка слоя описаны;
- atmosphere — атмосфера процесса («N2 glovebox», «air»), если указана.""",

    "measurements": """Заполни measurements — условия измерений:
- Отдельная запись на КАЖДОЕ направление развёртки (reverse/forward/average)
  и на КАЖДОЕ условие стресса для измерений стабильности. Одинаковые условия для
  нескольких приборов — ОДНА запись, device_id перечисляет приборы через «;»;
- measurement_type ∈ J-V, MPP tracking, EQE, stability (тёмная ВАХ — J-V, пометка в note);
- scan_direction — только если направление названо; scan_rate — скорость развёртки текстом
  с единицей («0.15 V/s»), если указана;
- spectrum (например AM1.5G) и light_intensity + intensity_unit (100 и mW/cm2), только если указаны;
- mask_used (true/false) — только если в статье сказано про маску/апертуру; certified
  — true, если сказано о независимой сертификации, иначе false (как в эталоне); certification_lab — лаборатория, если названа;
- temperature — температура измерения текстом с единицей, если указана;
- stability_protocol ∈ ISOS-D, ISOS-L, ISOS-T, other — ТОЛЬКО для измерений стабильности
  (measurement_type=stability): по README, если протокол ISOS в статье не назван, ставится other,
  а условия описываются в stress_conditions; для J-V/MPP/EQE — null;
- device_id — из «Уже извлечено».""",

    "results": """Заполни results — одно значение одного показателя для одного прибора
при одних условиях (включая направление развёртки) = одна строка:
- Champion и средние по партии — ОТДЕЛЬНЫЕ строки; aggregation ∈ champion/mean/median
  (если в статье не сказано, лучший это прибор или среднее — null).
- result_origin: primary (собственные результаты авторов) / cited (из другой работы);
  для cited — device_id и measurement_id = null, cited_publication_id как в статье («[12]»).
- Повтор того же значения из другого места статьи (аннотация vs текст vs таблица) —
  ОТДЕЛЬНАЯ строка со своим source_*; в duplicate_of — result_id основной записи.
- extraction_status ∈ extracted, present_not_extractable (например, только на графике
  или только в supplementary), absent.
- raw_value/raw_unit — значение и единица ДОСЛОВНО как в статье.
- Единицы: PCE, FF, HI, retained_PCE → %; Voc → V; Jsc, Jsc_EQE → mA/cm2;
  Rs, Rsh → Ohm*cm2; T80 → h. metric_value = normalized_value; metric_unit = normalized_unit.
  Внимание на конвертации: мВ→В (÷1000), доля→% (×100), мкА/см2→мА/см2 (÷1000).
- spread_value (± или стандартное отклонение) и n_devices — только если указаны.
- time_point + time_unit — момент измерения для стабильности.
- metric_name: PCE, Voc, Jsc, FF, Rs, Rsh, HI, Jsc_EQE, stabilized_PCE, T80, retained_PCE
  (или понятное название, если показателя нет в списке: Voc_deficit, Jmpp, Vmpp, AVT...).
- source_page — страница PDF по маркеру <!-- page N -->; source_section/source_table/source_figure —
  где найдено (например 3.4 / Table 4 / Figure 10); source_fragment — короткая дословная цитата.
- note — только если формулировка в статье нестандартна.""",

    "issues": """Проверь ГОТОВЫЙ датасет (JSON) — найди ТОЛЬКО реальные проблемы, видимые в данных/тексте.
check_name — ровно одно из:
- jv_consistency — PCE не согласуется с Voc×Jsc×FF/Pin (расхождение >10 %);
- voc_limit — Voc выше Eg/q; range — значение вне физических пределов;
- eqe_consistency — Jsc по ВАХ и по EQE расходятся (>5 %);
- champion_vs_mean — среднее больше лучшего значения;
- hysteresis_index — аномальный индекс гистерезиса;
- contradiction — одно значение в разных местах статьи расходится (Table 4: 3.85 % против текста: 5.25 %);
- unit_inconsistency — единица не согласуется со значением (FF «0.71%» при доле);
- possible_swap — значения, вероятно, перепутаны местами;
- figure_mismatch — рисунок не совпадает с таблицей; figure_only — результаты только на графиках;
- cited_in_table — чужие результаты стоят в таблице собственных;
- dedup_trap — два разных прибора с одинаковым значением, объединять нельзя;
- rounding / rounding_inconsistency — расхождение из-за округления;
- plausibility — значение правдоподобно с трудом (FF 87 % при Voc 0.98 В);
- missing_conditions — не указаны условия измерения (площадь, спектр, развёртка);
- measurement_conditions — нестандартные условия измерения (не AM1.5G, предобработка светом);
- comparability — результат несопоставим с остальными (модуль vs ячейка, нестандартное освещение);
- ambiguity — неоднозначность в тексте; derived_values — «производные» числа в тексте (разности, относительный прирост);
- typo — опечатка в статье; text_mix — фраза объединяет максимумы разных приборов;
- voc_deficit_consistency — дефицит Voc не равен Eg/q − Voc.
Каждая запись — ОДНА строка в description: числа с обеих сторон; record_ids — result_id через ;
(можно null, если замечание не про конкретные результаты); source_page — страница по маркеру
<!-- page N --> (если есть текст статьи) и дословный source_fragment.
Если проблем нет — {"issues": []}. Максимум 20; только реальные проблемы, не выдумывай.""",
}

STEP_ORDER = ["publications", "materials", "devices", "layers", "measurements", "results"]


def pub_id_from_filename(path: Path) -> str:
    m = re.search(r"[A-Za-zА-Яа-я]+[-_]?\d+", path.stem)
    return m.group(0) if m else path.stem


def parse_json_loose(raw: str) -> dict:
    raw = raw.strip()
    candidates = [raw]
    for pattern in (r"```(?:json)?\s*(\{.*\})\s*```", r"\{.*\}"):
        m = re.search(pattern, raw, re.DOTALL)
        if m:
            candidates.append(m.group(1) if m.lastindex else m.group(0))
    for cand in candidates:
        for strict in (True, False):
            try:
                return json.loads(cand, strict=strict)
            except json.JSONDecodeError:
                continue
    raise ValueError("ответ модели не разбирается как JSON")


def nullify_empties(node):
    if isinstance(node, dict):
        return {k: nullify_empties(v) for k, v in node.items()}
    if isinstance(node, list):
        return [nullify_empties(v) for v in node]
    if isinstance(node, str) and not node.strip():
        return None
    return node


def check_dataset(d: dict) -> list[str]:
    problems = []
    tabs = {}
    for name in TABLES:
        rows = d.get(name)
        if not isinstance(rows, list):
            problems.append(f"ключ {name}: ожидается массив")
            rows = []
        tabs[name] = [r for r in rows if isinstance(r, dict)]

    pub_id = tabs["publications"][0].get("publication_id") if tabs["publications"] else None
    if pub_id:
        for t, rows in tabs.items():
            for i, r in enumerate(rows):
                if r.get("publication_id") not in (None, pub_id):
                    problems.append(f"{t}[{i}]: publication_id={r.get('publication_id')!r} != {pub_id!r}")

    enum_map = {
        "materials": [("material_class", MATERIAL_CLASSES), ("dimensionality", DIMENSIONALITY),
                      ("bandgap_method", BANDGAP_METHODS), ("bandgap_unit", BANDGAP_UNITS)],
        "devices": [("device_role", DEVICE_ROLES), ("device_type", DEVICE_TYPES),
                    ("architecture", ARCHITECTURES), ("substrate_type", SUBSTRATE_TYPES),
                    ("area_definition", AREA_DEFINITIONS), ("area_unit", AREA_UNITS)],
        "layers": [("layer_function", LAYER_FUNCTIONS), ("thickness_unit", ["nm"])],
        "measurements": [("measurement_type", MEASUREMENT_TYPES), ("scan_direction", SCAN_DIRECTIONS),
                         ("stability_protocol", STABILITY_PROTOCOLS)],
        "results": [("aggregation", AGGREGATIONS), ("result_origin", RESULT_ORIGINS),
                    ("extraction_status", EXTRACTION_STATUS)],
    }
    for tab, cols in enum_map.items():
        for r in tabs[tab]:
            for col, allowed in cols:
                v = r.get(col)
                if v is not None and v not in allowed:
                    problems.append(f"{tab}: {col}={v!r} вне enum")

    key_map = {"materials": "material_id", "devices": "device_id", "layers": "layer_id",
               "measurements": "measurement_id", "results": "result_id", "issues": "issue_id"}
    ids = {}
    for t, key in key_map.items():
        seen = set()
        for r in tabs[t]:
            v = r.get(key)
            if v is not None:
                if v in seen:
                    problems.append(f"{t}: дубликат {key}={v!r}")
                seen.add(v)
        ids[t] = seen

    for r in tabs["devices"]:
        ref = r.get("absorber_material_id")
        if ref is not None and ref not in ids.get("materials", set()):
            problems.append(f"devices: absorber_material_id={ref!r} нет в materials")
    # [faktura-patch] у measurements device_id может быть списком через «;»
    for t in ("layers", "measurements"):
        for r in tabs[t]:
            dv = r.get("device_id")
            parts = [x.strip() for x in str(dv).split(";") if x.strip()] if dv is not None else []
            if not parts or any(x not in ids.get("devices", set()) for x in parts):
                problems.append(f"{t}: device_id={dv!r} нет в devices")
    for r in tabs["results"]:
        rid = r.get("result_id")
        if r.get("result_origin") == "cited":
            if r.get("device_id") is not None or r.get("measurement_id") is not None:
                problems.append(f"results {rid}: cited, но device_id/measurement_id не null")
        else:
            if r.get("device_id") is not None and r["device_id"] not in ids.get("devices", set()):
                problems.append(f"results {rid}: device_id={r['device_id']!r} нет в devices")
            if r.get("measurement_id") is not None and r["measurement_id"] not in ids.get("measurements", set()):
                problems.append(f"results {rid}: measurement_id={r['measurement_id']!r} нет в measurements")
        if r.get("material_id") is not None and r["material_id"] not in ids.get("materials", set()):
            problems.append(f"results {rid}: material_id={r['material_id']!r} нет в materials")
        if r.get("duplicate_of") is not None and r["duplicate_of"] not in ids.get("results", set()):
            problems.append(f"results {rid}: duplicate_of={r['duplicate_of']!r} не существует")
    for r in tabs["results"]:
        sp = r.get("source_page")
        if sp is not None and (not isinstance(sp, int) or isinstance(sp, bool) or sp < 1):
            problems.append(f"results {r.get('result_id')}: source_page={sp!r} должен быть целым >= 1")
    for r in tabs["issues"]:
        for rid in (r.get("record_ids") or "").split(";"):
            rid = rid.strip()
            if rid and rid not in ids.get("results", set()):
                problems.append(f"issues {r.get('issue_id')}: record_id {rid!r} нет в results")

    if tabs["publications"] and tabs["publications"][0].get("in_scope") is False:
        for t in TABLES:
            if t not in ("publications", "issues") and tabs[t]:
                problems.append(f"in_scope=false, но в {t} есть строки")

    by_grp = {}
    for r in tabs["results"]:
        key = (r.get("device_id"), r.get("measurement_id"), r.get("aggregation"))
        by_grp.setdefault(key, {})[r.get("metric_name")] = r.get("normalized_value")
    for (dev, meas, agg), m in by_grp.items():
        pce, voc, jsc, ff = m.get("PCE"), m.get("Voc"), m.get("Jsc"), m.get("FF")
        if None not in (pce, voc, jsc, ff) and pce:
            est = voc * jsc * ff / 100.0
            if abs(est - pce) / pce > 0.15:
                problems.append(f"PCE-несоответствие ({dev}/{meas}/{agg}): "
                                f"PCE={pce}, а Voc×Jsc×FF/100={est:.2f}")
    return problems


class LLMError(Exception):
    pass


REASONING_TOKENS = int(os.environ.get("ROUTERAI_REASONING_TOKENS", "6000"))
SEED = int(os.environ.get("ROUTERAI_SEED", "42"))


def call_llm(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None):
    """[faktura-patch] Вызов с повтором: модель рассуждает и иногда тратит весь лимит на рассуждение
    (finish_reason=length, пустой ответ). Рассуждение ограничено (reasoning.max_tokens), при пустом/обрезанном
    ответе — один повтор с меньшим бюджетом рассуждения и большим общим лимитом."""
    try:
        return _call_llm_once(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report,
                              reasoning_tokens=min(REASONING_TOKENS, max_tokens // 2))
    except LLMError as e:
        print(f"[{label}] повтор после сбоя: {e}", flush=True)
        return _call_llm_once(client, args, messages, schema_name, schema, max_tokens * 2, label + ":retry",
                              raw_path, report, reasoning_tokens=max(1024, REASONING_TOKENS // 3))


def _call_llm_once(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
                   reasoning_tokens=None):
    """Один вызов с fallback json_schema -> json_object и диагностикой."""
    providers = [p.strip().lower() for p in args.provider.split(",") if p.strip()]  # [faktura-patch] slug строчными
    # [faktura-patch] список = приоритет: первый живой из списка, при сбое — следующий, вне списка не уходим
    extra_body = {"provider": {"order": providers, "allow_fallbacks": False}} if providers else {}
    if reasoning_tokens:
        extra_body["reasoning"] = {"max_tokens": int(reasoning_tokens)}
    extra_body["seed"] = SEED  # воспроизводимость замеров (если провайдер поддерживает)
    t0 = time.time()

    strict = not args.no_strict
    resp = None
    if strict:
        try:
            resp = client.chat.completions.create(
                model=args.model, messages=messages, temperature=0.0,
                max_tokens=max_tokens,
                response_format={"type": "json_schema",
                                 "json_schema": {"name": schema_name, "strict": True,
                                                 "schema": schema}},
                extra_body=extra_body)
        except Exception as e:
            msg = str(e).lower()
            if "response_format" in msg or "structured" in msg or "schema" in msg or getattr(e, "status_code", None) == 400:
                print(f"[{label}] json_schema не принят ({e}); повтор с json_object")
                strict = False
            else:
                raise

    if resp is None:
        messages = messages + [{
            "role": "user",
            "content": "Return ONLY the JSON object conforming to this JSON Schema, no extra text:\n"
                       + json.dumps(schema, ensure_ascii=False),
        }]
        resp = client.chat.completions.create(
            model=args.model, messages=messages, temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body=extra_body)

    dt = round(time.time() - t0, 2)
    choice = resp.choices[0]
    content = choice.message.content or ""
    finish = getattr(choice, "finish_reason", None)
    usage = getattr(resp, "usage", None)
    usage_d = usage.model_dump() if hasattr(usage, "model_dump") else (dict(usage) if usage else {})
    print(f"[{label}] finish_reason={finish}; ответ {len(content)} симв.; strict={strict}; {dt}s; tokens={usage_d}")
    if report is not None:
        report.append({"step": label, "finish_reason": finish, "strict": strict,
                       "seconds": dt, "usage": usage_d})
    try:
        if not content.strip():
            raise ValueError("пустой ответ")
        return parse_json_loose(content)
    except ValueError:
        raw_path.write_text(content, encoding="utf-8")
        hint = " Ответ обрезался по лимиту — поднимите --max-tokens." if finish == "length" else ""
        raise LLMError(f"[{label}] ответ не разобран как JSON. finish_reason={finish}. "
                       f"Сырой ответ: {raw_path}.{hint}")


def extract(md_path, *, publication_id=None, out=None, model=DEFAULT_MODEL,
            provider="wafer", no_strict=False, max_chars=200000,
            max_tokens=24576, issues_max_tokens=12288, no_issues=False,  # [faktura-patch] модель рассуждает: 2048 не хватало
            issues_with_text=False, meta_path=None, api_key=None) -> dict:
    """Извлекает датасет из markdown-статьи.

    Возвращает dict: dataset, counts, failed_tables, problems, out_path,
    meta_path, publication_id. Ненулевой код возврата не формирует — ошибки
    приходят исключениями (LLMError / FileNotFoundError).
    """
    key = api_key or os.environ.get("ROUTERAI_API_KEY")
    if not key:
        raise LLMError("Нет ключа RouterAI: задайте ROUTERAI_API_KEY или передайте api_key=...")

    src = Path(md_path)
    if not src.exists():
        raise FileNotFoundError(f"Файл не найден: {src}")
    text = src.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        print(f"[i] вход обрезан: {len(text)} -> {max_chars} символов", flush=True)
        text = text[:max_chars]

    pub_id = publication_id or pub_id_from_filename(src)
    client = OpenAI(api_key=key, base_url=BASE_URL)
    args = argparse.Namespace(model=model, provider=provider, no_strict=no_strict)
    report = []

    # общий префикс: системная часть + текст статьи (фиксированный, дружит с prompt-cache)
    state: dict = {t: [] for t in TABLES}
    failed: list[str] = []

    def step_user(table: str) -> str:
        return (f"publication_id = {pub_id}\n\n"
                f"Текст статьи (Markdown):\n{text}\n\n"
                f"Уже извлечено (JSON):\n{json.dumps(state, ensure_ascii=False, separators=(',', ':'))}\n\n"
                f"Задание ({table}):\n{STEP_PROMPTS[table]}")

    for step in STEP_ORDER:
        if step != "publications" and state["publications"] and state["publications"][0].get("in_scope") is False:
            print(f"[i] статья вне области (in_scope=false) — пропускаю {step} и дальше", flush=True)
            break

        messages = [{"role": "system", "content": COMMON_RULES},
                    {"role": "user", "content": step_user(step)}]
        raw_path = src.with_name(f"{src.name}.{step}.raw_response.txt")
        try:
            result = nullify_empties(call_llm(client, args, messages, f"table_{step}",
                                              table_schema(step), max_tokens,
                                              step, raw_path, report))
            rows = result.get(step, []) if isinstance(result, dict) else []
            for r in rows:
                r.setdefault("publication_id", pub_id)
            state[step] = rows
        except LLMError as e:
            print(f"[!] {e}\n    Таблица {step} будет пустой; продолжаю.", flush=True)
            failed.append(step)

    if not no_issues and (not state["publications"] or state["publications"][0].get("in_scope") is not False):
        issues_user = (f"publication_id = {pub_id}\n\n"
                       f"Датасет (JSON):\n{json.dumps(state, ensure_ascii=False, separators=(',', ':'))}")
        if issues_with_text:
            issues_user += f"\n\nТекст статьи (Markdown):\n{text}"
        messages = [{"role": "system", "content": COMMON_RULES + "\n" + STEP_PROMPTS["issues"]},
                    {"role": "user", "content": issues_user}]
        raw_path = src.with_name(f"{src.name}.issues.raw_response.txt")
        try:
            result = nullify_empties(call_llm(client, args, messages, "table_issues",
                                              table_schema("issues"), issues_max_tokens,
                                              "issues", raw_path, report))
            issues = result.get("issues", []) if isinstance(result, dict) else []
            for r in issues:
                r.setdefault("publication_id", pub_id)
            state["issues"] = issues
        except LLMError as e:
            print(f"[!] issues не получены: {e}\n    Основной датасет сохраню без issues.", flush=True)
            failed.append("issues")

    for k in TABLES:
        state.setdefault(k, [])
    dataset = {k: state[k] for k in TABLES}

    out_path = Path(out) if out else src.with_name(src.stem + ".dataset.json")
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    meta_out = None
    if meta_path:
        meta_out = Path(meta_path)
        meta = {
            "input_md": str(src),
            "publication_id": pub_id,
            "model": model,
            "provider": [p.strip() for p in provider.split(",") if p.strip()] or "auto",
            "steps": report,
            "failed_tables": failed,
            "counts": {k: len(v) for k, v in dataset.items()},
        }
        meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {k: len(v) for k, v in dataset.items()}
    print("\nСтрок по таблицам:", json.dumps(counts, ensure_ascii=False), flush=True)
    if failed:
        print("[!] таблицы, которые НЕ удалось извлечь:", ", ".join(failed), flush=True)
    problems = check_dataset(dataset)
    if problems:
        print(f"Замечания валидатора ({len(problems)}):", flush=True)
        for p in problems:
            print("   -", p, flush=True)
    else:
        print("Валидация: замечаний нет", flush=True)
    print(f"\nСохранено: {out_path}", flush=True)
    return {"dataset": dataset, "counts": counts, "failed_tables": failed,
            "problems": problems, "out_path": str(out_path),
            "meta_path": str(meta_out) if meta_out else None,
            "publication_id": pub_id}


def main() -> int:
    ap = argparse.ArgumentParser(description="Датасет статьи в JSON — по одной таблице за вызов")
    ap.add_argument("input", nargs="?", default="D001_stripped.md", help="markdown статьи")
    ap.add_argument("--publication-id", default=None)
    ap.add_argument("--out", help="выходной JSON (по умолчанию <input>.dataset.json)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--provider", default="wafer", help='провайдеры по приоритету через запятую; "" = не пинить')
    ap.add_argument("--no-strict", action="store_true")
    ap.add_argument("--max-chars", type=int, default=200000, help="обрезать вход")
    ap.add_argument("--max-tokens", type=int, default=16384, help="лимит на один main-вызов")
    ap.add_argument("--issues-max-tokens", type=int, default=12288)
    ap.add_argument("--no-issues", action="store_true")
    ap.add_argument("--issues-with-text", action="store_true",
                    help="к issues-проходу добавить текст статьи (дороже)")
    ap.add_argument("--api-key", default=os.environ.get("ROUTERAI_API_KEY"))
    ap.add_argument("--meta", help="куда сохранить метаданные прогона (пошаговые токены/время/finish_reason)")
    args = ap.parse_args()

    try:
        extract(args.input, publication_id=args.publication_id, out=args.out,
                model=args.model, provider=args.provider, no_strict=args.no_strict,
                max_chars=args.max_chars, max_tokens=args.max_tokens,
                issues_max_tokens=args.issues_max_tokens, no_issues=args.no_issues,
                issues_with_text=args.issues_with_text, meta_path=args.meta,
                api_key=args.api_key)
    except (LLMError, FileNotFoundError) as e:
        sys.exit(f"Ошибка: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
