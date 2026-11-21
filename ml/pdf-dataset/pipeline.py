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
              "not_configured": 503, "ml_error": 500}

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
    for name in ("pipeline.py", "extract_dataset.py", "strip_tail.py"):
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


def _page_texts(pdf):
    """Текст страниц PDF; всё после заголовка списка литературы (в последних 60% документа) отбрасывается."""
    import pymupdf
    raw = [p.get_text() for p in pymupdf.open(str(pdf))]
    cut_page, cut_pos = None, None
    for i in range(int(len(raw) * 0.4), len(raw)):
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


def _has_value(text, v):
    for s in _forms(v):
        ip, _, dp = s.partition(".")
        rx = r"(?<![\d.])" + _re.escape(ip) + (r"[.,]" + _re.escape(dp) + r"0*" if dp else r"(?:[.,]0+)?") + r"(?![\d])"
        if _re.search(rx, text):
            return True
    return False


def verify_values(results, pdf, pages=None):
    """Числовое значение должно встречаться в тексте PDF. Нет нигде -> строка отбрасывается (обычно OCR с
    картинки графика или выдумка); есть только на другой странице -> исправить source_page.
    Возвращает (оставленные строки, статистика)."""
    pages = pages or _page_texts(pdf)
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
        if 0 <= n < len(pages) and _has_value(pages[n], v):
            st["page"] += 1
            keep.append(r)
            continue
        other = [k for k, t in enumerate(pages) if _has_value(t, v)]
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


def run_pipeline(pdf, *, out_dir="/out", publication_id=None, no_strip=False) -> dict:
    """Прогоняет PDF через весь конвейер. Бросает исключение при фатальной ошибке
    (PDF не найден, glmocr не установлен/не создал md).

    Возвращает dict: ok, publication_id, extract_error, failed_tables,
    out_dir, paths {markdown,dataset,meta}, plus метаданные в meta.
    """
    _t_start = time.time()  # [faktura-patch] общее время разбора -> meta.total_seconds
    pdf = Path(pdf)
    if not pdf.exists():
        raise FileNotFoundError(f"Файл не найден: {pdf}")
    if not os.environ.get("ROUTERAI_API_KEY"):
        raise PipelineError("not_configured",
                            "Нет ROUTERAI_API_KEY в окружении — извлечение датасета будет невозможным.")
    config = config_path()
    if not config.exists():
        raise PipelineError("not_configured",
                            f"Нет конфига glmocr: {config} (задайте GLMOCR_CONFIG=/path/to/config.yaml)")
    # [faktura-patch] проверяем PDF и доступность OCR ДО тяжёлой работы
    check_pdf(pdf)
    if not ocr_reachable(config):
        raise PipelineError("ocr_unavailable", f"OCR-сервер недоступен ({ocr_endpoint(config)})",
                            retryable=True)

    out_dir = Path(out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    stem = pdf.stem
    pub_id = publication_id or stem

    # ---------- 1. glmocr: PDF -> markdown ----------
    try:
        import glmocr  # noqa: F401
    except ImportError as e:
        raise PipelineError("ml_error", "glmocr не установлен в образе (pip install 'glmocr[selfhosted]')") from e

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
        from strip_tail import strip_trailing_sections
        md_clean, strip_info = strip_trailing_sections(md_text)
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
                      pdf_path=pdf)
        failed_tables = res["failed_tables"]
    except LLMError as e:
        extract_failed = str(e)
        failed_tables = ["<all>"]
        print(f"[!] извлечение прервано: {e}", flush=True)

    llm_wall = round(time.time() - _t_llm, 2)
    _t_snap = time.time()
    snap_stats = None
    if dataset_out.exists():
        try:
            snap_stats = snap_fragments(dataset_out, pdf)
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
    meta.update({
        "input_pdf": str(pdf),
        "publication_id": pub_id,
        "pages": pages,
        "page_markers": paged,
        "ocr_seconds": ocr_seconds,
        "markdown_chars": len(md_text),
        "glmocr_config": str(config),
        "glmocr_config_text": config.read_text(encoding="utf-8", errors="replace"),
        "strip_tail": strip_info,
        "quote_snap": snap_stats,
        "total_seconds": round(time.time() - _t_start, 2),
        "timings": {"ocr": ocr_seconds, "llm_wall": llm_wall, "snap": snap_seconds,
                    "total": round(time.time() - _t_start, 2)},  # [faktura-patch]
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
    args = ap.parse_args()

    try:
        result = run_pipeline(args.pdf, out_dir=args.out_dir,
                              publication_id=args.publication_id,
                              no_strip=args.no_strip)
    except (FileNotFoundError, RuntimeError, PipelineError) as e:
        sys.exit(f"Ошибка: {e}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
