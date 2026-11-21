"""Паспорт набора данных (spec/04 §4.17; REQ-114; К-9: все 11 оцениваемых метрик).

Паспорт генерируется из профиля и констант формата сдачи (`faktura.domain.tables`), поэтому колонки, словари
и правила всегда 1:1 с тем, что реально выгружается. Результат детерминирован (без времени генерации).
"""
from __future__ import annotations

from typing import Any

from ..domain.profile import ExtractionProfile
from ..domain.records import NormStatus
from ..domain.tables import (BOOL_FIELDS, CHECK_CATALOG, EVAL_METRICS, ID_COLUMN, INTEGER_FIELDS, LIST_FIELDS,
                             NUMERIC_FIELDS, TABLES, TARGET_UNITS, VOCAB)

__all__ = ["build_passport", "passport_markdown", "PASSPORT_VERSION", "SCHEMA_VERSION", "METRIC_LABELS"]

PASSPORT_VERSION = "1.0"
SCHEMA_VERSION = "2026-11"

SCOPE = ("Тонкоплёночные однопереходные солнечные элементы: перовскит, CIGS, CdTe, кестерит (CZTS/CZTSSe), Sb2Se3, "
         "тонкоплёночный кремний; только экспериментальные работы с собственными приборами; без тандемов "
         "(тандемные и многопереходные элементы исключаются, in_scope=false), без чисто расчётных работ.")
COMPARABILITY = ("Результаты сопоставимы, если совпадают класс материала, тип прибора, спектр AM1.5G при "
                 "100 мВт/см², сопоставимая площадь и её определение, агрегация (champion/mean/median) и направление "
                 "развёртки; сертифицированные результаты и результаты стабильности сравниваются отдельно.")

TABLE_TITLES = {
    "publications": "Публикация", "materials": "Материал поглотителя", "devices": "Прибор",
    "layers": "Слой прибора", "measurements": "Условия измерения", "results": "Результат показателя",
    "issues": "Замечание проверки",
}
TABLE_ROW = {
    "publications": "статья корпуса (в т. ч. исключённая)", "materials": "материал поглотителя",
    "devices": "прибор", "layers": "слой прибора", "measurements": "условия измерения",
    "results": "одно значение одного показателя для одного прибора при одних условиях",
    "issues": "замечание проверки",
}

METRIC_LABELS = {
    "PCE": "КПД", "Voc": "Напряжение холостого хода", "Jsc": "Плотность тока короткого замыкания",
    "FF": "Фактор заполнения", "Rs": "Последовательное сопротивление", "Rsh": "Шунтирующее сопротивление",
    "HI": "Индекс гистерезиса", "Jsc_EQE": "Jsc по интегралу EQE", "stabilized_PCE": "Стабилизированный КПД",
    "T80": "Время до 80 % начального КПД", "retained_PCE": "Сохранённая доля КПД",
    "Voc_deficit": "Дефицит Voc", "Jmpp": "Ток в точке максимальной мощности",
    "Vmpp": "Напряжение в точке максимальной мощности", "AVT": "Средняя прозрачность",
    "CRI": "Индекс цветопередачи", "LUE": "Эффективность использования света",
    "bifaciality_factor": "Коэффициент двусторонности",
}

RULE_FORMULAS = {
    "jv_consistency": "|Voc·Jsc·FF/Pin − PCE| / PCE > порог (3 %), Pin = 100 мВт/см²",
    "voc_limit": "Voc > Eg/q",
    "range": "значение вне физических пределов показателя",
    "eqe_consistency": "|Jsc(EQE) − Jsc(ВАХ)| / Jsc(ВАХ) > порог (5 %)",
    "champion_vs_mean": "mean > champion для одного прибора и условий",
    "hysteresis_index": "HI = (PCE_rev − PCE_fwd) / PCE_rev",
    "voc_deficit_consistency": "Voc_deficit ≠ Eg/q − Voc (с учётом округления)",
    "rounding": "расхождение в пределах округления последнего знака",
}

# (таблица, колонка) -> (описание, источник)
_D: dict[tuple[str, str], tuple[str, str]] = {
    ("publications", "publication_id"): ("Имя PDF-файла без расширения", "сервис разбора"),
    ("publications", "title"): ("Название статьи", "модель"),
    ("publications", "year"): ("Год публикации", "модель"),
    ("publications", "DOI"): ("DOI статьи", "модель"),
    ("publications", "authors"): ("Авторы через «; »", "модель"),
    ("publications", "in_scope"): ("Относится ли статья к предметной области", "модель"),
    ("publications", "exclusion_reason"): ("Почему статья исключена (только при in_scope=false)", "модель"),
    ("materials", "material_id"): ("Идентификатор материала, уникален в статье", "модель"),
    ("materials", "material_name_raw"): ("Название материала как в статье", "модель"),
    ("materials", "formula_normalized"): ("Формула; для перовскита — вид A-B-X с раскрытыми MA, FA, Cs",
                                          "сервис разбора (нормализация)"),
    ("materials", "material_class"): ("Класс материала поглотителя", "модель"),
    ("materials", "dimensionality"): ("Размерность перовскита", "модель"),
    ("materials", "additives"): ("Добавки через «;»", "модель"),
    ("materials", "bandgap_value"): ("Ширина запрещённой зоны", "сервис разбора (нормализация raw от модели)"),
    ("materials", "bandgap_unit"): ("Единица ширины зоны; только при значении", "сервис разбора"),
    ("materials", "bandgap_method"): ("Метод определения ширины зоны", "модель"),
    ("devices", "device_id"): ("Идентификатор прибора, уникален в статье", "модель"),
    ("devices", "absorber_material_id"): ("Ссылка на поглотитель в materials", "модель"),
    ("devices", "device_label"): ("Метка прибора как в статье (по ней сопоставляется оценка)", "модель"),
    ("devices", "device_role"): ("Роль прибора в работе", "модель"),
    ("devices", "device_type"): ("Тип прибора", "модель"),
    ("devices", "architecture"): ("Архитектура", "модель"),
    ("devices", "substrate_type"): ("Тип подложки", "модель"),
    ("devices", "active_area"): ("Активная площадь", "сервис разбора (нормализация raw от модели)"),
    ("devices", "area_unit"): ("Единица площади; только при значении", "сервис разбора"),
    ("devices", "area_definition"): ("Определение площади", "модель"),
    ("devices", "encapsulation"): ("Инкапсулирован ли прибор", "сервис разбора (нормализация ответа модели)"),
    ("layers", "layer_id"): ("Идентификатор слоя, уникален в статье", "модель"),
    ("layers", "device_id"): ("Ссылка на прибор", "модель"),
    ("layers", "layer_order"): ("Порядковый номер слоя от подложки, с 1 (подложка не пишется)", "модель"),
    ("layers", "layer_function"): ("Функция слоя", "сервис разбора (нормализация ответа модели)"),
    ("layers", "layer_material"): ("Материал слоя", "модель"),
    ("layers", "layer_thickness"): ("Толщина слоя", "сервис разбора (нормализация raw от модели)"),
    ("layers", "thickness_unit"): ("Единица толщины; только при значении", "сервис разбора"),
    ("layers", "deposition_method"): ("Метод нанесения", "модель"),
    ("layers", "annealing_temperature"): ("Температура отжига, °C, с единицей", "модель"),
    ("layers", "annealing_time"): ("Время отжига в единицах статьи с явной единицей", "модель"),
    ("layers", "atmosphere"): ("Атмосфера нанесения или отжига", "модель"),
    ("measurements", "measurement_id"): ("Идентификатор измерения, уникален в статье", "модель"),
    ("measurements", "device_id"): ("Приборы измерения через «;»", "модель"),
    ("measurements", "measurement_type"): ("Тип измерения", "модель"),
    ("measurements", "spectrum"): ("Спектр освещения, например AM1.5G", "сервис разбора (нормализация ответа модели)"),
    ("measurements", "light_intensity"): ("Интенсивность освещения как в статье", "модель"),
    ("measurements", "intensity_unit"): ("Единица интенсивности как в статье", "модель"),
    ("measurements", "scan_direction"): ("Направление развёртки ВАХ", "модель"),
    ("measurements", "scan_rate"): ("Скорость развёртки с единицей", "модель"),
    ("measurements", "mask_used"): ("Использовалась ли маска", "модель"),
    ("measurements", "certified"): ("Сертифицировано ли измерение", "модель"),
    ("measurements", "certification_lab"): ("Сертифицирующая лаборатория", "модель"),
    ("measurements", "temperature"): ("Температура измерения", "модель"),
    ("measurements", "stability_protocol"): ("Протокол стабильности (ISOS)", "модель"),
    ("measurements", "stress_conditions"): ("Условия старения", "модель"),
    ("results", "result_id"): ("Идентификатор результата, уникален в статье", "модель"),
    ("results", "device_id"): ("Ссылка на прибор (пусто у cited)", "модель"),
    ("results", "measurement_id"): ("Ссылка на измерение (пусто у cited)", "модель"),
    ("results", "material_id"): ("Ссылка на материал", "модель"),
    ("results", "metric_name"): ("Показатель", "модель"),
    ("results", "metric_value"): ("Значение показателя в канонической единице (= normalized_value)",
                                  "сервис разбора (нормализация raw_value от модели)"),
    ("results", "metric_unit"): ("Каноническая единица (= normalized_unit)", "сервис разбора"),
    ("results", "aggregation"): ("Лучший прибор, среднее или медиана; пусто, если в статье не сказано", "модель"),
    ("results", "spread_value"): ("Разброс (± или стандартное отклонение)", "сервис разбора (нормализация ответа модели)"),
    ("results", "n_devices"): ("Число приборов в статистике", "модель"),
    ("results", "time_point"): ("Момент измерения стабильности", "модель"),
    ("results", "time_unit"): ("Единица времени как в статье", "модель"),
    ("results", "raw_value"): ("Значение дословно как в статье", "модель"),
    ("results", "raw_unit"): ("Единица дословно как в статье", "модель"),
    ("results", "normalized_value"): ("Значение в целевой единице", "сервис разбора (нормализация)"),
    ("results", "normalized_unit"): ("Целевая единица", "сервис разбора (нормализация)"),
    ("results", "result_origin"): ("Собственный результат или из другой работы", "модель"),
    ("results", "cited_publication_id"): ("Ссылка на цитируемую работу как в статье", "модель"),
    ("results", "source_page"): ("Страница PDF-файла, с 1", "модель"),
    ("results", "source_section"): ("Раздел статьи", "модель"),
    ("results", "source_table"): ("Таблица-источник", "модель"),
    ("results", "source_figure"): ("Рисунок-источник", "модель"),
    ("results", "source_fragment"): ("Короткая дословная цитата со страницы source_page", "модель"),
    ("results", "extraction_status"): ("Статус извлечения", "модель"),
    ("results", "duplicate_of"): ("result_id основной записи для повтора внутри статьи", "модель / сервис разбора"),
    ("issues", "issue_id"): ("Номер замечания в выгрузке: I1, I2… по страницам", "сервис разбора"),
    ("issues", "record_ids"): ("result_id записей замечания через «;»", "сервис разбора / модель"),
    ("issues", "check_name"): ("Правило проверки (каталог spec/05 §5.2)", "сервис разбора / модель"),
    ("issues", "description"): ("Что не согласуется: числа с обеих сторон", "сервис разбора / модель"),
    ("issues", "source_page"): ("Где это видно в статье: страница", "сервис разбора / модель"),
    ("issues", "source_fragment"): ("Где это видно в статье: цитата", "сервис разбора / модель"),
}

_LINKS: dict[str, list[tuple[str, str]]] = {
    "publications": [],
    "materials": [("publication_id", "publications.publication_id")],
    "devices": [("publication_id", "publications.publication_id"),
                ("absorber_material_id", "materials.material_id")],
    "layers": [("publication_id", "publications.publication_id"), ("device_id", "devices.device_id")],
    "measurements": [("publication_id", "publications.publication_id"), ("device_id", "devices.device_id")],
    "results": [("publication_id", "publications.publication_id"), ("device_id", "devices.device_id"),
                ("measurement_id", "measurements.measurement_id"), ("material_id", "materials.material_id"),
                ("duplicate_of", "results.result_id")],
    "issues": [("publication_id", "publications.publication_id"), ("record_ids", "results.result_id")],
}

_FIXED_UNITS = {("materials", "bandgap_unit"): ["eV"], ("layers", "thickness_unit"): ["nm"],
                ("devices", "area_unit"): ["cm2"]}
_VALUE_UNITS = {("materials", "bandgap_value"): "eV", ("layers", "layer_thickness"): "nm",
                ("devices", "active_area"): "cm2", ("results", "metric_value"): "по metric_unit",
                ("results", "normalized_value"): "по normalized_unit", ("results", "spread_value"): "по metric_unit",
                ("results", "time_point"): "по time_unit", ("measurements", "light_intensity"): "по intensity_unit"}


def _metrics(profile: ExtractionProfile) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in profile.fields:
        if f.table != "results" or f.column is not None or f.key in seen:
            continue
        seen.add(f.key)
        out.append({"name": f.key, "label": f.label if f.label and f.label != f.key else METRIC_LABELS.get(f.key, f.key),
                    "unit": f.unit or TARGET_UNITS.get(f.key), "evaluated": f.key in EVAL_METRICS,
                    "enabled": f.enabled, "synonyms": list(f.synonyms)})
    for m in EVAL_METRICS:  # К-9: все 11 оцениваемых метрик, даже если профиль их выключил
        if m not in seen:
            out.append({"name": m, "label": METRIC_LABELS.get(m, m), "unit": TARGET_UNITS.get(m), "evaluated": True,
                        "enabled": False, "synonyms": []})
    return out


def _field(table: str, col: str, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    key = (table, col)
    desc, source = _D.get(key, ("Свободное примечание: исходная формулировка, если она не ложится в словарь", "модель")
                          if col == "note" else ("", "модель"))
    if col == "publication_id" and table != "publications":
        desc, source = "Ссылка на публикацию", "сервис разбора"
    f: dict[str, Any] = {"name": col}
    if key in VOCAB:
        f["type"] = "enum"
        f["values"] = sorted(VOCAB[key]) if key in BOOL_FIELDS else _vocab_order(key)
    elif col == "metric_name" and table == "results":
        f["type"] = "enum"
        f["values"] = [m["name"] for m in metrics]
        f["evaluated_values"] = list(EVAL_METRICS)
        f["open"] = True  # нестандартные метрики допускаются, в оценку не идут
    elif key in _FIXED_UNITS:
        f["type"] = "enum"
        f["values"] = _FIXED_UNITS[key]
    elif key in BOOL_FIELDS:
        f["type"] = "boolean"
        f["values"] = ["true", "false"]
    elif key in NUMERIC_FIELDS:
        f["type"] = "integer" if key in INTEGER_FIELDS else "number"
    elif key in LIST_FIELDS:
        f["type"] = "list"
        f["separator"] = LIST_FIELDS[key]
    else:
        f["type"] = "string"
    f["required"] = col == "publication_id" or col == ID_COLUMN[table] or key in {
        ("results", "metric_name"), ("results", "result_origin"), ("results", "extraction_status"),
        ("issues", "check_name")}
    if key in _VALUE_UNITS:
        f["unit"] = _VALUE_UNITS[key]
    f["description"] = desc
    f["source"] = source
    return f


_VOCAB_ORDER = {
    ("results", "extraction_status"): ["extracted", "present_not_extractable", "absent"],
    ("results", "aggregation"): ["champion", "mean", "median"],
    ("results", "result_origin"): ["primary", "cited"],
    ("materials", "material_class"): ["perovskite", "CIGS", "CdTe", "kesterite", "Sb2Se3", "thin-film Si", "other"],
    ("layers", "layer_function"): ["front_contact", "ETL", "absorber", "passivation", "HTL", "interlayer",
                                   "recombination", "back_contact"],
}


def _vocab_order(key: tuple[str, str]) -> list[str]:
    order = _VOCAB_ORDER.get(key)
    return list(order) if order and set(order) == set(VOCAB[key]) else sorted(VOCAB[key])


def _units() -> list[dict[str, str]]:
    by_unit: dict[str, list[str]] = {}
    for metric, unit in TARGET_UNITS.items():
        by_unit.setdefault(unit, []).append(metric)
    out = [{"quantity": ", ".join(ms), "unit": u} for u, ms in by_unit.items()]
    out += [{"quantity": "Eg (bandgap_value)", "unit": "eV"},
            {"quantity": "толщина слоя (layer_thickness)", "unit": "nm"},
            {"quantity": "площадь (active_area)", "unit": "cm2"},
            {"quantity": "интенсивность освещения (light_intensity)", "unit": "как в статье (mW/cm2, W/m2)"},
            {"quantity": "время (time_point)", "unit": "как в статье (h, day); для T80 — h"}]
    return out


def _rules(profile: ExtractionProfile) -> list[dict[str, Any]]:
    out = []
    for name, info in CHECK_CATALOG.items():
        if not info.exported:
            continue  # внутренние правила интерфейса в issues.csv не выгружаются
        rc = profile.rules.get(name)
        if rc is not None:
            enabled = rc.enabled
            params = dict(rc.params)
        else:
            enabled = info.computed_by != "backend"
            params = {}
        out.append({"id": name, "title": info.title, "formula": RULE_FORMULAS.get(name, ""), "level": info.severity,
                    "ui_level": info.ui_level, "computed_by": info.computed_by, "scope": info.scope,
                    "enabled": enabled, "params": params, "tz_ref": "условия кейса, раздел 3.3"})
    return out


def build_passport(profile: ExtractionProfile) -> dict:
    """7 таблиц, все колонки, 11 метрик с единицами, правила проверок."""
    metrics = _metrics(profile)
    tables = []
    for t, cols in TABLES.items():
        tables.append({"name": t, "tz_name": TABLE_TITLES[t], "row": TABLE_ROW[t], "key": ID_COLUMN[t],
                       "file": f"{t}.csv", "fields": [_field(t, c, metrics) for c in cols],
                       "links": [{"field": f, "to": to} for f, to in _LINKS[t]]})
    return {
        "collection": profile.collection,
        "version": PASSPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "profile": {"id": profile.profile_id, "version": profile.version, "name": profile.name},
        "scope": SCOPE,
        "format": {"encoding": "UTF-8 с BOM", "delimiter": ",", "decimal": ".", "quoting": "QUOTE_MINIMAL",
                   "newline": "CRLF", "boolean": ["true", "false"], "list_separator": ";",
                   "empty": "пустая ячейка — в статье этого нет"},
        "tables": tables,
        "metrics": metrics,
        "units": _units(),
        "statuses": {
            "extraction_status": ["extracted", "present_not_extractable", "absent"],
            "norm_status": [s.value for s in NormStatus],
            "review_status": ["new", "accepted", "rejected"],
            "issue_status": ["open", "fixed", "ok"],
        },
        "selection": {
            "review": "выгружаются факты new и accepted; rejected — нет",
            "export_absent": profile.flags.export_absent,
            "ml_issue_min_confidence": profile.flags.ml_issue_min_confidence,
            "issues": "выгружаются замечания open и fixed; ok — нет",
        },
        "rules": _rules(profile),
        "comparability": COMPARABILITY,
    }


# --- Markdown ------------------------------------------------------------------------------------

def _md(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (list, tuple)):
        return ", ".join(f"`{x}`" for x in v)
    return str(v).replace("|", "\\|").replace("\n", " ")


def passport_markdown(passport: dict) -> str:
    """Markdown для людей: область, таблицы с полями и связями, показатели, единицы, статусы, правила."""
    p = passport or {}
    lines: list[str] = [f"# Паспорт данных: {p.get('collection', '')}", ""]
    meta = [f"Версия паспорта: {p.get('version', '')}"]
    if p.get("schema_version"):
        meta.append(f"схема: {p['schema_version']}")
    prof = p.get("profile") or {}
    if prof:
        meta.append(f"профиль: {prof.get('name') or prof.get('id')} (`{prof.get('id')}`, версия {prof.get('version')})")
    lines += ["; ".join(meta) + ".", ""]
    if p.get("scope"):
        lines += ["## Область", "", str(p["scope"]), ""]
    fmt = p.get("format") or {}
    if fmt:
        lines += ["## Формат файлов", ""]
        lines += [f"- {k}: {_md(v)}" for k, v in fmt.items()]
        lines.append("")
    tables = p.get("tables") or []
    if tables:
        lines += ["## Таблицы", ""]
    for t in tables:
        title = f"### `{t.get('name')}.csv`"
        if t.get("tz_name"):
            title += f" — {t['tz_name']}"
        lines += [title, ""]
        if t.get("row"):
            lines.append(f"Строка — {t['row']}. Ключ: `publication_id` + `{t.get('key')}`.")
            lines.append("")
        lines += ["| Поле | Тип | Обязательное | Единица | Значения | Описание | Источник |",
                  "|---|---|---|---|---|---|---|"]
        for f in t.get("fields") or []:
            values = f.get("values") or ([f"через «{f['separator']}»"] if f.get("separator") else [])
            vals = _md(values) if f.get("values") else _md(values[0] if values else "")
            lines.append(f"| `{f.get('name')}` | {_md(f.get('type'))} | {_md(bool(f.get('required')))} | "
                         f"{_md(f.get('unit'))} | {vals} | {_md(f.get('description'))} | {_md(f.get('source'))} |")
        links = t.get("links") or []
        if links:
            lines += ["", "Связи:"]
            lines += [f"- `{l.get('field')}` → `{l.get('to')}`" for l in links]
        lines.append("")
    metrics = p.get("metrics") or []
    if metrics:
        lines += ["## Показатели (`results.metric_name`)", "",
                  "| Показатель | Название | Единица | В оценке |", "|---|---|---|---|"]
        for m in metrics:
            lines.append(f"| `{m.get('name')}` | {_md(m.get('label'))} | {_md(m.get('unit'))} | "
                         f"{_md(bool(m.get('evaluated')))} |")
        lines += ["", "Нестандартные показатели можно выгружать с понятным названием; в оценке они не участвуют.", ""]
    units = p.get("units") or []
    if units:
        lines += ["## Единицы", "", "| Величина | Единица |", "|---|---|"]
        lines += [f"| {_md(u.get('quantity'))} | {_md(u.get('unit'))} |" for u in units]
        lines.append("")
    statuses = p.get("statuses") or {}
    if statuses:
        lines += ["## Статусы", ""]
        lines += [f"- `{k}`: {_md(v)}" for k, v in statuses.items()]
        lines.append("")
    sel = p.get("selection") or {}
    if sel:
        lines += ["## Что попадает в выгрузку", ""]
        lines += [f"- {k}: {_md(v)}" for k, v in sel.items()]
        lines.append("")
    rules = p.get("rules") or []
    if rules:
        lines += ["## Правила проверок (`issues.check_name`)", "",
                  "| Правило | Название | Уровень | Кто считает | Включено | Формула |", "|---|---|---|---|---|---|"]
        for r in rules:
            lines.append(f"| `{r.get('id')}` | {_md(r.get('title'))} | {_md(r.get('level'))} | "
                         f"{_md(r.get('computed_by'))} | {_md(r.get('enabled'))} | {_md(r.get('formula'))} |")
        lines.append("")
    if p.get("comparability"):
        lines += ["## Сопоставимость", "", str(p["comparability"]), ""]
    return "\n".join(lines).rstrip("\n") + "\n"
