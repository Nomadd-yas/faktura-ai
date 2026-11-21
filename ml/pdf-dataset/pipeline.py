#!/usr/bin/env python3
# Полный конвейер в ОДНОМ процессе, используемый и CLI (pipeline.py файл.pdf),
# и HTTP API (api.py, POST /extract):
#   PDF -> glmocr (layout + OCR на вашем сервере) -> markdown
#       -> strip_tail (удаление хвостовых разделов)
#       -> extract_dataset.extract (импорт модуля, НЕ подпроцесс)
#          -> <stem>.dataset.json + <stem>.meta.json
#
# Конфигурация glmocr — отдельный файл glmocr.config.yaml рядом с pipeline.py
# или путь из GLMOCR_CONFIG (например, смонтированный свой файл).
# RouterAI: ROUTERAI_API_KEY (обяз.), ROUTERAI_MODEL, ROUTERAI_PROVIDER (wafer; несколько через запятую — по приоритету).

import argparse
import json
import os
import re
import shutil
import socket
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))  # импорт соседних модулей при любом cwd


DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"  # совпадает с extract_dataset.DEFAULT_MODEL


class PipelineError(Exception):
    """[faktura-patch] Типизированная ошибка конвейера: api.py превращает её в HTTP-ответ.

    code: pdf_broken | pdf_encrypted (422), ocr_unavailable | not_configured (503), ml_error (500).
    """
    STATUS = {"pdf_broken": 422, "pdf_encrypted": 422, "ocr_unavailable": 503,
              "not_configured": 503, "ml_error": 500,
              "quota_exceeded": 402,
              "domain_not_supported": 422}  # [faktura-patch] неизвестное значение поля domain  # [faktura-patch] исчерпан лимит трат ключа RouterAI — повтор не поможет

    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = self.STATUS.get(code, 500)


def _code_hash() -> str:
    """[faktura-patch] хеш кода конвейера и промптов: правка промптов -> новая версия -> бэкенд не берёт старый кеш."""
    import hashlib
    h = hashlib.sha256()
    for name in ("pipeline.py", "extract_dataset.py", "strip_tail.py", "figures.py"):  # [faktura-patch]
        p = BASE / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:8]


_CODE_HASH = _code_hash()


def model_version() -> str:
    return f"pdf-dataset/{_CODE_HASH}/" + os.environ.get("ROUTERAI_MODEL", DEFAULT_MODEL)


# ---------- [faktura-patch] подтяжка цитат к текстовому слою PDF ----------
# Модель цитирует OCR-markdown (LaTeX, переносы, перефразы) — проверка сдачи ищет цитату в тексте PDF.
# Нормализация — как в tools/check_submission.py: только [a-z0-9.%] в нижнем регистре.
import re as _re

_KEYWORDS = {
    "PCE": ("pce", "efficienc", "η"), "stabilized_PCE": ("pce", "efficienc", "stabiliz"),
    "Voc": ("voc", "v oc", "open-circuit", "open circuit"), "Jsc": ("jsc", "j sc", "short-circuit", "short circuit"),
    "Jsc_EQE": ("eqe", "integrat"), "FF": ("ff", "fill factor"), "Rs": ("series", "rs"),
    "Rsh": ("shunt", "rsh"), "HI": ("hysteresis", "hi"), "T80": ("t80", "80%"),
    "retained_PCE": ("retain", "maintain", "initial"),
}


def _norm(s: str) -> str:
    s = (s.replace("ﬃ", "ffi").replace("ﬄ", "ffl").replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff").replace("−", "-").replace("–", "-"))
    return _re.sub(r"[^a-z0-9.%]+", "", s.lower())


def _value_regex(v):
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    txt = repr(f) if f != int(f) else str(int(f))
    if "e" in txt:
        return None
    ip, _, dp = txt.partition(".")
    dp = dp.rstrip("0")
    body = _re.escape(ip) + (r"[.,]" + _re.escape(dp) + "0*" if dp else r"(?:[.,]0+)?")
    return _re.compile(r"(?<![\d.,])" + body + r"(?![\d])")


def _sentence(text: str, start: int, end: int, limit: int = 160) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("\n\n", 0, start))
    left = 0 if left < 0 else left + 2
    right = text.find(". ", end)
    right = len(text) if right < 0 else right + 1
    if right - left > limit:  # окно вокруг значения по границам слов
        half = limit // 2
        left = max(left, start - half)
        right = min(right, end + half)
        while left > 0 and not text[left - 1].isspace():
            left += 1
        while right < len(text) and not text[right].isspace():
            right -= 1
    return " ".join(text[left:right].split()).strip(" ,;")


def snap_fragments(dataset_path, pdf) -> dict:
    import pymupdf
    doc = pymupdf.open(str(pdf))
    raw = [pg.get_text() for pg in doc]
    normed = [_norm(t) for t in raw]
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    st = {"ok": 0, "page_fixed": 0, "snapped": 0, "unresolved": 0, "table_ref": 0}

    def find_value(row, pages):
        rx = _value_regex(row.get("raw_value"))
        if rx is None:
            return None
        kws = _KEYWORDS.get(row.get("metric_name") or "", ())
        best = None
        for n in pages:
            for m in rx.finditer(raw[n]):
                snip = _sentence(raw[n], m.start(), m.end())
                if not snip or _norm(snip) not in normed[n]:
                    continue
                score = 2 if any(k in snip.lower() for k in kws) else 1
                if best is None or score > best[0]:
                    best = (score, n, snip)
        return best

    for table in ("results", "issues"):
        for row in data.get(table) or []:
            frag = (row.get("source_fragment") or "").strip()
            try:
                n = int(row.get("source_page")) - 1
            except (TypeError, ValueError):
                n = None
            if frag and _re.match(r"^(Table|TABLE|Supplementary)", frag):
                st["table_ref"] += 1
                continue
            nf = _norm(frag) if frag else ""
            if nf and n is not None and 0 <= n < len(normed) and nf in normed[n]:
                st["ok"] += 1
                continue
            if nf:
                other = [k for k, t in enumerate(normed) if nf in t]
                if len(other) == 1:
                    row["source_page"] = other[0] + 1
                    st["page_fixed"] += 1
                    continue
            if table == "results":
                here = [n] if n is not None and 0 <= n < len(raw) else []
                hit = find_value(row, here) or find_value(row, [k for k in range(len(raw)) if k not in here])
                if hit and (hit[0] == 2 or here and hit[1] == here[0]):
                    row["source_page"], row["source_fragment"] = hit[1] + 1, hit[2]
                    st["snapped"] += 1
                    continue
            st["unresolved"] += 1
    Path(dataset_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def config_path() -> Path:
    return Path(os.environ.get("GLMOCR_CONFIG", BASE / "glmocr.config.yaml"))


def ocr_endpoint(config: Path):
    """(host, port) OCR-сервера из pipeline.ocr_api glmocr-конфига; None, если не нашли."""
    try:
        text = Path(config).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        import yaml
        api = ((yaml.safe_load(text) or {}).get("pipeline") or {}).get("ocr_api") or {}
        host, port = api.get("api_host"), api.get("api_port")
    except Exception:
        host = port = None
        mh = re.search(r"api_host:\s*([^\s#]+)", text)
        mp = re.search(r"api_port:\s*(\d+)", text)
        if mh and mp:
            host, port = mh.group(1), mp.group(1)
    if not host or not port:
        return None
    return str(host), int(port)


def ocr_reachable(config: Path, timeout: float = 2.0) -> bool:
    """[faktura-patch] Быстрая TCP-проверка OCR-сервера (host:port из конфига)."""
    ep = ocr_endpoint(config)
    if ep is None:
        return False
    try:
        with socket.create_connection(ep, timeout=timeout):
            return True
    except OSError:
        return False


def _is_connection_error(exc) -> bool:
    """Ищем ошибку соединения по цепочке причин (requests/httpx/urllib3/socket)."""
    seen = 0
    while exc is not None and seen < 10:
        names = {c.__name__ for c in type(exc).__mro__}
        if names & {"ConnectionError", "ConnectError", "ConnectTimeout", "ConnectionRefusedError",
                    "NewConnectionError", "MaxRetryError"}:
            return True
        if "Failed to connect to API server" in str(exc):  # glmocr: TimeoutError при недоступном OCR
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


def check_pdf(pdf: Path) -> int:
    """[faktura-patch] Проверка PDF до OCR (pymupdf). Возвращает число страниц."""
    try:
        import pymupdf
    except ImportError:
        return 0  # pymupdf нет — пропускаем проверку
    try:
        doc = pymupdf.open(str(pdf))
    except Exception as e:
        raise PipelineError("pdf_broken", f"PDF не читается: {e}") from e
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise PipelineError("pdf_encrypted", "PDF зашифрован (нужен пароль)")
        n = doc.page_count
        if n < 1:
            raise PipelineError("pdf_broken", "В PDF нет страниц")
        return n
    finally:
        doc.close()


def build_paged_markdown(json_result, fallback_markdown=None):
    """[faktura-patch] Markdown постранично из result.json_result с маркерами <!-- page N -->.

    json_result — список по страницам, каждая страница — список блоков (dict с label/content).
    Блоки image пропускаются, таблицы остаются. Номер страницы — PDF, с 1.
    Если форма неожиданная — возвращаем fallback_markdown (без маркеров) и пишем в лог.
    Возвращает (текст, paged: bool).
    """
    pages = None
    if isinstance(json_result, list) and json_result and all(isinstance(pg, list) for pg in json_result):
        pages = json_result
    if pages is not None:
        parts = []
        for i, blocks in enumerate(pages, start=1):
            texts = []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                content = b.get("content")
                if b.get("label") == "image" or not isinstance(content, str) or not content.strip():
                    continue
                texts.append(content.strip())
            parts.append(f"<!-- page {i} -->\n\n" + "\n\n".join(texts))
        md = "\n\n".join(parts)
        if md.strip():
            return md, True
    print("[!] json_result неожиданной формы — беру markdown_result без маркеров страниц "
          "(source_page будет ненадёжен)", flush=True)
    return (fallback_markdown or ""), False


# [faktura-patch] прогретый парсер glmocr: создаётся один раз на процесс (layout-модель + потоки конвейера),
# дальше parse() вызывается на каждую статью. В API живёт в воркере ProcessPool (см. api.py).
_PARSER = None
_PARSER_CONFIG = None


def _close_parser():
    global _PARSER
    if _PARSER is not None:
        try:
            _PARSER.close()
        except Exception:
            pass
        _PARSER = None


def _get_parser(config):
    global _PARSER, _PARSER_CONFIG
    if _PARSER is not None and _PARSER_CONFIG == str(config):
        return _PARSER
    _close_parser()
    import atexit
    from glmocr import GlmOcr
    _PARSER = GlmOcr(config_path=str(config))
    _PARSER_CONFIG = str(config)
    atexit.register(_close_parser)
    return _PARSER


def warm_parser():
    """[faktura-patch] Прогрев воркера: загрузить layout-модель заранее (не бросает исключений)."""
    try:
        _get_parser(config_path())
    except Exception as e:  # noqa: BLE001
        print(f"[!] прогрев glmocr не удался: {type(e).__name__}: {e}", flush=True)




# ---------- [faktura-patch] R6: текстовый слой PDF вместо OCR там, где он надёжен ----------
# TEXT_SOURCE: ocr (по умолчанию, как раньше — OCR всех страниц) | auto (по странице: текстовый слой или OCR)
# | pdf (текстовый слой везде, для экспериментов; OCR не запускается). Решения пишутся в meta.text_source.
TEXT_SOURCE_MODES = ("ocr", "auto", "pdf")
RASTER_OCR_FRAC = 0.25        # доля страницы под растром, с которой «Table N» рядом с картинкой = таблица-картинка
IMAGE_MIN_FRAC = 0.05         # картинка меньше этой доли страницы не считается кандидатом в таблицу-картинку
CAPTION_NEAR_PT = 30.0        # подпись «рядом» с картинкой, если bbox картинки (расширенный на столько pt) её касается
TABLE_MIN_ROWS = 2
TABLE_MIN_COLS = 2
TABLE_MAX_EMPTY_FRAC = 0.6    # доля пустых ячеек, выше которой таблица считается битой
TABLE_MERGED_NUMS = 3         # ячейка с >= стольких чисел через пробел («0.42 0.51 0.63») — склейка столбцов
TABLE_MERGED_FRAC = 0.15      # доля таких ячеек, выше которой таблица битая
LOOSE_TABLE_ROWS, LOOSE_TABLE_COLS = 4, 3   # битая таблица без подписи, но такого размера — всё равно OCR
HEADING_SIZE_RATIO = 1.12     # кегль / кегль основного текста -> заголовок
MARGIN_BAND = 0.07            # верхняя/нижняя полоса страницы для колонтитулов
RUNNING_MIN_PAGES = 3         # повтор колонтитула на >= стольких страницах (и >= RUNNING_FRAC от всех)
RUNNING_FRAC = 0.4
SPANNER_FRAC = 0.6            # блок шире этой доли страницы — на всю ширину (не колонка)
TWO_COL_MIN_BLOCKS = 2        # блоков в каждой колонке, чтобы страница считалась двухколоночной
TABLE_CAP_RX = _re.compile(r"^\s*(?:table|tab\.)\s*(?:[A-Z]?\d+|[IVXL]+)\b(?![ \t]+[a-z])", _re.I)
_NUMTOK_RX = _re.compile(r"^[-+−]?\d[\d.,]*%?$|^[-+−]?\.\d+$")
_TICK_RX = _re.compile(r"^[\d.,\-+−%\s]{1,8}$")
_STRAY_PUNCT_RX = _re.compile(r"(?:^|\s)[.,](?:\s|$)")
_LIGS = {"ﬃ": "ffi", "ﬄ": "ffl", "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", " ": " ", " ": " "}


def text_source_mode() -> str:
    m = (os.environ.get("TEXT_SOURCE") or "ocr").strip().lower()
    return m if m in TEXT_SOURCE_MODES else "ocr"


def _clean_text(s):
    for a, b in _LIGS.items():
        s = s.replace(a, b)
    return s


def _join_lines(lines):
    """Строки блока -> абзац; перенос «слово-<конец строки>продолжение» склеивается без дефиса."""
    cur = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not cur:
            cur = ln
        elif cur.endswith("-") and len(cur) > 1 and cur[-2].isalpha() and ln[:1].islower():
            cur = cur[:-1] + ln
        else:
            cur += " " + ln
    return _re.sub(r"[ \t]+", " ", cur).strip()


def _page_text_blocks(page):
    """Текстовые блоки страницы: {bbox, text, size, bold, nlines}."""
    out = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") != 0:
            continue
        lines, sizes, bolds, nchars = [], [], 0, 0
        for ln in b.get("lines", []):
            t = "".join(s.get("text", "") for s in ln.get("spans", []))
            if not t.strip():
                continue
            lines.append(_clean_text(t))
            for s in ln.get("spans", []):
                n = len(s.get("text", "").strip())
                if n:
                    sizes.append((s.get("size", 0.0), n))
                    nchars += n
                    if s.get("flags", 0) & 16 or "bold" in (s.get("font") or "").lower():
                        bolds += n
        text = _join_lines(lines)
        if not text:
            continue
        size = sum(s * n for s, n in sizes) / max(nchars, 1)
        out.append({"bbox": tuple(b["bbox"]), "text": text, "size": size,
                    "bold": bolds >= 0.8 * max(nchars, 1), "nlines": len(lines), "lines": lines})
    return out


def _body_size(all_blocks):
    """Кегль основного текста документа — самый частый (по символам)."""
    from collections import Counter
    c = Counter()
    for blocks in all_blocks:
        for b in blocks:
            c[round(b["size"] * 2) / 2] += len(b["text"])
    return c.most_common(1)[0][0] if c else 10.0


def _running_key(text):
    return _re.sub(r"\d+", "#", text.lower()).strip()


def _in_margin(bbox, rect):
    return bbox[1] < rect.y0 + MARGIN_BAND * rect.height or bbox[3] > rect.y1 - MARGIN_BAND * rect.height


def _running_keys(all_blocks, page_rects):
    """Ключи колонтитулов: тексты из верхней/нижней полосы, повторяющиеся (с точностью до цифр) на многих страницах."""
    from collections import Counter
    cnt = Counter()
    for blocks, r in zip(all_blocks, page_rects):
        cnt.update({_running_key(b["text"]) for b in blocks if _in_margin(b["bbox"], r)})
    need = max(RUNNING_MIN_PAGES, RUNNING_FRAC * len(all_blocks))
    return {k for k, v in cnt.items() if v >= need and k}


def _cell_text(c):
    if c is None:
        return ""
    c = _clean_text(str(c)).replace("\r", "")
    c = _re.sub(r"(?<=[A-Za-z])-\n(?=[a-z])", "", c)
    return "\n".join(x for x in (_re.sub(r"\s+", " ", ln).strip() for ln in c.split("\n")) if x)


def _words_in(words, bbox):
    """слова страницы (get_text('words')) с центром в bbox — в порядке чтения: блок, строка, номер слова.
    Строки ячейки разделены «\\n» (нужно для проверки склейки строк таблицы; в HTML заменяется пробелом)."""
    sel = [w for w in words if bbox[0] <= (w[0] + w[2]) / 2 <= bbox[2] and bbox[1] <= (w[1] + w[3]) / 2 <= bbox[3]]
    sel.sort(key=lambda w: (w[5], w[6], w[7]))
    out, prev = "", None
    for w in sel:
        t = w[4]
        if not out:
            out = t
        elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and t[:1].islower():
            out = out[:-1] + t
        else:
            new_line = prev is not None and abs(w[1] - prev[1]) > 0.5 * max(prev[3] - prev[1], 1.0)
            out += ("\n" if new_line else " ") + t
        prev = w
    return out


def _table_rows(tab, words=None):
    """Строки таблицы find_tables() -> list[list[str]] без полностью пустых строк. Текст ячейки берём из слов страницы
    (get_text('words'): десятичные точки и пробелы целы; многострочные ячейки склеены), иначе — tab.extract()."""
    if words is not None:
        try:
            raw = [[(_words_in(words, c) if c is not None else "") for c in r.cells] for r in tab.rows]
        except Exception:  # noqa: BLE001
            raw = tab.extract()
    else:
        raw = tab.extract()
    rows = [[_cell_text(c) for c in r] for r in raw]
    return [r for r in rows if any(r)]


def _table_problem(rows):
    """None, если таблица из текстового слоя выглядит целой; иначе короткая причина."""
    if len(rows) < TABLE_MIN_ROWS:
        return "rows<%d" % TABLE_MIN_ROWS
    if max((len(r) for r in rows), default=0) < TABLE_MIN_COLS:
        return "cols<%d" % TABLE_MIN_COLS
    cells = [c for r in rows for c in r]
    if sum(1 for c in cells if not c) / len(cells) > TABLE_MAX_EMPTY_FRAC:
        return "mostly_empty"
    if sum(1 for c in rows[0] if c and not _NUMTOK_RX.match(c)) == 0:
        return "no_header_row"
    ne = [c for c in cells if c]

    def _stacked(c):  # несколько значений-строк в одной ячейке: «0.93/0.91» или одинаковые «ZX Bank/ZX Bank»
        ls = c.split("\n")
        return len(ls) >= 2 and (sum(1 for ln in ls if _NUMTOK_RX.match(ln)) >= 2 or len(set(ls)) < len(ls))

    merged = sum(1 for c in ne if sum(1 for t in c.split() if _NUMTOK_RX.match(t)) >= TABLE_MERGED_NUMS or _stacked(c))
    if merged / max(len(ne), 1) > TABLE_MERGED_FRAC:
        return "merged_numbers"
    stray = sum(1 for c in ne if _STRAY_PUNCT_RX.search(c) and _re.search(r"\d", c))
    if stray / max(len(ne), 1) > TABLE_MERGED_FRAC:
        return "stray_decimal_points"  # десятичная точка вынесена из числа отдельным глифом: «682% .»
    return None


def _table_html(rows):
    ncols = max(len(r) for r in rows)
    trs = []
    for r in rows:
        r = list(r) + [""] * (ncols - len(r))
        trs.append("<tr>" + "".join("<td>" + _html.escape(c.replace("\n", " "), quote=False) + "</td>" for c in r) + "</tr>")
    return "<table>\n" + "\n".join(trs) + "\n</table>"


def _big_images(page):
    import figures
    area = max(page.rect.width * page.rect.height, 1.0)
    return [r for r in figures._image_rects(page) if (r[2] - r[0]) * (r[3] - r[1]) >= IMAGE_MIN_FRAC * area]


def _overlaps_caption(img, cap_bbox):
    x0, y0, x1, y1 = (img[0] - CAPTION_NEAR_PT, img[1] - CAPTION_NEAR_PT,
                      img[2] + CAPTION_NEAR_PT, img[3] + CAPTION_NEAR_PT)
    return cap_bbox[0] < x1 and cap_bbox[2] > x0 and cap_bbox[1] < y1 and cap_bbox[3] > y0


def analyze_text_source(pdf, mode="auto") -> list:
    """Решение по каждой странице: [{page, source: pdf|ocr, reason, tables: [(bbox, html)], blocks, ...}].
    Признаки: текстовый слой (figures.page_state: ok/poor/none), подписи «Table N» против find_tables(),
    целостность таблиц, таблица-картинка (растр >= RASTER_OCR_FRAC рядом с подписью). mode=pdf — всё pdf."""
    import pymupdf
    import figures
    doc = pymupdf.open(str(pdf))
    try:
        all_blocks = [_page_text_blocks(p) for p in doc]
        infos = []
        for i, page in enumerate(doc):
            blocks = all_blocks[i]
            info = {"page": i + 1, "source": "pdf", "reason": "text_layer_ok", "tables": [], "blocks": blocks}
            infos.append(info)
            try:
                st = figures.page_state(page)
                caps = [b for b in blocks if any(TABLE_CAP_RX.match(ln) for ln in b["lines"])]  # подпись может идти в середине блока
                good, broken = [], []
                try:
                    words = page.get_text("words")
                    for tab in page.find_tables().tables:
                        rows = _table_rows(tab, words)
                        prob = _table_problem(rows)
                        if prob is None:
                            good.append((tuple(tab.bbox), _table_html(rows)))
                        else:
                            broken.append((prob, len(rows), max((len(r) for r in rows), default=0)))
                except Exception as e:  # noqa: BLE001
                    broken.append((f"find_tables_error:{type(e).__name__}", 0, 0))
                info["tables"] = good
                reason = None
                if st["text_layer"] in ("none", "poor"):
                    reason = f"text_layer_{st['text_layer']}"
                elif caps and st["raster_frac"] >= RASTER_OCR_FRAC and any(
                        _overlaps_caption(im, c["bbox"]) for im in _big_images(page) for c in caps):
                    reason = "image_table"
                elif caps and not good:
                    reason = "table_caption_without_text_table" + (f"({broken[0][0]})" if broken else "")
                elif caps and len(caps) > len(good):
                    reason = "table_caption_count_gt_tables"
                else:
                    bad = [b for b in broken if caps or (b[1] >= LOOSE_TABLE_ROWS and b[2] >= LOOSE_TABLE_COLS)]
                    if bad:
                        reason = f"broken_table({bad[0][0]})"
                if mode == "pdf":
                    info["reason"] = "mode_pdf" + (f"; {reason}" if reason else "")
                elif reason:
                    info["source"], info["reason"] = "ocr", reason
            except Exception as e:  # noqa: BLE001
                if mode != "pdf":
                    info["source"], info["reason"] = "ocr", f"analyze_error:{type(e).__name__}"
        bsize = _body_size([inf["blocks"] for inf in infos])
        run = _running_keys(all_blocks, [p.rect for p in doc])
        for inf, page in zip(infos, doc):
            inf["body_size"], inf["running"], inf["rect"] = bsize, run, tuple(page.rect)
        return infos
    finally:
        doc.close()


def _reading_order(items, pw):
    """items: [{bbox,...}] -> порядок чтения. Двухколоночная страница: блоки на всю ширину делят её на полосы,
    внутри полосы сначала левая колонка по y, потом правая. Иначе — по y, затем по x."""
    mid = pw / 2.0

    def spans(b):
        x0, _, x1, _ = b["bbox"]
        return (x1 - x0) > SPANNER_FRAC * pw or (x0 < mid - 10 and x1 > mid + 10)

    cols = [b for b in items if not spans(b)]
    left = [b for b in cols if (b["bbox"][0] + b["bbox"][2]) / 2 < mid]
    right = [b for b in cols if (b["bbox"][0] + b["bbox"][2]) / 2 >= mid]
    if len(left) < TWO_COL_MIN_BLOCKS or len(right) < TWO_COL_MIN_BLOCKS:
        return sorted(items, key=lambda b: (round(b["bbox"][1]), b["bbox"][0]))
    sp = sorted((b for b in items if spans(b)), key=lambda b: b["bbox"][1])
    lid = {id(b) for b in left}

    def key(b):
        if spans(b):
            return (sp.index(b), 2, b["bbox"][1])
        band = sum(1 for s in sp if s["bbox"][1] < b["bbox"][1])
        return (band, 0 if id(b) in lid else 1, b["bbox"][1])

    return sorted(items, key=key)


def build_pdf_page_markdown(info) -> str:
    """Markdown страницы из текстового слоя: блоки в порядке чтения, заголовки «## », таблицы find_tables() как HTML
    (как у glmocr), без колонтитулов и без блоков внутри таблиц."""
    rect = info["rect"]
    pw, ph = rect[2] - rect[0], rect[3] - rect[1]
    tabs = info["tables"]
    n_tick = sum(1 for b in info["blocks"] if _TICK_RX.match(b["text"]))
    items = []
    for b in info["blocks"]:
        x0, y0, x1, y1 = b["bbox"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(t[0][0] <= cx <= t[0][2] and t[0][1] <= cy <= t[0][3] for t in tabs):
            continue
        text = b["text"]
        if (y0 < rect[1] + MARGIN_BAND * ph or y1 > rect[3] - MARGIN_BAND * ph) and (
                _running_key(text) in info["running"] or _re.fullmatch(r"(?:page\s*)?\d{1,4}", text.strip(), _re.I)):
            continue  # колонтитул / номер страницы
        if n_tick >= 5 and _TICK_RX.match(text) and b["size"] < info["body_size"] * 0.95:
            continue  # подписи делений осей в векторных рисунках
        head = (b["nlines"] <= 2 and len(text) <= 150 and not TABLE_CAP_RX.match(text)
                and not _re.match(r"\s*fig(?:ure|\.)?\s*\d", text, _re.I)
                and (b["size"] >= info["body_size"] * HEADING_SIZE_RATIO
                     or (b["bold"] and len(text) <= 100 and not text.endswith("."))))
        items.append({"bbox": b["bbox"], "md": ("## " + text) if head else text})
    for bbox, html_ in tabs:
        items.append({"bbox": bbox, "md": html_})
    return "\n\n".join(it["md"] for it in _reading_order(items, pw))


def _ocr_page_texts(json_result):
    """list[str] по страницам из json_result glmocr или None, если форма неожиданная."""
    if not (isinstance(json_result, list) and json_result and all(isinstance(pg, list) for pg in json_result)):
        return None
    out = []
    for blocks in json_result:
        texts = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            content = b.get("content")
            if b.get("label") == "image" or not isinstance(content, str) or not content.strip():
                continue
            texts.append(content.strip())
        out.append("\n\n".join(texts))
    return out


# ---------- [faktura-patch] §10: ocr_confidence файла (0..1) ----------
# Определение (meta.ocr_confidence, meta.ocr_confidence_basis): среднее по страницам оценки надёжности текста страницы.
#   страница из текстового слоя PDF: ok -> 1.0, poor -> 0.5, none -> 0.2 (по figures.page_state / meta.text_source);
#   страница через glmocr: если в блоках json_result есть числовой confidence/score (0..1) - его среднее по блокам;
#     иначе эвристика: 0.9 * (1 - min(1, 5 * доля мусорных символов «�»/(cid:N) в тексте страницы));
#     пустой текст OCR - 0.5 (страница могла быть чисто графической).
# Потолок 0.9 для OCR-страниц без собственной оценки: распознавание не бывает увереннее текстового слоя.
OCR_HEURISTIC_CAP = 0.9
OCR_EMPTY_PAGE = 0.5
LAYER_SCORE = {"ok": 1.0, "poor": 0.5, "none": 0.2}


def _ocr_page_confs(json_result):
    """list[float|None] по страницам: среднее числового confidence/score блоков (0..1) или None, если его нет."""
    if not (isinstance(json_result, list) and json_result and all(isinstance(pg, list) for pg in json_result)):
        return None
    out = []
    for blocks in json_result:
        vals = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            for k in ("confidence", "score", "conf"):
                v = b.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0:
                    vals.append(float(v))
                    break
        out.append(sum(vals) / len(vals) if vals else None)
    return out


def ocr_page_score(text, conf=None):
    """Оценка 0..1 для страницы, распознанной glmocr (см. определение выше)."""
    if conf is not None:
        return max(0.0, min(1.0, float(conf)))
    t = text or ""
    if not t.strip():
        return OCR_EMPTY_PAGE
    bad = t.count("�") + 6 * len(re.findall(r"\(cid:\d+\)", t))
    return round(OCR_HEURISTIC_CAP * (1.0 - min(1.0, 5.0 * bad / max(len(t), 1))), 4)


def compute_ocr_confidence(pages):
    """pages: [{"source": "pdf"|"ocr", "reason": str, "text": str, "conf": float|None}] -> (ocr_confidence|None, basis)."""
    if not pages:
        return None, {"method": "none", "pages": 0}
    scores, n_pdf, n_ocr, n_native = [], 0, 0, 0
    for p in pages:
        if p.get("source") == "ocr":
            n_ocr += 1
            n_native += p.get("conf") is not None
            scores.append(ocr_page_score(p.get("text"), p.get("conf")))
        else:
            n_pdf += 1
            reason = p.get("reason") or ""
            layer = "none" if "text_layer_none" in reason else "poor" if "text_layer_poor" in reason else "ok"
            scores.append(LAYER_SCORE[layer])
    native = bool(n_ocr) and n_native == n_ocr and not n_pdf
    basis = {"method": "glmocr_confidence" if native else "text_layer_ocr_heuristic",
             "pages": len(pages), "pdf_pages": n_pdf, "ocr_pages": n_ocr, "ocr_pages_with_native_conf": n_native}
    return round(sum(scores) / len(scores), 3), basis


def _ocr_subset(parser, pdf, pages, work, stem, confs_out=None):
    """glmocr только на страницах `pages` (номера с 1): под-PDF через insert_pdf, parse(), страницы результата
    возвращаются к номерам исходного PDF. -> {page: text}."""
    import pymupdf
    sub, src = pymupdf.open(), pymupdf.open(str(pdf))
    sub_path = Path(work) / f"{stem}.ocr_subset.pdf"
    try:
        for p in pages:
            sub.insert_pdf(src, from_page=p - 1, to_page=p - 1)
        sub.save(str(sub_path))
    finally:
        sub.close()
        src.close()
    result = parser.parse(str(sub_path))
    result.save(output_dir=str(work))
    texts = _ocr_page_texts(getattr(result, "json_result", None))
    if confs_out is not None:
        pc = _ocr_page_confs(getattr(result, "json_result", None)) or []
        if len(pc) == len(pages):
            confs_out.update({pg: c for pg, c in zip(pages, pc) if c is not None})
    if texts is None or len(texts) != len(pages):
        raise PipelineError("ml_error", f"glmocr: под-PDF из {len(pages)} страниц вернул "
                                        f"{None if texts is None else len(texts)} страниц результата")
    return dict(zip(pages, texts))



# ---------- [faktura-patch] качество: проверка значений по тексту PDF и кандидаты для дозаполнения ----------
import html as _html

NUM = r"(\d{1,5}(?:[.,]\d+)?)"
# метрика -> (шаблоны «ключевое слово … число единица»); группа 1 — число, группа 2 (если есть) — единица
TEXT_PATTERNS = {
    "PCE": [r"(?:\bPCEs?\b|efficienc\w*|η)[^%\n]{0,80}?" + NUM + r"\s*%"],
    "FF": [r"(?:\bFF\b|fill factor)[^%\n]{0,60}?" + NUM + r"\s*%"],
    "Voc": [r"(?:V\s?_?\s?OC\b|V\s?oc\b|open[- ]circuit voltage)[^\n]{0,60}?" + NUM + r"\s*(m?V)\b"],
    "Jsc": [r"(?:J\s?_?\s?SC\b|J\s?sc\b|short[- ]circuit current(?: density)?)[^\n]{0,60}?" + NUM + r"\s*(mA|µA|μA|uA)"],
    "Rs": [r"(?:series resistance|\bR\s?_?\s?s\b|\bR\s?S\b)[^\n]{0,60}?" + NUM + r"\s*(kΩ|Ω|Ohm|ohm)"],
    "Rsh": [r"(?:shunt resistance|\bR\s?_?\s?sh\b|\bR\s?SH\b)[^\n]{0,60}?" + NUM + r"\s*(kΩ|Ω|Ohm|ohm)"],
}
# заголовок столбца таблицы -> метрика
HEADER_PATTERNS = [
    ("Jsc_EQE", r"EQE|integrat"), ("Voc", r"V\s*_?\{?\s*\\?(?:mathrm)?\{?\s*O\s*C|V\s*oc|V_\{oc\}|open"),
    ("Jsc", r"J\s*_?\{?\s*\\?(?:mathrm)?\{?\s*S\s*C|J\s*sc|J_\{sc\}|short"), ("FF", r"\bFF\b|fill"),
    ("Rsh", r"R\s*_?\{?\s*\\?(?:mathrm)?\{?\s*sh|shunt"), ("Rs", r"R\s*_?\{?\s*\\?(?:mathrm)?\{?\s*s\b|R_\{?s\}?|series"),
    ("HI", r"\bHI\b|hysteresis"), ("PCE", r"PCE|efficien|η|\\eta"),
]
FAMILY = {"stabilized_PCE": "PCE", "retained_PCE": "PCE"}


def _num(s):
    try:
        return float(str(s).replace(",", ".").replace("−", "-"))
    except (TypeError, ValueError):
        return None


_REF_HEAD = _re.compile(r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:References|REFERENCES|Bibliography|Literature Cited|Notes and references)\s*\n")


def _page_texts(pdf, cut_refs=True):
    """Текст страниц PDF; всё после заголовка списка литературы (в последних 60% документа) отбрасывается.
    [faktura-patch] cut_refs=False — без обрезки (RAG: таблицы результатов в приложениях после References)."""
    import pymupdf
    raw = [p.get_text() for p in pymupdf.open(str(pdf))]
    cut_page, cut_pos = None, None
    for i in range(int(len(raw) * 0.4), len(raw) if cut_refs else 0):
        m = _REF_HEAD.search(raw[i])
        if m:
            cut_page, cut_pos = i, m.start()
            break
    out = []
    for i, t in enumerate(raw):
        if cut_page is not None and i > cut_page:
            t = ""
        elif i == cut_page:
            t = t[:cut_pos]
        t = (t.replace("−", "-").replace(" ", " ").replace(" ", " ")
             .replace("ﬃ", "ffi").replace("ﬄ", "ffl").replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff"))  # [faktura-patch] лигатуры
        t = _re.sub(r"-\n(?=[a-z])", "", t)
        out.append(_re.sub(r"[ \t]*\n[ \t]*", " ", t))
    return out


def _forms(v):
    out = set()
    for x in (v, v * 1000, v / 1000, v * 100, v / 100):
        if x:
            s = f"{x:.6f}".rstrip("0").rstrip(".")
            if s.replace(".", "").strip("0"):
                out.add(s)
    return out


def _has_value(text, v, leading_zero=False):
    for s in _forms(v):
        ip, _, dp = s.partition(".")
        rx = r"(?<![\d.])" + _re.escape(ip) + (r"[.,]" + _re.escape(dp) + r"0*" if dp else r"(?:[.,]0+)?") + r"(?![\d])"
        if leading_zero and ip == "0" and dp:  # [faktura-patch] RAG: «.204» без ведущего нуля
            rx = r"(?<![\d.])0?[.,]" + _re.escape(dp) + r"0*(?![\d])"
        if _re.search(rx, text):
            return True
    return False


def _md_pages(md):
    """markdown glmocr с метками <!-- page N --> -> {N: текст}."""
    out, cur = {}, 1
    for part in _re.split(r"(<!-- page \d+ -->)", md or ""):
        m = _re.match(r"<!-- page (\d+) -->", part)
        if m:
            cur = int(m.group(1))
        else:
            out[cur] = out.get(cur, "") + part
    return out


def verify_values(results, pdf, pages=None, md=None, leading_zero=False):
    """Числовое значение должно встречаться в тексте PDF ИЛИ в OCR-markdown (таблица может быть картинкой —
    тогда чисел нет в текстовом слое, но OCR их прочитал). Нет нигде -> строка отбрасывается (выдумка);
    есть только на другой странице -> исправить source_page. Возвращает (оставленные строки, статистика)."""
    pages = pages or _page_texts(pdf)
    mdp = _md_pages(md) if md else {}
    if mdp:  # [faktura-patch] страница = текстовый слой + OCR той же страницы
        pages = [t + "\n" + mdp.get(i + 1, "") for i, t in enumerate(pages)]
    keep, st = [], {"page": 0, "page_fixed": 0, "dropped": 0, "nonnum": 0}
    for r in results:
        v = _num(r.get("raw_value")) if r.get("raw_value") is not None else _num(r.get("metric_value"))
        if v is None or r.get("extraction_status") not in (None, "extracted"):
            st["nonnum"] += 1
            keep.append(r)
            continue
        try:
            n = int(r.get("source_page")) - 1
        except (TypeError, ValueError):
            n = -1
        if 0 <= n < len(pages) and _has_value(pages[n], v, leading_zero):
            st["page"] += 1
            keep.append(r)
            continue
        other = [k for k, t in enumerate(pages) if _has_value(t, v, leading_zero)]
        if other:
            if len(other) == 1:
                r["source_page"] = other[0] + 1
            st["page_fixed"] += 1
            keep.append(r)
        else:
            st["dropped"] += 1
            r["_dropped_reason"] = "значение не найдено в тексте PDF"
    return keep, st


def _table_candidates(md):
    """HTML-таблицы glmocr: столбец-показатель по заголовку, каждая числовая ячейка — кандидат."""
    out = []
    page = 1
    pos_pages = [(m.start(), int(m.group(1))) for m in _re.finditer(r"<!-- page (\d+) -->", md)]
    for tm in _re.finditer(r"<table.*?</table>", md, _re.S):
        page = max([p for s, p in pos_pages if s < tm.start()] or [1])
        cap = md[max(0, tm.start() - 300):tm.start()]
        capm = _re.findall(r"(Table\s*\d+[^\n]{0,120})", cap)
        caption = capm[-1].strip() if capm else ""
        rows = []
        for tr in _re.findall(r"<tr>(.*?)</tr>", tm.group(0), _re.S):
            cells = [_html.unescape(_re.sub(r"<[^>]+>", "", c)).strip() for c in _re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, _re.S)]
            rows.append(cells)
        if not rows:
            continue
        # заголовок: первые строки, где есть ключевые слова
        hdr_idx, colmetric = None, {}
        for hi, row in enumerate(rows[:3]):
            cm = {}
            for ci, c in enumerate(row):
                for m, rx in HEADER_PATTERNS:
                    if _re.search(rx, c, _re.I):
                        cm[ci] = m
                        break
            if len(cm) >= 1:
                hdr_idx, colmetric = hi, cm
                break
        if hdr_idx is None:
            continue
        ncols_hdr = len(rows[hdr_idx])
        scale = {}
        for ci, m in colmetric.items():
            h = rows[hdr_idx][ci] if ci < len(rows[hdr_idx]) else ""
            hs = _re.sub(r"[\s{}\\]|mathrm", "", h)
            if m == "Voc" and "mV" in hs:
                scale[ci] = 0.001
            elif m in ("Jsc", "Jsc_EQE") and _re.search(r"[µμu]A", hs):
                scale[ci] = 0.001
            elif m in ("Rs", "Rsh") and _re.search(r"kΩ|kOmega|kOhm", hs):
                scale[ci] = 1000.0
        for row in rows[hdr_idx + 1:]:
            off = ncols_hdr - len(row) if len(row) < ncols_hdr else 0   # rowspan слева
            label = " ".join(c for c in row[: max(1, min(row and len(row), 2))] if c and _num(c) is None)
            for ci, c in enumerate(row):
                m = colmetric.get(ci + off)
                if not m:
                    continue
                mm = _re.match(r"\s*\$?\s*(\d{1,5}(?:[.,]\d+)?)", c)
                if mm:
                    out.append({"metric": m, "value": _num(mm.group(1)) * scale.get(ci + off, 1.0), "page": page, "source": "table",
                                "context": f"{caption} | строка: {label} | ячейка: {c}"})
    return out


def mine_candidates(pdf, md, pages=None):
    pages = pages or _page_texts(pdf)
    out = []
    for i, t in enumerate(pages):
        for m, pats in TEXT_PATTERNS.items():
            for rx in pats:
                for mm in _re.finditer(rx, t, _re.I):
                    v = _num(mm.group(1))
                    unit = (mm.group(2) if mm.lastindex and mm.lastindex >= 2 else "") or ""
                    if v is None:
                        continue
                    if m == "Voc" and unit.lower() == "mv":
                        v /= 1000
                    if m in ("Rs", "Rsh") and unit.lower().startswith("k"):
                        v *= 1000
                    if m == "Jsc" and unit.lower() in ("µa", "μa", "ua"):
                        v /= 1000
                    a, b = max(0, mm.start() - 120), min(len(t), mm.end() + 60)
                    out.append({"metric": m, "value": v, "page": i + 1, "source": "text",
                                "context": t[a:b].strip()})
    if md:
        out += _table_candidates(md)
    # [faktura-patch] значения только в доп. материалах: «reported in Table S5», «Supplementary Table S2»
    supp = _re.compile(r"(?:reported|summari[sz]ed|listed|given|provided|tabulated|presented|shown)\s+in\s+(?:the\s+)?(?:Supplementary\s+|Supporting\s+Information\s+)?Table\s*S\d+|Supplementary\s+Table\s*S?\d+", _re.I)
    for i, t in enumerate(pages):
        for sm in supp.finditer(t):
            a0, b0 = max(0, sm.start() - 160), min(len(t), sm.end() + 60)
            win = t[a0:b0]
            supp_kw = {
                "PCE": r"\bPCEs?\b|efficienc", "FF": r"\bFF\b|fill factor",
                "Voc": r"V\s?_?\s?OC\b|\bV\s?oc\b|open[- ]circuit", "Jsc": r"J\s?_?\s?SC\b|\bJ\s?sc\b|short[- ]circuit",
                "Rs": r"series resistance", "Rsh": r"shunt resistance"}
            # общие формулировки («external/photovoltaic/J-V parameters») — про все параметры ВАХ
            generic = _re.search(r"(?:external|photovoltaic|J\s?[-–]\s?V|device)\s+(?:performance\s+)?parameters", win, _re.I)
            for m, key in supp_kw.items():
                if _re.search(key, win, _re.I) or (generic and m in ("PCE", "Voc", "Jsc", "FF")):
                    out.append({"metric": m, "value": None, "page": i + 1, "source": "supplementary",
                                "context": win.strip()})
    # дубли кандидатов (метрика, значение, страница) — один
    seen, uniq = set(), []
    for c in out:
        k = (c["metric"], round(c["value"], 6) if c["value"] is not None else None, c["page"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def uncovered(cands, results):
    """Кандидаты, чьих (семейство метрики, значение) нет среди извлечённых results (любой страницы)."""
    have = []
    for r in results:
        v = _num(r.get("metric_value")) if r.get("metric_value") not in (None, "") else _num(r.get("raw_value"))
        if v is not None:
            m = r.get("metric_name") or ""
            have.append((FAMILY.get(m, m), v))

    def close(a, b):
        return abs(a - b) <= max(abs(b) * 0.005, 1e-9)
    pne = {FAMILY.get(r.get("metric_name") or "", r.get("metric_name") or "") for r in results
           if r.get("extraction_status") == "present_not_extractable"}
    out = []
    for c in cands:
        fam = FAMILY.get(c["metric"], c["metric"])
        if c["value"] is None:
            if fam not in pne:
                out.append(c)
        elif not any(fam == hm and close(c["value"], hv) for hm, hv in have):
            out.append(c)
    return out




# ---------- [faktura-patch] DOI и год из текстового слоя PDF: glmocr отбрасывает колонтитулы ----------
DOI_RX = _re.compile(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", _re.I)
YEAR = r"((?:19|20)\d\d)"


def _clean_doi(d):
    d = d.rstrip(".)]")
    return d


def pub_meta_from_pdf(pdf, pages=3):
    """-> (doi | None, year | None) по первым страницам PDF."""
    import pymupdf
    doc = pymupdf.open(str(pdf))
    text = "\n".join(doc[i].get_text() for i in range(min(pages, len(doc))))
    text = text.replace("‐", "-").replace("‑", "-")
    doi = None
    # приоритет: явная метка DOI / doi.org, затем любое 10.xxxx/...
    m = _re.search(r"(?:doi\.org/|DOI[:\s]*|doi[:\s]+)" + DOI_RX.pattern, text, _re.I)
    if m:
        doi = _clean_doi(m.group(1))
    else:
        m = DOI_RX.search(text)
        if m:
            doi = _clean_doi(m.group(1))
    year = None
    for rx in (r"Published(?:\s+online)?[:\s]+[^\n]{0,40}?" + YEAR,
               r"(?:Version of Record|Available online)[:\s]+[^\n]{0,40}?" + YEAR,
               r"©\s*" + YEAR, r"\(c\)\s*" + YEAR, r"Copyright\s*©?\s*" + YEAR):
        m = _re.search(rx, text, _re.I)
        if m:
            year = m.group(1)
            break
    if year is None and doi:
        m = _re.search(r"[./](20\d\d)\d{2,}", doi)   # solr.202200501, j.mtener.2024.101715
        if m:
            year = m.group(1)
    if year is None:
        ys = _re.findall(r"(?:Received|Accepted|Revised)[:\s]+[^\n]{0,40}?" + YEAR, text, _re.I)
        if ys:
            year = max(ys)
    return doi, year


def fill_pub_meta(dataset_path, pdf) -> dict:
    """Пустые DOI/year в publications заполняются из PDF; заполненные моделью не трогаем."""
    data = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    pubs = data.get("publications") or []
    st = {}
    if pubs:
        doi, year = pub_meta_from_pdf(pdf)
        p0 = pubs[0]
        if not p0.get("DOI") and doi:
            p0["DOI"] = doi
            st["DOI"] = "pdf"
        if not p0.get("year") and year:
            p0["year"] = int(year)
            st["year"] = "pdf"
        Path(dataset_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return st


def run_pipeline(pdf, *, out_dir="/out", publication_id=None, no_strip=False, domain="solar") -> dict:
    """Прогоняет PDF через весь конвейер. Бросает исключение при фатальной ошибке
    (PDF не найден, glmocr не установлен/не создал md).

    Возвращает dict: ok, publication_id, extract_error, failed_tables,
    out_dir, paths {markdown,dataset,meta}, plus метаданные в meta.
    """
    _t_start = time.time()  # [faktura-patch] общее время разбора -> meta.total_seconds
    if domain not in ("solar", "rag"):  # [faktura-patch]
        raise PipelineError("domain_not_supported", f"область {domain!r} не поддерживается (solar | rag)")
    pdf = Path(pdf)
    if not pdf.exists():
        raise FileNotFoundError(f"Файл не найден: {pdf}")
    if not os.environ.get("ROUTERAI_API_KEY"):
        raise PipelineError("not_configured",
                            "Нет ROUTERAI_API_KEY в окружении — извлечение датасета будет невозможным.")
    config = config_path()
    # [faktura-patch] проверяем PDF и доступность OCR ДО тяжёлой работы
    check_pdf(pdf)
    # [faktura-patch] R6: решение по страницам (auto/pdf) до обращения к OCR; ocr — как раньше, без анализа
    ts_mode = text_source_mode()
    ts_infos = None
    if ts_mode != "ocr":
        try:
            ts_infos = analyze_text_source(pdf, ts_mode)
        except Exception as e:  # noqa: BLE001
            print(f"[!] анализ текстового слоя не удался ({type(e).__name__}: {e}) — OCR всех страниц", flush=True)
            ts_mode, ts_infos = "ocr", None
    ocr_pages = ([i["page"] for i in ts_infos if i["source"] == "ocr"] if ts_infos is not None else None)
    need_ocr = ts_infos is None or (ts_mode == "auto" and bool(ocr_pages))
    if need_ocr:
        if not config.exists():
            raise PipelineError("not_configured",
                                f"Нет конфига glmocr: {config} (задайте GLMOCR_CONFIG=/path/to/config.yaml)")
        if not ocr_reachable(config):
            raise PipelineError("ocr_unavailable", f"OCR-сервер недоступен ({ocr_endpoint(config)})",
                                retryable=True)

    out_dir = Path(out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    stem = pdf.stem
    pub_id = publication_id or stem

    # ---------- 1. glmocr: PDF -> markdown ----------
    if need_ocr:
        try:
            import glmocr  # noqa: F401
        except ImportError as e:
            raise PipelineError("ml_error", "glmocr не установлен в образе (pip install 'glmocr[selfhosted]')") from e

    ocr_seconds = 0.0
    text_source_meta = None
    conf_pages = None  # [faktura-patch] §10: данные для meta.ocr_confidence
    if ts_infos is None:
        print(f"[1/3] glmocr: {pdf.name} -> markdown (config={config})", flush=True)
        t0 = time.time()
        try:
            parser = _get_parser(config)  # [faktura-patch] переиспользуем прогретый парсер
            result = parser.parse(str(pdf))
            result.save(output_dir=str(work))
        except PipelineError:
            raise
        except Exception as e:
            if _PARSER is not None and not _is_connection_error(e):
                _close_parser()  # неизвестный сбой — пересоздадим парсер на следующей статье
            if _is_connection_error(e):
                raise PipelineError("ocr_unavailable", f"OCR-сервер недоступен: {e}", retryable=True) from e
            raise PipelineError("ml_error", f"glmocr: {type(e).__name__}: {e}") from e
        ocr_seconds = round(time.time() - t0, 2)

        md_orig = work / stem / f"{stem}.md"
        if not md_orig.exists():
            raise PipelineError("ml_error", f"glmocr не создал {md_orig} — смотрите лог выше")
        raw_md = md_orig.read_text(encoding="utf-8", errors="replace")
        # [faktura-patch] markdown для LLM — постранично, с маркерами <!-- page N -->
        md_text, paged = build_paged_markdown(getattr(result, "json_result", None),
                                              getattr(result, "markdown_result", None) or raw_md)
        try:
            jr = getattr(result, "json_result", None)
            _t, _c = _ocr_page_texts(jr), (_ocr_page_confs(jr) or [])
            if _t:
                conf_pages = [{"source": "ocr", "text": t, "conf": (_c[i] if i < len(_c) else None)}
                              for i, t in enumerate(_t)]
        except Exception:  # noqa: BLE001 - оценка качества не роняет прогон
            conf_pages = None
    else:
        # [faktura-patch] R6: страницы из текстового слоя + OCR только там, где нужно
        print(f"[1/3] text_source={ts_mode}: {len(ts_infos) - len(ocr_pages)} стр. из PDF, "
              f"{len(ocr_pages)} стр. через glmocr", flush=True)
        ocr_texts, ocr_confs = {}, {}
        if need_ocr:
            t0 = time.time()
            try:
                ocr_texts = _ocr_subset(_get_parser(config), pdf, ocr_pages, work, stem, ocr_confs)
            except PipelineError:
                raise
            except Exception as e:
                if _PARSER is not None and not _is_connection_error(e):
                    _close_parser()
                if _is_connection_error(e):
                    raise PipelineError("ocr_unavailable", f"OCR-сервер недоступен: {e}", retryable=True) from e
                raise PipelineError("ml_error", f"glmocr: {type(e).__name__}: {e}") from e
            ocr_seconds = round(time.time() - t0, 2)
        parts = []
        for inf in ts_infos:
            body = ocr_texts.get(inf["page"], "") if inf["source"] == "ocr" else build_pdf_page_markdown(inf)
            parts.append(f"<!-- page {inf['page']} -->\n\n{body}")
        md_text, paged = "\n\n".join(parts), True
        text_source_meta = {
            "mode": ts_mode,
            "pages": [{"page": i["page"], "source": i["source"], "reason": i["reason"]} for i in ts_infos],
            "ocr_pages": len(ocr_pages), "pdf_pages": len(ts_infos) - len(ocr_pages)}
    if ts_infos is not None:
        conf_pages = [{"source": i["source"], "reason": i["reason"], "text": ocr_texts.get(i["page"], ""),
                       "conf": ocr_confs.get(i["page"])} for i in ts_infos]
    md_src = work / f"{stem}.paged.md"
    md_src.write_text(md_text, encoding="utf-8")
    print(f"      готово за {ocr_seconds}s, markdown {len(md_text)} символов "
          f"(маркеры страниц: {'да' if paged else 'НЕТ, fallback'})", flush=True)

    # ---------- 2. strip_tail: режем хвостовые разделы ----------
    strip_info = None
    if no_strip:
        clean_md = md_src
        print("[2/3] strip_tail пропущен (--no-strip)", flush=True)
    else:
        from strip_tail import strip_trailing_sections, strip_references_only
        # [faktura-patch] RAG: приложения с таблицами результатов сохраняем — режется только список литературы
        md_clean, strip_info = (strip_references_only(md_text) if domain == "rag" else strip_trailing_sections(md_text))
        clean_md = work / f"{stem}.clean.md"
        clean_md.write_text(md_clean, encoding="utf-8")
        if strip_info:
            print(f"[2/3] strip_tail: отрезано от строки {strip_info['line']} "
                  f"(«{strip_info['heading']}»), −{strip_info['removed_chars']} симв.", flush=True)
        else:
            print("[2/3] strip_tail: хвостовых разделов не найдено", flush=True)

    # ---------- 3. извлечение датасета: markdown -> dataset.json (+ meta.json) ----------
    dataset_out = out_dir / f"{stem}.dataset.json"
    meta_out = out_dir / f"{stem}.meta.json"
    print("[3/3] извлечение датасета (по таблице за вызов)...", flush=True)

    extract_failed = None
    failed_tables = []
    from extract_dataset import extract, LLMError
    _t_llm = time.time()
    try:
        res = extract(clean_md,
                      publication_id=pub_id,
                      out=str(dataset_out),
                      model=os.environ.get("ROUTERAI_MODEL", DEFAULT_MODEL),
                      provider=os.environ.get("ROUTERAI_PROVIDER", "wafer"),
                      meta_path=str(meta_out),
                      pdf_path=pdf,
                      **({"domain": domain} if domain != "solar" else {}))  # [faktura-patch] solar — вызов как раньше
        failed_tables = res["failed_tables"]
    except LLMError as e:
        extract_failed = str(e)
        failed_tables = ["<all>"]
        print(f"[!] извлечение прервано: {e}", flush=True)
    except Exception as e:  # noqa: BLE001
        # [faktura-patch] лимит трат ключа RouterAI (HTTP 429 «spending limit») — отдельная понятная ошибка
        msg = str(e)
        if "spending limit" in msg or "insufficient" in msg.lower() or type(e).__name__ == "RateLimitError":
            raise PipelineError("quota_exceeded",
                                "Исчерпан лимит трат ключа RouterAI: " + msg[:200], retryable=False) from e
        raise

    llm_wall = round(time.time() - _t_llm, 2)
    _t_snap = time.time()
    snap_stats = None
    if dataset_out.exists():
        try:
            snap_stats = snap_fragments(dataset_out, pdf)
            pub_fill = fill_pub_meta(dataset_out, pdf)
            if pub_fill:
                snap_stats = {**snap_stats, "pub_meta": pub_fill}
            print(f"      цитаты: {snap_stats}", flush=True)
        except Exception as e:  # цитаты — улучшение, не роняем прогон
            print(f"[!] подтяжка цитат не удалась: {e}", flush=True)

    snap_seconds = round(time.time() - _t_snap, 2)

    # ---------- метаданные прогона поверх extract-meta ----------
    pages = None
    try:
        import pymupdf
        pages = pymupdf.open(str(pdf)).page_count
    except Exception:
        pass

    meta = {}
    if meta_out.exists():
        try:
            meta = json.loads(meta_out.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    md_copy = out_dir / f"{stem}.md"
    shutil.copy(clean_md, md_copy)
    try:
        _conf = compute_ocr_confidence(conf_pages)
    except Exception:  # noqa: BLE001
        _conf = (None, {"method": "error"})
    meta.update({
        "input_pdf": str(pdf),
        "domain": domain,  # [faktura-patch]
        "publication_id": pub_id,
        "pages": pages,
        "page_markers": paged,
        "ocr_seconds": ocr_seconds,
        "markdown_chars": len(md_text),
        "glmocr_config": str(config),
        "glmocr_config_text": config.read_text(encoding="utf-8", errors="replace") if config.exists() else None,
        "ocr_confidence": _conf[0], "ocr_confidence_basis": _conf[1],  # [faktura-patch] §10
        "text_source": text_source_meta or {"mode": "ocr", "pages": None, "ocr_pages": pages, "pdf_pages": 0},  # [faktura-patch] R6
        "strip_tail": strip_info,
        "quote_snap": snap_stats,
        "total_seconds": round(time.time() - _t_start, 2),
        "cost": round(sum(((s.get("usage") or {}).get("cost") or 0) for s in (meta.get("steps") or [])), 4),
        "timings": {"ocr": ocr_seconds, "llm_wall": llm_wall, "snap": snap_seconds,
                    "total": round(time.time() - _t_start, 2),
                    **{k: v for k, v in ((meta.get("rag") or {}).get("timings") or {}).items()}},  # [faktura-patch] figures_*
        "failed_tables": meta.get("failed_tables", failed_tables),
        "extract_error": extract_failed,
        "outputs": {
            "markdown": str(md_copy),
            "dataset": str(dataset_out) if dataset_out.exists() else None,
            "meta": str(meta_out),
        },
    })
    meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(flush=True)
    print("Готово.", flush=True)
    print(f"  markdown: {md_copy}", flush=True)
    print(f"  dataset:  {dataset_out if dataset_out.exists() else 'НЕ СОЗДАН'}", flush=True)
    print(f"  meta:     {meta_out}", flush=True)

    dataset = None
    if dataset_out.exists():
        try:
            dataset = json.loads(dataset_out.read_text(encoding="utf-8"))
        except Exception:
            dataset = None

    return {
        "ok": extract_failed is None,
        "model_version": model_version(),
        "publication_id": pub_id,
        "extract_error": extract_failed,
        "failed_tables": meta.get("failed_tables", failed_tables),
        "out_dir": str(out_dir),
        "paths": meta["outputs"],
        "meta": meta,
        "dataset": dataset,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF -> dataset.json одним процессом")
    ap.add_argument("pdf", help="путь к PDF (например, смонтированному в /data)")
    ap.add_argument("--out-dir", default="/out", help="куда писать результаты [/out]")
    ap.add_argument("--publication-id", default=None, help="id публикации (по умолчанию — имя файла)")
    ap.add_argument("--no-strip", action="store_true", help="не обрезать хвостовые разделы")
    ap.add_argument("--domain", default="solar", choices=["solar", "rag"], help="предметная область [solar]")  # [faktura-patch]
    args = ap.parse_args()

    try:
        result = run_pipeline(args.pdf, out_dir=args.out_dir,
                              publication_id=args.publication_id,
                              no_strip=args.no_strip, domain=args.domain)
    except (FileNotFoundError, RuntimeError, PipelineError) as e:
        sys.exit(f"Ошибка: {e}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
