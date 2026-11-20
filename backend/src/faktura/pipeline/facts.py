"""Построение фактов и якорей из bundle (план 5.6; spec/07 §7.4 шаги 7-8; К-2).

Факт - одно значение на экране: по факту на каждую строку `results` (target = {entity: results, id, field:
metric_value}); строки `present_not_extractable`/`absent` тоже получают факт (без значения). Якорь - место
значения на странице: из `_bbox` ML (`rects` в долях страницы от левого верхнего угла, `bbox_to_rects`), а без
bbox - запасной поиск цитаты `ParseAdapter.find_rects`. Факты с одним местом делят якорь.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Literal

from ..domain.entities import AnchorDraft, FactDraft, SourceKind
from ..domain.extraction import ParsedDocument
from ..domain.ports import ParseAdapter
from ..domain.profile import metric_to_param
from ..domain.records import PaperBundle, Rect, Result
from ._pdf import PDF_LOCK

log = logging.getLogger(__name__)


def _int(v) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, Decimal):
        return int(v) if v.is_finite() and v == v.to_integral_value() else None
    try:
        d = Decimal(str(v).strip())
    except Exception:  # noqa: BLE001
        return None
    return int(d) if d.is_finite() and d == d.to_integral_value() else None


def _dec(v) -> Decimal | None:
    return v if isinstance(v, Decimal) and v.is_finite() else None


def _str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def source_kind_of(r: Result) -> SourceKind:
    if _str(r.source_figure):
        return SourceKind.figure
    if _str(r.source_table):
        return SourceKind.table
    return SourceKind.text


def fact_ref(result_id: str | None, used: set[str]) -> str:
    base = f"results:{result_id or ''}"
    ref, n = base, 1
    while ref in used:
        n += 1
        ref = f"{base}#{n}"
    used.add(ref)
    return ref


def _page_size(doc: ParsedDocument | None, parser: ParseAdapter, pdf_path: Path | None,
               page: int) -> tuple[float, float] | None:
    if doc is not None:
        for p in doc.pages:
            if p.n == page and p.width_pt > 0 and p.height_pt > 0:
                return float(p.width_pt), float(p.height_pt)
    if pdf_path is None:
        return None
    try:
        with PDF_LOCK:
            w, h = parser.page_size(pdf_path, page)
    except Exception as e:  # noqa: BLE001 - нет страницы или файла: якоря нет
        log.debug("page_size %s p.%s: %s", pdf_path, page, e)
        return None
    if not w or not h or w <= 0 or h <= 0:
        return None
    return float(w), float(h)


def _clip(rects: list[Rect]) -> list[Rect]:
    out: list[Rect] = []
    for r in rects:
        if r.x >= 0 and r.y >= 0 and r.w > 0 and r.h > 0 and r.x + r.w <= 1.0 and r.y + r.h <= 1.0:
            out.append(r)
            continue
        x0, y0 = min(max(r.x, 0.0), 1.0), min(max(r.y, 0.0), 1.0)
        x1, y1 = min(max(r.x + r.w, 0.0), 1.0), min(max(r.y + r.h, 0.0), 1.0)
        if x1 > x0 and y1 > y0:
            out.append(Rect(x=x0, y=y0, w=x1 - x0, h=y1 - y0))
    return out


def _bbox_rects(bbox, size: tuple[float, float], origin: str) -> list[Rect]:
    from ..pdfio.parse import bbox_to_rects

    try:
        return bbox_to_rects(tuple(float(v) for v in bbox), size[0], size[1], origin)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []


def build_facts(bundle: PaperBundle, doc: ParsedDocument | None, parser: ParseAdapter, pdf_path: Path | None, *,
                bbox_origin: Literal["top-left", "bottom-left"] = "top-left") -> tuple[list[FactDraft], list[AnchorDraft]]:
    """При _bbox - rects в долях страницы; без bbox - запасной поиск find_rects."""
    devices = {d.device_id: d for d in bundle.devices}
    used_refs: set[str] = set()
    ref_by_rid: dict[str, str] = {}
    facts: list[FactDraft] = []
    anchors: list[AnchorDraft] = []
    anchor_by_place: dict[tuple, AnchorDraft] = {}
    find_cache: dict[tuple[int, str], list[Rect]] = {}

    pairs: list[tuple[Result, FactDraft]] = []
    for r in bundle.results:
        ref = fact_ref(r.result_id, used_refs)
        if r.result_id and r.result_id not in ref_by_rid:
            ref_by_rid[r.result_id] = ref
        dev = devices.get(r.device_id) if r.device_id else None
        metric = r.metric_name if isinstance(r.metric_name, str) and r.metric_name.strip() else None
        f = FactDraft(
            ref=ref,
            target={"entity": "results", "id": r.result_id, "field": "metric_value"},
            param=metric_to_param(metric) if metric else "",
            metric_name=metric,
            row_id=r.device_id or None,
            row_label=(dev.device_label if dev is not None else None) or None,
            raw_value=_str(r.raw_value),
            raw_unit=_str(r.raw_unit),
            normalized_value=_dec(r.normalized_value),
            normalized_unit=_str(r.normalized_unit) if _dec(r.normalized_value) is not None else None,
            norm_status=r.meta.norm_status,
            extraction_status=_str(r.extraction_status),
            confidence=r.meta.confidence,
            source_page=_int(r.source_page),
            source_fragment=_str(r.source_fragment),
            source_table=_str(r.source_table),
            source_figure=_str(r.source_figure),
            source_kind=source_kind_of(r),
            quote_found=r.meta.quote_found,
            added_by=r.meta.added_by,
            trace=list(r.meta.norm_trace),
        )
        facts.append(f)
        pairs.append((r, f))

    for r, f in pairs:
        if r.duplicate_of and r.duplicate_of in ref_by_rid and ref_by_rid[r.duplicate_of] != f.ref:
            f.duplicate_of_ref = ref_by_rid[r.duplicate_of]

        page = r.meta.page if isinstance(r.meta.page, int) and r.meta.page > 0 else f.source_page
        if page is None or page < 1:
            continue
        rects: list[Rect] = []
        if r.meta.bbox is not None:
            size = _page_size(doc, parser, pdf_path, page)
            if size is not None:
                rects = _bbox_rects(r.meta.bbox, size, bbox_origin)
        if not rects and pdf_path is not None and f.source_fragment:
            key = (page, f.source_fragment)
            if key not in find_cache:
                try:
                    with PDF_LOCK:
                        find_cache[key] = list(parser.find_rects(pdf_path, page, f.source_fragment) or [])
                except Exception as e:  # noqa: BLE001 - поиск якоря не роняет статью
                    log.debug("find_rects %s p.%s: %s", pdf_path, page, e)
                    find_cache[key] = []
            rects = _clip(find_cache[key])
        if not rects:
            continue
        place = (page, tuple((round(x.x, 6), round(x.y, 6), round(x.w, 6), round(x.h, 6)) for x in rects))
        a = anchor_by_place.get(place)
        if a is None:
            a = AnchorDraft(ref=f"anc:{len(anchors) + 1}", kind="value", page=page, rects=rects,
                            text=f.source_fragment)
            anchor_by_place[place] = a
            anchors.append(a)
        a.fact_refs.append(f.ref)
        f.anchor_ref = a.ref
    return facts, anchors
