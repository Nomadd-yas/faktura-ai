# [faktura-patch] POST /ocr: блочный OCR (glmocr: layout + пер-блочные промпты) как сервис.
# Логика без FastAPI: разбор списка страниц, конвертация json_result glmocr в блоки, дисковый кеш,
# выполнение в прогретом процессе пула (api.py держит пул). Без RouterAI — только локальный vLLM.
#
# Структура json_result glmocr 0.1.5: список по страницам; страница — список блоков
#   {"index": int, "label": "text|table|formula|image", "native_label": "<метка layout-модели>",
#    "content": str | None, "bbox_2d": [x1, y1, x2, y2], ...}
# bbox_2d — НОРМАЛИЗОВАННЫЕ координаты 0..1000 по ширине/высоте страницы (начало — левый верхний угол),
# а не пиксели и не pt. Перевод в pt: x_pt = x/1000 * page_width_pt.

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

FORMATS = ("markdown", "blocks", "both")


class OcrRequestError(Exception):
    """Ошибка запроса: status 400 (плохие страницы/формат) | 413 (слишком много страниц)."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def max_pages() -> int:
    return max(1, int(os.environ.get("OCR_MAX_PAGES", "50")))


def parse_pages(spec, n_pages: int) -> list:
    """'1,3,5-7' -> [1, 3, 5, 6, 7] (с 1, без повторов, по возрастанию). Пусто/None -> все страницы."""
    if spec is None or not str(spec).strip():
        return list(range(1, n_pages + 1))
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            raise OcrRequestError(400, "bad_request", f"пустой элемент в pages: {spec!r}")
        m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not m:
            raise OcrRequestError(400, "bad_request", f"не понимаю страницы {part!r} (ожидается '1,3,5-7')")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < a or b > n_pages:
            raise OcrRequestError(400, "bad_request", f"страницы {part!r} вне диапазона 1..{n_pages}")
        out.update(range(a, b + 1))
    return sorted(out)


def page_markdown(blocks) -> str:
    """Markdown страницы: содержимое блоков по порядку, image пропускается (как _ocr_page_texts)."""
    texts = []
    for b in blocks:
        c = b.get("content")
        if b.get("label") == "image" or not isinstance(c, str) or not c.strip():
            continue
        texts.append(c.strip())
    return "\n\n".join(texts)


def blocks_from_json_page(page_blocks) -> list:
    """Блоки страницы из json_result glmocr -> [{index, label, native_label, bbox, content}]."""
    out = []
    for i, b in enumerate(page_blocks or []):
        if not isinstance(b, dict):
            continue
        bbox = b.get("bbox_2d")
        if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
            bbox = None
        out.append({"index": b.get("index", i), "label": b.get("label"),
                    "native_label": b.get("native_label", b.get("label")),
                    "bbox": [int(v) for v in bbox] if bbox else None,
                    "content": b.get("content") if isinstance(b.get("content"), str) else None})
    return out


def page_confidence(page_blocks):
    """Среднее числового confidence/score блоков (0..1) или None."""
    vals = []
    for b in page_blocks or []:
        if isinstance(b, dict):
            for k in ("confidence", "score", "conf"):
                v = b.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= float(v) <= 1.0:
                    vals.append(float(v))
                    break
    return round(sum(vals) / len(vals), 4) if vals else None


# ---------- выполнение в воркере ----------
def ocr_blocks_child(pdf_path, pages, work_dir):
    """Выполняется в процессе пула. -> {"pages": {page: {"blocks", "confidence", "seconds"}}} или {"perr"/"err"}."""
    import pipeline
    try:
        import pymupdf
        parser = pipeline._get_parser(pipeline.config_path())
        pdf, work = Path(pdf_path), Path(work_dir)
        sub, src = pymupdf.open(), pymupdf.open(str(pdf))
        sub_path = work / "ocr_subset.pdf"
        try:
            for p in pages:
                sub.insert_pdf(src, from_page=p - 1, to_page=p - 1)
            sub.save(str(sub_path))
        finally:
            sub.close()
            src.close()
        t0 = time.time()
        result = parser.parse(str(sub_path))
        dt = time.time() - t0
        jr = getattr(result, "json_result", None)
        if not (isinstance(jr, list) and len(jr) == len(pages) and all(isinstance(pg, list) for pg in jr)):
            raise pipeline.PipelineError("ml_error", f"glmocr: ожидалось {len(pages)} страниц результата, "
                                                     f"пришло {len(jr) if isinstance(jr, list) else None}")
        per = dt / len(pages)
        return {"pages": {p: {"blocks": blocks_from_json_page(pg), "confidence": page_confidence(pg),
                              "seconds": round(per, 3)} for p, pg in zip(pages, jr)}}
    except pipeline.PipelineError as e:
        return {"perr": {"code": e.code, "message": e.message, "retryable": e.retryable, "status": e.status}}
    except Exception as e:  # noqa: BLE001
        if pipeline._is_connection_error(e):
            return {"perr": {"code": "ocr_unavailable", "status": 503, "retryable": True,
                             "message": "OCR-сервер (vLLM) недоступен"}}
        return {"err": f"{type(e).__name__}: {e}"}


def ocr_model_name(config: Path):
    """Имя модели OCR (pipeline.ocr_api.model) из конфига или None."""
    try:
        text = Path(config).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        import yaml
        return (((yaml.safe_load(text) or {}).get("pipeline") or {}).get("ocr_api") or {}).get("model")
    except Exception:  # noqa: BLE001
        m = re.search(r"ocr_api:(?:.*\n)*?\s+model:\s*([^\s#]+)", text)
        return m.group(1) if m else None


# ---------- кеш ----------
def config_hash(config: Path) -> str:
    """Хеш настроек, влияющих на результат (page_loader, layout, модель); адрес сервера не входит."""
    try:
        import yaml
        cfg = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
        pl = cfg.get("pipeline") or {}
        key = {"page_loader": pl.get("page_loader"), "layout": pl.get("layout"),
               "model": (pl.get("ocr_api") or {}).get("model"), "formatter": pl.get("result_formatter")}
        text = json.dumps(key, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        text = Path(config).read_text(encoding="utf-8", errors="replace")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class OcrCache:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, sha, page, cfg):
        return self.root / sha / f"p{page:04d}.{cfg}.json"

    def get(self, sha, page, cfg):
        try:
            return json.loads(self._path(sha, page, cfg).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, sha, page, cfg, entry):
        p = self._path(sha, page, cfg)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass


def build_response(entries: dict, pages: list, fmt: str, *, model, seconds, cached, sizes=None) -> dict:
    out_pages = []
    for p in pages:
        e = entries[p]
        item = {"page": p}
        if fmt in ("markdown", "both"):
            item["markdown"] = page_markdown(e["blocks"])
        if fmt in ("blocks", "both"):
            item["blocks"] = e["blocks"]
        if e.get("confidence") is not None:
            item["confidence"] = e["confidence"]
        item["seconds"] = e.get("seconds")
        if sizes and p in sizes:
            item["page_size_pt"] = sizes[p]
        out_pages.append(item)
    return {"pages": out_pages, "model": model, "seconds": round(seconds, 3), "cached": cached,
            "bbox_format": "normalized 0..1000, [x1,y1,x2,y2], origin top-left"}


def work_dir(out_dir) -> Path:
    base = Path(out_dir) / "ocr_work"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path(tempfile.gettempdir())
    return Path(tempfile.mkdtemp(prefix="ocr_", dir=str(base)))


def cleanup(path):
    shutil.rmtree(path, ignore_errors=True)
