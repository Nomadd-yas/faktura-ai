"""Очистка текста страницы и нарезка на чанки (~1200 символов, перекрытие ~150) по границам абзацев и предложений.

Чанк принадлежит одной странице: страницы никогда не склеиваются.
"""
from __future__ import annotations

import re

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
MIN_TEXT_LAYER_CHARS = 50  # короче - страница считается без текстового слоя (берём OCR)

_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st",
              "\ufb06": "st", "\u0132": "IJ", "\u0133": "ij", "\u00a0": " ", "\u2009": " ", "\u200a": " ",
              "\u202f": " ", "\u00ad": "", "\u200b": "", "\ufeff": "", "\ufffd": ""}
_LIG_RE = re.compile("|".join(map(re.escape, _LIGATURES)))
_HYPHEN_BREAK = re.compile(r"(?<=[A-Za-z])[-\u2010\u2011]\s*\n\s*(?=[a-z])")
_PAGE_MARK = re.compile(r"<!--\s*page\s+\d+\s*-->")
_PARA_SPLIT = re.compile(r"\n\s*\n")
_SENT_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9(\[])")


def clean_text(text: str) -> str:
    """Лигатуры -> буквы, склейка переносов по дефису, нормализация пробелов; абзацы разделяются пустой строкой."""
    if not text:
        return ""
    t = _PAGE_MARK.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    t = _LIG_RE.sub(lambda m: _LIGATURES[m.group(0)], t)
    t = _HYPHEN_BREAK.sub("", t)
    paras = []
    for para in _PARA_SPLIT.split(t):
        # внутри абзаца переводы строк - это переносы строк вёрстки
        line = re.sub(r"\s+", " ", para).strip()
        if line:
            paras.append(line)
    return "\n\n".join(paras)


def _units(text: str, size: int) -> list[str]:
    """Абзацы; слишком длинные дробятся на предложения, а слишком длинные предложения - по словам."""
    out: list[str] = []
    for para in text.split("\n\n"):
        if len(para) <= size:
            out.append(para)
            continue
        for sent in _SENT_END.split(para):
            while len(sent) > size:
                cut = sent.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                out.append(sent[:cut].strip())
                sent = sent[cut:].strip()
            if sent:
                out.append(sent)
    return [u for u in out if u]


def _tail(text: str, overlap: int) -> str:
    """Хвост до `overlap` символов, начинающийся с границы слова."""
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    cut = text[-overlap:]
    sp = cut.find(" ")
    return cut[sp + 1:] if 0 <= sp < len(cut) - 1 else cut


def chunk_page(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Очищенный текст одной страницы -> чанки. Пустая страница -> []."""
    text = clean_text(text)
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    cur = ""
    for u in _units(text, size - overlap - 1):
        if cur and len(cur) + 1 + len(u) > size:
            chunks.append(cur)
            cur = (_tail(cur, overlap) + " " + u).strip()
        else:
            cur = (cur + "\n" + u).strip() if cur else u
    if cur:
        chunks.append(cur)
    return chunks
