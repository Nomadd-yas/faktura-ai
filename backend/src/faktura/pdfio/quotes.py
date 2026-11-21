"""Проверка цитат той же логикой, что у check_submission.py, и ключ смысловой дедупликации (план 5.2).

REQ-060: цитата проверяется той же функцией `norm()` и тем же pypdf, что в валидаторе. Тексты страниц
кешируются один раз на статью. Отличие от скрипта одно: цитата, пустая после `norm()`, считается
ненайденной (у скрипта пустая строка «находится» вырожденно).
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

_NORM_RE = re.compile(r"[^a-z0-9.%]+")
# check_submission.py: re.match(r"^(Table|TABLE|Supplementary)", frag) - такие цитаты не проверяются
_SKIP_RE = re.compile(r"^(Table|TABLE|Supplementary)")


def validator_norm(s: str) -> str:
    """Копия norm() из check_submission.py (лигатуры, U+2212/U+2013 -> '-', нижний регистр, [a-z0-9.%])."""
    s = (s.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff").replace("−", "-").replace("–", "-"))
    return _NORM_RE.sub("", s.lower())


def is_checkable_fragment(fragment: str | None) -> bool:
    """Проверяет ли валидатор такую цитату: непустая и не начинается с Table/TABLE/Supplementary."""
    frag = (fragment or "").strip()
    return bool(frag) and not _SKIP_RE.match(frag)


def _page_number(page: object) -> int | None:
    """Как в скрипте: int(float(page)); нераспознанное -> None (скрипт такую строку пропускает)."""
    if page is None or isinstance(page, bool):
        return None
    try:
        return int(float(str(page).strip()))
    except (TypeError, ValueError, OverflowError):
        return None


class PypdfQuoteIndex:
    """QuoteChecker: тексты страниц pypdf кешируются на статью. Цитата, пустая после norm(), считается ненайденной.

    `check` возвращает None, если цитата не проверяется (нет страницы или текста, ссылка на таблицу,
    нераспознанный номер страницы), иначе True/False. Нечитаемый PDF не роняет проверку: все цитаты False.
    """

    def __init__(self, pdf_path: Path) -> None:
        self.pdf_path = Path(pdf_path)
        self._pages: list[str] | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def pages(self) -> list[str]:
        """Нормализованные тексты страниц (загрузка при первом обращении)."""
        if self._pages is None:
            with self._lock:
                if self._pages is None:
                    self._pages = self._load()
        return self._pages

    def _load(self) -> list[str]:
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(self.pdf_path))
            return [validator_norm(pg.extract_text() or "") for pg in reader.pages]
        except Exception as e:  # noqa: BLE001 - как в скрипте: любой сбой чтения = цитаты не найдены
            self.error = f"{type(e).__name__}: {e}"
            log.warning("pypdf не читает %s: %s", self.pdf_path, self.error)
            return []

    def check(self, page: int | None, fragment: str | None) -> bool | None:
        if not is_checkable_fragment(fragment):
            return None
        n = _page_number(page)
        if n is None:
            return None
        nf = validator_norm((fragment or "").strip())
        if not nf:
            return False
        pages = self.pages
        if not (1 <= n <= len(pages)):
            return False
        return nf in pages[n - 1]

    def find_pages(self, fragment: str | None) -> list[int]:
        """Страницы (с 1), на которых цитата находится; подсказка «найдено на с. …» как у скрипта."""
        nf = validator_norm((fragment or "").strip())
        if not nf:
            return []
        return [i + 1 for i, txt in enumerate(self.pages) if nf in txt]


# ---------------------------------------------------------------------------------------------
# Ключ смысловой дедупликации (REQ-074): название + год + первый автор
# ---------------------------------------------------------------------------------------------

def _fold(s: str) -> str:
    """Без диакритики, нижний регистр, только буквы и цифры (любой алфавит)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return "".join(ch for ch in s.casefold() if ch.isalnum())


def _is_initials(token: str) -> bool:
    """«H.W.», «A.», «hw», «J-P.» - инициалы, а не фамилия."""
    letters = _fold(token)
    if not letters:
        return True
    if "." in token and len(letters) <= 3:
        return True
    return len(letters) <= 2


def first_author_surname(author: str | None) -> str | None:
    """Фамилия первого автора: «Qiao H.W.», «H.W. Qiao», «Qiao, Hongwei» -> «qiao»."""
    if author is None:
        return None
    a = str(author).strip()
    if not a:
        return None
    if "," in a:
        head = a.split(",", 1)[0]
        folded = _fold(head)
        if folded:
            return folded
    tokens = [t for t in re.split(r"\s+", a) if t]
    names = [t for t in tokens if not _is_initials(t)]
    pick = names[-1] if names else (tokens[0] if tokens else "")
    folded = _fold(pick)
    return folded or None


def title_key(title: str | None, year: int | None, first_author: str | None) -> str | None:
    """Ключ смысловой дедупликации; без названия, года или автора -> None.

    Регистр, пунктуация, пробелы и диакритика не влияют. Формат: «<название>|<год>|<фамилия>».
    """
    if title is None or year is None or first_author is None:
        return None
    t = _fold(str(title))
    if not t:
        return None
    try:
        y = int(str(year).strip()) if not isinstance(year, int) else int(year)
    except (TypeError, ValueError):
        try:
            y = int(float(str(year).strip()))
        except (TypeError, ValueError):
            return None
    surname = first_author_surname(first_author)
    if not surname:
        return None
    return f"{t}|{y}|{surname}"


# ---------------------------------------------------------------------------------------------
# Проверка цитат области rag: нормализация и два чтения PDF, как в RAG-варианте check_submission.py
# ---------------------------------------------------------------------------------------------

_HYPHEN_BREAK = re.compile(r"(?<=\w)-\s*\r?\n\s*(?=\w)")
_WS = re.compile(r"\s+")


def rag_norm(text: str) -> str:
    """Копия norm() RAG-скрипта: NFKC, без мягкого переноса, склейка переносов слов, схлопнутые пробелы."""
    text = unicodedata.normalize("NFKC", text or "").replace("\u00ad", "")
    text = _HYPHEN_BREAK.sub("", text)
    return _WS.sub(" ", text).strip()


class VariantQuoteIndex:
    """Цитаты rag: страница читается pypdf и PyMuPDF (независимые варианты), цитата найдена, если она есть
    хотя бы в одном. `check` -> None, если цитата не проверяется (нет страницы, ссылка на Table/Supplementary,
    у страницы нет текстового слоя), иначе True/False. Нечитаемый PDF: все цитаты None."""

    def __init__(self, pdf_path: Path) -> None:
        self.pdf_path = Path(pdf_path)
        self._pages: list[list[str]] | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def pages(self) -> list[list[str]]:
        if self._pages is None:
            with self._lock:
                if self._pages is None:
                    self._pages = self._load()
        return self._pages

    def _load(self) -> list[list[str]]:
        variants: list[list[str]] = []
        try:
            from pypdf import PdfReader

            variants.append([rag_norm(pg.extract_text() or "") for pg in PdfReader(str(self.pdf_path)).pages])
        except Exception as e:  # noqa: BLE001
            self.error = f"pypdf: {type(e).__name__}"
        try:
            import fitz

            from ..pipeline._pdf import PDF_LOCK

            with PDF_LOCK:
                with fitz.open(str(self.pdf_path)) as doc:
                    variants.append([rag_norm(p.get_text() or "") for p in doc])
        except Exception as e:  # noqa: BLE001
            self.error = (self.error + "; " if self.error else "") + f"PyMuPDF: {type(e).__name__}"
        if not variants or len({len(v) for v in variants}) != 1:
            return []
        return [[v[i] for v in variants] for i in range(len(variants[0]))]

    def check(self, page: object, fragment: str | None) -> bool | None:
        if not is_checkable_fragment(fragment):
            return None
        n = _page_number(page)
        if n is None:
            return None
        pages = self.pages
        if not (1 <= n <= len(pages)):
            return None if not pages else False
        variants = pages[n - 1]
        if not any(variants):
            return None  # нет текстового слоя: цитату нечем проверить (скан)
        needle = rag_norm((fragment or "").strip())
        if not needle:
            return None
        return any(needle in t for t in variants)
