"""Deterministic policy and validation. Runs on every plan, edit, preview and publish.

Nothing a planner or client sends is trusted: schema, catalog allowlists, chart
compatibility, filters and dataset entitlements are all re-checked here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .catalog import Catalog
from .data_provider import VizResult
from .identity import UserContext
from .schema import DashboardSpec

MAX_RANGE_MONTHS = 60


@dataclass
class Report:
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[dict[str, str]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def fail(self, code: str, message: str, path: str = "") -> None:
        self.errors.append({"code": code, "message": message, "path": path})

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "status": "pass" if ok else "fail", "detail": detail})

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings, "checks": self.checks}


def validate_spec(
    raw: dict | DashboardSpec,
    catalog: Catalog,
    user: UserContext,
    *,
    cardinality: dict[str, int],
    coverage: tuple[Any, Any] | None = None,
    max_series: int = 12,
    max_categories: int = 40,
) -> tuple[Report, DashboardSpec | None]:
    rep = Report()
    try:
        spec = raw if isinstance(raw, DashboardSpec) else DashboardSpec.model_validate(raw)
    except ValidationError as exc:
        for e in exc.errors()[:20]:
            rep.fail("SCHEMA_INVALID", e["msg"], ".".join(str(p) for p in e["loc"]))
        rep.check("schema", False)
        return rep, None
    rep.check("schema", True, "Strict schema v1.0")

    ds = catalog.dataset(spec.dataset_id)
    if ds is None or ds.status != "approved_for_poc":
        rep.fail("DATASET_NOT_APPROVED", "Dataset is not in the approved catalog.", "dataset_id")
        return rep, spec
    if not catalog.can_access(ds.id, user.roles):
        rep.fail("DATASET_NOT_ENTITLED", "You are not entitled to this dataset.", "dataset_id")
        rep.check("authorization", False)
        return rep, spec
    rep.check("authorization", True, "Dataset entitlement re-checked server-side")

    # Time range
    tr = spec.time_range
    if tr.field != ds.date_field:
        rep.fail("TIME_FIELD_INVALID", "Time range must use the dataset's approved date field.", "time_range.field")
    if tr.end < tr.start:
        rep.fail("TIME_RANGE_INVALID", "Time range end is before start.", "time_range")
    months = (tr.end.year - tr.start.year) * 12 + tr.end.month - tr.start.month + 1
    if months > MAX_RANGE_MONTHS:
        rep.fail("TIME_RANGE_TOO_LONG", f"Time range is limited to {MAX_RANGE_MONTHS} months.", "time_range")
    if tr.start.day != 1 or tr.end.day != 1:
        rep.fail("TIME_RANGE_INVALID", "Monthly data: dates must be the first day of a month.", "time_range")
    if coverage and (tr.start < coverage[0] or tr.end > coverage[1]):
        rep.warnings.append("Part of the time range is outside the synthetic data coverage.")
    rep.check("time_range", not any(e["path"].startswith("time_range") for e in rep.errors))

    # Metrics
    metric_ids = [m.metric_id for m in spec.metrics]
    if len(set(metric_ids)) != len(metric_ids):
        rep.fail("DUPLICATE_METRIC", "Metrics must be unique.", "metrics")
    metrics = {}
    for i, mid in enumerate(metric_ids):
        m = catalog.metric(mid)
        if m is None or m.status != "approved_for_poc":
            rep.fail("METRIC_NOT_APPROVED", f"KPI '{mid}' is not in the approved catalog.", f"metrics.{i}")
        elif m.dataset_id != ds.id:
            rep.fail("METRIC_DATASET_MISMATCH", f"KPI '{mid}' does not belong to this dataset.", f"metrics.{i}")
        else:
            metrics[mid] = m
    rep.check("metrics_allowlist", not any(e["path"].startswith("metrics") for e in rep.errors))

    # Dimensions
    for i, d in enumerate(spec.dimensions):
        f = ds.field(d)
        if f is None or f.role not in ("dimension", "time"):
            rep.fail("DIMENSION_NOT_ALLOWED", f"'{d}' is not an approved dimension.", f"dimensions.{i}")
    rep.check("dimensions_allowlist", not any(e["path"].startswith("dimensions") for e in rep.errors))

    # Filters
    seen = set()
    for i, flt in enumerate(spec.filters):
        f = ds.field(flt.field)
        if f is None or f.role != "dimension" or not f.values:
            rep.fail("FILTER_NOT_ALLOWED", f"Filtering on '{flt.field}' is not permitted.", f"filters.{i}")
            continue
        if flt.field in seen:
            rep.fail("FILTER_DUPLICATE", "Each field can be filtered once.", f"filters.{i}")
        seen.add(flt.field)
        bad = [v for v in flt.values if v not in f.values]
        if bad:
            rep.fail("FILTER_VALUE_NOT_ALLOWED", f"Unknown value(s) for {f.name}.", f"filters.{i}")
    rep.check("filters", not any(e["path"].startswith("filters") for e in rep.errors))

    # Visualizations / chart compatibility
    viz_ids = [v.id for v in spec.visualizations]
    if len(set(viz_ids)) != len(viz_ids):
        rep.fail("DUPLICATE_VISUALIZATION", "Visualization IDs must be unique.", "visualizations")
    filtered_card = {flt.field: len(flt.values) for flt in spec.filters}
    for i, v in enumerate(spec.visualizations):
        p = f"visualizations.{i}"
        for mid in v.metric_ids:
            if mid not in metric_ids:
                rep.fail("VIZ_METRIC_UNDECLARED", f"Chart uses KPI '{mid}' that is not declared in metrics.", p)
        for d in v.dimension_ids:
            if d not in spec.dimensions:
                rep.fail("VIZ_DIMENSION_UNDECLARED", f"Chart uses dimension '{d}' that is not declared.", p)
            for mid in v.metric_ids:
                m = metrics.get(mid)
                if m and d not in m.allowed_dimensions:
                    rep.fail("DIMENSION_NOT_ALLOWED_FOR_KPI", f"{m.name} cannot be broken down by '{d}'.", p)
        cats = [d for d in v.dimension_ids if d != ds.date_field]
        has_time = ds.date_field in v.dimension_ids

        def card(d: str) -> int:
            return filtered_card.get(d) or cardinality.get(f"{ds.id}.{d}", 0)

        if v.type == "kpi_card":
            if len(v.metric_ids) != 1 or v.dimension_ids:
                rep.fail("CHART_INCOMPATIBLE", "A KPI card shows exactly one KPI with no breakdown.", p)
        elif v.type == "line":
            if not v.dimension_ids or v.dimension_ids[0] != ds.date_field:
                rep.fail("CHART_INCOMPATIBLE", "A line chart needs the time dimension on its x-axis.", p)
            if len(cats) > 1:
                rep.fail("CHART_INCOMPATIBLE", "A line chart supports at most one series breakdown.", p)
            if cats and len(v.metric_ids) > 1:
                rep.fail("CHART_INCOMPATIBLE", "A line chart with series supports one KPI.", p)
            if cats and min(card(cats[0]), v.top_n or 10**6) > max_series:
                rep.fail("TOO_MANY_SERIES", f"More than {max_series} lines; add a filter or top-N.", p)
        elif v.type == "bar":
            if not v.dimension_ids:
                rep.fail("CHART_INCOMPATIBLE", "A bar chart needs a breakdown dimension.", p)
            if has_time and v.dimension_ids[0] != ds.date_field:
                rep.fail("CHART_INCOMPATIBLE", "Put the time dimension first in a bar chart.", p)
            if len(v.dimension_ids) == 2 and len(v.metric_ids) > 1:
                rep.fail("CHART_INCOMPATIBLE", "A grouped bar chart supports one KPI.", p)
            if len(v.metric_ids) > 2:
                rep.fail("CHART_INCOMPATIBLE", "A bar chart supports at most two KPIs.", p)
            if cats and not has_time and min(card(cats[0]), v.top_n or 10**6) > max_categories:
                rep.fail("TOO_MANY_CATEGORIES", f"More than {max_categories} bars; add a filter or top-N.", p)
        elif v.type == "table":
            if not v.dimension_ids:
                rep.fail("CHART_INCOMPATIBLE", "A table needs at least one breakdown dimension.", p)
        if v.highlight_top_n and not (v.type == "bar" and len(cats) == 1 and not has_time):
            rep.fail("CHART_INCOMPATIBLE", "Top-N highlighting is supported on single-dimension bar charts.", p)
        if v.top_n and not cats:
            rep.fail("CHART_INCOMPATIBLE", "Top-N needs a categorical breakdown.", p)
    rep.check("chart_compatibility", not any(e["path"].startswith("visualizations") for e in rep.errors))
    return rep, spec


def validate_results(spec: DashboardSpec, results: list[VizResult], catalog: Catalog, *, max_rows: int, max_series: int) -> Report:
    rep = Report()
    for res in results:
        keys = [c["key"] for c in res.columns]
        if len(res.rows) > max_rows:
            rep.fail("RESULT_TOO_LARGE", f"{res.title}: too many rows.", res.viz_id)
        if res.viz_type == "kpi_card" and len(res.rows) != 1:
            rep.fail("RESULT_SHAPE", f"{res.title}: KPI card must have exactly one row.", res.viz_id)
        for row in res.rows:
            missing = [k for k in keys if k not in row]
            if missing:
                rep.fail("RESULT_COLUMNS", f"{res.title}: missing columns {missing}.", res.viz_id)
                break
            for c in res.columns:
                val = row.get(c["key"])
                if c["type"] == "number" and val is not None and (not isinstance(val, (int, float)) or not math.isfinite(val)):
                    rep.fail("RESULT_TYPE", f"{res.title}: non-numeric value in {c['label']}.", res.viz_id)
        series_cols = [c["key"] for c in res.columns if c["type"] == "string"]
        if res.viz_type == "line" and series_cols:
            n = len({r[series_cols[0]] for r in res.rows})
            if n > max_series:
                rep.fail("TOO_MANY_SERIES", f"{res.title}: {n} series exceed the limit.", res.viz_id)
        for w in res.warnings:
            if w not in rep.warnings:
                rep.warnings.append(w)
    rep.check("result_shape_and_types", rep.valid)

    # Reconciliation for additive KPIs: totals must equal their breakdowns.
    by_id = {r.viz_id: r for r in results}
    vizs = {v.id: v for v in spec.visualizations}
    ds = catalog.dataset(spec.dataset_id)
    for kpi in (r for r in results if r.viz_type == "kpi_card"):
        mid = vizs[kpi.viz_id].metric_ids[0]
        m = catalog.metric(mid)
        total = kpi.rows[0].get(mid) if kpi.rows else None
        if not m or not m.additive or total is None:
            continue
        stock = ds.field(m.formula.field).time_aggregation == "period_end"
        for v in spec.visualizations:
            res = by_id[v.id]
            if mid not in v.metric_ids or v.top_n or v.type == "kpi_card" or len(v.dimension_ids) != 1:
                continue
            vals = [r[mid] for r in res.rows if r.get(mid) is not None]
            if v.dimension_ids[0] == ds.date_field:
                if not res.rows:
                    continue
                last = res.rows[-1].get(mid)
                got = last if stock else sum(vals)
            else:
                got = sum(vals)
            ok = got is not None and math.isclose(got, total, rel_tol=1e-9, abs_tol=0.5)
            rep.check(f"reconcile:{kpi.viz_id}~{v.id}", ok, f"total={total} breakdown={got}")
            if not ok:
                rep.fail("RECONCILIATION_FAILED", f"{m.name}: breakdown in '{res.title}' does not match the KPI total.", v.id)
    return rep
