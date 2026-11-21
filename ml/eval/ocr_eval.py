#!/usr/bin/env python3
"""Оценка блочного OCR (glmocr: layout + пер-блочные промпты) против текстового слоя PDF.

Нужна, чтобы команда могла держать быстрый Q8-вариант GLM-OCR и не терять качество таблиц: прогнать до/после смены
модели/ручек и сравнить отчёты. Запускается ВНУТРИ ML-контейнера (нужен vLLM на host.docker.internal:8123 из
glmocr-конфига; RouterAI не вызывается). ml/eval в образ не входит — скопируйте файл вручную:

    docker cp ml/eval/ocr_eval.py <ml-container>:/app/ocr_eval.py
    docker exec <ml-container> python /app/ocr_eval.py /data/D002.pdf /data/D005.pdf --whole-page --out /out/ocr_eval.json

Использование:
    ocr_eval.py PDF_OR_DIR [PDF_OR_DIR ...] [--top 2] [--pages 5,12] [--whole-page]
                [--out report.json] [--min-words 60] [--no-table]

  --top N        страниц на PDF (по умолчанию 2) с наибольшим числом чисел в текстовом слое
  --pages LIST   явные страницы (с 1, '5,12-13') для ВСЕХ PDF вместо --top
  --whole-page   дополнительно OCR целой страницы одним запросом «Text Recognition:» (как у поиска коллег) —
                 показывает разницу с блочным OCR
  --out FILE     JSON-отчёт (иначе только таблица в stdout)
  --min-words N  страница без текстового слоя (< N слов) пропускается: сравнивать не с чем

Конфиг: GLMOCR_CONFIG (как у сервиса); ручки OCR_PDF_DPI, OCR_MAX_TOKENS, OCR_LAYOUT_* действуют так же, как в сервисе
(поэтому для A/B достаточно запустить дважды с разными env). Каталог сервиса: PDF_DATASET_DIR (по умолчанию /app или
../pdf-dataset).

Метрики (после нормализации: снятие HTML/markdown/LaTeX-разметки, NFKC, лигатуры, склейка переносов, lowercase):
  CER           расстояние Левенштейна / длину эталона (эталон — текстовый слой страницы целиком; колонтитулы и сноски
                glmocr отбрасывает, поэтому у страницы CER чуть выше нуля даже при идеальном OCR)
  word P/R      мультимножество слов; number P/R — мультимножество чисел
  таблицы       для каждого блока label=table: числа в блоке OCR против чисел слов текстового слоя внутри bbox блока
                (PyMuPDF get_text('words')); tbl_num_P/R суммарно по страницам, tbl_merged — число «склеенных» токенов
                (токен OCR = конкатенации двух соседних слов эталона, напр. GEMINI1.0Pro)
  время         секунды на страницу (блочный OCR гонится по одной странице, чтобы время было честным)
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

LIGS = {"ﬃ": "ffi", "ﬄ": "ffl", "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬅ": "st", "ﬆ": "st"}
BREAK_TAG_RX = re.compile(r"<\s*/?\s*(?:br|p|div|li|tr|td|th|table)\b[^>]*>", re.I)
NUM_RX = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:[.,]\d+)*|[.,]\d+)")


# ---------------------------------------------------------------- нормализация и метрики (чистые функции)
def normalize(text: str) -> str:
    """Текст для сравнения: без разметки (HTML/markdown/LaTeX), NFKC, лигатуры, склейка переносов, lowercase."""
    s = text or ""
    s = BREAK_TAG_RX.sub(" ", s)              # границы ячеек/строк -> пробел
    s = re.sub(r"<[^>]+>", "", s)             # прочие теги (sub/sup/b/i/span)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)             # картинки markdown
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)          # ссылки -> текст
    s = re.sub(r"\\(?:mathrm|mathbf|mathit|text|textbf|textit|operatorname|rm|bf|it)\b", "", s)
    s = re.sub(r"\\(?:left|right|big|Big|quad|qquad|,|;|:|!)", " ", s)
    s = re.sub(r"\\[A-Za-z]+", lambda m: m.group(0)[1:] if m.group(0)[1:] in
               ("alpha", "beta", "gamma", "delta", "eta", "mu", "sigma", "Omega", "pm", "times") else " ", s)
    s = s.replace("\\%", "%").replace("\\_", "_").replace("\\&", "&")
    s = re.sub(r"[$^_{}\\]", "", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*", "", s, flags=re.M)     # заголовки
    s = re.sub(r"^\s*[-*+]\s+", "", s, flags=re.M)          # маркеры списка
    s = re.sub(r"[*`|]", " ", s)                            # жирный/курсив/код/границы таблицы
    s = unicodedata.normalize("NFKC", s)
    for k, v in LIGS.items():
        s = s.replace(k, v)
    s = s.replace("−", "-").replace("–", "-").replace("—", "-").replace("­", "")
    s = re.sub(r"(?<=\w)-\s*\n\s*(?=[a-zа-я])", "", s)      # перенос слова
    return re.sub(r"\s+", " ", s).strip().lower()


def words(text: str) -> list:
    return re.findall(r"[^\W_]+", normalize(text))


def numbers(text: str) -> list:
    """Числа из текста (после нормализации): тысячные запятые убраны, десятичная запятая -> точка."""
    out = []
    for m in NUM_RX.findall(normalize(text)):
        t = m.lstrip("+")
        if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", t):
            t = t.replace(",", "")
        t = t.replace(",", ".")
        out.append(t)
    return out


def levenshtein(a: str, b: str) -> int:
    """Расстояние Левенштейна (Hyyrö, битовые операции на больших int: быстро на страницах в тысячи символов)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    m = len(a)
    peq = {}
    for i, c in enumerate(a):
        peq[c] = peq.get(c, 0) | (1 << i)
    mask, top = (1 << m) - 1, 1 << (m - 1)
    pv, mv, score = mask, 0, m
    for c in b:
        eq = peq.get(c, 0)
        xv = eq | mv
        xh = (((eq & pv) + pv) ^ pv) | eq
        ph = (mv | ~(xh | pv)) & mask
        mh = pv & xh
        if ph & top:
            score += 1
        elif mh & top:
            score -= 1
        ph = ((ph << 1) | 1) & mask
        mh = (mh << 1) & mask
        pv = (mh | ~(xv | ph)) & mask
        mv = ph & xv
    return score


def cer(ref: str, hyp: str, normalized: bool = False) -> float:
    """Char error rate = lev(ref, hyp) / len(ref) после нормализации (может быть >1)."""
    r, h = (ref, hyp) if normalized else (normalize(ref), normalize(hyp))
    if not r:
        return 0.0 if not h else 1.0
    return levenshtein(r, h) / len(r)


def multiset_pr(ref_items, hyp_items) -> dict:
    """Precision/recall/F1 мультимножеств: совпадения = сумма min(count)."""
    rc, hc = Counter(ref_items), Counter(hyp_items)
    match = sum((rc & hc).values())
    nr, nh = sum(rc.values()), sum(hc.values())
    p = match / nh if nh else (1.0 if not nr else 0.0)
    r = match / nr if nr else (1.0 if not nh else 0.0)
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"p": round(p, 4), "r": round(r, 4), "f1": round(f, 4), "match": match, "ref": nr, "hyp": nh}


def count_merged(ref_tokens, hyp_tokens) -> int:
    """Токены OCR (>=5 симв.), которых нет среди слов эталона, но равных склейке двух-трёх соседних слов эталона."""
    refs = set(ref_tokens)
    glued = set()
    for n in (2, 3):
        for i in range(len(ref_tokens) - n + 1):
            glued.add("".join(ref_tokens[i:i + n]))
    return sum(1 for t in hyp_tokens if len(t) >= 5 and t not in refs and t in glued)


def block_px(bbox, page_w, page_h):
    """bbox glmocr (нормализованный 0..1000, [x1,y1,x2,y2]) -> pt страницы."""
    x1, y1, x2, y2 = bbox
    return (x1 * page_w / 1000.0, y1 * page_h / 1000.0, x2 * page_w / 1000.0, y2 * page_h / 1000.0)


def words_in_rect(page_words, rect, pad=2.0) -> list:
    """Слова текстового слоя (кортежи get_text('words')) с центром внутри rect (+pad pt), в порядке чтения."""
    x0, y0, x1, y1 = rect
    sel = [w for w in page_words
           if x0 - pad <= (w[0] + w[2]) / 2 <= x1 + pad and y0 - pad <= (w[1] + w[3]) / 2 <= y1 + pad]
    return [w[4] for w in sorted(sel, key=lambda w: (w[5], w[6], w[7]))]


def table_metrics(blocks, page_words, page_w, page_h) -> dict:
    """Числа в table-блоках OCR против чисел слов текстового слоя внутри bbox блока (суммарно по блокам)."""
    ref_n, hyp_n, ref_w, hyp_w, per = Counter(), Counter(), [], [], []
    for b in blocks:
        if b.get("label") != "table" or not b.get("bbox") or not isinstance(b.get("content"), str):
            continue
        rw = words_in_rect(page_words, block_px(b["bbox"], page_w, page_h))
        rn, hn = numbers(" ".join(rw)), numbers(b["content"])
        ref_n.update(rn)
        hyp_n.update(hn)
        ref_w += words(" ".join(rw))
        hyp_w += words(b["content"])
        per.append({"bbox": b["bbox"], "num": multiset_pr(rn, hn)})
    res = {"tables": len(per), "per_table": per}
    res["num"] = multiset_pr(list(ref_n.elements()), list(hyp_n.elements()))
    res["word"] = multiset_pr(ref_w, hyp_w)
    res["merged"] = count_merged(ref_w, hyp_w)
    return res


def page_metrics(ref_text: str, hyp_text: str) -> dict:
    r, h = normalize(ref_text), normalize(hyp_text)
    return {"cer": round(cer(r, h, normalized=True), 4),
            "word": multiset_pr(re.findall(r"[^\W_]+", r), re.findall(r"[^\W_]+", h)),
            "num": multiset_pr(numbers(ref_text), numbers(hyp_text))}


def number_density(page) -> int:
    return len(numbers(page.get_text("text")))


# ---------------------------------------------------------------- запуск OCR
def _service_dir() -> Path:
    for c in (os.environ.get("PDF_DATASET_DIR"), "/app", str(Path(__file__).resolve().parents[1] / "pdf-dataset")):
        if c and (Path(c) / "pipeline.py").exists():
            return Path(c)
    raise SystemExit("не найден каталог сервиса с pipeline.py (задайте PDF_DATASET_DIR)")


def block_ocr_page(pdf: Path, page: int, work: Path) -> dict:
    """Блочный OCR одной страницы тем же кодом, что /ocr. -> {"blocks", "seconds"}."""
    import ocr_service
    out = ocr_service.ocr_blocks_child(str(pdf), [page], str(work))
    if "perr" in out:
        raise RuntimeError(f"{out['perr']['code']}: {out['perr']['message']}")
    if "err" in out:
        raise RuntimeError(out["err"])
    return out["pages"][page]


def whole_page_ocr(pdf: Path, page: int, dpi: int = 200, max_tokens: int = 8192, timeout: int = 300) -> dict:
    """Один запрос «Text Recognition:» с картинкой всей страницы прямо к vLLM (локальному). -> {"text", "seconds"}."""
    import pymupdf
    import pipeline
    import urllib.request
    cfg = pipeline.config_path()
    host, port = pipeline.ocr_endpoint(cfg)
    import ocr_service
    model = ocr_service.ocr_model_name(cfg) or "zai-org/GLM-OCR"
    doc = pymupdf.open(str(pdf))
    try:
        png = doc[page - 1].get_pixmap(dpi=dpi).tobytes("png")
    finally:
        doc.close()
    body = {"model": model, "temperature": 0.0, "top_p": 1e-5, "top_k": 1, "repetition_penalty": 1.1,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                {"type": "text", "text": "Text Recognition:"}]}]}
    req = urllib.request.Request(f"http://{host}:{port}/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return {"text": data["choices"][0]["message"]["content"] or "", "seconds": round(time.time() - t0, 3)}


def eval_page(pdf: Path, page: int, *, whole: bool, min_words: int, work: Path) -> dict:
    import pymupdf
    doc = pymupdf.open(str(pdf))
    try:
        pg = doc[page - 1]
        pw, ph = pg.rect.width, pg.rect.height
        ref_text = pg.get_text("text")
        page_words = pg.get_text("words")
    finally:
        doc.close()
    res = {"pdf": pdf.name, "page": page, "ref_words": len(words(ref_text)), "ref_numbers": len(numbers(ref_text))}
    if res["ref_words"] < min_words:
        res["skipped"] = f"в текстовом слое < {min_words} слов (не born-digital)"
        return res
    blk = block_ocr_page(pdf, page, work)
    import ocr_service
    md = ocr_service.page_markdown(blk["blocks"])
    res["block"] = {"seconds": blk["seconds"], "blocks": len(blk["blocks"]), **page_metrics(ref_text, md),
                    "tables": table_metrics(blk["blocks"], page_words, pw, ph)}
    if whole:
        wp = whole_page_ocr(pdf, page)
        wres = {"seconds": wp["seconds"], **page_metrics(ref_text, wp["text"])}
        # числа таблиц эталона (по bbox из блочного прогона) — какая доля найдена где-либо в тексте страницы
        ref_t = Counter()
        for b in blk["blocks"]:
            if b.get("label") == "table" and b.get("bbox"):
                ref_t.update(numbers(" ".join(words_in_rect(page_words, block_px(b["bbox"], pw, ph)))))
        wres["tbl_num_recall"] = multiset_pr(list(ref_t.elements()), numbers(wp["text"]))["r"] if ref_t else None
        res["whole"] = wres
    return res


# ---------------------------------------------------------------- CLI
def parse_pages(spec: str, n: int) -> list:
    out = set()
    for part in spec.split(","):
        m = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not m:
            raise SystemExit(f"плохой --pages: {spec!r}")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        out.update(p for p in range(a, b + 1) if 1 <= p <= n)
    return sorted(out)


def pick_pages(pdf: Path, top: int, explicit: str | None) -> list:
    import pymupdf
    doc = pymupdf.open(str(pdf))
    try:
        if explicit:
            return parse_pages(explicit, doc.page_count)
        dens = sorted(((number_density(doc[i]), i + 1) for i in range(doc.page_count)), reverse=True)
        return sorted(p for d, p in dens[:top] if d > 0)
    finally:
        doc.close()


def summarize(rows) -> dict:
    """Суммарно по страницам: среднее CER, мультимножественные P/R по сумме совпадений, время."""
    ok = [r for r in rows if "block" in r]

    def agg(key):
        vals = [r[key] for r in ok if key in r]
        if not vals:
            return None
        o = {"pages": len(vals), "cer": round(sum(v["cer"] for v in vals) / len(vals), 4),
             "sec_per_page": round(sum(v["seconds"] for v in vals) / len(vals), 2)}
        for k in ("word", "num"):
            m, nr, nh = (sum(v[k][x] for v in vals) for x in ("match", "ref", "hyp"))
            o[f"{k}_p"], o[f"{k}_r"] = round(m / nh, 4) if nh else 0.0, round(m / nr, 4) if nr else 0.0
        return o
    s = {"block": agg("block"), "whole": agg("whole")}
    t = [r["block"]["tables"] for r in ok]
    m, nr, nh = (sum(x["num"][k] for x in t) for k in ("match", "ref", "hyp"))
    s["tables"] = {"count": sum(x["tables"] for x in t), "num_p": round(m / nh, 4) if nh else None,
                   "num_r": round(m / nr, 4) if nr else None, "merged_tokens": sum(x["merged"] for x in t)}
    return s


def fmt_table(rows, summary) -> str:
    h = f"{'pdf':14} {'pg':>3} {'mode':6} {'CER':>6} {'wordP':>6} {'wordR':>6} {'numP':>6} {'numR':>6} {'tblP':>6} {'tblR':>6} {'merged':>6} {'sec':>6}"
    lines = [h, "-" * len(h)]
    for r in rows:
        if "block" not in r:
            lines.append(f"{r['pdf'][:14]:14} {r['page']:>3} skipped: {r.get('skipped')}")
            continue
        b, t = r["block"], r["block"]["tables"]
        lines.append(f"{r['pdf'][:14]:14} {r['page']:>3} {'block':6} {b['cer']:>6.3f} {b['word']['p']:>6.3f} {b['word']['r']:>6.3f} "
                     f"{b['num']['p']:>6.3f} {b['num']['r']:>6.3f} {t['num']['p']:>6.3f} {t['num']['r']:>6.3f} "
                     f"{t['merged']:>6} {b['seconds']:>6.1f}")
        if "whole" in r:
            w = r["whole"]
            tr = "  n/a" if w["tbl_num_recall"] is None else f"{w['tbl_num_recall']:.3f}"
            lines.append(f"{'':14} {'':>3} {'whole':6} {w['cer']:>6.3f} {w['word']['p']:>6.3f} {w['word']['r']:>6.3f} "
                         f"{w['num']['p']:>6.3f} {w['num']['r']:>6.3f} {'':>6} {tr:>6} {'':>6} {w['seconds']:>6.1f}")
    lines.append("")
    for k in ("block", "whole"):
        s = summary.get(k)
        if s:
            lines.append(f"TOTAL {k:5} pages={s['pages']} CER={s['cer']} word P/R={s['word_p']}/{s['word_r']} "
                         f"num P/R={s['num_p']}/{s['num_r']} sec/page={s['sec_per_page']}")
    t = summary["tables"]
    lines.append(f"TABLES count={t['count']} num P/R={t['num_p']}/{t['num_r']} merged_tokens={t['merged_tokens']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Оценка блочного OCR против текстового слоя PDF")
    ap.add_argument("pdfs", nargs="+", help="PDF-файлы или каталоги")
    ap.add_argument("--top", type=int, default=2)
    ap.add_argument("--pages", default=None)
    ap.add_argument("--whole-page", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-words", type=int, default=60)
    a = ap.parse_args(argv)
    sys.path.insert(0, str(_service_dir()))
    files = []
    for p in a.pdfs:
        pp = Path(p)
        files += sorted(pp.glob("*.pdf")) if pp.is_dir() else [pp]
    import tempfile
    rows = []
    work = Path(tempfile.mkdtemp(prefix="ocr_eval_"))
    for pdf in files:
        for page in pick_pages(pdf, a.top, a.pages):
            print(f"[{pdf.name} p.{page}] ...", file=sys.stderr, flush=True)
            try:
                rows.append(eval_page(pdf, page, whole=a.whole_page, min_words=a.min_words, work=work))
            except Exception as e:  # noqa: BLE001 - одна страница не роняет прогон
                rows.append({"pdf": pdf.name, "page": page, "skipped": f"ошибка: {type(e).__name__}: {e}"})
    summary = summarize(rows)
    print(fmt_table(rows, summary))
    if a.out:
        env = {k: v for k, v in os.environ.items() if k.startswith(("OCR_", "GLMOCR"))}
        Path(a.out).write_text(json.dumps({"env": env, "summary": summary, "pages": rows}, ensure_ascii=False, indent=1),
                               encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
