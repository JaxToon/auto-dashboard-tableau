"""Typed contracts: dashboard specification, analytical intent and API payloads.

Every model forbids unknown keys, so a planner (rules or LLM) or a client cannot smuggle
SQL, formulas, code or extra fields into a specification.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

VizType = Literal["kpi_card", "line", "bar", "table"]
Grain = Literal["month", "quarter"]
OutputDecision = Literal["ANSWER_ONLY", "CHART_OR_ANALYSIS", "DASHBOARD_RECOMMENDATION"]
Layout = Literal["executive_overview", "detailed_grid"]
Recipe = Literal["auto", "executive", "band", "rail"]  # Tableau dashboard arrangement (tableau_theme.py)
Theme = Literal["auto", "slate", "navy", "teal", "plum", "forest", "sunset"]  # Tableau dashboard look
VIZ_TYPES: tuple[str, ...] = ("kpi_card", "line", "bar", "table")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TimeRange(Strict):
    field: str = Field(pattern=ID_PATTERN)
    start: date
    end: date
    grain: Grain = "month"


class MetricRef(Strict):
    metric_id: str = Field(pattern=ID_PATTERN)
    aggregation: Literal["approved_formula"] = "approved_formula"


class FilterSpec(Strict):
    field: str = Field(pattern=ID_PATTERN)
    op: Literal["in"] = "in"
    values: list[str] = Field(min_length=1, max_length=40)

    @field_validator("values")
    @classmethod
    def _short_values(cls, v: list[str]) -> list[str]:
        if any(len(x) > 80 for x in v):
            raise ValueError("filter values are limited to 80 characters")
        return v


class Visualization(Strict):
    id: str = Field(pattern=ID_PATTERN)
    type: VizType
    title: str = Field(min_length=1, max_length=120)
    metric_ids: list[str] = Field(min_length=1, max_length=4)
    dimension_ids: list[str] = Field(default_factory=list, max_length=2)
    sort: Literal["none", "asc", "desc"] = "none"
    top_n: int | None = Field(default=None, ge=1, le=20)
    highlight_top_n: int | None = Field(default=None, ge=1, le=10)


class DashboardSpec(Strict):
    schema_version: Literal["1.0"] = "1.0"
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    dataset_id: str = Field(pattern=ID_PATTERN)
    time_range: TimeRange
    metrics: list[MetricRef] = Field(min_length=1, max_length=6)
    dimensions: list[str] = Field(default_factory=list, max_length=4)
    filters: list[FilterSpec] = Field(default_factory=list, max_length=6)
    visualizations: list[Visualization] = Field(min_length=1, max_length=8)
    layout: Layout = "executive_overview"
    recipe: Recipe = "auto"
    theme: Theme = "auto"
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=20)


# --- Intent ------------------------------------------------------------------------


class Period(Strict):
    start: date
    end: date
    period_type: str  # e.g. rolling_12_months, calendar_year, single_month, ytd
    label: str


class Intent(Strict):
    dataset_id: str
    metric_ids: list[str]
    dimension_ids: list[str] = Field(default_factory=list)  # non-time dimensions
    time_grain: Grain | None = None
    period: Period
    filters: list[FilterSpec] = Field(default_factory=list)
    top_n: int | None = None
    top_order: Literal["desc", "asc"] = "desc"
    comparison: bool = False
    explicit_dashboard: bool = False
    output_decision: OutputDecision
    decision_reason: str
    intent_key: str
    assumptions: list[str] = Field(default_factory=list)
    matched_terms: dict[str, list[str]] = Field(default_factory=dict)
    summary: str = ""


class ClarificationOption(Strict):
    value: str
    label: str
    description: str = ""


class Clarification(Strict):
    id: str
    kind: Literal["metric", "dataset"]
    question: str
    options: list[ClarificationOption]


class InterpretRequest(Strict):
    question: str = Field(min_length=1)
    dataset_id: str | None = Field(default=None, pattern=ID_PATTERN)
    clarifications: dict[str, str] = Field(default_factory=dict, max_length=5)


class PlanRequest(Strict):
    intent: Intent
    force_dashboard: bool = False


class DraftPatch(Strict):
    """Only user-editable properties. KPI formulas, dataset and metric IDs are protected."""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    layout: Layout | None = None
    recipe: Recipe | None = None
    theme: Theme | None = None
    time_range_start: date | None = None
    time_range_end: date | None = None
    filters: list[FilterSpec] | None = None
    chart_types: dict[str, VizType] | None = None
    visualization_titles: dict[str, str] | None = None
    remove_visualizations: list[str] | None = None


class PreviewRequest(Strict):
    """Viewer-side filters for exploring a draft. They are validated like the spec but never saved."""

    filters: list[FilterSpec] | None = Field(default=None, max_length=6)
    time_range_start: date | None = None
    time_range_end: date | None = None


class ApproveRequest(Strict):
    decision: Literal["approved", "changes_requested"]
    comment: str = Field(default="", max_length=1000)


class PublishRequest(Strict):
    confirm: bool
    target_project_id: str | None = Field(default=None, max_length=80)


class RecommendationDecision(Strict):
    decision: Literal["accept", "dismiss", "create_draft"]


class SettingsPatch(Strict):
    feature_ai_planning: bool | None = None
    feature_data_access: bool | None = None
    feature_dashboard_publish: bool | None = None
    feature_recurring_recommendations: bool | None = None
    feature_recommendation_telemetry: bool | None = None
    review_required: bool | None = None
    rec_min_events: int | None = Field(default=None, ge=2, le=1000)
    rec_window_days: int | None = Field(default=None, ge=1, le=365)
    rec_min_actors: int | None = Field(default=None, ge=1, le=100)
