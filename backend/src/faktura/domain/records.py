"""Pydantic-модели строк 7 таблиц и PaperBundle (план 4.3).

Имена полей совпадают с колонками шаблона сдачи (проверяется tests/shared).
Числа - Decimal, списки - list[str], булевы - bool. Служебные данные записи лежат в `meta`
и в CSV не выгружаются.

Числовые, целые и булевы поля допускают также `str`: так хранится нераспознанный текст ячейки
(ответ ML вроде «1,5», «18.2-19.0», «63-68»; эталон dev_gold: layer_thickness «63-68»). Это нужно для
roundtrip без потерь; `export.sanitize` приводит такие значения к числу или очищает ячейку
(+ примечание), так что после санитайзера остаются только Decimal/int/bool/None. Модули до санитайзера
обязаны проверять `isinstance(v, str)` перед арифметикой.
"""
from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from .tables import ID_COLUMN


class ReviewStatus(StrEnum):
    new = "new"
    accepted = "accepted"
    rejected = "rejected"


class AddedBy(StrEnum):
    auto = "auto"      # в API: "model"
    manual = "manual"  # в API: "user"


class NormStatus(StrEnum):
    ok = "ok"
    converted = "converted"
    failed = "failed"
    not_applicable = "not_applicable"
    passthrough = "passthrough"


class IssueStatus(StrEnum):
    open = "open"
    fixed = "fixed"
    ok = "ok"


class Rect(BaseModel):
    """Прямоугольник в долях страницы от левого верхнего угла."""
    x: float
    y: float
    w: float
    h: float


class TraceStep(BaseModel):
    """Шаг нормализации для трассы факта (spec/06)."""
    step: str
    before: str | None = None
    after: str | None = None
    note: str | None = None


class RecordMeta(BaseModel):
    ml_order: int = 0
    confidence: float | None = None
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None  # пункты PDF, как прислал ML
    review_status: ReviewStatus = ReviewStatus.new
    corrected_value: Decimal | None = None
    corrected_raw: str | None = None
    added_by: AddedBy = AddedBy.auto
    quote_found: bool | None = None
    norm_status: NormStatus | None = None
    norm_issue: str | None = None
    norm_trace: list[TraceStep] = Field(default_factory=list)
    notes_backend: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)  # поля вне шаблона -> unmapped_values
    linked_by_backend: bool = False


class _Row(BaseModel):
    model_config = ConfigDict(validate_assignment=False)
    meta: RecordMeta = Field(default_factory=RecordMeta)


class Publication(_Row):
    publication_id: str
    title: str | None = None
    year: int | str | None = None
    DOI: str | None = None
    authors: list[str] = Field(default_factory=list)
    in_scope: bool | str | None = None
    exclusion_reason: str | None = None


class Material(_Row):
    publication_id: str
    material_id: str
    material_name_raw: str | None = None
    formula_normalized: str | None = None
    material_class: str | None = None
    dimensionality: str | None = None
    additives: list[str] = Field(default_factory=list)
    bandgap_value: Decimal | str | None = None
    bandgap_unit: str | None = None
    bandgap_method: str | None = None
    note: str | None = None


class Device(_Row):
    publication_id: str
    device_id: str
    absorber_material_id: str | None = None
    device_label: str | None = None
    device_role: str | None = None
    device_type: str | None = None
    architecture: str | None = None
    substrate_type: str | None = None
    active_area: Decimal | str | None = None
    area_unit: str | None = None
    area_definition: str | None = None
    encapsulation: bool | str | None = None
    note: str | None = None


class Layer(_Row):
    publication_id: str
    layer_id: str
    device_id: str | None = None
    layer_order: int | str | None = None
    layer_function: str | None = None
    layer_material: str | None = None
    layer_thickness: Decimal | str | None = None
    thickness_unit: str | None = None
    deposition_method: str | None = None
    annealing_temperature: str | None = None
    annealing_time: str | None = None
    atmosphere: str | None = None
    note: str | None = None


class Measurement(_Row):
    publication_id: str
    measurement_id: str
    device_id: list[str] = Field(default_factory=list)
    measurement_type: str | None = None
    spectrum: str | None = None
    light_intensity: Decimal | str | None = None
    intensity_unit: str | None = None
    scan_direction: str | None = None
    scan_rate: str | None = None
    mask_used: bool | str | None = None
    certified: bool | str | None = None
    certification_lab: str | None = None
    temperature: str | None = None
    stability_protocol: str | None = None
    stress_conditions: str | None = None
    note: str | None = None


class Result(_Row):
    publication_id: str
    result_id: str
    device_id: str | None = None
    measurement_id: str | None = None
    material_id: str | None = None
    metric_name: str | None = None
    metric_value: Decimal | str | None = None
    metric_unit: str | None = None
    aggregation: str | None = None
    spread_value: Decimal | str | None = None
    n_devices: int | str | None = None
    time_point: Decimal | str | None = None
    time_unit: str | None = None
    raw_value: str | None = None
    raw_unit: str | None = None
    normalized_value: Decimal | str | None = None
    normalized_unit: str | None = None
    result_origin: str | None = None
    cited_publication_id: str | None = None
    source_page: int | str | None = None
    source_section: str | None = None
    source_table: str | None = None
    source_figure: str | None = None
    source_fragment: str | None = None
    extraction_status: str | None = None
    duplicate_of: str | None = None
    note: str | None = None


class IssueRecord(_Row):
    publication_id: str
    issue_id: str = ""
    record_ids: list[str] = Field(default_factory=list)
    check_name: str
    description: str | None = None
    source_page: int | str | None = None
    source_fragment: str | None = None
    # служебные поля (не в CSV)
    key: str = ""  # внутренний I-... (К-6)
    severity: Literal["error", "warn", "info"] | None = None
    computed_by: Literal["backend", "ml"] = "backend"
    merged: bool = False
    confidence: float | None = None
    status: IssueStatus = IssueStatus.open
    numbers: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str | None:
        return self.description


ROW_MODELS: dict[str, type[_Row]] = {
    "publications": Publication,
    "materials": Material,
    "devices": Device,
    "layers": Layer,
    "measurements": Measurement,
    "results": Result,
    "issues": IssueRecord,
}


class PaperBundle(BaseModel):
    """Канонический формат статьи: строки 7 таблиц и метаданные записей."""
    publication: Publication
    materials: list[Material] = Field(default_factory=list)
    devices: list[Device] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)
    results: list[Result] = Field(default_factory=list)
    issues: list[IssueRecord] = Field(default_factory=list)

    @property
    def publication_id(self) -> str:
        return self.publication.publication_id

    def iter_tables(self) -> Iterator[tuple[str, list[_Row]]]:
        yield "publications", [self.publication]
        for t in ("materials", "devices", "layers", "measurements", "results", "issues"):
            yield t, getattr(self, t)

    def by_id(self, table: str, id: str) -> _Row | None:
        if table == "publications":
            return self.publication if self.publication.publication_id == id else None
        col = ID_COLUMN[table]
        for r in getattr(self, table):
            if getattr(r, col) == id:
                return r
        return None

    @staticmethod
    def effective_value(result: Result) -> Decimal | None:
        """Действующее значение: corrected_value ?? normalized_value."""
        cv = result.meta.corrected_value
        return cv if cv is not None else result.normalized_value

    @classmethod
    def failed_stub(cls, publication_id: str) -> "PaperBundle":
        """Пакет упавшего файла (К-14): в сдаче только строка publications с publication_id."""
        return cls(publication=Publication(publication_id=publication_id))
