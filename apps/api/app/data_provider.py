"""Approved data adapter and deterministic KPI engine.

The planner never touches data. This module only executes plans that already passed
policy validation, using catalog formulas (sum / ratio / growth). No SQL is generated.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .catalog import Catalog, Dataset, Metric
from .errors import FeatureDisabled, ValidationFailed
from .identity import UserContext
from .schema import DashboardSpec, FilterSpec, Visualization


def month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def index_to_date(mi: int) -> date:
    return date(mi // 12, mi % 12 + 1, 1)


def bucket_of(mi: int, grain: str) -> int:
    return mi - (mi % 3) if grain == "quarter" else mi


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    null_values_excluded: int = 0


@dataclass
class VizResult:
    viz_id: str
    viz_type: str
    title: str
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "viz_id": self.viz_id,
            "type": self.viz_type,
            "title": self.title,
            "columns": self.columns,
            "rows": self.rows,
            "warnings": self.warnings,
        }


class DataProvider(ABC):
    """Contract from PRD section 9.2 / 15.3."""

    @abstractmethod
    def list_available_datasets(self, user: UserContext) -> list[Dataset]: ...

    @abstractmethod
    def get_dataset_metadata(self, dataset_id: str, user: UserContext) -> Dataset: ...

    @abstractmethod
    def get_metric_definition(self, metric_id: str, user: UserContext) -> Metric: ...

    @abstractmethod
    def execute_validated_plan(self, spec: DashboardSpec, user: UserContext) -> list[VizResult]: ...

    @abstractmethod
    def get_data_freshness(self, dataset_id: str) -> dict[str, Any]: ...


class SyntheticDataAdapter(DataProvider):
    """Serves the deterministic synthetic CSV datasets declared in the catalog."""

    def __init__(self, catalog: Catalog, data_dir: Path, *, max_rows: int = 5000, max_series: int = 12, enabled=lambda: True):
        self.catalog = catalog
        self.max_rows = max_rows
        self.max_series = max_series
        self._enabled = enabled
        self._data: dict[str, list[dict[str, Any]]] = {}
        self._loaded_at: dict[str, datetime] = {}
        for ds in catalog.datasets:
            if ds.source_adapter != "synthetic":
                continue
            path = data_dir / "synthetic" / ds.source_file
            self._data[ds.id] = self._load(path, ds)
            self._loaded_at[ds.id] = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)

    @classmethod
    def from_rows(cls, catalog: Catalog, dataset_id: str, rows: list[dict[str, Any]], **kw) -> SyntheticDataAdapter:
        """Test helper: build an adapter around in-memory fixture rows."""
        inst = cls.__new__(cls)
        inst.catalog = catalog
        inst.max_rows = kw.get("max_rows", 5000)
        inst.max_series = kw.get("max_series", 12)
        inst._enabled = lambda: True
        inst._data = {dataset_id: rows}
        inst._loaded_at = {dataset_id: datetime.now(UTC)}
        return inst

    @staticmethod
    def _load(path: Path, ds: Dataset) -> list[dict[str, Any]]:
        types = {f.id: f.type for f in ds.fields}
        rows = []
        with path.open(encoding="utf-8", newline="") as f:
            for raw in csv.DictReader(f):
                row: dict[str, Any] = {}
                for k, v in raw.items():
                    t = types.get(k)
                    if t is None:
                        continue  # default deny: columns not in the catalog are never loaded
                    if v in ("", None):
                        row[k] = None
                    elif t == "date":
                        row[k] = date.fromisoformat(v)
                    elif t == "integer":
                        row[k] = int(v)
                    elif t == "decimal":
                        row[k] = Decimal(v)
                    else:
                        row[k] = v
                rows.append(row)
        return rows

    # --- contract -------------------------------------------------------------------
    def _check_enabled(self) -> None:
        if not self._enabled():
            raise FeatureDisabled("Data access is currently disabled by an administrator.", code="DATA_ACCESS_DISABLED")

    def list_available_datasets(self, user: UserContext) -> list[Dataset]:
        return self.catalog.accessible_datasets(user.roles)

    def get_dataset_metadata(self, dataset_id: str, user: UserContext) -> Dataset:
        return self.catalog.require_dataset(dataset_id, user.roles)

    def get_metric_definition(self, metric_id: str, user: UserContext) -> Metric:
        m = self.catalog.metric(metric_id)
        if m is None:
            raise ValidationFailed("Metric is not in the approved catalog.", code="METRIC_NOT_FOUND")
        self.catalog.require_dataset(m.dataset_id, user.roles)
        return m

    def get_data_freshness(self, dataset_id: str) -> dict[str, Any]:
        rows = self._data.get(dataset_id, [])
        ds = self.catalog.dataset(dataset_id)
        date_field = ds.date_field if ds else "report_month"
        months = [r[date_field] for r in rows if r.get(date_field)]
        return {
            "dataset_id": dataset_id,
            "earliest_period": min(months).isoformat() if months else None,
            "latest_period": max(months).isoformat() if months else None,
            "row_count": len(rows),
            "snapshot_loaded_at": self._loaded_at[dataset_id].isoformat() if dataset_id in self._loaded_at else None,
            "refresh": ds.refresh if ds else "",
            "synthetic": True,
        }

    def latest_period(self, dataset_id: str) -> date:
        return date.fromisoformat(self.get_data_freshness(dataset_id)["latest_period"])

    def execute_validated_plan(self, spec: DashboardSpec, user: UserContext) -> list[VizResult]:
        self._check_enabled()
        ds = self.catalog.require_dataset(spec.dataset_id, user.roles)  # re-authorize on every request
        return [self.run_visualization(spec, ds, viz) for viz in spec.visualizations]

    def components(self, spec: DashboardSpec, user: UserContext) -> tuple[list[str], list[dict[str, Any]]]:
        """Additive KPI parts at the finest filterable grain (every dimension), for the saved view.

        Returns the dimension IDs and rows of {scope, period, <dims>, metric_id, a, b}, where scope
        is "range" (whole period, for KPI cards and breakdowns) or "period" (per month/quarter).
        """
        self._check_enabled()
        ds = self.catalog.require_dataset(spec.dataset_id, user.roles)
        dims = [f.id for f in ds.dimension_fields]
        metrics = [self.catalog.metric(m.metric_id) for m in spec.metrics]
        scopes = [("range", None)]
        if any(ds.date_field in v.dimension_ids for v in spec.visualizations):
            scopes.append(("period", spec.time_range.grain))
        out: list[dict[str, Any]] = []
        for scope, grain in scopes:
            res = self.query(ds, metrics, dims, grain, spec.time_range.start, spec.time_range.end, spec.filters, components=True)
            for r in res.rows:
                for m in metrics:
                    a, b = r[m.id]
                    if a is None and b is None:
                        continue
                    out.append({"scope": scope, "period": r.get("period", ""), **{d: r[d] for d in dims}, "metric_id": m.id, "a": a, "b": b})
        return dims, out

    # --- engine ---------------------------------------------------------------------
    def run_visualization(self, spec: DashboardSpec, ds: Dataset, viz: Visualization) -> VizResult:
        metrics = [self.catalog.metric(m) for m in viz.metric_ids]
        time_dims = [d for d in viz.dimension_ids if d == ds.date_field]
        cat_dims = [d for d in viz.dimension_ids if d != ds.date_field]
        grain = spec.time_range.grain if time_dims else None

        keep_keys = None
        if viz.top_n and cat_dims:
            ranking = self.query(ds, [metrics[0]], cat_dims, None, spec.time_range.start, spec.time_range.end, spec.filters)
            ranked = sorted((r for r in ranking.rows if r[metrics[0].id] is not None), key=lambda r: r[metrics[0].id], reverse=viz.sort != "asc")
            keep_keys = {tuple(r[d] for d in cat_dims) for r in ranked[: viz.top_n]}

        res = self.query(ds, metrics, cat_dims, grain, spec.time_range.start, spec.time_range.end, spec.filters)
        rows = res.rows
        if keep_keys is not None:
            rows = [r for r in rows if tuple(r[d] for d in cat_dims) in keep_keys]

        columns: list[dict[str, Any]] = []
        out: list[dict[str, Any]] = []
        if viz.type == "kpi_card":
            columns = [_metric_col(m) for m in metrics]
            out = [{m.id: rows[0][m.id] if rows else None for m in metrics} | {"as_of": spec.time_range.end.isoformat()}]
        elif viz.type == "line" or (viz.type in ("bar", "table") and grain):
            if grain:
                columns.append({"key": "period", "label": "Period", "type": "date", "grain": grain})
            if cat_dims:
                columns += [{"key": d, "label": ds.field(d).name, "type": "string"} for d in cat_dims]
            columns += [_metric_col(m) for m in metrics]
            out = sorted(rows, key=lambda r: (r.get("period") or "", *[str(r.get(d) or "") for d in cat_dims]))
        else:  # bar / table by categorical dimension(s)
            columns = [{"key": d, "label": ds.field(d).name, "type": "string"} for d in cat_dims] + [_metric_col(m) for m in metrics]
            out = rows
            if viz.sort != "none":
                m0 = metrics[0].id
                present = sorted((r for r in out if r[m0] is not None), key=lambda r: r[m0], reverse=viz.sort == "desc")
                out = present + [r for r in out if r[m0] is None]
            if viz.highlight_top_n and cat_dims:
                ranked = sorted((r for r in out if r[metrics[0].id] is not None), key=lambda r: r[metrics[0].id], reverse=True)
                top = {id(r) for r in ranked[: viz.highlight_top_n]}
                out = [r | {"highlight": id(r) in top} for r in out]
                columns.append({"key": "highlight", "label": f"Top {viz.highlight_top_n}", "type": "boolean"})

        if len(out) > self.max_rows:
            raise ValidationFailed("Result exceeds the configured row limit.", code="RESULT_TOO_LARGE")
        return VizResult(viz.id, viz.type, viz.title, columns, out, list(res.warnings))

    def query(
        self,
        ds: Dataset,
        metrics: list[Metric],
        dims: list[str],
        grain: str | None,
        start: date,
        end: date,
        filters: list[FilterSpec],
        components: bool = False,
    ) -> QueryResult:
        """Aggregate metrics by `dims` (and time `grain` when given) over [start, end].

        With `components=True` each metric cell holds its additive parts `(a, b)` instead of the
        final value: sum -> (total, None), ratio -> (numerator, denominator), growth -> (current,
        previous). Summing parts over any subset of groups and then applying the formula gives
        the same answer as querying that subset directly (for period-end measures this holds when
        every group has data in the period-end month), which is what Tableau filters need.
        """
        self._check_enabled()
        date_field = ds.date_field
        s_mi, e_mi = month_index(start), month_index(end)
        if e_mi < s_mi:
            raise ValidationFailed("Time range end is before start.", code="INVALID_TIME_RANGE")
        range_len = e_mi - s_mi + 1
        lookback = range_len if grain is None else (3 if grain == "quarter" else 1)
        allowed = {f.field: set(f.values) for f in filters}

        index: dict[tuple, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for r in self._data[ds.id]:
            d = r.get(date_field)
            if d is None:
                continue
            mi = month_index(d)
            if mi < s_mi - lookback or mi > e_mi:
                continue
            if any(r.get(fid) not in vals for fid, vals in allowed.items()):
                continue
            index[tuple(r.get(k) for k in dims)][mi].append(r)

        warnings: set[str] = set()
        nulls = 0
        agg_of = {f.id: f.time_aggregation or "sum" for f in ds.fields if f.role == "measure"}

        def base(group: dict[int, list[dict]], fld: str, window: list[int]) -> Decimal | None:
            nonlocal nulls
            months = [m for m in window if group.get(m)]
            if not months:
                return None
            if agg_of[fld] == "period_end":
                months = [max(months)]
                if not group.get(max(window)) and max(window) >= s_mi:
                    warnings.add(f"Some groups have no data for {index_to_date(max(window)):%b %Y}; the latest available month was used.")
            vals = [r[fld] for m in months for r in group[m]]
            present = [Decimal(v) for v in vals if v is not None]
            nulls += len(vals) - len(present)
            return sum(present, Decimal(0)) if present else None

        def value(metric: Metric, group: dict[int, list[dict]], window: list[int], prev: list[int]) -> float | None:
            f = metric.formula
            if f.type == "sum":
                v = base(group, f.field, window)
                return None if v is None else float(v)
            if f.type == "ratio":
                n, d = base(group, f.numerator, window), base(group, f.denominator, window)
                if d is None or d == 0:
                    warnings.add(f"{metric.name}: blank where the denominator is zero or missing.")
                    return None
                return None if n is None else round(float(n / d * Decimal(str(f.scale))), 6)
            if f.type == "growth":
                cur, before = base(group, f.field, window), base(group, f.field, prev)
                if before is None or before == 0:
                    warnings.add(f"{metric.name}: blank where the previous period is missing or zero.")
                    return None
                return None if cur is None else round(float((cur / before - 1) * 100), 6)
            raise ValidationFailed("Unsupported formula type.", code="UNSUPPORTED_FORMULA")

        def parts(metric: Metric, group: dict[int, list[dict]], window: list[int], prev: list[int]) -> tuple[float | None, float | None]:
            f = metric.formula
            if f.type == "sum":
                a, b = base(group, f.field, window), None
            elif f.type == "ratio":
                a, b = base(group, f.numerator, window), base(group, f.denominator, window)
            else:
                a, b = base(group, f.field, window), base(group, f.field, prev)
            return (None if a is None else float(a)), (None if b is None else float(b))

        if grain is None:
            buckets = {None: (list(range(s_mi, e_mi + 1)), list(range(s_mi - range_len, s_mi)))}
        else:
            buckets = {}
            step = 3 if grain == "quarter" else 1
            for mi in range(s_mi, e_mi + 1):
                b = bucket_of(mi, grain)
                window, _ = buckets.get(b, ([], []))
                window.append(mi)
                buckets[b] = (window, list(range(b - step, b)))
            if grain == "quarter" and (s_mi % 3 or (e_mi + 1) % 3):
                warnings.add("The range starts or ends mid-quarter; edge quarters are partial.")

        rows: list[dict[str, Any]] = []
        for key, group in index.items():
            if not any(s_mi <= m <= e_mi for m in group):
                continue
            for b, (window, prev) in buckets.items():
                if not any(group.get(m) for m in window):
                    continue
                row: dict[str, Any] = dict(zip(dims, key, strict=True))
                if b is not None:
                    row["period"] = index_to_date(b).isoformat()
                for m in metrics:
                    row[m.id] = parts(m, group, window, prev) if components else value(m, group, window, prev)
                rows.append(row)

        if dims and len({tuple(r[d] for d in dims) for r in rows}) > max(self.max_series, 40):
            warnings.add("Many categories returned; consider a filter or top-N.")
        if nulls:
            warnings.add(f"{nulls} null value(s) were excluded according to KPI null handling.")
        if not rows:
            warnings.add("No data matched the selected period and filters.")
        return QueryResult(rows, sorted(warnings), nulls)


def _metric_col(m: Metric) -> dict[str, Any]:
    return {"key": m.id, "label": m.name, "type": "number", "unit": m.unit, "format": m.format, "metric_id": m.id}
