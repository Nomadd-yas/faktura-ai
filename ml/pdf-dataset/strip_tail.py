#!/usr/bin/env python3
# Обрезать "хвостовые" разделы (список литературы, благодарности и т.п.) из .md:
# удаляется всё от первого найденного заголовка такого рода до конца файла.
#
# Примеры:
#   python strip_tail.py D001_new.md                  # -> D001_new.clean.md (рядом)
#   python strip_tail.py a.md b.md c.md                # для каждого -> *.clean.md
#   python strip_tail.py D001_new.md --in-place        # перезаписать файл
#   python strip_tail.py D001_new.md -o out.md         # указать выходной файл (только один вход)
#   python strip_tail.py D001_new.md --dry-run         # показать, что будет отрезано, не писать
#
# Совпадением считается СТРОКА, состоящая только из заголовка (с опциональными
# «#», «**», нумерацией «12.», «A)», двоеточием и т.п.) — упоминания вида
# «см. References [12]» не считаются.

import argparse
import re
import sys
from pathlib import Path

TITLES = [
    # English
    r"author\s+contributions?", r"authors?\s+contributions?",
    r"contribution\s+statement", r"credit\s+author(ing)?\s+statement",
    r"funding(\s+(acknowledg\w*|statement|information|support))?",
    r"financial\s+support",
    r"acknowledg\w*",  # acknowledgements / acknowledgments
    r"references?(\s+and\s+notes)?", r"bibliography", r"works\s+cited",
    r"reference\s+list",
    r"supplementary\s+(materials?|methods?|figures?|data|information|appendix)",
    r"appendi(x|ces)",
    r"conflict\s+of\s+interest", r"competing\s+interests?",
    r"data\s+availability(\s+statement)?", r"code\s+availability",
    r"ethics?\s+(statement|declaration)",
    # Russian
    r"список\s+(литературы|источников|использованных\s+источников)",
    r"использованная\s+литература", r"литература",
    r"благодарности", r"выражения?\s+благодарности",
    r"финансирование", r"финансовая\s+поддержка",
    r"приложения?", r"дополнительные\s+(материалы|сведения|данные)",
    r"конфликт\s+интересов",
]

_TITLE_RE = "|".join(sorted(TITLES, key=len, reverse=True))

HEADING_LINE = re.compile(
    r"^[\s>*#_-]*"                                  # markdown-оформление строки
    r"(?:\(?(?:\d+(?:\.\d+)*|[A-Za-zА-Яа-я])\)?[.)]?\s+)?"  # опциональная нумерация "12." "A)" "IV."
    r"(?:" + _TITLE_RE + r")"
    r"[\s:.\-*_]*$",                                # хвостовая пунктуация/маркап
    re.IGNORECASE | re.MULTILINE,
)


def strip_trailing_sections(md_text: str):
    """Вернуть (очищенный_текст, info). info=None, если хвостовой раздел не найден."""
    m = HEADING_LINE.search(md_text)
    if not m:
        return md_text, None
    line_no = md_text.count("\n", 0, m.start()) + 1
    heading = md_text[m.start():m.end()].strip()
    cleaned = md_text[:m.start()].rstrip() + "\n"
    return cleaned, {
        "line": line_no,
        "heading": heading,
        "removed_chars": len(md_text) - len(cleaned),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Обрезать хвостовые разделы из .md")
    ap.add_argument("files", nargs="+", help="входные .md файлы")
    ap.add_argument("-o", "--output", help="выходной файл (только при одном входе)")
    ap.add_argument("--in-place", action="store_true", help="перезаписать входные файлы")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что будет отрезано")
    args = ap.parse_args()

    if args.output and len(args.files) != 1:
        sys.exit("Ошибка: -o применим только к одному файлу")

    exit_code = 0
    for name in args.files:
        src = Path(name)
        if not src.exists():
            print(f"[пропуск] не найден: {src}")
            exit_code = 1
            continue
        text = src.read_text(encoding="utf-8")
        cleaned, info = strip_trailing_sections(text)

        if info is None:
            print(f"{src.name}: хвостовые разделы не найдены — файл не изменён")
            continue

        print(f"{src.name}: отрезано от строки {info['line']} "
              f"(«{info['heading']}»), удалено {info['removed_chars']} симв.")
        if args.dry_run:
            continue
        if args.in_place:
            dst = src
        elif args.output:
            dst = Path(args.output)
        else:
            dst = src.with_name(src.stem + ".clean.md")
        dst.write_text(cleaned, encoding="utf-8")
        print(f"  -> {dst}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
