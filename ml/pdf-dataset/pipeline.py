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
# RouterAI: ROUTERAI_API_KEY (обяз.), ROUTERAI_MODEL, ROUTERAI_PROVIDER (deepinfra).

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))  # импорт соседних модулей при любом cwd


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
        raise RuntimeError("Нет ROUTERAI_API_KEY в окружении — извлечение датасета будет невозможным.")
    config = Path(os.environ.get("GLMOCR_CONFIG", BASE / "glmocr.config.yaml"))
    if not config.exists():
        raise RuntimeError(f"Нет конфига glmocr: {config} (задайте GLMOCR_CONFIG=/path/to/config.yaml)")

    out_dir = Path(out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    stem = pdf.stem
    pub_id = publication_id or stem

    # ---------- 1. glmocr: PDF -> markdown ----------
    try:
        from glmocr import GlmOcr
    except ImportError as e:
        raise RuntimeError("glmocr не установлен в образе (pip install 'glmocr[selfhosted]')") from e

    print(f"[1/3] glmocr: {pdf.name} -> markdown (config={config})", flush=True)
    t0 = time.time()
    with GlmOcr(config_path=str(config)) as parser:
        result = parser.parse(str(pdf))
        result.save(output_dir=str(work))
    ocr_seconds = round(time.time() - t0, 2)

    md_src = work / stem / f"{stem}.md"
    if not md_src.exists():
        raise RuntimeError(f"glmocr не создал {md_src} — смотрите лог выше")
    md_text = md_src.read_text(encoding="utf-8", errors="replace")
    print(f"      готово за {ocr_seconds}s, markdown {len(md_text)} символов", flush=True)

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
                      model=os.environ.get("ROUTERAI_MODEL", "deepseek/deepseek-v4.1-flash-1017"),
                      provider=os.environ.get("ROUTERAI_PROVIDER", "deepinfra"),
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
    except (FileNotFoundError, RuntimeError) as e:
        sys.exit(f"Ошибка: {e}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
