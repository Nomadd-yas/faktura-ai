"""Паспорт набора разбора в формате фронта (BACKEND_ONLY §7а.6): тот же JSON отдаётся в `GET /runs/{id}/passport.json`
и кладётся в `submission.zip` как `passport.json`; `PASSPORT.md` строится из этого же JSON.

Содержимое области (критерии, поля и словари сущностей, правила, нормализация) лежит в `passport/data/{solar,rag}.json`
(перенесено из `src/passport.ts` фронта). Колонки сущностей сверяются с шаблонами сдачи тестом
`tests/passport/test_run_passport.py`. Результат детерминирован: одни и те же входные данные - одни и те же байты.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from ..domain.registry import RAG_SCHEMA_PATH, get_domain

__all__ = ["build_run_passport", "passport_json_bytes", "passport_md", "passport_md_bytes", "RAG_RULES_VERSION"]

DATA_DIR = Path(__file__).parent / "data"
TITLE = "Паспорт данных «Фактура AI»"
VERSION = "1"
FORMAT = "CSV UTF-8 с BOM, запятая, точка, списки через «;»; пустая ячейка = в статье нет"
RAG_RULES_VERSION = "rag-checks-1"
LEVEL_LABEL = {"error": "Ошибка", "warn": "Предупреждение", "info": "Справка", "format": "Формат"}
TYPE_LABEL = {"string": "текст", "int": "целое", "number": "число", "bool": "да / нет", "enum": "словарь",
              "list": "список через «;»"}
COMPUTED_BY = {"бэк": "Сервис разбора", "ML": "Модель", "бэк + ML": "Сервис разбора + модель"}
PIPE = "|"
ESCAPED_PIPE = "\\|"


@lru_cache(maxsize=None)
def _domain_content(domain: str) -> str:
    return (DATA_DIR / f"{domain}.json").read_text(encoding="utf-8")


def _rag_dictionary_version() -> str | None:
    try:
        v = json.loads(RAG_SCHEMA_PATH.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None
    return str(v) if v else None


def build_run_passport(domain: str, *, model_versions: Iterable[str] | None = None, rules: str | None = None,
                       dictionary: str | None = None) -> dict[str, Any]:
    """Паспорт набора области `domain`. `versions.model` - версии модели статей набора через «, » (null, если статей
    ещё нет); `rules` / `dictionary` - версии правил проверок и словаря."""
    get_domain(domain)  # неизвестная область -> 422 domain_not_supported
    models = sorted({str(v) for v in (model_versions or []) if v})
    if domain == "rag":
        rules = rules or RAG_RULES_VERSION
        dictionary = dictionary or _rag_dictionary_version()
    return {
        "title": TITLE, "version": VERSION, "format": FORMAT,
        "versions": {"model": ", ".join(models) or None, "rules": rules or None, "dictionary": dictionary or None},
        "domains": [json.loads(_domain_content(domain))],
    }


def passport_json_bytes(passport: dict[str, Any]) -> bytes:
    return (json.dumps(passport, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _cell(s: Any) -> str:
    return str("" if s is None else s).replace(PIPE, ESCAPED_PIPE).replace("\n", " ")


def _factor(x: float | None) -> str:
    return "" if x is None else "×" + f"{float(f'{x:.6g}'):g}"


def passport_md(passport: dict[str, Any]) -> str:
    """Markdown по тому же JSON (вид - как у экрана «Паспорт» фронта) плюс строка версий."""
    out: list[str] = [f"# {passport.get('title') or TITLE}", "",
                      "Формат: CSV UTF-8 с BOM, запятая-разделитель, точка в числах, true и false, списки через «;». "
                      "Пустая ячейка означает, что в статье этого нет.", ""]
    ver = passport.get("versions") or {}
    if ver:
        out += ["Собран с версиями: " + ", ".join(f"{k} {v or '—'}" for k, v in ver.items()) + ".", ""]
    for d in passport.get("domains") or []:
        out += [f"## {d['name']}", ""]
        for g in d.get("guides") or []:
            out += [f"### {g['title']}", "", g.get("description") or "", "",
                    *[f"- {x}" for x in g.get("items") or []], ""]
        for e in d.get("entities") or []:
            out += [f"### {e['id']}", "", f"Одна строка: {e['row']}. Ключ: {e['key']}.", "",
                    "| Поле | Тип | Единица | Обяз. | Описание | Допустимые значения |", "|---|---|---|---|---|---|"]
            for x in e.get("fields") or []:
                extra = ", ".join(x["values"]) if x.get("values") else f"→ {x['ref']}" if x.get("ref") else ""
                out.append(f"| {x['name']} | {TYPE_LABEL.get(x['type'], x['type'])} | {x.get('unit') or ''} | "
                           f"{'да' if x.get('required') else ''} | {_cell(x.get('description'))} | {_cell(extra)} |")
            out.append("")
        out += ["### Правила проверки", "", "| Правило | Что проверяет | Уровень | Кто считает |", "|---|---|---|---|"]
        for r in d.get("rules") or []:
            out.append(f"| {r['id']} | {_cell(r.get('description'))} | {LEVEL_LABEL.get(r['level'], r['level'])} | "
                       f"{COMPUTED_BY.get(r['computed_by'], r['computed_by'])} |")
        out += ["", "### Нормализация", "", "| Величина | Из | В | Множитель | Примечание |", "|---|---|---|---|---|"]
        for n in d.get("normalization") or []:
            out.append(f"| {n['quantity']} | {n['from']} | {n['to']} | {_factor(n.get('factor'))} | "
                       f"{n.get('note') or ''} |")
        out.append("")
    return "\n".join(out)


def passport_md_bytes(passport: dict[str, Any]) -> bytes:
    return passport_md(passport).encode("utf-8")
