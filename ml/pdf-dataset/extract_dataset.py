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
from collections import Counter, defaultdict
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
  для прочих — формула как в статье, если дана. ТОЛЬКО если состав прямо написан в статье формулой
  (в тексте/таблице/подписи). НЕ вычисляй состав из соотношения прекурсоров, концентраций растворов или
  долей в смеси и не восстанавливай по догадке — тогда formula_normalized = null (описание — в material_name_raw);
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
- layer_material — материал слоя как в статье, с уточнениями, если они даны: добавки, обработка,
  характеристика подложки («FTO glass (8 Ω/sq)», «Spiro-OMeTAD (4-tBP, Li-TFSI, FK209)»,
  «MoS2, exfoliated with ethylene diamine», «compact TiO2»);
- layer_thickness — только если указана, В НМ (мкм × 1000); число, либо текст-диапазон «63-68»;
  thickness_unit = nm, если толщина указана;
- deposition_method — метод нанесения с ключевыми параметрами, если описаны
  («spin-coating, 4000 rpm, 30 s», «thermal evaporation», «chemical bath (TiCl4 in water, 70 °C)»,
  «RF magnetron sputtering, RT»). Ключевые параметры — скорость и время вращения, температура (или RT),
  число циклов. НЕ включай название установки/производителя, мощность, давление, поток газа, чистоту мишени,
  расстояние до подложки, аббревиатуру метода в скобках («(PED)», «(CBD)») — это в note;
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
при одних условиях (включая направление развёртки) = одна строка. [faktura-patch v2]
ПОЛНОТА — обойди статью систематически:
  1) КАЖДАЯ таблица с характеристиками приборов: каждая строка × каждый столбец-показатель =
     отдельная запись (обе развёртки Forward/Reverse, champion и среднее — разные строки);
  2) текст разделов результатов и обсуждения; 3) аннотация и заключение (см. повторы ниже);
  4) значения ЧУЖИХ работ (введение, обсуждение, таблицы сравнения с литературой).
- Champion и средние по партии — ОТДЕЛЬНЫЕ строки; aggregation ∈ champion/mean/median
  (если в статье не сказано, лучший это прибор или среднее — null).
- result_origin: ПО УМОЛЧАНИЮ primary — всё, что измерено на приборах авторов этой статьи
  (у таких строк есть device_id). cited — ТОЛЬКО значения ДРУГИХ работ: упомянутые в тексте
  («рекорд 22.9% [12]», «reported PCE of 13.1% [24]», история рекордов во введении «from 3.8% to
  over 26%» — даже без номера ссылки) и строки таблиц сравнения с литературой (каждая строка со
  ссылкой — отдельная запись). Записывай такие значения все. Для cited device_id и measurement_id =
  null, cited_publication_id — метка ссылки как в статье («[24]»), если её нет — null.
- ПОВТОРЫ: основная запись — самое подробное место (обычно таблица или раздел результатов).
  Повтор (отдельная строка, duplicate_of = result_id основной) заводи, только если то же значение
  того же прибора пересказано в ДРУГОМ месте статьи: в аннотации, заключении, другой таблице.
  Повторы заводи ПРАКТИЧЕСКИ ТОЛЬКО для PCE (главный результат в аннотации/заключении).
  Voc/Jsc/FF/retained_PCE и прочие из аннотации/заключения повторами НЕ записывай, если эти же числа
  уже есть в таблице/результатах. Внутри одной страницы повторы НЕ заводи.
- extraction_status ∈ extracted, present_not_extractable (например, только на графике
  или только в supplementary), absent.
- ЧИСЛА: raw_value — ТОЛЬКО число. Слова-уточнители («more than», «~», «about», «over», «close to»,
  «>») не пишутся в значение (их можно упомянуть в note): «more than 90%» → 90; «~50%» → 50.
  Степени считай: «4.76×10^4 Ω cm2» → 47600. Если значение однозначно выводится из текста —
  запиши его: retained_PCE при «decline of over 20%» → 80; «device cannot work after 300 h» → 0.
  raw_unit — единица как в статье.
- Единицы: PCE, FF, HI, retained_PCE → %; Voc → V; Jsc, Jsc_EQE → mA/cm2;
  Rs, Rsh → Ohm*cm2; T80 → h. metric_value = normalized_value; metric_unit = normalized_unit.
  Внимание на конвертации: мВ→В (÷1000), доля→% (×100), мкА/см2→мА/см2 (÷1000), кОм·см2→Ом·см2 (×1000).
- spread_value (± или стандартное отклонение) и n_devices — только если указаны.
- time_point + time_unit — момент измерения для стабильности.
- metric_name: PCE, Voc, Jsc, FF, Rs, Rsh, HI, Jsc_EQE, stabilized_PCE, T80, retained_PCE
  (или понятное название, если показателя нет в списке: Voc_deficit, Jmpp, Vmpp, AVT...).
  Jsc_EQE — ТОЛЬКО интегрированный по спектру EQE ток; Jsc из ВАХ — это Jsc.
- source_page — страница PDF по маркеру <!-- page N -->; source_section/source_table/source_figure —
  где найдено (например 3.4 / Table 4 / Figure 10).
- source_fragment: для значения из ТАБЛИЦЫ — «Table N, строка <метка строки как в таблице>»
  (например «Table 2, строка Spiro-OMeTAD Forward»); для значения из ТЕКСТА — короткая дословная
  цитата (обычные слова вокруг числа; формулы LaTeX не переписывай — бери соседние слова).
- note — только если формулировка в статье нестандартна.""",

    "issues": """[faktura-patch v4] Прочитай СТАТЬЮ и сверь её с датасетом. Найди проблемы, которые видны
только из ТЕКСТА статьи (числовые проверки jv_consistency, voc_limit, range, eqe_consistency,
champion_vs_mean, hysteresis_index, voc_deficit_consistency делает программа — их НЕ пиши).
Что искать (check_name — ровно одно из):
- contradiction — одно и то же расходится в разных местах статьи: значение (аннотация 0.465 V, Table 2 —
  456 mV), материал или структура (в методике Ag, на рисунке Au), подложка, толщины слоёв;
- ambiguity — из текста нельзя однозначно понять: число приборов в статистике, прямая или обратная
  развёртка, собственное это значение или повтор литературы, число без значения («rapid decay»);
- derived_values — в тексте «производные» числа, а не результаты: разности строк таблицы, относительный
  прирост («improved by 3.6% over the reference», «1.225% higher PCE», «increases Voc by 80 mV»);
- figure_only — результаты (приборы, партии, распределения) есть только на рисунках, без чисел;
- figure_mismatch — точки рисунка не совпадают с таблицей;
- missing_conditions — не указаны условия измерения ВАХ: площадь, спектр/интенсивность, развёртка;
  или они вынесены в Supporting Information;
- measurement_conditions — нестандартные условия: не AM1.5G, «1 sun» по показаниям 1.2 sun, предобработка
  светом/током, освещение с двух сторон;
- comparability — результат нельзя сравнивать с другими статьями: мини-модуль (Voc всего модуля),
  галогенная лампа, нестандартная площадь;
- dedup_trap — две строки (разных таблиц/разделов) — один и тот же прибор, ИЛИ два разных прибора
  с одинаковым значением, которые нельзя объединять;
- cited_in_table — в таблице собственных результатов стоят строки из чужой работы;
- text_mix — фраза объединяет максимумы разных приборов («values up to 41% and 0.46 V»);
- possible_swap — значения, вероятно, перепутаны местами (средние/лучшие, EQE контроля и образца);
- unit_inconsistency — единица не согласуется со значением (FF «0.71%» при доле);
- rounding_inconsistency — в разных местах одно значение с разным округлением (57.4 и 57.49);
- plausibility — значение правдоподобно с трудом (FF 87 % при Voc 0.98 В и нестандартном освещении);
- typo — опечатка в статье (CsCO3 вместо Cs2CO3, CIGS вместо CZTS).
Каждая запись — ОДНА фраза-описание по-русски с конкретикой (числа/формулировки с обеих сторон).
record_ids — result_id из датасета через «;», если замечание про конкретные результаты, иначе null.
source_page — страница по маркеру <!-- page N -->; source_fragment — короткая дословная цитата.
Если проблем нет — {"issues": []}. Не больше 10; только то, что реально есть в статье, не выдумывай.""",
}

STEP_ORDER = ["publications", "materials", "devices", "layers", "measurements", "results"]
# [faktura-patch] порядок зависимостей (вместо STEP_GROUPS), см. extract():
#   publications || materials -> devices -> (layers в фоне) || (measurements -> results) -> issues


# ---------------------------------------------------------------- [faktura-patch] solar: deterministic conventions
_TCO_RX = re.compile(r"(?i)^\s*(i-?ZnO|intrinsic ZnO|undoped ZnO|UZO|AZO|ZnO:Al|Al:ZnO|Al-doped ZnO|BZO|ZnO:B|ITO|IZO|IWO|"
                     r"IOH|FTO|ZnO\s*/\s*(AZO|ITO|ZnO:Al))\b")
_INORGANIC = {"CIGS", "CdTe", "kesterite", "Sb2Se3"}
_CLASS_RX = [(re.compile(r"(?i)CIGS|Cu\(In,?\s*Ga\)|CuInGa|CuIn\(?S?,?Se"), "CIGS"), (re.compile(r"CdTe"), "CdTe"),
             (re.compile(r"(?i)CZTS|Cu2ZnSn|kesterite"), "kesterite"), (re.compile(r"Sb2\(?S?e?"), "Sb2Se3"),
             (re.compile(r"(?i)perovskite|Pb[IBC]|MAPb|FAPb|CsPb"), "perovskite")]


def _acronym(words: str) -> str:
    return "".join(w[0] for w in re.findall(r"[A-Za-z][A-Za-z\-]*", words) if w.lower() not in ("of", "and", "the")).upper()


def _drop_synonym_paren(s: str, prefer_short: bool) -> str:
    """'AZO (Al-doped ZnO)' -> 'AZO'; 'aluminum-doped zinc oxide (AZO)' -> 'AZO' (materials, prefer_short);
    'pulsed electron deposition (PED)' -> 'pulsed electron deposition' (methods). Other brackets are kept."""
    if not s:
        return s
    m = re.match(r"^\s*([^()]+?)\s*\(([^()]+)\)\s*(.*)$", s)
    if not m:
        return s
    head, inner, tail = m.group(1), m.group(2), m.group(3)
    hn, inn = re.sub(r"[\W_]+", "", head).upper(), re.sub(r"[\W_]+", "", inner).upper()
    if 2 <= len(inn) <= 6 and inner.strip().isupper() and _acronym(head).startswith(inn[:2]) and len(head.split()) >= 2:
        keep = inner.strip() if prefer_short else head.strip()            # "long words (ACR)"
    elif 2 <= len(hn) <= 6 and (
            (len(inner.split()) >= 2 and (_acronym(inner).startswith(hn[:2]) or hn in inn))
            or re.fullmatch(r"(?i)[\w\s\-]*(doped|oxide|intrinsic|undoped|molybdenum|gold|silver|alumin(i)?um|copper|"
                            r"nickel|titanium|carbon|tin)[\w\s\-]*", inner.strip())):
        keep = head.strip()                                               # "ACR (long words)"
    else:
        return s
    return (keep + (" " + tail if tail else "")).strip()


def solar_normalize(state: dict, text: str) -> dict:
    """Conventions of the solar template (README) that the model applies unevenly; generic, per paper:
    device_role record->target; area defined by a mask -> aperture; architecture consistent with the absorber class
    and the layer order; substrate_type rigid for glass; TCO windows of inorganic thin-film cells -> front_contact;
    J-V spectrum/intensity from the paper's single stated condition; no synonym brackets in materials/methods."""
    st = Counter()
    text = text or ""
    for m in state.get("materials") or []:  # antimony chalcogenides with Se (Sb2(S,Se)3, Sb2S3/Sb2(S,Se)3) -> Sb2Se3
        f = f"{m.get('formula_normalized') or ''} {m.get('material_name_raw') or ''}"
        if m.get("material_class") == "other" and re.search(r"Sb2\s*\(?S?,?\s*Se", f):
            m["material_class"] = "Sb2Se3"; st["material_class"] += 1
    mats = {m.get("material_id"): (m.get("material_class") or "") for m in state.get("materials") or []}
    layers_by_dev = defaultdict(list)
    for l in state.get("layers") or []:
        layers_by_dev[l.get("device_id")].append(l)
    for ls in layers_by_dev.values():
        ls.sort(key=lambda l: (l.get("layer_order") is None, _as_num(l.get("layer_order")) or 0))
    glass = re.search(r"(?i)\bglass\b|soda[- ]lime|\bSLG\b", text) and not re.search(
        r"(?i)flexible|polyimide|\bPET\b|\bPEN\b|metal foil|stainless[- ]steel foil", text)
    for d in state.get("devices") or []:
        if d.get("device_role") == "record":
            d["device_role"] = "target"; st["device_role"] += 1
        if d.get("area_definition") == "designated illumination" and re.search(r"(?i)\bmask|aperture", text):
            d["area_definition"] = "aperture"; st["area_definition"] += 1
        if not d.get("substrate_type") and glass:
            d["substrate_type"] = "rigid"; st["substrate_type"] += 1
        if not d.get("device_role"):  # the template has a role for every device; not control/record -> target
            d["device_role"] = "target"; st["device_role"] += 1
        ls = layers_by_dev.get(d.get("device_id")) or []
        cls = mats.get(d.get("absorber_material_id")) or (next(iter(set(mats.values()))) if len(set(mats.values())) == 1 else "")
        if not cls:
            ab = " ".join(l.get("layer_material") or "" for l in ls if l.get("layer_function") == "absorber")
            cls = next((c for rx, c in _CLASS_RX if rx.search(ab)), "")
        funcs = [l.get("layer_function") for l in ls]
        arch = d.get("architecture")
        if cls in _INORGANIC:
            for l in ls:
                if l.get("layer_function") in ("interlayer", "ETL") and _TCO_RX.match(l.get("layer_material") or ""):
                    l["layer_function"] = "front_contact"; st["layer_function"] += 1
            funcs = [l.get("layer_function") for l in ls]
            if funcs:
                want = "substrate" if funcs[0] == "back_contact" else ("superstrate" if funcs[0] == "front_contact" else None)
                if want and want != arch:
                    d["architecture"] = want; st["architecture"] += 1
        elif cls == "perovskite" and "absorber" in funcs:
            k = funcs.index("absorber")
            below = funcs[:k]
            want = "n-i-p" if "ETL" in below and "HTL" not in below else ("p-i-n" if "HTL" in below and "ETL" not in below else None)
            if want and arch != want:
                d["architecture"] = want; st["architecture"] += 1
    for l in state.get("layers") or []:
        for f, short in (("layer_material", True), ("deposition_method", False)):
            v = l.get(f)
            if isinstance(v, str):
                nv = _drop_synonym_paren(v, short)
                if nv != v:
                    l[f] = nv; st[f] += 1
    # results from a J-V table: the paper's single J-V curve figure is also their source (template: source_figure);
    # a table whose caption says "best/champion devices" (and not average) -> aggregation champion
    flat = " ".join(text.split())
    jv = _jv_figures(flat)
    best = _best_tables(flat)
    for r in state.get("results") or []:
        if r.get("result_origin") != "primary" or not r.get("source_table") or r.get("metric_name") not in _JV_METRICS:
            continue
        tn = re.search(r"(\d+)", str(r.get("source_table")))
        if not r.get("aggregation") and tn and tn.group(1) in best:
            r["aggregation"] = "champion"; st["aggregation"] += 1
        if not r.get("source_figure") and len(jv) == 1:
            n, (lab, pan) = next(iter(jv.items()))
            r["source_figure"] = ("Fig. " if lab.lower().startswith("fig.") else "Figure ") + n + pan
            st["source_figure"] += 1
    specs = {re.sub(r"\s+", "", m.group(0)).upper().replace("AM1.5GLOBAL", "AM1.5G")
             for m in re.finditer(r"(?i)\bAM\s?1\.5\s?G\b|\bAM\s?1\.5\s?global", text)}
    ints = {(m.group(1), re.sub(r"\s+", "", m.group(2)).replace("cm-2", "/cm2").replace("cm−2", "/cm2")
             .replace("m-2", "/m2").replace("m−2", "/m2").replace("·", "").replace("//", "/"))
            for m in re.finditer(r"(?<![\d.])(100|1000)\s*(mW\s*/?\s*cm[-−]?2|mW\s*cm[-−]2|W\s*/?\s*m[-−]?2|W\s*m[-−]2)", text)}
    for ms in state.get("measurements") or []:
        if ms.get("measurement_type") not in ("J-V", "MPP tracking"):
            continue
        if not ms.get("spectrum") and len(specs) == 1:
            ms["spectrum"] = "AM1.5G"; st["spectrum"] += 1
        if ms.get("light_intensity") in (None, "") and len(ints) == 1:
            v, u = next(iter(ints))
            ms["light_intensity"] = float(v)
            ms["intensity_unit"] = "mW/cm2" if u.lower().startswith("mw") else "W/m2"
            st["light_intensity"] += 1
    return dict(st)


SOLAR_NORMALIZE = os.environ.get("SOLAR_NORMALIZE", "1").strip() not in ("0", "off", "false", "no")
_JV_METRICS = {"PCE", "Voc", "Jsc", "FF", "Rs", "Rsh"}
_JV_CAP_RX = re.compile(r"(?i)\bJ\s*[–\-−]\s*V\b|\bI\s*[–\-−]\s*V\b|current[\s-]*(?:density)?\s*[–\-−]\s*voltage")


def _captions(text: str, kind: str) -> dict:
    lab = r"Fig\.|Figure" if kind == "fig" else r"Table"
    out: dict = {}
    for m in re.finditer(r"(?i)\b(" + lab + r")\s*(\d+)\s*[.:|]\s", text):
        out.setdefault(m.group(2), []).append((m.group(1), text[m.end():m.end() + 260]))
    return out


def _jv_figures(text: str) -> dict:
    """{figure number: (label word, panel letter)} for figures whose caption starts with J-V curves."""
    hits = {}
    for n, occ in _captions(text, "fig").items():
        for lab, cap in occ:
            mm = _JV_CAP_RX.search(cap[:160])
            if mm:
                pan = re.findall(r"(?:^|[\s;,(])\(?([a-h])\)\s", cap[:mm.start()])
                hits[n] = (lab, pan[-1] if pan else "")
                break
    return hits


def _best_tables(text: str) -> set:
    return {n for n, occ in _captions(text, "table").items()
            if any(re.search(r"(?i)\b(best|champion|highest[- ]efficiency|record)\b", cap[:200])
                   and re.search(r"(?i)parameter|performance|characteristic|efficienc", cap[:200])
                   and not re.search(r"(?i)\b(average|mean|median|statistic)", cap[:200]) for _, cap in occ)}


def _as_num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fix_result_origin(rows):
    """[faktura-patch] Детерминированно: у значения чужой работы нет прибора/измерения (README),
    у собственного — есть. Модель иногда помечает cited все строки статьи."""
    for r in rows:
        if r.get("device_id") or r.get("measurement_id"):
            r["result_origin"] = "primary"
        elif r.get("cited_publication_id"):
            r["result_origin"] = "cited"
        elif not r.get("result_origin"):
            r["result_origin"] = "primary"


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


def call_llm(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
             retry=True, reasoning_tokens=None):
    """[faktura-patch] Вызов с повтором: модель рассуждает и иногда тратит весь лимит на рассуждение
    (finish_reason=length, пустой ответ). Рассуждение ограничено (reasoning.max_tokens), при пустом/обрезанном
    ответе — один повтор с меньшим бюджетом рассуждения и большим общим лимитом.
    [faktura-patch] retry=False — без повтора; reasoning_tokens — свой бюджет рассуждения (вместо REASONING_TOKENS)."""
    budget = REASONING_TOKENS if reasoning_tokens is None else reasoning_tokens
    try:
        return _call_llm_once(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report,
                              reasoning_tokens=min(budget, max_tokens // 2))
    except LLMError as e:
        if not retry:
            raise
        if budget == 0 and "finish_reason=length" in str(e):
            raise  # [faktura-patch] рассуждение уже выключено: тот же вызов снова обрежется — пусть вызывающий делит
        print(f"[{label}] повтор после сбоя: {e}", flush=True)
        # [faktura-patch] провайдер (wafer) игнорирует reasoning.max_tokens: модель иногда рассуждает до конца
        # лимита и отдаёт пустой ответ. Повтор — с выключенным рассуждением (reasoning.enabled=false работает).
        return _call_llm_once(client, args, messages, schema_name, schema, max_tokens, label + ":retry",
                              raw_path, report, reasoning_tokens=0)


def _call_llm_once(client, args, messages, schema_name, schema, max_tokens, label, raw_path, report=None,
                   reasoning_tokens=None):
    """Один вызов с fallback json_schema -> json_object и диагностикой."""
    providers = [p.strip().lower() for p in args.provider.split(",") if p.strip()]  # [faktura-patch] slug строчными
    # [faktura-patch] список = приоритет: первый живой из списка, при сбое — следующий, вне списка не уходим
    extra_body = {"provider": {"order": providers, "allow_fallbacks": False}} if providers else {}
    if reasoning_tokens == 0:
        extra_body["reasoning"] = {"enabled": False}
    elif reasoning_tokens:
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


# [faktura-patch] дозаполнение пропусков results по кандидатам из текста/таблиц
GAPFILL_PROMPT = """Ниже — уже извлечённые строки results статьи и список УПОМИНАНИЙ значений показателей,
найденных в тексте/таблицах статьи, которые НЕ попали в results. Для КАЖДОГО упоминания реши:
  (а) это результат, который нужно добавить — собственный (primary, укажи device_id/measurement_id из
      «Уже извлечено») или значение другой работы (cited, device_id=null, measurement_id=null);
  (б) это повтор уже извлечённого значения того же прибора из другого места статьи — добавь строку с
      duplicate_of = result_id основной (только если это действительно другое место статьи, обычно PCE);
  (в) не результат (часть другой фразы, условия эксперимента, номер, промежуточное значение,
      неверно распознанный показатель) — пропусти.
  (г) упоминание «source=supplementary»: если по тексту значение показателя прибора есть ТОЛЬКО в
      дополнительных материалах (Table S…, Supporting Information) или только на рисунке — добавь строку
      extraction_status=present_not_extractable, raw_value/metric_value = null, source_fragment — дословная
      ссылка из текста («reported in Table S5»), note — пояснение по-русски; device_id — какой прибор;
      если упоминание не про значение показателя — пропусти.
В контексте упоминания могут быть и другие значения (например «from 3.8% to over 26%») — добавь все,
которые являются результатами.
Верни ТОЛЬКО новые строки (не повторяй уже извлечённые) в ключе "results"; result_id — новые,
не совпадающие с существующими (продолжай нумерацию). Правила полей — как в задании results.
Если добавлять нечего — {"results": []}."""


def gap_fill(client, args, text, state, mentions, pub_id, max_tokens, raw_path, report, call_llm,
             table_schema, common_rules, results_prompt, nullify_empties, fix_result_origin):
    if not mentions:
        return []
    import json
    lines = [f"- {m['metric']} = {(format(m['value'], 'g') if m['value'] is not None else 'без числа')} (с. {m['page']}, {m['source']}): «{m['context'][:220]}»"
             for m in mentions[:40]]
    compact = {k: state.get(k, []) for k in ("materials", "devices", "measurements", "results")}
    user = (f"publication_id = {pub_id}\n\nТекст статьи (Markdown):\n{text}\n\n"
            f"Уже извлечено (JSON):\n{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}\n\n"
            f"Упоминания, не попавшие в results ({len(lines)}):\n" + "\n".join(lines) +
            f"\n\nЗадание (дозаполнение results):\n{GAPFILL_PROMPT}\n\nПравила полей results:\n{results_prompt}")
    messages = [{"role": "system", "content": common_rules}, {"role": "user", "content": user}]
    res = nullify_empties(call_llm(client, args, messages, "table_results_gapfill", table_schema("results"),
                                   max_tokens, "gapfill", raw_path, report))
    rows = res.get("results", []) if isinstance(res, dict) else []
    have = {r.get("result_id") for r in state.get("results", [])}
    out, n = [], len(have) + 1
    for r in rows:
        r["publication_id"] = pub_id  # [faktura-patch] всегда id из запроса (модель пишет «.»)
        if not r.get("result_id") or r["result_id"] in have:
            while f"R{n:03d}" in have:
                n += 1
            r["result_id"] = f"R{n:03d}"
        have.add(r["result_id"])
        out.append(r)
    fix_result_origin(out)
    return out


# ====================================================================================================
# [faktura-patch] Область RAG (domain="rag"): схемы, промпты, results по таблицам кусками.
# Область solar выше не затронута; сюда попадаем только из extract(..., domain="rag").
# Колонки — 1:1 с data/caseA_drive/submission_template/schema.json.
# ====================================================================================================
DOMAINS = ("solar", "rag")
RAG_RESULT_ORIGINS = ["primary", "cited"]
RAG_EXTRACTION_STATUS = ["extracted", "present_not_extractable", "absent"]
RAG_METRIC_DIRECTIONS = ["higher_is_better", "lower_is_better", "target"]  # рекомендуемый словарь (null допустим)
RAG_TABLE_CELLS = int(os.environ.get("RAG_TABLE_CELLS", "110"))      # числовых ячеек таблицы на один вызов
RAG_RESULTS_MAX_TOKENS = int(os.environ.get("RAG_RESULTS_MAX_TOKENS", "32768"))
RAG_WORKERS = 4
# [faktura-patch] экономия: перенос ячеек таблицы в строки не требует рассуждения (85 % стоимости RAG — рассуждение results)
RAG_TABLE_REASONING = os.environ.get("RAG_TABLE_REASONING", "off").strip().lower() not in ("off", "0", "false", "no")
RAG_SMALL_TABLE_CELLS = int(os.environ.get("RAG_SMALL_TABLE_CELLS", "40"))  # целые таблицы не больше — склеиваются
RAG_BATCH_CELLS = int(os.environ.get("RAG_BATCH_CELLS", "110"))             # потолок числовых ячеек в склейке
# [faktura-patch] ~200–230 выходных токенов на строку results: 110 ячеек укладываются в RAG_RESULTS_MAX_TOKENS=32768.
# Шаг issues: провайдер игнорирует бюджет рассуждения, первый вызов всегда сжигал 16384 токена на рассуждение и
# обрезался, в итог шёл повтор без рассуждения. Поэтому по умолчанию сразу без рассуждения (0); "" — как раньше.
_ri = os.environ.get("RAG_ISSUES_REASONING", "0").strip()
RAG_ISSUES_REASONING = int(_ri) if _ri else None

RAG_PUB = PUB

RAG_CONFIG = obj({
    "publication_id": STR(),
    "config_id": STR(),
    "rag_variant": S(),
    "retrieval_method": S(),
    "retriever_model": S(),
    "embedding_model": S(),
    "embedding_version": S(),
    "chunking_method": S(),
    "chunk_size": N(),
    "chunk_overlap": N(),
    "chunk_unit": S(),
    "index_type": S(),
    "vector_store": S(),
    "top_k": N(),
    "reranker_model": S(),
    "generator_model": S(),
    "generator_version": S(),
    "prompt_strategy": S(),
    "context_strategy": S(),
    "note": S(),
})

RAG_DATASET = obj({
    "publication_id": STR(),
    "dataset_id": STR(),
    "dataset_name": S(),
    "dataset_version": S(),
    "language": S(),
    "data_split": S(),
    "scientific_domain": S(),
    "note": S(),
})

RAG_TASK = obj({
    "publication_id": STR(),
    "task_id": STR(),
    "task_type": S(),
    "note": S(),
})

RAG_EXPERIMENT = obj({
    "publication_id": STR(),
    "experiment_id": STR(),
    "evaluation_protocol": S(),
    "baseline_name": S(),
    "hardware": S(),
    "latency_ms": N(),
    "tokens_per_query": N(),
    "sample_size": NS(),
    "note": S(),
})

RAG_RESULT = obj({
    "publication_id": STR(),
    "result_id": STR(),
    "experiment_id": S(),
    "config_id": S(),
    "dataset_id": S(),
    "task_id": S(),
    "metric_group": S(),
    "metric_name": S(),
    "metric_variant": S(),
    "metric_direction": S(RAG_METRIC_DIRECTIONS),
    "metric_k": N(),
    "metric_value": N(),
    "metric_unit": S(),
    "baseline_value": N(),
    "aggregation": S(),
    "sample_size": NS(),
    "raw_value": NS(),
    "raw_unit": S(),
    "normalized_value": N(),
    "normalized_unit": S(),
    "result_origin": S(RAG_RESULT_ORIGINS),
    "cited_publication_id": S(),
    "source_page": I(),
    "source_section": S(),
    "source_table": S(),
    "source_figure": S(),
    "source_fragment": S(),
    "extraction_status": S(RAG_EXTRACTION_STATUS),
    "duplicate_of": S(),
    "note": S(),
})

RAG_ISSUE = ISSUE

RAG_TABLES = {
    "publications": RAG_PUB,
    "configurations": RAG_CONFIG,
    "datasets": RAG_DATASET,
    "tasks": RAG_TASK,
    "experiments": RAG_EXPERIMENT,
    "results": RAG_RESULT,
    "issues": RAG_ISSUE,
}
RAG_KEYS = {"publications": "publication_id", "configurations": "config_id", "datasets": "dataset_id",
            "tasks": "task_id", "experiments": "experiment_id", "results": "result_id", "issues": "issue_id"}


def rag_table_schema(name: str) -> dict:
    return {"type": "object",
            "properties": {name: {"type": "array", "items": RAG_TABLES[name]}},
            "required": [name],
            "additionalProperties": False}


def check_dataset_rag(d: dict) -> list[str]:
    """Аналог check_dataset для RAG: колонки/типы enum, уникальность id, ссылки results -> experiment/config/dataset/task,
    duplicate_of, issues.record_ids. Рекомендуемый словарь metric_direction — предупреждение."""
    problems = []
    tabs = {}
    for name in RAG_TABLES:
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
    for r in tabs["results"]:
        for col, allowed in (("result_origin", RAG_RESULT_ORIGINS), ("extraction_status", RAG_EXTRACTION_STATUS)):
            v = r.get(col)
            if v is not None and v not in allowed:
                problems.append(f"results {r.get('result_id')}: {col}={v!r} вне enum")
        v = r.get("metric_direction")
        if v is not None and v not in RAG_METRIC_DIRECTIONS:
            problems.append(f"предупреждение: results {r.get('result_id')}: metric_direction={v!r} вне рекомендуемых")
        sp = r.get("source_page")
        if sp is not None and (not isinstance(sp, int) or isinstance(sp, bool) or sp < 1):
            problems.append(f"results {r.get('result_id')}: source_page={sp!r} должен быть целым >= 1")
    for r in tabs["publications"]:
        if r.get("in_scope") is not None and not isinstance(r.get("in_scope"), bool):
            problems.append("publications: in_scope должен быть boolean")
    ids = {}
    for t, key in RAG_KEYS.items():
        seen = set()
        for r in tabs[t]:
            v = r.get(key)
            if v is not None:
                if v in seen:
                    problems.append(f"{t}: дубликат {key}={v!r}")
                seen.add(v)
        ids[t] = seen
    for r in tabs["results"]:
        rid = r.get("result_id")
        for col, tab in (("experiment_id", "experiments"), ("config_id", "configurations"),
                         ("dataset_id", "datasets"), ("task_id", "tasks")):
            v = r.get(col)
            if v is not None and v not in ids[tab]:
                problems.append(f"results {rid}: {col}={v!r} нет в {tab}")
        dup = r.get("duplicate_of")
        if dup is not None:
            if dup == rid:
                problems.append(f"results {rid}: duplicate_of ссылается на себя")
            elif dup not in ids["results"]:
                problems.append(f"results {rid}: duplicate_of={dup!r} не существует")
    by_id = {r.get("result_id"): r for r in tabs["results"]}
    for r in tabs["results"]:  # цикл по duplicate_of
        seen, cur = set(), r
        while cur is not None and cur.get("duplicate_of") is not None:
            if cur.get("result_id") in seen:
                problems.append(f"results {r.get('result_id')}: цикл duplicate_of")
                break
            seen.add(cur.get("result_id"))
            cur = by_id.get(cur.get("duplicate_of"))
    for r in tabs["issues"]:
        for rid in (r.get("record_ids") or "").split(";"):
            rid = rid.strip()
            if rid and rid not in ids["results"]:
                problems.append(f"issues {r.get('issue_id')}: record_id {rid!r} нет в results")
    if tabs["publications"] and tabs["publications"][0].get("in_scope") is False:
        for t in RAG_TABLES:
            if t not in ("publications", "issues") and tabs[t]:
                problems.append(f"in_scope=false, но в {t} есть строки")
    return problems


RAG_COMMON_RULES = """Ты — экстрактор данных из научных статей о RAG-системах (Retrieval-Augmented Generation:
система находит сведения во внешней коллекции документов и использует найденный контекст при формировании ответа).
Извлекай ТОЛЬКО то, что прямо написано в тексте статьи. Ничего не додумывай.
Ключевые правила:
- Верхний ключ JSON — ТОЛЬКО указанный в задании. Значение — массив объектов.
- publication_id везде равен заданному в задании.
- Пустая ячейка = null (не "", не "N/A", не "unknown", не "-"). Ноль — это 0, а не null.
- СТРОГО: ячейка заполняется, только если значение ЯВНО указано в статье именно для этой записи; иначе null.
  Не достраивай версии моделей, параметры, шкалы и условия по известным настройкам библиотек, по «типичной
  практике» или по другим статьям. Сохраняй исходное обозначение (имя модели, метода, набора данных, метрики —
  как в статье); неоднозначность поясняй в note. Одинаковые названия моделей или наборов не дают права
  объединять разные эксперименты. Лучше пустая ячейка, чем догадка.
- ID короткие и уникальные внутри публикации: C1.. (configurations), DS1.. (datasets), T1.. (tasks), E1.. (experiments),
  R001.. (results), I1.. (issues). Если передаётся «Уже извлечено» — используй те же id, не переименовывай.
- Числовые поля — JSON-числа без единиц и множителей. Если параметр дан диапазоном или словами — числовое поле
  null, исходная запись — в note. Среднее вместо диапазона не подставлять.
- Страницы: перед содержимым каждой PDF-страницы стоит маркер <!-- page N -->. source_page = N ближайшего
  ПРЕДШЕСТВУЮЩЕГО маркера (страница PDF, с 1; не номер страницы журнала). source_fragment — короткая (до ~120 симв.)
  ДОСЛОВНАЯ цитата из текста этой страницы: символ в символ, без перефразирования; не склеивай разные места.
- Выводи ТОЛЬКО JSON-объект, без пояснений и markdown-ограждений.
- [faktura-patch] ЯЗЫК: все текстовые поля (note, evaluation_protocol, description, exclusion_reason, rag_variant и
  т.п.) — ПО-АНГЛИЙСКИ, в терминах статьи (эталон области RAG англоязычный). Названия — как в статье.
"""

RAG_STEP_PROMPTS = {
    "publications": """Заполни таблицу publications РОВНО ОДНОЙ записью:
- title, year (целое), DOI (только если указан в тексте статьи, иначе null), authors (через «; »);
- in_scope (true/false). Предметная область: система находит сведения во внешней коллекции документов и
  использует найденный контекст при формировании ответа; статья описывает конфигурацию такой системы и результаты
  её оценки. Решение — по СОДЕРЖАНИЮ, а не по слову RAG в названии. in_scope=true, если в статье есть
  RAG-система ИЛИ её компонент, разработанный для RAG (поиск/retrieval, чанкинг, индексация, эмбеддинги,
  reranking, сжатие контекста, генерация с найденным контекстом; графовые, агентные, мультимодальные варианты,
  бенчмарки для оценки RAG), и приведены СОБСТВЕННЫЕ количественные результаты (метрики). [faktura-patch]
  Оценка только поиска/чанкинга внутри RAG-конвейера — тоже in_scope=true; позиционная статья с собственными
  экспериментами — in_scope=true. in_scope=false — только если: обзор без собственных чисел; статья не про
  поиск по внешней коллекции для генерации (чистая LLM без retrieval, только дообучение); слово RAG лишь упомянуто.
- exclusion_reason — только при in_scope=false (иначе null): одно–два предложения по-английски — что сделано в
  работе и почему это не подходит.""",

    "configurations": """Заполни configurations — все конфигурации RAG-систем и сравниваемые baseline-варианты, для которых
в статье есть числовые результаты (в таблицах, тексте, ablation):
- ОТДЕЛЬНАЯ конфигурация на каждый вариант системы/baseline, на каждый генератор, на каждый ablation-вариант и
  каждую отличающуюся настройку. Строка таблицы результатов = обычно одна конфигурация (если одна и та же система
  оценена с разными генераторами/настройками — отдельная конфигурация на каждую пару). Не объединяй разные.
  Не создавай конфигурации для методов, у которых в статье нет ни одного числового результата.
  [faktura-patch] ГЛУБИНА ПОИСКА (top_k) — тоже настройка: если одна система оценена при k = 10/20/40, это РАЗНЫЕ
  конфигурации (по одной на каждое значение top_k), а не одна. Аналогично для разных генераторов, retrieval-методов
  (semantic-only vs hybrid), моделей извлечения/разметки, размеров чанка. Конфигурация = уникальная комбинация
  параметров, при которой в статье есть отдельные числа. Если в статье есть числа, не относящиеся к RAG-системе
  (статистика бенчмарка, цены, обзорные подсчёты), заведи под них отдельную служебную конфигурацию
  (rag_variant — что это, например «Benchmark statistics and published pricing»).
- rag_variant — название варианта/baseline как в статье (метка строки таблицы, «w/o reranker»).
- retrieval_method (исходная формулировка: BM25, dense, hybrid, graph...), retriever_model, embedding_model +
  embedding_version (версия — отдельно и только если указана явно), chunking_method, chunk_size + chunk_overlap
  (числа, если указаны численно) + chunk_unit (tokens/characters... по источнику), index_type, vector_store
  (одно не выводится из другого), top_k (число извлекаемых элементов; НЕ k метрики; 0 — если контекст не
  извлекается), reranker_model, generator_model + generator_version (только явная), prompt_strategy,
  context_strategy.
- Параметры, общие для всех систем статьи (одна и та же модель-генератор, один и тот же поиск), записывай в каждую
  конфигурацию, к которой они относятся по тексту.
- note — где описана конфигурация («Table 1, строка X; generator LLaMA3-8B»), дополнительные параметры,
  неоднозначности, диапазоны параметров.""",

    "datasets": """Заполни datasets — наборы данных, на которых оценивались системы:
- dataset_name — как в статье; dataset_version, language, data_split (train/dev/test/выборка), scientific_domain —
  ТОЛЬКО если указаны в статье.
- Разные версии или разбиения набора, участвующие в разных сравнениях, — отдельные записи; один набор в одном
  разбиении — одна запись.
- [faktura-patch] ПОДМНОЖЕСТВА — отдельные записи, если результаты приводятся по ним отдельно: каждая организация/
  поддомен/категория вопросов/тип запросов/отдельный объект (отель, компания), по которым в таблице свои числа, —
  своя запись с именем «<набор> / <подмножество>» (например «RAG-Multi-Corpus / ZX Bank», «Forum questions:
  Experience»). Плюс запись для агрегата («<набор> / Overall»), если есть общая строка.
- Вспомогательные выборки, на которых есть числа (подвыборка для ручной разметки, выборка для опроса людей, перечень
  бенчмарков в обзорной таблице), — тоже отдельные записи.
- note — где упомянут/описан (страницы, раздел) и неоднозначности.""",

    "tasks": """Заполни tasks — задачи оценки:
- task_type — как в статье (например «open-domain multi-hop question answering», «fact verification»,
  «retrieval», «long-form generation»). Не закрытый словарь: сохраняй формулировку статьи.
- Связь задачи с набором фиксируется в results, здесь её не пиши. note — где описана.
- Обычно задач немного (1–7): не дроби задачу по наборам данных, если статья относит их к одному типу.
- [faktura-patch] Задача = вид измеряемой деятельности, по которому в статье есть числа: кроме основной (QA, поиск,
  генерация) это может быть ingestion/индексация и chunk planning, согласованность ручной разметки, учёт ресурсов
  (стоимость, время), состав бенчмарка. Заведи задачу под каждый такой вид.""",

    "experiments": """Заполни experiments — протоколы/условия эксперимента (отдельный эксперимент — когда меняются протокол,
условия оценки, способ расчёта метрик, оборудование, размер выборки; одна основная таблица/раздел оценки обычно = один
эксперимент):
- evaluation_protocol — протокол, условия оценки, способ расчёта, ограничения сравнения (кратко, ПО-АНГЛИЙСКИ, в
  терминах статьи);
- baseline_name — названия сравниваемых baseline через «;» (если указаны);
- hardware — оборудование и число устройств по статье;
- latency_ms — задержка в МИЛЛИСЕКУНДАХ числом, только если исходная единица известна (иначе null, исходная запись — в note);
- tokens_per_query — число токенов на запрос (общий расход не делить на запросы без основания);
- sample_size — размер ОЦЕНОЧНОЙ выборки (не размер всего датасета);
- note — уточнения, где описан протокол. Значения, зависящие от конфигурации (задержка, токены), не распространяй на
  все конфигурации — их оформит results.""",
}

RAG_RESULT_FIELDS = """Правила полей results (одна строка = ОДНО значение ОДНОЙ метрики для ОДНОЙ связки конфигурация ×
набор данных × задача (× эксперимент) при одном k):
- experiment_id, config_id, dataset_id, task_id — ТОЛЬКО id из «Уже извлечено»; если подходящей записи нет — null
  (не выдумывай id). Строка таблицы -> config_id по метке строки (и генератору/настройке); столбец/блок столбцов ->
  dataset_id и метрика.
- metric_group — группа показателей по смыслу источника, свободный текст («answer quality», «retrieval quality»,
  «efficiency», «robustness»); metric_name — название метрики как в статье (EM, F1, Accuracy, Recall@5, NDCG@10,
  latency...). Если у метрики есть @k, metric_name — без «@k», а k — в metric_k (число); отдельная строка на каждый k.
  [faktura-patch] Общепринятые сокращения оставляй (EM, F1, MRR, NDCG, BLEU, ROUGE-L). Сокращения, придуманные для
  конкретной таблицы и расшифрованные в подписи/тексте/легенде, РАСКРЫВАЙ полностью: «Sent-Int σ» -> «Sentiment-Intensity
  sigma», «Biz Impact σ» -> «Business Impact sigma», «Entity Match» (если в тексте «entity match rate») -> «Entity Match Rate».
  Производные значения (прирост, разность, относительное изменение, Δ) — ОТДЕЛЬНОЙ строкой с metric_name «<метрика>
  relative change» / «<метрика> relative improvement» / «<метрика> delta» (как названо в статье) и normalized_unit
  «relative_percent» (относительные %) или «percentage_points» (разность в п.п.). Указанные в статье уровни значимости —
  отдельной строкой metric_name «p-value bound», значение — граница («<0.001» -> 0.001), metric_variant — тест и что
  сравнивается («Wilcoxon signed-rank; Entity Match Rate; Enriched vs Raw»).
  metric_variant — определение/вариант (token-level F1, macro, micro, judge-based, «LLM-as-judge GPT-4o»), только если
  указан. metric_direction — higher_is_better / lower_is_better / target (стрелка ↑/↓ в заголовке, явное указание
  или очевидная природа метрики: ошибка, задержка — lower; неясно -> null).
- baseline_value — значение baseline, ТОЛЬКО если оно стоит рядом в той же ячейке/строке сравнения для сопоставимых
  задачи, набора, метрики и протокола; иначе null.
- aggregation — среднее/медиана/champion/CI и т.п. ТОЛЬКО при явном указании. sample_size — размер выборки для
  этого результата, если указан отдельно.
- raw_value — значение ТАК, КАК НАПЕЧАТАНО в статье (строка: ".204", "63.2", "12.5±0.3", «>90»), raw_unit — единица/знак
  как в статье («%», «ms», null). metric_value + metric_unit и normalized_value + normalized_unit — одно и то же:
  число без изменения масштаба (только добавь ведущий ноль «.204» -> 0.204, раздели «±», запятая -> точка). НЕ ПЕРЕСЧИТЫВАЙ
  доли/проценты, BLEU/ROUGE, время по имени метрики: если шкала не установлена статьёй — normalized_value = число как
  есть, normalized_unit = null (или «%» / «fraction», если статья прямо задаёт шкалу: «all scores in %»; значение 0–1 без
  пометки — «fraction» не ставь). Для диапазона/неточного значения — normalized_value = null, исходное в raw_value и note.
  metric_unit = normalized_unit.
- result_origin: primary — число получено в данной работе (в том числе воспроизведённый авторами baseline);
  cited — перенесено из другой работы (в подписи/тексте сказано «reported in [12]», «results from Lewis et al.») —
  тогда cited_publication_id = метка ссылки как в статье («[12]», «Lewis et al., 2020»), иначе null. Название baseline
  само по себе НЕ делает число cited.
- extraction_status: extracted; present_not_extractable — значение действительно обозначено, но достоверно извлечь
  его нельзя (ссылка на отсутствующее приложение; raw_value/metric_value = null, source_fragment — дословная ссылка);
  absent — не использовать без необходимости. Не создавай декартово произведение метрик и конфигураций с
  искусственными пустыми значениями: строка — только для реально напечатанного числа.
- duplicate_of — result_id основной записи (в рамках этого вызова), если то же значение того же результата повторно
  встречено в другом месте (в другой таблице/разделе); у основной записи null. Разные числа дублями не объявляй.
- source_page — страница PDF по маркеру <!-- page N -->; source_section — раздел (например «4.2»); source_table —
  «Table N»; source_figure — не заполняй (строки с графиков добавляет программа отдельным проходом); source_fragment:
  для значения из таблицы — «Table N, строка <метка строки как в таблице>» (например «Table 1, строка RA-CoT»),
  для значения из текста — короткая дословная цитата (слова вокруг числа).
- note — уточнение столбца/блока столбцов («column EM, dataset HotpotQA»), нестандартность значения, ссылка на условие.
- result_id в этом проходе — локальные R1, R2, ... (позже будут перенумерованы)."""

RAG_RESULTS_TABLE_TASK = """Заполни results по ОДНОЙ таблице, данной ниже в виде HTML (она может быть частью большой таблицы:
строки с номерами указаны). Каждая числовая ячейка с результатом метрики = отдельная строка results; пропуск
ячеек не допускается. Нечисловые ячейки («-», «N/A») строк не дают. Метки столбцов (метрика, набор данных, @k,
единицы) могут занимать несколько строк заголовка (colspan/rowspan) — сопоставляй столбцы внимательно. Столбцы с
явной разностью/приростом («Δ», «improvement») — отдельная метрика (metric_group «derived», metric_variant разности), не
подменяй ими обычные значения. Значения других работ в таблице (строки со ссылкой) — result_origin=cited.
Для всех строк этой таблицы source_page = страница, указанная в задании, source_table = номер из задания."""

RAG_RESULTS_PROSE_TASK = """Заполни results значениями, которые приводятся в ТЕКСТЕ статьи (аннотация, введение, результаты,
обсуждение, заключение, подписи), а НЕ в таблицах: таблицы в тексте заменены пометками «[ТАБЛИЦА ...]» и обрабатываются
отдельно. Записывай результаты метрик систем/конфигураций (собственные primary и чужие cited, например «outperforms X by 5.2
points», «achieves 63.1 EM on HotpotQA», «reduces latency by 40%»). Числа, не являющиеся результатом метрики
(гиперпараметры, размеры датасетов, годы, номера разделов) НЕ записывай. Если то же значение явно относится к строке
таблицы, всё равно запиши его (повтор определит программа); при этом config_id/dataset_id/experiment_id определи по
смыслу из «Уже извлечено». Относительные приросты («+5.2%», «improves by 3 points») — metric_group «derived», raw_value со
знаком как в статье, normalized_value не пересчитывай. Для значений только в приложениях/на графиках без чисел в тексте —
extraction_status=present_not_extractable только если в тексте прямо названо конкретное значение или таблица, недоступные
в PDF; иначе пропусти."""

RAG_ISSUES_PROMPT = """Прочитай СТАТЬЮ и сверь её с датасетом. Найди проблемы, видимые из текста и чисел статьи, для которых важно
замечание (check_name — короткий код/название правила, открытый список; рекомендуемые: metric_scale, metric_k,
protocol_mismatch, normalization, contradiction):
- contradiction — одно и то же значение/условие расходится в разных местах (аннотация vs таблица; размер выборки; модель);
- metric_scale — шкала метрики не названа/смешана (доли и проценты, значение вне диапазона метрики, «%» у абсолютной
  разности — percentage points vs relative percent);
- metric_k — k метрики путается с top_k конфигурации или не указан;
- protocol_mismatch — значения нельзя сравнивать: разные протоколы, наборы, разбиения, модели оценки, перенос чисел baseline из
  других работ с другим протоколом;
- normalization — округление/пересчёт/арифметика не сходятся (суммы, разности, «improves by» не совпадает со строками);
- ambiguity — из текста нельзя однозначно понять модель/версию/выборку/агрегацию;
- figure_only — НЕ создавай: такие замечания добавляет программа для графиков, которые не удалось оцифровать. [faktura-patch]
Каждая запись — ОДНА фраза-описание ПО-АНГЛИЙСКИ с конкретикой (числа и условия с обеих сторон). record_ids — result_id из
датасета через «;», если замечание про конкретные результаты, иначе null. source_page — страница по маркеру
<!-- page N -->; source_fragment — короткая дословная цитата. Если проблем нет — {"issues": []}. Не больше 12;
только то, что реально есть в статье."""

_RAG_TABLE_RX = re.compile(r"<table\b.*?</table>", re.S | re.I)
_RAG_TR_RX = re.compile(r"<tr\b[^>]*>.*?</tr>", re.S | re.I)
_RAG_CELL_RX = re.compile(r"<t([dh])\b[^>]*>(.*?)</t\1>", re.S | re.I)
_RAG_MARK_RX = re.compile(r"<!-- page (\d+) -->")
_RAG_NUMCELL_RX = re.compile(r"[\s$\\{}^_~≈<>±+\-−–.,:;%()\d/×*†‡§‖]*")


def _rag_cell_texts(tr_html: str) -> list[str]:
    import html as _h
    return [_h.unescape(re.sub(r"<[^>]+>", "", c)).strip() for _, c in _RAG_CELL_RX.findall(tr_html)]


def _rag_is_num(cell: str) -> bool:
    c = re.sub(r"\\[A-Za-z]+", "", cell or "")
    return bool(re.search(r"\d", c)) and bool(_RAG_NUMCELL_RX.fullmatch(c)) and len(c) <= 40


def _rag_row_numcells(tr_html: str) -> int:
    return sum(1 for c in _rag_cell_texts(tr_html) if _rag_is_num(c))


def rag_split_table(table_html: str, max_cells: int = RAG_TABLE_CELLS) -> list[dict]:
    """Разбивает HTML-таблицу на куски по группам строк так, чтобы в куске было <= max_cells числовых ячеек.
    Строки заголовка (ведущие строки без числовых ячеек, до 3) повторяются в каждом куске. Возвращает список
    {header: [tr...], rows: [tr...], lo, hi, total, cells}; lo/hi — номера строк данных (с 1)."""
    trs = _RAG_TR_RX.findall(table_html)
    if not trs:
        return [{"header": [], "rows": [], "lo": 1, "hi": 1, "total": 0, "cells": 0, "raw": table_html}]
    h = 0
    while h < min(3, len(trs) - 1) and _rag_row_numcells(trs[h]) == 0:
        h += 1
    header, body = trs[:h], trs[h:]
    counts = [_rag_row_numcells(t) for t in body]
    if sum(counts) <= max_cells or len(body) <= 1:
        return [{"header": header, "rows": body, "lo": 1, "hi": len(body), "total": len(body), "cells": sum(counts)}]
    chunks, cur, cur_n, lo = [], [], 0, 1
    for i, (t, n) in enumerate(zip(body, counts), start=1):
        if cur and cur_n + n > max_cells:
            chunks.append({"header": header, "rows": cur, "lo": lo, "hi": i - 1, "total": len(body), "cells": cur_n})
            cur, cur_n, lo = [], 0, i
        cur.append(t)
        cur_n += n
    if cur:
        chunks.append({"header": header, "rows": cur, "lo": lo, "hi": len(body), "total": len(body), "cells": cur_n})
    return chunks


def _rag_chunk_html(ch: dict) -> str:
    if ch.get("raw"):
        return ch["raw"]
    return "<table>\n" + "\n".join(ch["header"] + ch["rows"]) + "\n</table>"


def rag_plan_results(text: str, max_cells: int = RAG_TABLE_CELLS):
    """Markdown статьи -> (куски таблиц для вызовов results, текст без таблиц для «прозаического» вызова).
    Кусок: {label, page, caption, before, after, header, rows, lo, hi, total, cells}."""
    marks = [(m.start(), int(m.group(1))) for m in _RAG_MARK_RX.finditer(text)]
    tables = list(_RAG_TABLE_RX.finditer(text))
    cap_rx = re.compile(r"(?:^|\n)[#*_\s]*((?:Table|TABLE)\s*[A-Z]?\d+[A-Za-z]?[^\n]{0,250})")
    chunks, prose, last = [], [], 0
    for i, tm in enumerate(tables):
        s, e = tm.start(), tm.end()
        page = max([p for pos, p in marks if pos < s] or [1])
        prev_end = tables[i - 1].end() if i else 0
        next_start = tables[i + 1].start() if i + 1 < len(tables) else len(text)
        before = text[max(prev_end, s - 1500):s].strip()
        after = text[e:min(next_start, e + 800)].strip()
        found = cap_rx.findall(before[-700:])
        caption = found[-1].strip() if found else ""
        if not caption:
            fa = cap_rx.findall("\n" + after[:400])
            caption = fa[0].strip() if fa else ""
        lm = re.match(r"(?i)table\s*([A-Z]?\d+[A-Za-z]?)", caption)
        label = f"Table {lm.group(1)}" if lm else None
        for ch in rag_split_table(tm.group(0), max_cells):
            ch.update({"label": label, "page": page, "caption": caption, "before": before, "after": after,
                       "index": len(chunks)})
            chunks.append(ch)
        prose.append(text[last:s])
        prose.append(f"[ТАБЛИЦА {label or '?'} (с. {page}) — значения извлекаются отдельно, здесь не нужны]")
        last = e
    prose.append(text[last:])
    return chunks, "".join(prose)


def _rag_split_chunk(ch: dict):
    """Пополам по строкам данных (повтор после усечения ответа). [faktura-patch] Склейка — пополам по таблицам."""
    if ch.get("batch"):
        b = ch["batch"]
        if len(b) < 2:
            return None
        m = len(b) // 2

        def pack(part):
            if len(part) == 1:
                return part[0]
            return dict(ch, batch=part, index=part[0]["index"], page=part[0]["page"],
                        cells=sum(x["cells"] for x in part))
        return pack(b[:m]), pack(b[m:])
    n = len(ch["rows"])
    if n <= 1 or ch.get("raw"):
        return None
    m = n // 2
    a = dict(ch, rows=ch["rows"][:m], hi=ch["lo"] + m - 1)
    b = dict(ch, rows=ch["rows"][m:], lo=ch["lo"] + m)
    for x in (a, b):
        x["cells"] = sum(_rag_row_numcells(t) for t in x["rows"])
    return a, b


def _rag_compact(state: dict, with_pubs=False) -> dict:
    def slim(r, note=160):
        out = {k: v for k, v in r.items() if v is not None and k != "publication_id"}
        if isinstance(out.get("note"), str) and len(out["note"]) > note:
            out["note"] = out["note"][:note] + "…"
        return out
    keys = ("configurations", "datasets", "tasks", "experiments")
    d = {k: [slim(r) for r in state.get(k, [])] for k in keys}
    if with_pubs:
        d = {"publications": state.get("publications", []), **d}
    return d


def _rag_num(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("−", "-").replace(",", ".")
    if re.fullmatch(r"-?\d*\.?\d+", s):
        return float(s)
    return None


def fix_rag_results(rows):
    """Детерминированные поправки: origin/status по умолчанию, metric_value == normalized_value, пустые строки."""
    for r in rows:
        if not r.get("result_origin"):
            r["result_origin"] = "cited" if r.get("cited_publication_id") else "primary"
        if not r.get("extraction_status"):
            r["extraction_status"] = "extracted"
        if r.get("normalized_value") is None and r.get("metric_value") is not None:
            r["normalized_value"] = r["metric_value"]
        elif r.get("metric_value") is None and r.get("normalized_value") is not None:
            r["metric_value"] = r["normalized_value"]
        if r.get("normalized_unit") is None and r.get("metric_unit") is not None:
            r["normalized_unit"] = r["metric_unit"]
        elif r.get("metric_unit") is None and r.get("normalized_unit") is not None:
            r["metric_unit"] = r["normalized_unit"]
        if r.get("raw_value") is not None and not isinstance(r["raw_value"], str):
            r["raw_value"] = str(r["raw_value"])


def rag_renumber(rows, id_prefix="R"):
    """rows: строки с временными result_id (уникальными), duplicate_of — на временные id. Перенумеровывает R001..
    в порядке списка; ссылки duplicate_of переписываются; висячие/на себя -> None. Возвращает карту старый->новый."""
    mapping = {}
    for i, r in enumerate(rows, start=1):
        mapping[r["result_id"]] = f"{id_prefix}{i:03d}"
    for r in rows:
        d = r.get("duplicate_of")
        new = mapping.get(d) if d is not None else None
        r["result_id"] = mapping[r["result_id"]]
        r["duplicate_of"] = new if new != r["result_id"] else None
    return mapping


_UNIT_PAREN_RX = re.compile(r"\s*[\(\[]\s*(%|percent(age)?|ms|s|sec|seconds|min|MiB|MB|GB|KB|tokens?|x|×)\s*[\)\]]\s*$", re.I)


def _ds_names(ds: dict) -> list[str]:
    """dataset name + alias in parentheses + name without the parenthesised part (len >= 3)."""
    n = (ds.get("dataset_name") or "").strip()
    out = {n}
    for m in re.finditer(r"\(([^()]{3,40})\)", n):
        out.add(m.group(1).strip())
    out.add(re.sub(r"\s*\([^()]*\)", "", n).strip())
    return sorted((x for x in out if len(x) >= 3), key=len, reverse=True)


def rag_normalize_metric_names(rows: list, datasets: list) -> int:
    """[faktura-patch] metric_name = the metric itself. Generic, from this paper's own datasets table:
    "QuaLITY Accuracy" / "Accuracy on QuaLITY" -> "Accuracy" (+ dataset_id when it was empty);
    trailing unit in brackets "Accuracy (%)" -> "Accuracy"; "NDCG@5" without metric_k -> metric_k=5."""
    names = [(ds.get("dataset_id"), n) for ds in datasets for n in _ds_names(ds)]
    names.sort(key=lambda t: len(t[1]), reverse=True)
    changed = 0
    for r in rows:
        name = (r.get("metric_name") or "").strip()
        if not name:
            continue
        new = _UNIT_PAREN_RX.sub("", name).strip() or name
        for dsid, dn in names:
            e = re.escape(dn)
            m = (re.match(rf"(?i)^{e}[\s:\-–—/]+(.+)$", new)
                 or re.match(rf"(?i)^(.+?)\s+(?:on|for|in|@)\s+{e}$", new)
                 or re.match(rf"(?i)^(.+?)[\s\-–—/]+{e}$", new)
                 or re.match(rf"(?i)^(.+?)\s*\(\s*{e}\s*\)$", new))
            if m and re.search(r"[A-Za-z]", m.group(1)) and len(m.group(1).strip()) >= 2:
                new = m.group(1).strip()
                if r.get("dataset_id") is None and dsid:
                    r["dataset_id"] = dsid
                break
        mk = re.search(r"@\s*(\d+)\b", new)
        if mk and r.get("metric_k") is None:
            r["metric_k"] = int(mk.group(1))
        if new != name:
            r["metric_name"] = new
            changed += 1
    return changed


_SWEEP_FIELDS = [  # swept parameter name -> configuration column it sets (number)
    (re.compile(r"(?i)top[\s_-]*k|retrieved (?:doc|passage|chunk)|number of (?:retrieved )?(?:doc|passage|chunk|context)"), "top_k"),
    (re.compile(r"(?i)chunk[\s_-]*size"), "chunk_size"),
    (re.compile(r"(?i)chunk[\s_-]*overlap|overlap"), "chunk_overlap"),
]


def rag_sweep_configs(rows: list, configs: list) -> int:
    """[faktura-patch] A figure that sweeps a setting (training tokens, threshold, top-k, noise level...) reports one
    configuration per swept value: copy of the series' base configuration (or a minimal one named after the series)
    + "<parameter>=<value>" in note (and top_k / chunk_size when that is the parameter). Rows of the same point
    (value and its band endpoints) share the configuration. Returns the number of configurations added."""
    def cid_num(c):
        m = re.search(r"(\d+)$", c or "")
        return int(m.group(1)) if m else 0
    by_id = {c.get("config_id"): c for c in configs}
    nxt = max([cid_num(c.get("config_id")) for c in configs] + [0]) + 1
    made: dict = {}
    for r in rows:
        sw = r.pop("_sweep", None)
        if not sw:
            continue
        key = (sw["base"], sw["series"] if not sw["base"] else None, sw["figure"], sw["name"], sw["value"])
        if key not in made:
            base = by_id.get(sw["base"])
            new = {k: v for k, v in (base or {}).items()} if base else {"rag_variant": sw["series"]}
            new["config_id"] = f"C{nxt}"
            nxt += 1
            for rx, col in _SWEEP_FIELDS:
                if rx.search(sw["name"] or "") and isinstance(sw["raw"], (int, float)):
                    new[col] = int(round(sw["raw"])) if abs(sw["raw"] - round(sw["raw"])) < 1e-6 else sw["raw"]
                    break
            tag = f"{sw['name']}={sw['value']} ({sw['figure']}, series '{sw['series']}')"
            new["note"] = f"{new['note']}; {tag}" if new.get("note") else tag
            configs.append(new)
            made[key] = new["config_id"]
        r["config_id"] = made[key]
    return len(made)


def rag_link_duplicates(rows) -> int:
    """Одно и то же значение (эксперимент, конфигурация, набор, задача, метрика, вариант, k, значение), найденное в РАЗНЫХ
    местах (страница/таблица), — повтор: у более позднего duplicate_of -> основная запись (предпочтительно из таблицы).
    Не трогает строки, у которых duplicate_of уже задан моделью. Возвращает число проставленных ссылок."""
    groups = {}
    for r in rows:
        v = _rag_num(r.get("normalized_value") if r.get("normalized_value") is not None else r.get("raw_value"))
        if v is None or not r.get("config_id") or not r.get("metric_name") or r.get("extraction_status") not in (None, "extracted"):
            continue
        key = (r.get("experiment_id"), r.get("config_id"), r.get("dataset_id"), r.get("task_id"),
               re.sub(r"\W+", "", str(r.get("metric_name")).lower()), (r.get("metric_variant") or "").lower(),
               r.get("metric_k"), round(v, 6))
        groups.setdefault(key, []).append(r)
    n = 0
    for grp in groups.values():
        if len(grp) < 2:
            continue
        base = sorted(grp, key=lambda r: (0 if r.get("source_table") else 1))[0]
        while base.get("duplicate_of") is not None:  # основная — конечная в цепочке
            nxt = next((x for x in rows if x["result_id"] == base["duplicate_of"]), None)
            if nxt is None or nxt is base:
                break
            base = nxt
        for r in grp:
            if r is base or r.get("duplicate_of") is not None:
                continue
            if (r.get("source_page"), r.get("source_table")) == (base.get("source_page"), base.get("source_table")):
                continue
            r["duplicate_of"] = base["result_id"]
            n += 1
    return n


def _extract_rag(*, src, text, pub_id, client, args, max_tokens, issues_max_tokens, no_issues, out, meta_path,
                 pdf_path, model, provider, report) -> dict:
    """Конвейер области RAG: publications ‖ configurations ‖ datasets ‖ tasks -> experiments -> results (кусками) ->
    проверка значений -> issues. [faktura-patch] Графики: оцифровка из векторной геометрии PDF (figures.py) с оценкой
    состояния (meta.extraction_state), запасной вариант — vision-модель (RAG_VISION_MODEL) или issue figure_only."""
    from concurrent.futures import ThreadPoolExecutor
    state: dict = {t: [] for t in RAG_TABLES}
    failed: list[str] = []

    def step_user(table: str) -> str:
        return (f"publication_id = {pub_id}\n\n"
                f"Текст статьи (Markdown):\n{text}\n\n"
                f"Уже извлечено (JSON):\n{json.dumps(_rag_compact(state, True), ensure_ascii=False, separators=(',', ':'))}\n\n"
                f"Задание ({table}):\n{RAG_STEP_PROMPTS[table]}")

    def run_step(step):
        messages = [{"role": "system", "content": RAG_COMMON_RULES},
                    {"role": "user", "content": step_user(step)}]
        raw_path = src.with_name(f"{src.name}.{step}.raw_response.txt")
        try:
            result = nullify_empties(call_llm(client, args, messages, f"table_{step}", rag_table_schema(step),
                                              max_tokens, step, raw_path, report))
            rows = result.get(step, []) if isinstance(result, dict) else []
            for r in rows:
                r["publication_id"] = pub_id  # [faktura-patch] всегда id из запроса (модель пишет «.»)
            return step, rows, None
        except LLMError as e:
            return step, None, e

    def apply(step, rows, err):
        if err is not None:
            print(f"[!] {err}; таблица {step} будет пустой, продолжаю.", flush=True)
            failed.append(step)
        else:
            state[step] = rows

    def out_of_scope():
        return bool(state["publications"]) and state["publications"][0].get("in_scope") is False

    fig_on = pdf_path is not None and os.environ.get("RAG_FIGURES", "1").strip() != "0"  # [faktura-patch]
    rag_meta = {"domain": "rag", "figures_skipped": not fig_on, "chunks": [], "timings": {}}
    fig_box: dict = {"asm": None, "error": None, "issues": [], "stats": None}

    def run_chunk(ch, prose=False, depth=0):
        """Один вызов results -> (список строк с локальными id, ошибка | None). При обрезке ответа — деление пополам."""
        if prose:
            label = "results:prose"
            task = RAG_RESULTS_PROSE_TASK
            body = f"Текст статьи (Markdown; таблицы заменены пометками):\n{ch['text']}\n\n"
        elif ch.get("batch"):  # [faktura-patch] несколько мелких таблиц одним вызовом
            label = "results:batch#" + "+".join(f"{c['label'] or 'table'}" for c in ch["batch"])
            task = RAG_RESULTS_TABLE_TASK + ("\nВ этом задании НЕСКОЛЬКО таблиц: для каждой строки results укажи source_table и "
                                             "source_page той таблицы, из которой взято значение (как в заголовке таблицы ниже).")
            body = "".join(f"=== Таблица: {c['label'] or 'номер не определён'}; страница PDF (source_page) = {c['page']}\n"
                           f"Подпись: {c['caption'] or 'не найдена'}\nТекст перед таблицей:\n{c['before'][-600:]}\n"
                           f"Текст после таблицы:\n{c['after'][:300]}\nHTML таблицы:\n{_rag_chunk_html(c)}\n\n"
                           for c in ch["batch"])
        else:
            rng = f"{ch['lo']}-{ch['hi']}"
            label = f"results:{ch['label'] or 'table'}#{ch['index']}:{rng}"
            task = RAG_RESULTS_TABLE_TASK
            part = "" if ch["lo"] == 1 and ch["hi"] == ch["total"] else f" (ЧАСТЬ таблицы: строки данных {rng} из {ch['total']}; строки заголовка повторены)"
            body = (f"Таблица: {ch['label'] or 'номер не определён (source_table = null)'}; страница PDF (source_page) = {ch['page']}{part}\n"
                    f"Подпись: {ch['caption'] or 'не найдена'}\n\n"
                    f"Текст перед таблицей:\n{ch['before']}\n\nТекст после таблицы:\n{ch['after']}\n\n"
                    f"HTML таблицы:\n{_rag_chunk_html(ch)}\n\n")
        user = (f"publication_id = {pub_id}\n\n{body}"
                f"Уже извлечено (JSON):\n{json.dumps(_rag_compact(state), ensure_ascii=False, separators=(',', ':'))}\n\n"
                f"Задание (results):\n{task}\n\n{RAG_RESULT_FIELDS}")
        messages = [{"role": "system", "content": RAG_COMMON_RULES}, {"role": "user", "content": user}]
        raw_path = src.with_name(f"{src.name}.{re.sub(r'[^A-Za-z0-9_.-]+', '_', label)}.raw_response.txt")
        try:
            rt = None if (prose or RAG_TABLE_REASONING) else 0  # 0 = рассуждение выключено
            res = nullify_empties(call_llm(client, args, messages, "table_results", rag_table_schema("results"),
                                           max(max_tokens, RAG_RESULTS_MAX_TOKENS), label, raw_path, report,
                                           reasoning_tokens=rt))
            rows = res.get("results", []) if isinstance(res, dict) else []
            pages_by_label = {c["label"]: c["page"] for c in ch.get("batch", []) if c.get("label")}
            for r in rows:
                r["publication_id"] = pub_id  # [faktura-patch] всегда id из запроса (модель пишет «.»)
                if ch.get("batch"):  # склейка: страница — по таблице, которую указала модель
                    if r.get("source_page") is None and r.get("source_table") in pages_by_label:
                        r["source_page"] = pages_by_label[r["source_table"]]
                elif not prose:  # детерминированно: страница и номер таблицы известны
                    if r.get("source_page") is None:
                        r["source_page"] = ch["page"]
                    if r.get("source_table") is None and ch["label"]:
                        r["source_table"] = ch["label"]
            return rows, None
        except LLMError as e:
            halves = None if prose or depth >= 2 else _rag_split_chunk(ch)
            if halves is None:
                return None, e
            print(f"[{label}] делю кусок пополам после сбоя: {e}", flush=True)
            a, ea = run_chunk(halves[0], depth=depth + 1)
            b, eb = run_chunk(halves[1], depth=depth + 1)
            if a is None and b is None:
                return None, ea
            return (a or []) + (b or []), None

    def run_results():
        chunks, prose_text = rag_plan_results(text)
        # [faktura-patch] экономия: таблицы без чисел — без вызова; мелкие целые таблицы — склейкой
        for c in [c for c in chunks if not c.get("cells")]:
            rag_meta["chunks"].append({"label": f"{c['label'] or 'table'}#{c['index']}", "cells": 0, "results": 0,
                                       "skipped": "no_numeric_cells"})
        chunks = [c for c in chunks if c.get("cells")]
        small = [c for c in chunks if c["cells"] <= RAG_SMALL_TABLE_CELLS and c["lo"] == 1 and c["hi"] == c["total"]]
        big = [c for c in chunks if c not in small]
        batches, cur = [], []
        for c in small:
            if cur and sum(x["cells"] for x in cur) + c["cells"] > RAG_BATCH_CELLS:
                batches.append(cur)
                cur = []
            cur.append(c)
        if cur:
            batches.append(cur)
        units = big + [b[0] if len(b) == 1 else {"batch": b, "index": b[0]["index"], "label": None, "page": b[0]["page"],
                                                 "lo": 1, "hi": 1, "total": 1, "rows": [],
                                                 "cells": sum(x["cells"] for x in b)} for b in batches]
        jobs = [(ch, False) for ch in units] + [({"text": prose_text}, True)]
        with ThreadPoolExecutor(max_workers=RAG_WORKERS) as pool:
            futs = [pool.submit(run_chunk, ch, p) for ch, p in jobs]
            done = [f.result() for f in futs]
        merged, nrows = [], 0
        for ji, ((ch, is_prose), (rows, err)) in enumerate(zip(jobs, done)):
            label = "prose" if is_prose else (
                "batch:" + "+".join(c["label"] or "table" for c in ch["batch"]) if ch.get("batch")
                else f"{ch['label'] or 'table'}#{ch['index']}:{ch['lo']}-{ch['hi']}")
            if err is not None:
                print(f"[!] results {label}: {err}", flush=True)
                failed.append(f"results[{label}]")
                rag_meta["chunks"].append({"label": label, "cells": None if is_prose else ch["cells"], "error": str(err)[:200]})
                continue
            rows = [r for r in rows if isinstance(r, dict)]
            local = {}
            for k, r in enumerate(rows, start=1):  # локальные id -> глобально уникальные временные
                rid = r.get("result_id") or f"R{k}"
                tmp = f"c{ji}:{rid}"
                while tmp in local.values():
                    tmp += "'"
                local[rid] = tmp
                r["result_id"] = tmp
                r["_local_dup"] = r.get("duplicate_of")
            for r in rows:
                d = r.pop("_local_dup")
                r["duplicate_of"] = local.get(d) if d is not None else None
            fix_rag_results(rows)
            merged.extend(rows)
            rag_meta["chunks"].append({"label": label, "cells": None if is_prose else ch["cells"], "results": len(rows)})
            nrows += len(rows)
        return merged

    def fig_phase1():
        """[faktura-patch] оценка состояния страниц/графиков + запасной вариант (vision): не зависит от LLM-таблиц."""
        t0 = time.time()
        try:
            import figures as F
            asm = F.assess_document(pdf_path, text)
            vm = os.environ.get("RAG_VISION_MODEL", "").strip() or None
            F.run_fallbacks(asm, pdf_path, client if vm else None, vm)
            fig_box["asm"] = asm
        except Exception as e:  # noqa: BLE001 — графики не роняют прогон
            fig_box["error"] = f"{type(e).__name__}: {e}"[:300]
            print(f"[!] графики: {fig_box['error']}", flush=True)
        rag_meta["timings"]["figures_assess"] = round(time.time() - t0, 2)

    def fig_phase2():
        """[faktura-patch] семантическое сопоставление серий (1 вызов LLM на график) -> строки results."""
        phase1.result()
        asm = fig_box["asm"]
        if asm is None:
            return []
        import figures as F
        ctx = json.dumps(_rag_compact(state), ensure_ascii=False, separators=(",", ":"))
        ids = {"configs": {r.get("config_id") for r in state["configurations"]},
               "datasets": {r.get("dataset_id") for r in state["datasets"]},
               "tasks": {r.get("task_id") for r in state["tasks"]},
               "experiments": {r.get("experiment_id") for r in state["experiments"]}}

        def llm(messages, schema_name, schema, label):
            raw = src.with_name(f"{src.name}.{re.sub(r'[^A-Za-z0-9_.-]+', '_', label)}.raw_response.txt")
            return nullify_empties(call_llm(client, args, messages, schema_name, schema, 8192, label, raw, report))

        rows, issues, st = F.map_and_generate(asm, pub_id, ctx, ids, llm, workers=4)
        fig_box["issues"], fig_box["stats"] = issues, st
        rag_meta["timings"]["figures_map"] = st["seconds"]
        return rows

    def quality(rows):
        """verify_values: значение должно встречаться в тексте PDF или OCR-markdown; дозаполнения (mining) для RAG нет."""
        st = {"step": "quality"}
        if pdf_path is None or not rows:
            return rows
        try:
            from pipeline import _page_texts, verify_values
            keep, vst = verify_values(rows, pdf_path, _page_texts(pdf_path, cut_refs=False), md=text, leading_zero=True)
            st["verify"] = vst
            rows = keep
        except Exception as e:  # noqa: BLE001 — улучшение качества не роняет прогон
            st["error"] = f"{type(e).__name__}: {e}"[:200]
        print(f"      качество: {st}", flush=True)
        report.append(st)
        return rows

    fig_ex = ThreadPoolExecutor(max_workers=2)
    phase1 = fig_ex.submit(fig_phase1) if fig_on else None
    with ThreadPoolExecutor(max_workers=RAG_WORKERS) as ex:
        futs = {s: ex.submit(run_step, s) for s in ("publications", "configurations", "datasets", "tasks")}
        apply(*futs["publications"].result())
        rest = {s: futs[s].result() for s in ("configurations", "datasets", "tasks")}
        if out_of_scope():
            print("[i] статья вне области (in_scope=false) — пропускаю остальные таблицы", flush=True)
        else:
            for s in ("configurations", "datasets", "tasks"):
                apply(*rest[s])
            apply(*run_step("experiments"))
            phase2 = fig_ex.submit(fig_phase2) if fig_on else None
            rows = quality(run_results())
            if phase2 is not None:  # [faktura-patch] строки графиков — после таблиц/текста, со связью дублей
                try:
                    import figures as F
                    rag_meta["figures_merged_duplicates"] = F.merge_figure_rows(rows, phase2.result())
                except Exception as e:  # noqa: BLE001
                    fig_box["error"] = fig_box["error"] or f"{type(e).__name__}: {e}"[:300]
                    print(f"[!] графики (строки): {fig_box['error']}", flush=True)
            # [faktura-patch] swept settings on figures -> own configurations (after duplicate linking with tables)
            rag_meta["sweep_configs_added"] = rag_sweep_configs(rows, state["configurations"])
            # перенумерация R001.. + ссылки; id вне извлечённых таблиц -> null
            rag_renumber(rows)
            ids = {t: {r.get(RAG_KEYS[t]) for r in state[t]} for t in ("experiments", "configurations", "datasets", "tasks")}
            nfix = 0
            for r in rows:
                for col, tab in (("experiment_id", "experiments"), ("config_id", "configurations"),
                                 ("dataset_id", "datasets"), ("task_id", "tasks")):
                    if r.get(col) is not None and r[col] not in ids[tab]:
                        r[col] = None
                        nfix += 1
            rag_meta["ref_nullified"] = nfix
            rag_meta["metric_names_normalized"] = rag_normalize_metric_names(rows, state["datasets"])
            rag_meta["duplicates_linked"] = rag_link_duplicates(rows)
            state["results"] = rows

    if phase1 is not None:
        phase1.result()
    fig_ex.shutdown(wait=True)

    if not no_issues and not out_of_scope():
        compact = _rag_compact(state)
        compact["results"] = [{k: r.get(k) for k in ("result_id", "experiment_id", "config_id", "dataset_id", "metric_name",
                                                     "metric_variant", "metric_k", "raw_value", "raw_unit", "result_origin",
                                                     "source_page", "source_table", "duplicate_of")}
                              for r in state["results"][:1500]]
        issues_user = (f"publication_id = {pub_id}\n\nТекст статьи (Markdown):\n{text}\n\n"
                       f"Датасет (JSON):\n{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}")
        messages = [{"role": "system", "content": RAG_COMMON_RULES + "\n" + RAG_ISSUES_PROMPT},
                    {"role": "user", "content": issues_user}]
        raw_path = src.with_name(f"{src.name}.issues.raw_response.txt")
        try:
            result = nullify_empties(call_llm(client, args, messages, "table_issues", rag_table_schema("issues"),
                                              issues_max_tokens, "issues", raw_path, report, retry=True,
                                              reasoning_tokens=RAG_ISSUES_REASONING))
            issues = result.get("issues", []) if isinstance(result, dict) else []
            for r in issues:
                r["publication_id"] = pub_id  # [faktura-patch] всегда id из запроса (модель пишет «.»)
            state["issues"] = issues
        except LLMError as e:
            print(f"[!] issues не получены: {e}\n    Основной датасет сохраню без issues.", flush=True)
            failed.append("issues")

    if fig_box["issues"] and not out_of_scope():  # [faktura-patch] figure_only — программные issues
        state["issues"] = state["issues"] + fig_box["issues"]
        for i, r in enumerate(state["issues"], start=1):
            r["issue_id"] = f"I{i}"
    extraction_state = None
    if fig_box["asm"] is not None:
        import figures as F
        extraction_state = F.extraction_state_meta(fig_box["asm"], {"error": fig_box["error"],
                                                                    "mapping": fig_box["stats"]})
    elif fig_on:
        extraction_state = {"error": fig_box["error"], "pages": [], "figures": []}
    if fig_box["asm"] is not None:
        rag_meta["figures_skipped"] = False

    dataset = {k: state[k] for k in RAG_TABLES}
    out_path = Path(out) if out else src.with_name(src.stem + ".dataset.json")
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {k: len(v) for k, v in dataset.items()}
    meta_out = None
    if meta_path:
        meta_out = Path(meta_path)
        meta = {"input_md": str(src), "publication_id": pub_id, "model": model,
                "provider": [p.strip() for p in provider.split(",") if p.strip()] or "auto",
                "steps": report, "failed_tables": failed, "counts": counts,
                "domain": "rag", "figures_skipped": rag_meta["figures_skipped"], "rag": rag_meta,
                "extraction_state": extraction_state}
        meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nСтрок по таблицам:", json.dumps(counts, ensure_ascii=False), flush=True)
    if failed:
        print("[!] таблицы/куски, которые НЕ удалось извлечь:", ", ".join(failed), flush=True)
    problems = check_dataset_rag(dataset)
    if problems:
        print(f"Замечания валидатора ({len(problems)}):", flush=True)
        for p in problems[:50]:
            print("   -", p, flush=True)
    else:
        print("Валидация: замечаний нет", flush=True)
    print(f"\nСохранено: {out_path}", flush=True)
    return {"dataset": dataset, "counts": counts, "failed_tables": failed, "problems": problems,
            "out_path": str(out_path), "meta_path": str(meta_out) if meta_out else None, "publication_id": pub_id}


def extract(md_path, *, publication_id=None, out=None, model=DEFAULT_MODEL,
            provider="wafer", no_strict=False, max_chars=200000,
            max_tokens=24576, issues_max_tokens=16384, no_issues=False,  # [faktura-patch] модель рассуждает: 2048 не хватало
            issues_with_text=False, meta_path=None, api_key=None, pdf_path=None,
            domain="solar") -> dict:  # [faktura-patch] domain: solar (по умолчанию) | rag
    """Извлекает датасет из markdown-статьи.

    Возвращает dict: dataset, counts, failed_tables, problems, out_path,
    meta_path, publication_id. Ненулевой код возврата не формирует — ошибки
    приходят исключениями (LLMError / FileNotFoundError).
    """
    if domain not in DOMAINS:  # [faktura-patch]
        raise ValueError(f"domain_not_supported: {domain!r}")
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

    if domain == "rag":  # [faktura-patch] область RAG — отдельный конвейер (см. _extract_rag)
        return _extract_rag(src=src, text=text, pub_id=pub_id, client=client, args=args, max_tokens=max_tokens,
                            issues_max_tokens=issues_max_tokens, no_issues=no_issues, out=out, meta_path=meta_path,
                            pdf_path=pdf_path, model=model, provider=provider, report=report)

    # общий префикс: системная часть + текст статьи (фиксированный, дружит с prompt-cache)
    state: dict = {t: [] for t in TABLES}
    failed: list[str] = []

    def step_user(table: str) -> str:
        return (f"publication_id = {pub_id}\n\n"
                f"Текст статьи (Markdown):\n{text}\n\n"
                f"Уже извлечено (JSON):\n{json.dumps(state, ensure_ascii=False, separators=(',', ':'))}\n\n"
                f"Задание ({table}):\n{STEP_PROMPTS[table]}")

    def run_step(step):
        messages = [{"role": "system", "content": COMMON_RULES},
                    {"role": "user", "content": step_user(step)}]
        raw_path = src.with_name(f"{src.name}.{step}.raw_response.txt")
        try:
            result = nullify_empties(call_llm(client, args, messages, f"table_{step}",
                                              table_schema(step), max_tokens,
                                              step, raw_path, report))
            rows = result.get(step, []) if isinstance(result, dict) else []
            for r in rows:
                r["publication_id"] = pub_id  # [faktura-patch] всегда id из запроса (модель пишет «.»)
            return step, rows, None
        except LLMError as e:
            return step, None, e

    # [faktura-patch] критический путь: publications || materials -> devices -> (layers в фоне) || measurements -> results
    from concurrent.futures import ThreadPoolExecutor

    def apply(step, rows, err):
        if err is not None:
            print(f"[!] {err}; таблица {step} будет пустой, продолжаю.", flush=True)
            failed.append(step)
        else:
            if step == "results":
                fix_result_origin(rows)
            state[step] = rows

    def quality_pass(pdf):
        """[faktura-patch] 1) значения, которых нет в тексте PDF, отбрасываются; страницы правятся;
        2) кандидаты из текста PDF и HTML-таблиц, не попавшие в results, — на дозаполнение (один вызов LLM)."""
        from pipeline import _page_texts, verify_values, mine_candidates, uncovered
        st = {"step": "quality"}
        try:
            pages = _page_texts(pdf)
            keep, vst = verify_values(state["results"], pdf, pages, md=text)
            st["verify"] = vst
            state["results"] = keep
            un = uncovered(mine_candidates(pdf, text, pages), keep)
            st["gap_mentions"] = len(un)
            if un:
                raw_path = src.with_name(f"{src.name}.gapfill.raw_response.txt")
                new = gap_fill(client, args, text, state, un, pub_id, max_tokens, raw_path, report, call_llm,
                               table_schema, COMMON_RULES, STEP_PROMPTS["results"], nullify_empties,
                               fix_result_origin)
                new, nst = verify_values(new, pdf, pages, md=text)
                st["gap_added"], st["gap_verify"] = len(new), nst
                state["results"] = state["results"] + new
        except LLMError as e:
            st["error"] = str(e)[:200]
        except Exception as e:  # noqa: BLE001 — улучшение качества не роняет прогон
            st["error"] = f"{type(e).__name__}: {e}"[:200]
        print(f"      качество: {st}", flush=True)
        report.append(st)

    def out_of_scope():
        return bool(state["publications"]) and state["publications"][0].get("in_scope") is False

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_pub = ex.submit(run_step, "publications")
        f_mat = ex.submit(run_step, "materials")
        apply(*f_pub.result())
        mat = f_mat.result()  # дожидаемся, чтобы не оставлять поток висеть
        if out_of_scope():
            print("[i] статья вне области (in_scope=false) — пропускаю materials и дальше", flush=True)
        else:
            apply(*mat)
            apply(*run_step("devices"))
            f_lay = ex.submit(run_step, "layers")  # layers нужен только issues; results его не использует
            apply(*run_step("measurements"))
            apply(*run_step("results"))
            if pdf_path is not None and state["results"]:
                quality_pass(pdf_path)
            apply(*f_lay.result())

    if not no_issues and (not state["publications"] or state["publications"][0].get("in_scope") is not False):
        # [faktura-patch v4] замечания ищутся по ТЕКСТУ статьи: текст передаётся всегда, датасет — компактно
        compact = {"devices": state.get("devices", []), "measurements": state.get("measurements", []),
                   "results": [{k: r.get(k) for k in ("result_id", "device_id", "measurement_id", "metric_name",
                                                      "raw_value", "raw_unit", "aggregation", "result_origin",
                                                      "source_page", "source_table", "duplicate_of")}
                               for r in state.get("results", [])]}
        issues_user = (f"publication_id = {pub_id}\n\nТекст статьи (Markdown):\n{text}\n\n"
                       f"Датасет (JSON):\n{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}")
        messages = [{"role": "system", "content": COMMON_RULES + "\n" + STEP_PROMPTS["issues"]},
                    {"role": "user", "content": issues_user}]
        raw_path = src.with_name(f"{src.name}.issues.raw_response.txt")
        try:
            result = nullify_empties(call_llm(client, args, messages, "table_issues",
                                              table_schema("issues"), issues_max_tokens,
                                              "issues", raw_path, report,
                                              retry=True))  # [faktura-patch] лёгкий проход
            issues = result.get("issues", []) if isinstance(result, dict) else []
            for r in issues:
                r["publication_id"] = pub_id  # [faktura-patch] всегда id из запроса (модель пишет «.»)
            state["issues"] = issues
        except LLMError as e:
            print(f"[!] issues не получены: {e}\n    Основной датасет сохраню без issues.", flush=True)
            failed.append("issues")

    for k in TABLES:
        state.setdefault(k, [])
    norm_stats = {}
    if SOLAR_NORMALIZE:  # [faktura-patch] deterministic template conventions (see solar_normalize)
        try:
            norm_stats = solar_normalize(state, text)
        except Exception as e:  # noqa: BLE001 — never lose the dataset because of a post-rule
            norm_stats = {"error": f"{type(e).__name__}: {e}"[:200]}
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
            "solar_normalize": norm_stats,
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
    ap.add_argument("--issues-max-tokens", type=int, default=16384)
    ap.add_argument("--no-issues", action="store_true")
    ap.add_argument("--issues-with-text", action="store_true",
                    help="к issues-проходу добавить текст статьи (дороже)")
    ap.add_argument("--domain", default="solar", choices=list(DOMAINS), help="предметная область [solar]")  # [faktura-patch]
    ap.add_argument("--api-key", default=os.environ.get("ROUTERAI_API_KEY"))
    ap.add_argument("--meta", help="куда сохранить метаданные прогона (пошаговые токены/время/finish_reason)")
    args = ap.parse_args()

    try:
        extract(args.input, publication_id=args.publication_id, out=args.out,
                model=args.model, provider=args.provider, no_strict=args.no_strict,
                max_chars=args.max_chars, max_tokens=args.max_tokens,
                issues_max_tokens=args.issues_max_tokens, no_issues=args.no_issues,
                issues_with_text=args.issues_with_text, meta_path=args.meta,
                api_key=args.api_key, domain=args.domain)
    except (LLMError, FileNotFoundError) as e:
        sys.exit(f"Ошибка: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
