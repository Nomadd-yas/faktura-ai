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


def run_pipeline(pdf, *, out_dir="/out", publication_id=None, no_strip=False) -> dict:
    """Прогоняет PDF через весь конвейер. Бросает исключение при фатальной ошибке
    (PDF не найден, glmocr не установлен/не создал md).

    Возвращает dict: ok, publication_id, extract_error, failed_tables,
    out_dir, paths {markdown,dataset,meta}, plus метаданные в meta.
    """
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
        from glmocr import GlmOcr
    except ImportError as e:
        raise PipelineError("ml_error", "glmocr не установлен в образе (pip install 'glmocr[selfhosted]')") from e

    print(f"[1/3] glmocr: {pdf.name} -> markdown (config={config})", flush=True)
    t0 = time.time()
    try:
        with GlmOcr(config_path=str(config)) as parser:
            result = parser.parse(str(pdf))
            result.save(output_dir=str(work))
    except PipelineError:
        raise
    except Exception as e:
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
    try:
        res = extract(clean_md,
                      publication_id=pub_id,
                      out=str(dataset_out),
                      model=os.environ.get("ROUTERAI_MODEL", DEFAULT_MODEL),
                      provider=os.environ.get("ROUTERAI_PROVIDER", "wafer"),
                      meta_path=str(meta_out))
        failed_tables = res["failed_tables"]
    except LLMError as e:
        extract_failed = str(e)
        failed_tables = ["<all>"]
        print(f"[!] извлечение прервано: {e}", flush=True)

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
