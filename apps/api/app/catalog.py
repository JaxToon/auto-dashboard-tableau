"""Semantic catalog: approved datasets, fields and KPI definitions.

The catalog is the only source of dataset, field and metric IDs. Nothing outside it can
be planned, validated or executed (default deny).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import get_settings
from .errors import Forbidden, NotFound


class Field(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    type: Literal["date", "string", "decimal", "integer"]
    role: Literal["time", "dimension", "measure"]
    synonyms: list[str] = []
    values: list[str] | None = None
    unit: str | None = None
    time_aggregation: Literal["sum", "period_end"] | None = None


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    description: str
    domain: str
    owner: str
    classification: str
    permitted_use: str
    source_adapter: str
    source_file: str
    grain: str
    refresh: str
    date_field: str
    calendar: str
    allowed_roles: list[str]
    status: str
    version: str
    synonyms: list[str] = []
    example_prompts: list[str] = []
    fields: list[Field]

    def field(self, field_id: str) -> Field | None:
        return next((f for f in self.fields if f.id == field_id), None)

    @property
    def dimension_fields(self) -> list[Field]:
        return [f for f in self.fields if f.role == "dimension"]


class Formula(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["sum", "ratio", "growth"]
    field: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    scale: float = 1


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    dataset_id: str
    name: str
    definition: str
    formula: Formula
    formula_display: str
    calculation_notes: str
    unit: str
    format: Literal["currency", "percent", "number"]
    grain: str
    allowed_dimensions: list[str]
    null_handling: str
    comparison_rules: str
    synonyms: list[str] = []
    related_metrics: list[str] = []
    owner: str
    version: str
    effective_date: str
    status: str

    @property
    def additive(self) -> bool:
        """Additive metrics can be reconciled: breakdown values sum to the total."""
        return self.formula.type == "sum"


class UnsupportedTerm(BaseModel):
    terms: list[str]
    label: str


class AmbiguousTerm(BaseModel):
    terms: list[str]
    dataset_id: str
    candidates: list[str]
    question: str


class Catalog(BaseModel):
    model_config = ConfigDict(extra="ignore")
    catalog_version: str
    datasets: list[Dataset]
    metrics: list[Metric]
    known_unsupported_terms: list[UnsupportedTerm] = []
    ambiguous_terms: list[AmbiguousTerm] = []

    def dataset(self, dataset_id: str) -> Dataset | None:
        return next((d for d in self.datasets if d.id == dataset_id), None)

    def metric(self, metric_id: str) -> Metric | None:
        return next((m for m in self.metrics if m.id == metric_id), None)

    def metrics_for(self, dataset_id: str) -> list[Metric]:
        return [m for m in self.metrics if m.dataset_id == dataset_id and m.status == "approved_for_poc"]

    # --- entitlement-aware access -------------------------------------------------
    def accessible_datasets(self, roles: list[str]) -> list[Dataset]:
        return [d for d in self.datasets if d.status == "approved_for_poc" and set(d.allowed_roles) & set(roles)]

    def can_access(self, dataset_id: str, roles: list[str]) -> bool:
        return any(d.id == dataset_id for d in self.accessible_datasets(roles))

    def require_dataset(self, dataset_id: str, roles: list[str]) -> Dataset:
        ds = self.dataset(dataset_id)
        if ds is None:
            raise NotFound("Dataset is not in the approved catalog.", code="DATASET_NOT_FOUND")
        if not self.can_access(dataset_id, roles):
            raise Forbidden("You are not entitled to this dataset.", code="DATASET_NOT_ENTITLED")
        return ds


@lru_cache
def get_catalog() -> Catalog:
    path = get_settings().data_dir / "seeds" / "catalog.json"
    return Catalog.model_validate(json.loads(path.read_text(encoding="utf-8")))
