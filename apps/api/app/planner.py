"""AI planner boundary and the deterministic rules-based provider.

`LLMProvider` is the adapter interface for an approved model gateway. Until one is
approved, `RulesPlanner` interprets Bahasa Indonesia and English questions using only
catalog synonyms. User text is treated as data: it can select catalog terms, never
instructions, IDs outside the catalog, formulas or code.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel

from .catalog import Catalog, Dataset, Metric
from .errors import FeatureDisabled
from .schema import (
    Clarification,
    ClarificationOption,
    DashboardSpec,
    FilterSpec,
    Intent,
    MetricRef,
    Period,
    TimeRange,
    Visualization,
)

PLANNER_VERSION = "rules-1.0"

NUMBER_WORDS = {
    "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6, "tujuh": 7, "delapan": 8,
    "sembilan": 9, "sepuluh": 10, "sebelas": 11, "dua belas": 12, "one": 1, "two": 2, "three": 3,
    "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
MONTHS = {
    "januari": 1, "january": 1, "jan": 1, "februari": 2, "february": 2, "feb": 2, "maret": 3, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "mei": 5, "may": 5, "juni": 6, "june": 6, "jun": 6, "juli": 7, "july": 7, "jul": 7,
    "agustus": 8, "august": 8, "agu": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "oktober": 10,
    "october": 10, "okt": 10, "oct": 10, "november": 11, "nov": 11, "desember": 12, "december": 12, "des": 12, "dec": 12,
}
MONTH_GRAIN_WORDS = ["per bulan", "bulanan", "monthly", "per month", "by month", "each month", "month by month", "tiap bulan",
                     "setiap bulan", "month over month", "mom", "tren", "trend", "trends", "over time", "perkembangan",
                     "dari bulan ke bulan", "month-on-month"]
QUARTER_GRAIN_WORDS = ["kuartal", "kuartalan", "quarterly", "per quarter", "by quarter", "triwulan", "triwulanan", "qoq", "per kuartal"]
DASHBOARD_WORDS = ["dashboard", "dasbor", "pantau", "memantau", "pemantauan", "monitor", "monitoring", "laporan rutin", "reusable"]
COMPARISON_WORDS = ["bandingkan", "compare", "comparison", "perbandingan", "versus", "vs", "dibandingkan", "antar", "across", "between"]
TOP_WORDS = ["tertinggi", "terbesar", "teratas", "terbaik", "paling tinggi", "paling besar", "highest", "largest", "biggest", "best", "top"]
BOTTOM_WORDS = ["terendah", "terkecil", "terburuk", "paling rendah", "paling kecil", "lowest", "smallest", "worst", "bottom"]
LATEST_WORDS = ["bulan ini", "this month", "terkini", "terbaru", "latest", "saat ini", "current", "sekarang", "posisi akhir"]
INJECTION_PATTERNS = [r"ignore (all |the )?(previous|above|prior)", r"abaikan (semua |instruksi|perintah)", r"system prompt",
                      r"\bdrop\s+table\b", r"\bselect\s+\*?\s*from\b", r"\bdelete\s+from\b", r"<script", r"you are now",
                      r"\bunion\s+select\b", r"grant (me )?(admin|access)"]
# Values that are also everyday words and need their field name nearby to count as a filter.
CONTEXT_REQUIRED_VALUES = {"kecil"}


class InterpretResult(BaseModel):
    status: Literal["ok", "needs_clarification", "unsupported"]
    intent: Intent | None = None
    clarification: Clarification | None = None
    message: str = ""
    planner: str = PLANNER_VERSION
    flags: list[str] = []


@dataclass
class SemanticContext:
    catalog: Catalog
    accessible: list[Dataset]
    latest_period: dict[str, date]
    earliest_period: dict[str, date]
    max_series: int = 12
    selected_dataset_id: str | None = None
    dimension_cardinality: dict[str, int] = field(default_factory=dict)


class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def classify_intent(self, question: str, ctx: SemanticContext, clarifications: dict[str, str]) -> InterpretResult: ...

    @abstractmethod
    def generate_dashboard_spec(self, intent: Intent, ctx: SemanticContext, force_dashboard: bool = False) -> DashboardSpec: ...

    @abstractmethod
    def explain_recommendation(self, cluster_summary: dict) -> str: ...


# --- helpers -----------------------------------------------------------------------


def _norm(text: str) -> str:
    text = text.lower().replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def _find(text: str, term: str) -> list[tuple[int, int]]:
    pattern = r"(?<![\w])" + re.escape(term.lower()) + r"(?![\w])"
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)]


def _has_any(text: str, terms: list[str]) -> list[str]:
    return [t for t in terms if _find(text, t)]


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < b and a < span[1] for a, b in taken)


def _match_longest(text: str, entries: list[tuple[str, str]]) -> tuple[list[str], dict[str, list[str]], list[tuple[int, int]]]:
    """Match (term, id) pairs longest-first without overlapping spans. Returns ids in text order."""
    taken: list[tuple[int, int]] = []
    hits: list[tuple[int, str, str]] = []
    for term, ident in sorted(entries, key=lambda e: -len(e[0])):
        for span in _find(text, term):
            if not _overlaps(span, taken):
                taken.append(span)
                hits.append((span[0], ident, term))
    hits.sort()
    ids: list[str] = []
    terms: dict[str, list[str]] = {}
    for _, ident, term in hits:
        if ident not in ids:
            ids.append(ident)
        terms.setdefault(ident, []).append(term)
    return ids, terms, taken


def _add_months(d: date, n: int) -> date:
    mi = d.year * 12 + d.month - 1 + n
    return date(mi // 12, mi % 12 + 1, 1)


def _fmt_month(d: date) -> str:
    return d.strftime("%b %Y")


def _period_label(start: date, end: date) -> str:
    return _fmt_month(start) if start == end else f"{_fmt_month(start)} - {_fmt_month(end)}"


def _parse_number(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    return NUMBER_WORDS.get(tok)


class RulesPlanner(LLMProvider):
    name = "mock-rules"

    def __init__(self, enabled=lambda: True):
        self._enabled = enabled

    def _check(self) -> None:
        if not self._enabled():
            raise FeatureDisabled("AI planning is currently disabled by an administrator.", code="AI_PLANNING_DISABLED")

    # --- interpretation ---------------------------------------------------------------
    def classify_intent(self, question: str, ctx: SemanticContext, clarifications: dict[str, str]) -> InterpretResult:
        self._check()
        text = _norm(question)
        flags: list[str] = []
        assumptions: list[str] = []
        if any(re.search(p, text) for p in INJECTION_PATTERNS):
            flags.append("instruction_like_text_ignored")
            assumptions.append("Instructions or code inside the question were ignored; only approved catalog terms are used.")

        cat = ctx.catalog
        accessible_ids = {d.id for d in ctx.accessible}

        # 1. Dataset resolution ----------------------------------------------------------
        dataset: Dataset | None = None
        if clarifications.get("dataset") in accessible_ids:
            dataset = cat.dataset(clarifications["dataset"])
        elif ctx.selected_dataset_id:
            dataset = cat.dataset(ctx.selected_dataset_id)
        else:
            scores: dict[str, int] = {}
            for ds in cat.datasets:
                score = 2 * len(_has_any(text, ds.synonyms))
                shared = {s for m in cat.metrics if m.dataset_id != ds.id for s in m.synonyms}
                score += len(_has_any(text, [s for m in cat.metrics_for(ds.id) for s in m.synonyms if s not in shared]))
                if score:
                    scores[ds.id] = score
            if scores:
                best = max(scores.values())
                top = [d for d, s in scores.items() if s == best]
                top_accessible = [d for d in top if d in accessible_ids]
                if top_accessible:
                    dataset = cat.dataset(top_accessible[0])
                elif not any(d in accessible_ids for d in scores):
                    return InterpretResult(
                        status="unsupported",
                        message="The data this question refers to is not available to your role. Ask your data steward about access, or choose one of your available datasets.",
                        flags=flags + ["dataset_not_entitled"],
                    )
                else:
                    dataset = cat.dataset(next(d for d in scores if d in accessible_ids))
            elif len(ctx.accessible) == 1:
                dataset = ctx.accessible[0]
                assumptions.append(f"Using {dataset.name}, the only dataset available to you.")
            elif not ctx.accessible:
                return InterpretResult(status="unsupported", message="No approved datasets are available to your role.", flags=flags)

        if dataset is None:
            return InterpretResult(
                status="needs_clarification",
                clarification=Clarification(
                    id="dataset",
                    kind="dataset",
                    question="Which dataset should this question use?",
                    options=[ClarificationOption(value=d.id, label=d.name, description=d.description) for d in ctx.accessible],
                ),
                flags=flags,
            )

        metrics_in_ds = cat.metrics_for(dataset.id)
        matched: dict[str, list[str]] = {}

        # 2. Unsupported terms -------------------------------------------------------------
        unsupported = [u.label for u in cat.known_unsupported_terms if _has_any(text, u.terms)]

        # 3. Metrics -----------------------------------------------------------------------
        metric_ids, metric_terms, _ = _match_longest(text, [(s, m.id) for m in metrics_in_ds for s in m.synonyms])
        matched.update({f"metric:{k}": v for k, v in metric_terms.items()})
        chosen = clarifications.get("metric")
        if chosen:
            if chosen not in {m.id for m in metrics_in_ds}:
                return InterpretResult(status="unsupported", message="The selected KPI is not in the approved catalog for this dataset.", flags=flags)
            metric_ids = [chosen] + [m for m in metric_ids if m != chosen]

        if not metric_ids:
            if unsupported:
                names = ", ".join(m.name for m in metrics_in_ds)
                return InterpretResult(
                    status="unsupported",
                    message=f"\"{unsupported[0]}\" is not defined in the approved PoC catalog, so it cannot be calculated. Approved KPIs for {dataset.name}: {names}.",
                    flags=flags + ["unsupported_metric"],
                )
            amb = next((a for a in cat.ambiguous_terms if a.dataset_id == dataset.id and _has_any(text, a.terms)), None)
            candidates = [cat.metric(c) for c in amb.candidates] if amb else metrics_in_ds
            question = amb.question if amb else f"Which approved KPI from {dataset.name} would you like to see?"
            return InterpretResult(
                status="needs_clarification",
                clarification=Clarification(
                    id="metric",
                    kind="metric",
                    question=question,
                    options=[ClarificationOption(value=m.id, label=m.name, description=m.definition) for m in candidates if m],
                ),
                flags=flags,
            )
        if unsupported:
            assumptions.append(f"{', '.join(unsupported)} is not in the approved catalog and was excluded (not substituted).")
        metric_ids = metric_ids[:4]
        metrics = [cat.metric(m) for m in metric_ids]

        # 4. Dimensions -------------------------------------------------------------------
        allowed_dims = set.intersection(*(set(m.allowed_dimensions) for m in metrics))
        dim_entries = [(s, f.id) for f in dataset.dimension_fields if f.id in allowed_dims for s in f.synonyms]
        dim_ids, dim_terms, _ = _match_longest(text, dim_entries)
        matched.update({f"dimension:{k}": v for k, v in dim_terms.items()})

        # 5. Filters (dimension values) -----------------------------------------------------
        filters: list[FilterSpec] = []
        for f in dataset.dimension_fields:
            if not f.values:
                continue
            hits = []
            for v in f.values:
                for a, _b in _find(text, v):
                    if v.lower() in CONTEXT_REQUIRED_VALUES:
                        before = text[max(0, a - 25):a]
                        if not any(s in before for s in f.synonyms):
                            continue
                    hits.append(v)
                    break
            if hits:
                filters.append(FilterSpec(field=f.id, values=hits))
                matched[f"filter:{f.id}"] = hits
        for flt in filters:
            if len(flt.values) == 1 and flt.field in dim_ids:
                dim_ids.remove(flt.field)
                assumptions.append(f"Filtered to {flt.values[0]}, so it is not also used as a breakdown.")
            elif len(flt.values) > 1 and flt.field not in dim_ids:
                dim_ids.append(flt.field)
        dim_ids = dim_ids[:2]

        # 6. Time grain and period ----------------------------------------------------------
        grain = None
        if _has_any(text, QUARTER_GRAIN_WORDS):
            grain = "quarter"
        if _has_any(text, MONTH_GRAIN_WORDS):
            grain = grain or "month"
        latest = ctx.latest_period[dataset.id]
        earliest = ctx.earliest_period[dataset.id]
        period, period_assumption = self._parse_period(text, latest)
        if period_assumption:
            assumptions.append(period_assumption)
        if period.end < earliest or period.start > latest:
            return InterpretResult(
                status="unsupported",
                message=f"The requested period is outside the synthetic data coverage ({_fmt_month(earliest)} - {_fmt_month(latest)}).",
                flags=flags,
            )
        if period.start < earliest or period.end > latest:
            start, end = max(period.start, earliest), min(period.end, latest)
            assumptions.append(f"Period limited to available synthetic data: {_period_label(start, end)}.")
            period = Period(start=start, end=end, period_type=period.period_type, label=_period_label(start, end))
        if grain is None and period.start != period.end and not dim_ids and period.period_type != "single_month":
            if any(m.formula.type == "growth" for m in metrics) or _has_any(text, ["selama", "during", "over"]):
                grain = "month"

        # 7. Ranking --------------------------------------------------------------------------
        top_n, top_order = self._parse_top(text)
        if top_n and not dim_ids:
            assumptions.append("Top-N ranking needs a breakdown such as region; it was ignored.")
            top_n = None

        comparison = bool(_has_any(text, COMPARISON_WORDS))
        explicit_dashboard = bool(_has_any(text, DASHBOARD_WORDS))

        # 8. Output decision --------------------------------------------------------------------
        if explicit_dashboard:
            decision, reason = "DASHBOARD_RECOMMENDATION", "You asked for a dashboard or ongoing monitoring."
        elif len(metrics) >= 2:
            decision, reason = "DASHBOARD_RECOMMENDATION", "Several related KPIs are better monitored together on a dashboard."
        elif grain and dim_ids:
            decision, reason = "DASHBOARD_RECOMMENDATION", "A trend combined with a breakdown is a recurring monitoring need suited to a dashboard."
        elif top_n and (grain or comparison):
            decision, reason = "DASHBOARD_RECOMMENDATION", "Ranking with comparison over time is suited to a dashboard."
        elif not grain and not dim_ids:
            decision, reason = "ANSWER_ONLY", "This asks for a single value for one period, so a direct answer is enough."
        else:
            decision, reason = "CHART_OR_ANALYSIS", "This is one focused trend or comparison, so a single chart is enough."

        shape = "trend" if grain else "snapshot"
        intent_key = f"{metric_ids[0]}__{'_'.join(sorted(dim_ids)) or 'total'}__{shape}"
        summary = self._summary(dataset, metrics, dim_ids, grain, period, filters, top_n, top_order)
        intent = Intent(
            dataset_id=dataset.id,
            metric_ids=metric_ids,
            dimension_ids=dim_ids,
            time_grain=grain,
            period=period,
            filters=filters,
            top_n=top_n,
            top_order=top_order,
            comparison=comparison,
            explicit_dashboard=explicit_dashboard,
            output_decision=decision,
            decision_reason=reason,
            intent_key=intent_key,
            assumptions=assumptions,
            matched_terms=matched,
            summary=summary,
        )
        return InterpretResult(status="ok", intent=intent, flags=flags)

    def _parse_period(self, text: str, latest: date) -> tuple[Period, str | None]:
        m = re.search(r"(\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\s+(bulan|months?)\s+(terakhir|belakangan)", text) or re.search(
            r"(?:last|past|previous)\s+(\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")\s+months?", text
        )
        if m:
            n = max(1, min(_parse_number(m.group(1)) or 12, 60))
            start = _add_months(latest, -(n - 1))
            return Period(start=start, end=latest, period_type=f"rolling_{n}_months", label=_period_label(start, latest)), f"\"Last {n} months\" is measured up to the latest available month ({_fmt_month(latest)})."
        if _has_any(text, ["setahun terakhir", "satu tahun terakhir", "last year", "past year", "last 12 months", "1 tahun terakhir"]):
            start = _add_months(latest, -11)
            return Period(start=start, end=latest, period_type="rolling_12_months", label=_period_label(start, latest)), f"Rolling 12 months up to {_fmt_month(latest)}."
        if _has_any(text, ["ytd", "year to date", "year-to-date", "sejak awal tahun", "tahun berjalan"]):
            start = date(latest.year, 1, 1)
            return Period(start=start, end=latest, period_type="ytd", label=_period_label(start, latest)), None
        q = re.search(r"\b(?:q|kuartal |triwulan |quarter )([1-4])\s*(20\d\d)\b", text)
        if q:
            qs = date(int(q.group(2)), 3 * (int(q.group(1)) - 1) + 1, 1)
            return Period(start=qs, end=_add_months(qs, 2), period_type="quarter", label=f"Q{q.group(1)} {q.group(2)}"), None
        month_pat = "|".join(sorted(MONTHS, key=len, reverse=True))
        mm = re.search(r"\b(" + month_pat + r")\s+(20\d\d)\b", text)
        if mm:
            d = date(int(mm.group(2)), MONTHS[mm.group(1)], 1)
            return Period(start=d, end=d, period_type="single_month", label=_fmt_month(d)), None
        years = sorted({int(y) for y in re.findall(r"\b(20\d\d)\b", text)})
        if years:
            start, end = date(years[0], 1, 1), date(years[-1], 12, 1)
            return Period(start=start, end=end, period_type="calendar_year", label=_period_label(start, end)), None
        if _has_any(text, LATEST_WORDS):
            return Period(start=latest, end=latest, period_type="single_month", label=_fmt_month(latest)), f"\"Latest\" means the latest available month ({_fmt_month(latest)})."
        start = _add_months(latest, -11)
        return (
            Period(start=start, end=latest, period_type="rolling_12_months", label=_period_label(start, latest)),
            f"No period was given, so the last 12 available months ({_period_label(start, latest)}) are used.",
        )

    def _parse_top(self, text: str) -> tuple[int | None, Literal["desc", "asc"]]:
        order: Literal["desc", "asc"] = "asc" if _has_any(text, BOTTOM_WORDS) else "desc"
        m = re.search(r"\b(?:top|bottom)\s*(\d+|[a-z]+)\b", text)
        if m and _parse_number(m.group(1)):
            return min(_parse_number(m.group(1)), 20), order
        tokens = re.findall(r"[\w&-]+", text)
        sup = {w for w in TOP_WORDS + BOTTOM_WORDS if " " not in w}
        for i, tok in enumerate(tokens):
            if tok in sup or (tok == "paling" and i + 1 < len(tokens)):
                for j in range(i - 1, max(-1, i - 7), -1):
                    n = _parse_number(tokens[j])
                    if tokens[j] == "belas" and j > 0:
                        n = (_parse_number(tokens[j - 1]) or 0) + 10
                    nxt = tokens[j + 1] if j + 1 < len(tokens) else ""
                    if n and nxt not in ("bulan", "month", "months", "tahun", "year", "years"):
                        return min(n, 20), order
        return None, order

    def _summary(self, ds: Dataset, metrics: list[Metric], dims: list[str], grain, period: Period, filters, top_n, top_order) -> str:
        parts = [" & ".join(m.name for m in metrics)]
        if dims:
            parts.append("by " + " and ".join(ds.field(d).name for d in dims))
        if grain:
            parts.append(f"{grain}ly")
        parts.append(period.label)
        if filters:
            parts.append("filtered to " + "; ".join(f"{ds.field(f.field).name}: {', '.join(f.values)}" for f in filters))
        if top_n:
            parts.append(f"{'top' if top_order == 'desc' else 'bottom'} {top_n} highlighted")
        return ", ".join(parts) + f" ({ds.name})"

    # --- planning ---------------------------------------------------------------------------
    def generate_dashboard_spec(self, intent: Intent, ctx: SemanticContext, force_dashboard: bool = False) -> DashboardSpec:
        self._check()
        cat = ctx.catalog
        ds = cat.dataset(intent.dataset_id)
        decision = "DASHBOARD_RECOMMENDATION" if force_dashboard else intent.output_decision
        requested = [cat.metric(m) for m in intent.metric_ids]
        primary = requested[0]
        assumptions = list(intent.assumptions)
        warnings: list[str] = []
        time_field = ds.date_field
        dims = list(intent.dimension_ids)
        grain = intent.time_grain or "month"
        period = intent.period
        top_n = intent.top_n
        sort = "asc" if intent.top_order == "asc" else "desc"

        kpi_metrics = list(requested)
        if decision == "DASHBOARD_RECOMMENDATION" and len(requested) == 1:
            for rid in primary.related_metrics:
                rel = cat.metric(rid)
                if rel and rel.dataset_id == ds.id and len(kpi_metrics) < 3:
                    kpi_metrics.append(rel)
                    assumptions.append(f"Added related approved KPI \"{rel.name}\" as context.")

        def range_note(m: Metric) -> str:
            if m.formula.type == "growth":
                return f"{period.label}" if period.start != period.end else f"{_fmt_month(period.end)} vs previous month"
            fld = ds.field(m.formula.field or m.formula.numerator)
            if fld and fld.time_aggregation == "period_end":
                return f"as of {_fmt_month(period.end)}"
            return period.label

        series_dim = None
        series_top = top_n
        if dims:
            card = ctx.dimension_cardinality.get(f"{ds.id}.{dims[0]}", 0)
            series_dim = dims[0]
            if card > ctx.max_series and not series_top:
                series_top = min(10, ctx.max_series)
                assumptions.append(f"Trend shows the top {series_top} {ds.field(dims[0]).name.lower()} values to stay readable.")

        dim_label = " and ".join(ds.field(d).name for d in dims)
        vizs: list[Visualization] = []
        if decision == "ANSWER_ONLY":
            vizs = [Visualization(id=f"kpi_{m.id}"[:64], type="kpi_card", title=f"{m.name} ({range_note(m)})", metric_ids=[m.id]) for m in requested]
        elif decision == "CHART_OR_ANALYSIS":
            if intent.time_grain or not dims:
                vizs.append(Visualization(
                    id="trend", type="line",
                    title=f"{grain.capitalize()}ly trend: {primary.name}" + (f" by {dim_label}" if series_dim else ""),
                    metric_ids=[primary.id], dimension_ids=[time_field] + ([series_dim] if series_dim else []),
                    top_n=series_top if series_dim else None, sort=sort if series_top else "none",
                ))
            else:
                vizs.append(Visualization(
                    id="comparison", type="bar", title=f"{primary.name} by {dim_label} ({range_note(primary)})",
                    metric_ids=[primary.id], dimension_ids=dims[:2], sort=sort, top_n=top_n if top_n and top_n < 5 and len(dims) == 1 else None,
                    highlight_top_n=top_n if top_n and top_n <= 10 and len(dims) == 1 else None,
                ))
        else:
            for m in kpi_metrics[:4]:
                vizs.append(Visualization(id=f"kpi_{m.id}"[:64], type="kpi_card", title=f"{m.name} ({range_note(m)})", metric_ids=[m.id]))
            vizs.append(Visualization(
                id="trend", type="line",
                title=f"{grain.capitalize()}ly trend: {primary.name}" + (f" - top {series_top} {ds.field(series_dim).name}" if series_dim and series_top else f" by {dim_label}" if series_dim else ""),
                metric_ids=[primary.id], dimension_ids=[time_field] + ([series_dim] if series_dim else []),
                top_n=series_top if series_dim else None, sort=sort if series_dim and series_top else "none",
            ))
            if dims:
                vizs.append(Visualization(
                    id="comparison", type="bar", title=f"{primary.name} by {ds.field(dims[0]).name} ({range_note(primary)})",
                    metric_ids=[primary.id], dimension_ids=[dims[0]], sort=sort,
                    highlight_top_n=top_n if top_n and top_n <= 10 else None,
                ))
            for extra in requested[1:3]:
                if len(vizs) >= 8:
                    break
                if dims:
                    vizs.append(Visualization(id=f"comparison_{extra.id}"[:64], type="bar", title=f"{extra.name} by {ds.field(dims[0]).name} ({range_note(extra)})", metric_ids=[extra.id], dimension_ids=[dims[0]], sort="desc"))
                else:
                    vizs.append(Visualization(id=f"trend_{extra.id}"[:64], type="line", title=f"{grain.capitalize()}ly trend: {extra.name}", metric_ids=[extra.id], dimension_ids=[time_field]))
            if len(requested) > 1 and dims and len(vizs) < 8:
                vizs.append(Visualization(id="detail_table", type="table", title=f"KPI detail by {ds.field(dims[0]).name}", metric_ids=[m.id for m in requested], dimension_ids=[dims[0]], sort="desc"))

        if decision != "ANSWER_ONLY" and period.start == period.end and any(v.type == "line" for v in vizs):
            warnings.append("Single-month period: the trend chart has one point. Widen the period for a trend.")

        title = primary.name + (f" by {dim_label}" if dims else "")
        if top_n and dims:
            title += f" - {'Top' if sort == 'desc' else 'Bottom'} {top_n}"
        used_metrics = []
        for v in vizs:
            for m in v.metric_ids:
                if m not in used_metrics:
                    used_metrics.append(m)
        dimensions = sorted({d for v in vizs for d in v.dimension_ids})
        return DashboardSpec(
            title=title[:120],
            description=intent.summary[:500],
            dataset_id=ds.id,
            time_range=TimeRange(field=time_field, start=period.start, end=period.end, grain=grain),
            metrics=[MetricRef(metric_id=m) for m in used_metrics],
            dimensions=dimensions,
            filters=intent.filters,
            visualizations=vizs,
            layout="executive_overview",
            assumptions=assumptions[:20],
            warnings=warnings[:20],
        )

    def explain_recommendation(self, s: dict) -> str:
        return (
            f"This workspace explored \"{s['topic']}\" {s['count']} times in the last {s['window_days']} days "
            f"across {s['weeks']} different weeks"
            + (f" by {s['actors']} distinct (pseudonymous) groups" if s.get("actors") is not None else "")
            + f". It uses approved KPIs with a stable definition and {'a monthly trend' if s.get('trend') else 'a repeatable breakdown'}, "
            "so a reusable dashboard may make it easier to monitor."
        )


def get_provider(name: str, enabled=lambda: True) -> LLMProvider:
    if name in ("mock", "mock-rules", "rules"):
        return RulesPlanner(enabled)
    # An approved internal model gateway would be added here behind the same interface.
    raise FeatureDisabled(f"LLM provider '{name}' is not configured in this PoC. Use LLM_PROVIDER=mock.", code="LLM_PROVIDER_NOT_CONFIGURED")
