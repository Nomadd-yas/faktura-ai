"""Локальный разбор PDF на PyMuPDF (план 5.2, REQ-063). needs_ocr по spec/11 §11.3; OCR не реализуется (REQ-129).

Страница - скан, если текста < 100 символов и изображения занимают > 70 % площади, или если «мусорных»
символов (U+FFFD, Private Use Area, управляющие) > 30 %. Координаты наружу - доли страницы от левого
верхнего угла (К-2).
"""
from __future__ import annotations

import io
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal

from ..domain.errors import AppError, PdfError
from ..domain.extraction import ParsedDocument, ParsedPage
from ..domain.records import Rect

OCR_MIN_CHARS = 100
OCR_IMAGE_RATIO = 0.7
OCR_GARBAGE_RATIO = 0.3
GARBAGE_MIN_CHARS = 10   # на почти пустой странице доля мусора не показательна
MAX_RECTS = 20
_MAX_SCALE = 8.0
PARSER_NAME = "faktura-local-1"


def webp_available() -> bool:
    """WebP рендерится через Pillow (extras [img]); без него render_page отдаёт PNG."""
    try:
        from PIL import features

        return bool(features.check("webp"))
    except Exception:  # noqa: BLE001 - нет Pillow или сборка без webp
        return False


def image_mime(data: bytes) -> str:
    """MIME растра по сигнатуре: render_page может вернуть PNG вместо WebP (нет Pillow)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "application/octet-stream"


def _fitz():
    import fitz

    from .inputs import _silence_mupdf

    _silence_mupdf()
    return fitz


@contextmanager
def _open(pdf_path: Path) -> Iterator["object"]:
    fitz = _fitz()
    try:
        doc = fitz.open(str(pdf_path), filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise PdfError("pdf_broken", f"{Path(pdf_path).name}: PDF повреждён ({type(e).__name__})") from e
    try:
        if doc.needs_pass:
            raise PdfError("pdf_encrypted", f"{Path(pdf_path).name}: PDF защищён паролем")
        yield doc
    finally:
        doc.close()


def _load_page(doc, n: int):
    """Страница по номеру с 1; вне диапазона -> AppError not_found."""
    if not isinstance(n, int) or isinstance(n, bool) or not (1 <= n <= doc.page_count):
        raise AppError("not_found", f"Страница {n} вне диапазона 1..{doc.page_count}")
    return doc.load_page(n - 1)


def _is_garbage(ch: str) -> bool:
    o = ord(ch)
    if ch == "�":
        return True
    if 0xE000 <= o <= 0xF8FF or 0xF0000 <= o <= 0x10FFFF:  # Private Use Area
        return True
    return o < 32 and ch not in "\t\n\r"


def _union_area(rects: list[tuple[float, float, float, float]]) -> float:
    """Площадь объединения прямоугольников (сжатие координат; изображений на странице немного)."""
    rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
    if not rects:
        return 0.0
    if len(rects) > 200:  # защита от страниц с тысячами плиток: сумма площадей сверху ограничена страницей
        return sum((r[2] - r[0]) * (r[3] - r[1]) for r in rects)
    xs = sorted({r[0] for r in rects} | {r[2] for r in rects})
    area = 0.0
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        spans = sorted((r[1], r[3]) for r in rects if r[0] <= x0 and r[2] >= x1)
        covered = 0.0
        cur_s = cur_e = None
        for s, e in spans:
            if cur_e is None or s > cur_e:
                if cur_e is not None:
                    covered += cur_e - cur_s
                cur_s, cur_e = s, e
            else:
                cur_e = max(cur_e, e)
        if cur_e is not None:
            covered += cur_e - cur_s
        area += covered * (x1 - x0)
    return area


def _page_stats(page) -> tuple[ParsedPage, str]:
    rect = page.rect
    w, h = float(rect.width), float(rect.height)
    text = page.get_text("text") or ""
    # символы текста с пробелами между словами (подряд идущие пробелы и переводы строк - один символ)
    char_count = len(" ".join(text.split()))
    chars = [ch for ch in text if not ch.isspace()]
    garbage = sum(1 for ch in chars if _is_garbage(ch))
    garbage_ratio = garbage / len(chars) if len(chars) >= GARBAGE_MIN_CHARS else 0.0
    boxes: list[tuple[float, float, float, float]] = []
    try:
        infos = page.get_image_info()
    except Exception:  # noqa: BLE001 - нет сведений об изображениях = считаем, что их нет
        infos = []
    for info in infos:
        b = info.get("bbox")
        if not b:
            continue
        x0, y0, x1, y1 = (float(v) for v in b)
        x0, x1 = max(min(x0, x1), 0.0), min(max(x0, x1), w)
        y0, y1 = max(min(y0, y1), 0.0), min(max(y0, y1), h)
        boxes.append((x0, y0, x1, y1))
    page_area = w * h if w > 0 and h > 0 else 0.0
    image_ratio = min(_union_area(boxes) / page_area, 1.0) if page_area else 0.0
    needs_ocr = (char_count < OCR_MIN_CHARS and image_ratio > OCR_IMAGE_RATIO) or garbage_ratio > OCR_GARBAGE_RATIO
    pp = ParsedPage(n=page.number + 1, width_pt=w, height_pt=h, char_count=char_count,
                    image_ratio=round(image_ratio, 4), has_text_layer=char_count > 0, needs_ocr=needs_ocr)
    return pp, text


def _to_fraction(r, w: float, h: float) -> Rect | None:
    x0, y0, x1, y1 = (float(v) for v in (r.x0, r.y0, r.x1, r.y1))
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    x0, x1 = max(x0, 0.0), min(x1, w)
    y0, y1 = max(y0, 0.0), min(y1, h)
    if x1 <= x0 or y1 <= y0 or w <= 0 or h <= 0:
        return None
    return Rect(x=x0 / w, y=y0 / h, w=(x1 - x0) / w, h=(y1 - y0) / h)


def _needle_variants(needle: str) -> list[str]:
    collapsed = re.sub(r"\s+", " ", needle).strip()
    out = [needle, collapsed]
    for a, b in (("−", "-"), ("–", "-"), ("-", "−"), ("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff")):
        if a in collapsed:
            out.append(collapsed.replace(a, b))
    seen: list[str] = []
    for v in out:
        if v and v not in seen:
            seen.append(v)
    return seen


class LocalParseAdapter:
    """ParseAdapter на PyMuPDF: страницы, размеры, текст, needs_ocr, рендер, поиск якорей."""

    name = "local"

    @property
    def version(self) -> str:
        fitz = _fitz()
        return f"pymupdf-{getattr(fitz, 'VersionBind', '?')}/{PARSER_NAME}"

    def parse(self, pdf_path: Path) -> ParsedDocument:
        with _open(pdf_path) as doc:
            pages: list[ParsedPage] = []
            texts: list[str] = []
            for page in doc:
                pp, text = _page_stats(page)
                pages.append(pp)
                texts.append(text)
            return ParsedDocument(page_count=doc.page_count, pages=pages, needs_ocr=any(p.needs_ocr for p in pages),
                                  text_by_page=texts, parser_version=self.version)

    def page_info(self, pdf_path: Path) -> list[ParsedPage]:
        """Сведения о страницах без текста (диагностика сканов)."""
        return self.parse(pdf_path).pages

    def ocr_pages(self, pdf_path: Path) -> list[int]:
        """Номера страниц-сканов (spec/11 §11.3: options.ocr_pages)."""
        return [p.n for p in self.parse(pdf_path).pages if p.needs_ocr]

    def render_page(self, pdf_path: Path, n: int, scale: float = 1.5, fmt: str = "webp") -> bytes:
        """Растр страницы n (с 1). fmt: webp (нужен Pillow) | png | jpg | jpeg."""
        fmt = (fmt or "webp").lower()
        if fmt not in ("webp", "png", "jpg", "jpeg"):
            raise AppError("validation_error", f"Формат {fmt} не поддерживается: webp, png, jpg", field="fmt")
        if not scale or scale <= 0:
            raise AppError("validation_error", "scale должен быть больше 0", field="scale")
        scale = min(float(scale), _MAX_SCALE)
        fitz = _fitz()
        with _open(pdf_path) as doc:
            page = _load_page(doc, n)
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            if fmt != "webp":
                return pix.tobytes("jpg" if fmt == "jpeg" else fmt)
            if not webp_available():  # api_contract №39: без Pillow на сервере - всегда png
                return pix.tobytes("png")
            from PIL import Image
            mode = "RGB" if pix.n == 3 else ("L" if pix.n == 1 else "RGBA")
            img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=80, method=4)
            return buf.getvalue()

    def page_size(self, pdf_path: Path, page: int) -> tuple[float, float]:
        with _open(pdf_path) as doc:
            r = _load_page(doc, page).rect
            return float(r.width), float(r.height)

    def find_rects(self, pdf_path: Path, page: int, needle: str) -> list[Rect]:
        """Прямоугольники фразы на странице (доли от левого верхнего угла); не найдено или нет страницы -> [].

        Сначала фраза целиком (и варианты с пробелами, минусом, лигатурами); длинную цитату, не найденную
        целиком (перенос строки, дефис), ищем по первым и последним словам.
        """
        if not needle or not needle.strip():
            return []
        try:
            page = int(page)
        except (TypeError, ValueError):
            return []
        try:
            with _open(pdf_path) as doc:
                if not isinstance(page, int) or not (1 <= page <= doc.page_count):
                    return []
                pg = doc.load_page(page - 1)
                w, h = float(pg.rect.width), float(pg.rect.height)
                hits = []
                for v in _needle_variants(needle):
                    hits = pg.search_for(v)
                    if hits:
                        break
                if not hits:
                    words = re.sub(r"\s+", " ", needle).strip().split(" ")
                    if len(words) >= 6:
                        head, tail = " ".join(words[:4]), " ".join(words[-4:])
                        h1, h2 = pg.search_for(head), pg.search_for(tail)
                        if h1 and h2:
                            hits = [h1[0], h2[0]]
                rot = pg.rotation_matrix if pg.rotation else None
                out: list[Rect] = []
                for r in hits[:MAX_RECTS]:
                    if rot is not None:
                        r = r * rot
                    fr = _to_fraction(r, w, h)
                    if fr is not None and fr not in out:
                        out.append(fr)
                return out
        except AppError:
            return []


def bbox_to_rects(bbox: tuple[float, float, float, float], page_w: float, page_h: float,
                  origin: Literal["top-left", "bottom-left"] = "top-left") -> list[Rect]:
    """bbox в пунктах PDF [x0, y0, x1, y1] -> доли страницы от левого верхнего угла (К-2).

    origin - начало координат ML: top-left (по умолчанию) или bottom-left (ось Y переворачивается).
    Углы упорядочиваются, выход за страницу обрезается; пустой прямоугольник -> [].
    """
    if page_w is None or page_h is None or page_w <= 0 or page_h <= 0:
        raise ValueError("Размер страницы должен быть положительным")
    if bbox is None or len(bbox) != 4:
        raise ValueError("bbox - это [x0, y0, x1, y1]")
    x0, y0, x1, y1 = (float(v) for v in bbox)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if origin == "bottom-left":
        y0, y1 = page_h - y1, page_h - y0
    elif origin != "top-left":
        raise ValueError(f"Неизвестное начало координат: {origin}")
    x0, x1 = max(x0, 0.0), min(x1, float(page_w))
    y0, y1 = max(y0, 0.0), min(y1, float(page_h))
    if x1 <= x0 or y1 <= y0:
        return []
    return [Rect(x=x0 / page_w, y=y0 / page_h, w=(x1 - x0) / page_w, h=(y1 - y0) / page_h)]
