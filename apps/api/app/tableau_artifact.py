"""Deterministic Tableau workbook (.twbx) generation from a validated specification.

The planner never produces workbook XML. This module renders a fixed, tested template.
The embedded data file holds the additive *parts* of every approved KPI (sum; numerator and
denominator; current and previous) at the finest filterable grain of the saved view. Tableau
calculations apply the catalog formula (sum first, then divide), so the Region / segment /
branch filters on the dashboard always give the same numbers the app would compute, and with
every filter on "All" the published KPIs equal the validated preview.

Identical inputs produce byte-identical output (fixed zip timestamps, sorted entries).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date

from .catalog import Catalog, Metric
from .data_provider import VizResult
from .schema import DashboardSpec
from .tableau_theme import Theme, choose_style

USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)
REMOTE_TYPES = {"string": "129", "real": "5", "date": "7"}
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MARKS = {"kpi_card": "Text", "line": "Line", "bar": "Bar", "table": "Text"}
MAX_END_LABELS = 6  # line charts with more series than this become a heatmap
FORM_MARKS = {"area": "Area", "spotlight": "Line", "heatmap": "Square", "dots": "Circle", "table": "Text"}


def chart_form(viz_type: str, metric: Metric, n_cats: int, has_time: bool, n_series: int, highlighted: bool) -> str:
    """The visual form for a validated chart. Same data and KPI; only the encoding changes.

    trend without breakdown -> area; few series -> spotlight lines labelled at their ends;
    many series -> heatmap (member x period); target ratios -> bars against a 100% line;
    growth rates -> dots against the average; amounts -> ranked bars with the top 3 in the accent;
    bars over time -> columns with the latest period in the accent.
    """
    if viz_type == "line":
        if not n_cats:
            return "area"
        return "spotlight" if n_series <= MAX_END_LABELS else "heatmap"
    if viz_type == "bar":
        if has_time and not n_cats:
            return "columns"
        if highlighted:
            return "highlight"
        if n_cats > 1:
            return "series_bars"
        if metric.formula.type == "ratio" and metric.formula.scale == 100 and "target" in f"{metric.id} {metric.name}".lower():
            return "target"
        if metric.formula.type == "growth":
            return "dots"
        return "ranked"
    return "table"


def _short_title(title: str) -> str:
    """'Total Credit Outstanding (as of Dec 2025)' -> 'Total Credit Outstanding' (the period is in the header)."""
    return re.sub(r"\s*\((as of [^)]*|[A-Z][a-z]{2} \d{4}[^)]*)\)\s*$", "", title)


def chart_headline(form: str, res: VizResult, metric: Metric, dim_name: str, span: str, grain: str) -> tuple[str, str]:
    """A short headline that says what the chart shows (computed from the validated rows) and a subtitle."""
    mid = metric.id

    def fmt(v: float) -> str:
        return format_value(v, metric.unit)

    rows = [r for r in res.rows if r.get(mid) is not None]
    key = next((c["key"] for c in res.columns if c["type"] == "string"), None)
    by = f" by {dim_name}" if dim_name else ""
    sub = f"{_short_title(metric.name)}{by}  ·  {span}"
    if not rows:
        return _short_title(res.title), sub
    if form in ("area", "columns"):
        ordered = sorted(rows, key=lambda r: str(r.get("period", "")))
        first, last = ordered[0][mid], ordered[-1][mid]
        if form == "area":
            return f"{_short_title(metric.name)} moved from {fmt(first)} to {fmt(last)}", sub
        return f"{fmt(last)} in {_period_text(str(ordered[-1]['period']), grain)}", sub
    if not key:
        return _short_title(res.title), sub
    if form in ("spotlight", "heatmap"):
        lp = max(str(r.get("period", "")) for r in rows)
        top = max((r for r in rows if str(r.get("period", "")) == lp), key=lambda r: r[mid])
        head = f"{top[key]} leads at {fmt(top[mid])} in {_period_text(lp, grain)}"
        return head, sub + ("  ·  darker = higher" if form == "heatmap" else "")
    if form == "target":
        below = [r for r in rows if r[mid] < 100]
        head = f"{len(below)} of {len(rows)} below the 100% target" if below else f"Every {dim_name.lower()} meets the target"
        return head, sub
    if form == "dots":
        best, worst = max(rows, key=lambda r: r[mid]), min(rows, key=lambda r: r[mid])
        return f"{best[key]} grew fastest ({fmt(best[mid])}), {worst[key]} slowest ({fmt(worst[mid])})", sub
    top = max(rows, key=lambda r: r[mid])
    return f"{top[key]} leads with {fmt(top[mid])}", sub


HERO_FORMS = ("area", "spotlight", "heatmap", "columns")


@dataclass
class DesignPlan:
    """Every design decision for one dashboard, shared by the Tableau workbook and the app preview."""

    recipe: str
    theme: Theme
    span: str
    forms: dict[str, str]  # viz id -> chart form (charts only)
    headlines: dict[str, tuple[str, str]]  # viz id -> (headline, subtitle) for charts; (short label, "") for KPI cards
    hero: str | None  # viz id of the hero chart

    def as_dict(self) -> dict:
        t = self.theme
        return {
            "recipe": self.recipe, "span": self.span, "hero": self.hero,
            "theme": {"name": t.name, "page": t.page, "card": t.card, "ink": t.ink, "ink_2": t.ink_2, "muted": t.muted,
                      "accent": t.accent, "accent_soft": t.accent_soft, "highlight": t.highlight, "other": t.other,
                      "good": t.good, "bad": t.bad, "band": t.band, "band_ink": t.band_ink, "band_muted": t.band_muted,
                      "series": list(t.series), "ramp": list(t.ramp())},
            "vizzes": {vid: {"form": self.forms.get(vid, "kpi_card"), "headline": h, "subtitle": s} for vid, (h, s) in self.headlines.items()},
        }


def design_plan(spec: DashboardSpec, results: list[VizResult], catalog: Catalog) -> DesignPlan:
    ds = catalog.dataset(spec.dataset_id)
    kpis = [r for r in results if r.viz_type == "kpi_card"]
    charts = [r for r in results if r.viz_type != "kpi_card"]
    has_trend = any(ds.date_field in v.dimension_ids for v in spec.visualizations if v.type != "kpi_card")
    seed = f"{spec.dataset_id}|{spec.title}|{','.join(v.id for v in spec.visualizations)}"
    recipe, theme = choose_style(spec.recipe, spec.theme, len(kpis), len(charts), has_trend, seed)
    tr = spec.time_range
    span = f"{_period_text(tr.start.isoformat(), tr.grain)} – {_period_text(tr.end.isoformat(), tr.grain)}"
    hl_viz = next((v for v in spec.visualizations if v.highlight_top_n), None)
    forms: dict[str, str] = {}
    headlines: dict[str, tuple[str, str]] = {r.viz_id: (_short_title(r.title), "") for r in kpis}
    for res in charts:
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        cats = [d for d in viz.dimension_ids if d != ds.date_field]
        metric = catalog.metric(viz.metric_ids[0])
        n_series = len({str(r.get(cats[0])) for r in res.rows}) if cats else 0
        form = chart_form(res.viz_type, metric, len(cats), ds.date_field in viz.dimension_ids, n_series, viz is hl_viz)
        forms[viz.id] = form
        headlines[viz.id] = chart_headline(form, res, metric, ds.field(cats[0]).name if cats else "", span, tr.grain)
    hero = next((r.viz_id for r in charts if forms[r.viz_id] in HERO_FORMS), charts[0].viz_id if charts else None)
    return DesignPlan(recipe, theme, span, forms, headlines, hero)


@dataclass
class WorkbookArtifact:
    name: str
    filename: str
    content: bytes
    sha256: str
    sheet_names: dict[str, str]  # viz_id -> sheet name
    dashboard_name: str
    csv_rows: list[dict]  # validated results in long form (what the preview shows)
    kpi_expected: dict[str, float] = field(default_factory=dict)  # KPI sheet -> value as Tableau displays it


def format_value(value: float | None, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "IDR":
        for div, suffix in ((1e12, " T"), (1e9, " M"), (1e6, " Jt")):
            if abs(value) >= div:
                return f"Rp {value / div:,.2f}{suffix}"
        return f"Rp {value:,.0f}"
    if unit == "%":
        return f"{value:,.2f}%"
    return f"{value:,.0f}"


def _period_text(iso: str, grain: str) -> str:
    d = date.fromisoformat(iso)
    return f"Q{(d.month - 1) // 3 + 1} {d.year}" if grain == "quarter" else d.strftime("%b %Y")


def long_rows(spec: DashboardSpec, results: list[VizResult], catalog: Catalog) -> list[dict]:
    """Validated results flattened to one row per (viz, metric, period/category, series)."""
    ds = catalog.dataset(spec.dataset_id)
    out: list[dict] = []
    for res in results:
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        cats = [d for d in viz.dimension_ids if d != ds.date_field]
        for row in res.rows:
            for mid in viz.metric_ids:
                m = catalog.metric(mid)
                val = row.get(mid)
                rec = {
                    "viz_id": res.viz_id, "viz_title": res.title, "metric_id": mid, "metric_name": m.name, "unit": m.unit,
                    "period": row.get("period", ""), "category": "", "series": "", "highlight": "",
                    "value": "" if val is None else repr(float(val)), "value_label": format_value(val, m.unit),
                }
                if res.viz_type == "line":
                    rec["series"] = row.get(cats[0], "") if cats else ""
                elif res.viz_type in ("bar", "table"):
                    rec["category"] = str(row.get(cats[0], "")) if cats else (_period_text(row["period"], spec.time_range.grain) if row.get("period") else "")
                    if len(cats) > 1:
                        rec["series"] = str(row.get(cats[1], ""))
                    if "highlight" in row:
                        rec["highlight"] = f"Top {viz.highlight_top_n}" if row["highlight"] else "Other"
                out.append(rec)
    return out


@dataclass
class MetricCalc:
    """How one approved KPI is calculated and displayed in Tableau."""

    metric: Metric
    name: str  # calculated field name, e.g. Calculation_m0
    caption: str
    formula: str
    fmt: str
    divisor: float  # app value / divisor = number in display units (Rp T/M, or 0-100 for percent)
    prefix: str = ""
    suffix: str = ""


def metric_calcs(spec: DashboardSpec, catalog: Catalog, comp_rows: list[dict]) -> dict[str, MetricCalc]:
    out: dict[str, MetricCalc] = {}
    for i, ref in enumerate(spec.metrics):
        m = catalog.metric(ref.metric_id)
        a = f'SUM(IF [metric_id] = "{m.id}" THEN [a] END)'
        b = f'SUM(IF [metric_id] = "{m.id}" THEN [b] END)'
        if m.formula.type == "sum":
            if m.unit == "IDR":
                total = sum(r["a"] or 0 for r in comp_rows if r["metric_id"] == m.id and r["scope"] == "range")
                div, cap, suffix = (1e12, "Rp triliun", " T") if total >= 1e12 else (1e9, "Rp miliar", " M")
                out[m.id] = MetricCalc(m, f"Calculation_m{i}", f"{m.name} ({cap})", f"{a} / {int(div)}", "n#,##0.00", div, "Rp ", suffix)
            else:
                out[m.id] = MetricCalc(m, f"Calculation_m{i}", m.name, a, "n#,##0", 1.0)
        elif m.formula.type == "ratio":
            factor = m.formula.scale / 100
            formula = f"{a} / {b}" if factor == 1 else f"{a} / {b} * {factor}"
            out[m.id] = MetricCalc(m, f"Calculation_m{i}", m.name, formula, "p0.00%", 1.0)
        else:
            out[m.id] = MetricCalc(m, f"Calculation_m{i}", m.name, f"{a} / {b} - 1", "p0.00%", 1.0)
    return out


def _sheet_name(title: str, used: set[str]) -> str:
    base = re.sub(r"[^\w\s().%&,-]", "", title).strip()[:90] or "Sheet"
    name, i = base, 2
    while name.lower() in used:
        name = f"{base} {i}"
        i += 1
    used.add(name.lower())
    return name


def _uuid(seed: str) -> str:
    return "{" + str(uuid.UUID(hashlib.sha256(seed.encode()).hexdigest()[:32])).upper() + "}"


def _sub(parent: ET.Element, tag: str, attrib: dict | None = None, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def _member_filter(view: ET.Element, column: str, level: str, members: list[str]) -> None:
    flt = _sub(view, "filter", {"class": "categorical", "column": column})
    ui = {f"{{{USER_NS}}}ui-domain": "database", f"{{{USER_NS}}}ui-enumeration": "inclusive", f"{{{USER_NS}}}ui-marker": "enumerate"}
    if len(members) == 1:
        _sub(flt, "groupfilter", {"function": "member", "level": level, "member": members[0]} | ui)
        return
    union = _sub(flt, "groupfilter", {"function": "union"} | ui)
    for m in members:
        _sub(union, "groupfilter", {"function": "member", "level": level, "member": m})


def build_twb(
    spec: DashboardSpec,
    results: list[VizResult],
    catalog: Catalog,
    dims: list[str],
    comp_rows: list[dict],
    *,
    seed: str,
    footer: str,
    findings: list[str] | None = None,
) -> tuple[bytes, dict[str, str], str, dict[str, float]]:
    h = hashlib.sha256(seed.encode()).hexdigest()
    DS = f"federated.{h[:28]}"
    CONN = f"textscan.{h[12:40]}"
    q = lambda inst: f"[{DS}].[{inst}]"  # noqa: E731
    ds_meta = catalog.dataset(spec.dataset_id)
    calcs = metric_calcs(spec, catalog, comp_rows)
    columns = csv_columns(dims)
    params = [(f"Parameter {i + 1}", d, ds_meta.field(d)) for i, d in enumerate(dims) if ds_meta.field(d).values]

    wb = ET.Element("workbook", {"original-version": "18.1", "source-platform": "win", "version": "18.1"})
    _sub(wb, "preferences")
    kpis = [r for r in results if r.viz_type == "kpi_card"]
    charts = [r for r in results if r.viz_type != "kpi_card"]
    plan = design_plan(spec, results, catalog)  # the same decisions the app preview shows
    recipe, th, span = plan.recipe, plan.theme, plan.span
    wstyle = _sub(wb, "style")
    for el, fmts in (("all", {"font-family": th.font, "color": th.ink_2}), ("worksheet", {"font-size": "9"}),
                     ("title", {"font-family": th.font_strong, "font-size": "11", "color": th.ink}), ("tooltip", {"font-size": "9"})):
        rule = _sub(wstyle, "style-rule", {"element": el})
        for k_, v_ in fmts.items():
            _sub(rule, "format", {"attr": k_, "value": v_})
    dss = _sub(wb, "datasources")

    # Parameters drive the dashboard filters: one single-select list per dimension, "All" by default.
    pds = _sub(dss, "datasource", {"hasconnection": "false", "inline": "true", "name": "Parameters", "version": "18.1"})
    _sub(pds, "aliases", {"enabled": "yes"})

    def param_column(parent: ET.Element, pname: str, fld, with_members: bool) -> None:
        col = _sub(parent, "column", {"caption": fld.name, "datatype": "string", "name": f"[{pname}]", "param-domain-type": "list",
                                      "role": "measure", "type": "nominal", "value": '"All"'})
        _sub(col, "calculation", {"class": "tableau", "formula": '"All"'})
        if with_members:
            mem = _sub(col, "members")
            for v in ["All", *fld.values]:
                _sub(mem, "member", {"value": f'"{v}"'})

    for pname, _d, fld in params:
        param_column(pds, pname, fld, True)

    ds_el = _sub(dss, "datasource", {"caption": "PoC KPI parts (Synthetic)", "inline": "true", "name": DS, "version": "18.1"})
    conn = _sub(ds_el, "connection", {"class": "federated"})
    named = _sub(_sub(conn, "named-connections"), "named-connection", {"caption": "kpi_parts", "name": CONN})
    _sub(named, "connection", {"class": "textscan", "directory": "Data/results", "filename": "kpi_parts.csv", "password": "", "server": ""})
    rel = _sub(conn, "relation", {"connection": CONN, "name": "kpi_parts.csv", "table": "[kpi_parts#csv]", "type": "table"})
    cols = _sub(rel, "columns", {"character-set": "UTF-8", "header": "yes", "locale": "en_US", "separator": ","})
    for i, (name, typ) in enumerate(columns):
        _sub(cols, "column", {"datatype": typ, "name": name, "ordinal": str(i)})
    recs = _sub(conn, "metadata-records")
    for i, (name, typ) in enumerate(columns):
        r = _sub(recs, "metadata-record", {"class": "column"})
        _sub(r, "remote-name", text=name)
        _sub(r, "remote-type", text=REMOTE_TYPES[typ])
        _sub(r, "local-name", text=f"[{name}]")
        _sub(r, "parent-name", text="[kpi_parts.csv]")
        _sub(r, "remote-alias", text=name)
        _sub(r, "ordinal", text=str(i))
        _sub(r, "local-type", text=typ)
        _sub(r, "aggregation", text="Sum" if typ == "real" else "Year" if typ == "date" else "Count")
        _sub(r, "contains-null", text="true")
    _sub(ds_el, "aliases", {"enabled": "yes"})
    captions = {"period": "Period", "scope": "Scope", "metric_id": "KPI", "highlight": "Highlight", "a": "Part A", "b": "Part B"}
    captions |= {d: ds_meta.field(d).name for d in dims}

    def physical(parent: ET.Element, name: str, typ: str) -> None:
        role = "measure" if typ == "real" else "dimension"
        kind = "quantitative" if typ == "real" else "ordinal" if typ == "date" else "nominal"
        _sub(parent, "column", {"caption": captions.get(name, name), "datatype": typ, "name": f"[{name}]", "role": role, "type": kind})

    filter_formula = " AND ".join(f'([Parameters].[{p}] = "All" OR [{d}] = [Parameters].[{p}])' for p, d, _ in params) or "TRUE"

    def calc_column(parent: ET.Element, c: MetricCalc) -> None:
        col = _sub(parent, "column", {"caption": c.caption, "datatype": "real", "default-format": c.fmt, "name": f"[{c.name}]",
                                      "role": "measure", "type": "quantitative"})
        _sub(col, "calculation", {"class": "tableau", "formula": c.formula})

    def ok_column(parent: ET.Element) -> None:
        col = _sub(parent, "column", {"caption": "Matches dashboard filters", "datatype": "boolean", "name": "[Calculation_ok]",
                                      "role": "dimension", "type": "nominal"})
        _sub(col, "calculation", {"class": "tableau", "formula": filter_formula})

    # --- KPI-card calculations: value over the whole range, change vs the previous period -----
    periods = sorted({str(r["period"]) for r in comp_rows if r["scope"] == "period" and r["period"]})
    last_p, prev_p = (periods[-1], periods[-2]) if len(periods) >= 2 else (None, None)
    grain = spec.time_range.grain
    kpi_calcs: dict[str, dict[str, str]] = {}
    extra: list[tuple[str, str, str, str]] = []  # name, caption, formula, default format
    kpi_metric_ids = {v.metric_ids[0] for v in spec.visualizations if v.type == "kpi_card"}
    for i, (mid, mc) in enumerate(calcs.items()):
        if mid not in kpi_metric_ids:
            continue
        k = {"value": f"Calculation_k{i}"}
        extra.append((k["value"], mc.caption, mc.formula.replace("IF [metric_id] =", 'IF [scope] = "range" AND [metric_id] ='), mc.fmt))
        m = mc.metric
        if last_p and m.formula.type in ("sum", "ratio"):
            def part(p: str, col: str, mid: str = mid) -> str:
                return f'SUM(IF [scope] = "period" AND [period] = #{p}# AND [metric_id] = "{mid}" THEN [{col}] END)'
            if m.formula.type == "sum":
                d = f"({part(last_p, 'a')} / {part(prev_p, 'a')} - 1)"
                k |= {"fmt_up": '*"▲ "0.0%', "fmt_dn": '*"▼ "0.0%;"▼ "0.0%'}
            else:  # change in percentage points
                d = f"(({part(last_p, 'a')} / {part(last_p, 'b')} - {part(prev_p, 'a')} / {part(prev_p, 'b')}) * {m.formula.scale:g})"
                k |= {"fmt_up": '*"▲ "0.00" pp"', "fmt_dn": '*"▼ "0.00" pp";"▼ "0.00" pp"'}
            k |= {"up": f"Calculation_u{i}", "down": f"Calculation_w{i}",
                  "vs": f"  {_period_text(last_p, grain)} vs {_period_text(prev_p, grain)}", "good_up": "1" if m.higher_is_better else ""}
            extra += [(k["up"], f"{m.name} - increase", f"IF {d} >= 0 THEN {d} END", "n#,##0.00"),
                      (k["down"], f"{m.name} - decrease", f"IF {d} < 0 THEN {d} END", "n#,##0.00")]
        kpi_calcs[mid] = k

    def extra_column(parent: ET.Element, name: str, caption: str, formula: str, fmt: str) -> None:
        col = _sub(parent, "column", {"caption": caption, "datatype": "real", "default-format": fmt, "name": f"[{name}]",
                                      "role": "measure", "type": "quantitative"})
        _sub(col, "calculation", {"class": "tableau", "formula": formula})

    for name, typ in columns:
        physical(ds_el, name, typ)
    ok_column(ds_el)
    for c in calcs.values():
        calc_column(ds_el, c)
    for e in extra:
        extra_column(ds_el, *e)

    # --- chart forms: every chart gets the visual form that suits its KPI and shape -----------
    # Colour-coded forms (top 3, on/below target, latest period) use a calculated category whose
    # colours are fixed on the data source, so they follow the dashboard filters.
    forms: dict[str, str] = {}
    form_calcs: dict[str, tuple[str, str, str | None]] = {}  # viz id -> (calc name, formula, table-calc direction)
    palettes: dict[str, dict[str, str]] = {}
    hl_viz = next((v for v in spec.visualizations if v.highlight_top_n), None)
    if hl_viz:
        palettes["none:highlight:nk"] = {f"Top {hl_viz.highlight_top_n}": th.highlight, "Other": th.other}
    for i, res in enumerate(charts):
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        cats = [d for d in viz.dimension_ids if d != ds_meta.date_field]
        mc = calcs[viz.metric_ids[0]]
        form = forms[viz.id] = plan.forms[viz.id]
        if form in ("spotlight", "series_bars"):
            dim = cats[0] if form == "spotlight" else cats[1]
            totals: dict[str, float] = {}
            for r in res.rows:
                if r.get(dim) is not None:
                    totals[str(r[dim])] = totals.get(str(r[dim]), 0.0) + (r.get(mc.metric.id) or 0.0)
            pal = palettes.setdefault(f"none:{dim}:nk", {})
            for j, member in enumerate(sorted(totals, key=lambda x: (-totals[x], x))):
                if form == "spotlight":  # the leader in the accent, the runner-up in the highlight, the rest grey
                    pal.setdefault(member, th.accent if j == 0 else th.highlight if j == 1 else th.other)
                else:
                    pal.setdefault(member, th.series[len(pal) % len(th.series)])
        elif form in ("ranked", "target", "columns"):
            name = f"Calculation_c{i}"
            if form == "ranked":
                formula, tc, colours = f'IF RANK({mc.formula}) <= 3 THEN "Top 3" ELSE "Others" END', "Columns", {"Top 3": th.accent, "Others": th.accent_soft}
            elif form == "target":
                formula, tc, colours = f'IF {mc.formula} >= 1 THEN "On target" ELSE "Below target" END', None, {"On target": th.accent, "Below target": th.highlight}
            else:
                formula, tc, colours = f'IF MAX([period]) = #{last_p}# THEN "Latest" ELSE "Earlier" END', None, {"Latest": th.accent, "Earlier": th.accent_soft}
            form_calcs[viz.id] = (name, formula, tc)
            palettes[f"usr:{name}:nk"] = colours

    def category_calc(parent: ET.Element, name: str, formula: str, tc: str | None) -> None:
        col = _sub(parent, "column", {"caption": name.replace("Calculation_c", "Colour group "), "datatype": "string", "name": f"[{name}]",
                                      "role": "measure", "type": "nominal"})
        calc = _sub(col, "calculation", {"class": "tableau", "formula": formula})
        if tc:
            _sub(calc, "table-calc", {"ordering-type": tc})

    for name, formula, tc in form_calcs.values():
        category_calc(ds_el, name, formula, tc)
    if palettes:
        # Tableau applies a data-source colour map only to a column instance declared on the data source.
        for fld in palettes:
            prefix, col = fld.split(":")[:2]
            _sub(ds_el, "column-instance", {"column": f"[{col}]", "derivation": "User" if prefix == "usr" else "None", "name": f"[{fld}]",
                                            "pivot": "key", "type": "nominal"})
        prule = _sub(_sub(ds_el, "style"), "style-rule", {"element": "mark"})
        for fld, mapping in palettes.items():
            enc = _sub(prule, "encoding", {"attr": "color", "field": f"[{fld}]", "type": "palette"})
            for member, colour in mapping.items():
                _sub(_sub(enc, "map", {"to": colour}), "bucket", text=f'"{member}"')

    # --- worksheets ------------------------------------------------------------------------
    used: set[str] = set()
    sheet_names: dict[str, str] = {}
    series_dims: dict[str, tuple[str, int]] = {}  # sheet -> (colour dimension, number of series)
    kpi_expected: dict[str, float] = {}
    worksheets = _sub(wb, "worksheets")
    on_band = recipe == "band"
    kpi_bg = th.band if on_band else th.card

    def new_sheet(sheet: str, title: list[tuple[dict[str, str], str]] | None = None) -> tuple[ET.Element, ET.Element, ET.Element, ET.Element]:
        ws = _sub(worksheets, "worksheet", {"name": sheet})
        if title:
            ft = _sub(_sub(_sub(ws, "layout-options"), "title"), "formatted-text")
            for attrs, text in title:
                _sub(ft, "run", attrs, text)
        table = _sub(ws, "table")
        view = _sub(table, "view")
        # The data source listed first is the worksheet's primary source; Parameters must not be first.
        vds = _sub(view, "datasources")
        _sub(vds, "datasource", {"caption": "PoC KPI parts (Synthetic)", "name": DS})
        if params:
            _sub(vds, "datasource", {"name": "Parameters"})
            pdeps = _sub(view, "datasource-dependencies", {"datasource": "Parameters"})
            for pname, _d, fld in params:
                param_column(pdeps, pname, fld, False)
        deps = _sub(view, "datasource-dependencies", {"datasource": DS})
        for name, typ in columns:
            physical(deps, name, typ)
        ok_column(deps)
        return ws, table, view, deps

    def inst(deps: ET.Element, col: str, derivation: str, iname: str, itype: str) -> str:
        _sub(deps, "column-instance", {"column": f"[{col}]", "derivation": derivation, "name": f"[{iname}]", "pivot": "key", "type": itype})
        return q(iname)

    def finish_view(view: ET.Element, filters: list[tuple[str, str, list[str]]], sort: tuple[str, str, str] | None = None) -> None:
        for column, level, members in filters:
            _member_filter(view, column, level, members)
        if sort:
            _sub(view, "computed-sort", {"column": sort[0], "direction": sort[1], "using": sort[2]})
        slices = _sub(view, "slices")
        for column, _l, _m in filters:
            _sub(slices, "column", text=column)
        _sub(view, "aggregation", {"value": "true"})

    def clean_style(table: ET.Element, bg: str, more: dict[str, list[dict[str, str]]] | None = None,
                    ramp: tuple[str, tuple[str, ...]] | None = None) -> None:
        """No gridlines, zero lines, row dividers or axis ticks; field labels hidden (as in every template)."""
        rules: dict[str, list[dict[str, str]]] = {
            "axis": [{"attr": "tick-color", "value": "#00000000"}],
            "cell": [],
            "table": [{"attr": "background-color", "value": bg}, {"attr": "show-null-value-warning", "value": "false"}],
            "worksheet": [{"attr": "display-field-labels", "scope": s, "value": "false"} for s in ("rows", "cols")],
            "gridline": [{"attr": "line-visibility", "scope": s, "value": "off"} for s in ("rows", "cols")],
            "zeroline": [{"attr": "line-visibility", "value": "off"}],
            "table-div": [{"attr": "line-visibility", "scope": s, "value": "off"} for s in ("rows", "cols")],
        }
        for el, fmts in (more or {}).items():
            rules.setdefault(el, []).extend(fmts)
        st = _sub(table, "style")
        for el, fmts in rules.items():
            if fmts:
                rule = _sub(st, "style-rule", {"element": el})
                for f in fmts:
                    _sub(rule, "format", f)
        if ramp:  # sequential colours for a measure on Colour (heatmap)
            enc = _sub(_sub(st, "style-rule", {"element": "mark"}), "encoding", {"attr": "color", "field": ramp[0], "type": "custom-interpolated"})
            pal = _sub(enc, "color-palette", {"custom": "true", "name": "", "type": "ordered-sequential"})
            for colour in ramp[1]:
                _sub(pal, "color", text=colour)

    def label_runs(pane: ET.Element, runs: list[tuple[dict[str, str], str]]) -> None:
        ft = _sub(_sub(pane, "customized-label"), "formatted-text")
        for attrs, text in runs:
            _sub(ft, "run", attrs, text)

    def field_runs(attrs: dict[str, str], field: str) -> list[tuple[dict[str, str], str]]:
        return [(attrs, "<"), (attrs, field), (attrs, ">")]

    def mark_style(pane: ET.Element, mark: dict[str, str], datalabel: dict[str, str] | None = None) -> None:
        st = _sub(pane, "style")
        if datalabel:
            r = _sub(st, "style-rule", {"element": "datalabel"})
            for k, v in datalabel.items():
                _sub(r, "format", {"attr": k, "value": v})
        r = _sub(st, "style-rule", {"element": "mark"})
        for k, v in mark.items():
            _sub(r, "format", {"attr": k, "value": v})

    time_qk = "tqr:period:qk" if grain == "quarter" else "tmn:period:qk"
    trunc = "Quarter-Trunc" if grain == "quarter" else "Month-Trunc"

    for res in results:
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        mc = calcs[viz.metric_ids[0]]
        cats = [d for d in viz.dimension_ids if d != ds_meta.date_field]
        sheet = _sheet_name(res.title, used)
        sheet_names[viz.id] = sheet

        if res.viz_type == "kpi_card":
            k = kpi_calcs[mc.metric.id]
            ws, table, view, deps = new_sheet(sheet)
            for name, caption, formula, fmt in extra:
                if name in k.values():
                    extra_column(deps, name, caption, formula, fmt)
            value = inst(deps, k["value"], "User", f"usr:{k['value']}:qk", "quantitative")
            ok = inst(deps, "Calculation_ok", "None", "none:Calculation_ok:nk", "nominal")
            up = inst(deps, k["up"], "User", f"usr:{k['up']}:qk", "quantitative") if "up" in k else None
            down = inst(deps, k["down"], "User", f"usr:{k['down']}:qk", "quantitative") if "down" in k else None
            finish_view(view, [(ok, "[none:Calculation_ok:nk]", ["true"])])
            cell = [{"attr": "text-align", "value": "left"}, {"attr": "vertical-align", "value": "top"}]
            if mc.prefix:
                cell.append({"attr": "text-format", "field": value, "value": f'*"{mc.prefix}"#,##0.00"{mc.suffix}"'})
            if up:
                cell += [{"attr": "text-format", "field": up, "value": k["fmt_up"]}, {"attr": "text-format", "field": down, "value": k["fmt_dn"]}]
            clean_style(table, kpi_bg, {"cell": cell})
            pane = _sub(_sub(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
            _sub(_sub(pane, "view"), "breakdown", {"value": "auto"})
            _sub(pane, "mark", {"class": "Text"})
            enc = _sub(pane, "encodings")
            for f in (value, up, down):
                if f:
                    _sub(enc, "text", {"column": f})
            lab = th.band_muted if on_band else th.ink_2
            ink = th.band_ink if on_band else th.ink
            good, bad = ("#8be3b0", "#ffb08a") if on_band else (th.good, th.bad)
            runs = [({"fontcolor": lab, "fontname": th.font_strong, "fontsize": "10"}, _short_title(res.title) + "\n")]
            runs += field_runs({"fontcolor": ink, "fontname": th.font_strong, "fontsize": "24"}, value)
            if up:
                up_c, down_c = (good, bad) if k["good_up"] else (bad, good)
                runs += [({"fontsize": "6"}, "Æ\n")]  # Tableau's line-break marker; a bare newline run is dropped
                runs += field_runs({"fontcolor": up_c, "fontname": th.font_strong, "fontsize": "10"}, up)
                runs += field_runs({"fontcolor": down_c, "fontname": th.font_strong, "fontsize": "10"}, down)
                runs += [({"fontcolor": lab, "fontname": th.font, "fontsize": "9"}, k["vs"])]
            else:  # a growth rate has no period-over-period change; show the span it covers
                runs += [({"fontsize": "6"}, "\u00c6\n"), ({"fontcolor": lab, "fontname": th.font, "fontsize": "9"}, span)]
            label_runs(pane, runs)
            mark_style(pane, {"mark-labels-show": "true"})
            _sub(table, "rows")
            _sub(table, "cols")
            _sub(ws, "simple-id", {"uuid": _uuid(seed + sheet)})
            kpi_value = res.rows[0].get(mc.metric.id) if res.rows else None
            if kpi_value is not None:
                kpi_expected[sheet] = kpi_value / mc.divisor

            if "up" in k:  # sparkline under the number: the same KPI per period
                tsheet = _sheet_name(f"{res.title} - trend", used)
                sheet_names[f"{viz.id}__trend"] = tsheet
                ws, table, view, deps = new_sheet(tsheet)
                # KPI minus its lowest point (a table calculation along the periods): same shape, but the
                # change fills the card instead of sitting on top of an axis that starts at zero.
                spark = f"Calculation_s{list(calcs).index(mc.metric.id)}"
                extra_column(deps, spark, f"{mc.metric.name} (trend)", f"({mc.formula}) - WINDOW_MIN({mc.formula})", mc.fmt)
                measure = inst(deps, spark, "User", f"usr:{spark}:qk", "quantitative")
                period = inst(deps, "period", trunc, time_qk, "quantitative")
                scope = inst(deps, "scope", "None", "none:scope:nk", "nominal")
                ok = inst(deps, "Calculation_ok", "None", "none:Calculation_ok:nk", "nominal")
                finish_view(view, [(scope, "[none:scope:nk]", ['"period"']), (ok, "[none:Calculation_ok:nk]", ["true"])])
                clean_style(table, kpi_bg, {"axis": [{"attr": "display", "class": "0", "field": measure, "scope": "rows", "value": "false"},
                                                     {"attr": "display", "class": "0", "field": period, "scope": "cols", "value": "false"},
                                                     {"attr": "line-visibility", "value": "off"}],
                                            "pane": [{"attr": "border-style", "scope": s_, "value": "none"} for s_ in ("rows", "cols")],
                                            "dropline": [{"attr": "line-visibility", "value": "off"}]})
                pane = _sub(_sub(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
                _sub(_sub(pane, "view"), "breakdown", {"value": "auto"})
                _sub(pane, "mark", {"class": "Line"})
                mark_style(pane, {"mark-color": th.band_muted if on_band else th.accent, "mark-markers-mode": "none"})
                _sub(table, "rows", text=measure)
                _sub(table, "cols", text=period)
                _sub(ws, "simple-id", {"uuid": _uuid(seed + tsheet)})
            continue

        form = forms[viz.id]
        dim_name = ds_meta.field(cats[0]).name if cats else ""
        head, sub = chart_headline(form, res, mc.metric, dim_name, span, grain)
        ws, table, view, deps = new_sheet(sheet, [({"fontcolor": th.ink, "fontname": th.font_strong, "fontsize": "12"}, head + "\n"),
                                                  ({"fontcolor": th.muted, "fontname": th.font, "fontsize": "9"}, sub)])
        calc_column(deps, mc)
        measure = inst(deps, mc.name, "User", f"usr:{mc.name}:qk", "quantitative")
        scope = inst(deps, "scope", "None", "none:scope:nk", "nominal")
        ok = inst(deps, "Calculation_ok", "None", "none:Calculation_ok:nk", "nominal")
        rows = cols_shelf = ""
        color = text = sort_on = None
        top_members: list[str] = []
        axis: list[dict[str, str]] = []
        more: dict[str, list[dict[str, str]]] = {}
        mark: dict[str, str] = {"mark-labels-show": "true"}
        datalabel: dict[str, str] = {"font-size": "9", "color": th.ink_2}
        label: list[tuple[dict[str, str], str]] = []
        refline: dict[str, str] | None = None
        ramp: tuple[str, ...] | None = None
        hide_measure_axis = {"attr": "display", "class": "0", "field": measure, "value": "false"}
        if viz.top_n and cats:
            top_members = sorted({r[cats[0]] for r in res.rows})
        if form_calcs.get(viz.id):
            cname = form_calcs[viz.id][0]
            category_calc(deps, *form_calcs[viz.id])
            color = inst(deps, cname, "User", f"usr:{cname}:nk", "nominal")

        if form in ("area", "spotlight"):
            period = inst(deps, "period", trunc, time_qk, "quantitative")
            rows, cols_shelf = measure, period
            axis = [{"attr": "title", "class": "0", "field": measure, "scope": "rows", "value": ""},
                    {"attr": "title", "class": "0", "field": period, "scope": "cols", "value": ""}]
            mark |= {"mark-labels-mode": "line-ends", "mark-labels-line-first": "false", "mark-labels-cull": "true", "mark-markers-mode": "none"}
            if form == "area":
                text = measure
                mark |= {"mark-color": th.accent, "mark-transparency": "170"}
                datalabel |= {"color": th.ink, "font-size": "10"}
            else:  # one line per member, labelled at its end; the leader stands out
                color = text = inst(deps, cats[0], "None", f"none:{cats[0]}:nk", "nominal")
                datalabel["color-mode"] = "match"
                mark["size"] = "1.2"
            label = field_runs({}, text)
        elif form == "heatmap":  # many members over time: one row each, colour = value
            cat = inst(deps, cats[0], "None", f"none:{cats[0]}:nk", "nominal")
            period = inst(deps, "period", "Month" if grain == "month" else "Quarter", "mn:period:ok" if grain == "month" else "qr:period:ok", "ordinal")
            rows, cols_shelf, sort_on = cat, period, cat
            color = text = measure
            ramp = th.ramp()
            datalabel = {"font-size": "8", "color": th.ink}
            more["label"] = [{"attr": "text-format", "field": period, "value": "iLLL"}] if grain == "month" else []
            more["header"] = [{"attr": "height", "field": period, "value": "22"}]
        elif form == "columns":  # one bar per period, the latest one in the accent
            period = inst(deps, "period", "Month" if grain == "month" else "Quarter", "mn:period:ok" if grain == "month" else "qr:period:ok", "ordinal")
            cols_shelf, rows = period, measure
            axis = [hide_measure_axis | {"scope": "rows"}]
            more["label"] = [{"attr": "text-format", "field": period, "value": "iLLL"}] if grain == "month" else []
        elif form == "table":
            rows = inst(deps, cats[0], "None", f"none:{cats[0]}:nk", "nominal")
            text = measure
        else:  # ranked / target / dots / highlight / series bars: one row per member, value labelled
            cat = inst(deps, cats[0], "None", f"none:{cats[0]}:nk", "nominal")
            rows, cols_shelf = cat, measure
            axis = [hide_measure_axis | {"scope": "cols"}]
            sort_on = cat if viz.sort != "none" or form in ("target", "dots") else None
            if form == "target":  # 100% target line; below-target members in the highlight colour
                refline = {"formula": "constant", "value": "1", "label": "Target 100%", "label-type": "custom"}
            elif form == "dots":  # growth as dots against the average
                refline = {"formula": "average", "label": "Average", "label-type": "custom"}
                mark |= {"mark-color": th.accent, "size": "1.8"}
            elif form == "highlight":
                color = inst(deps, "highlight", "None", "none:highlight:nk", "nominal")
            elif form == "series_bars":
                color = inst(deps, cats[1], "None", f"none:{cats[1]}:nk", "nominal")
                series_dims[sheet] = (cats[1], len({r[cats[1]] for r in res.rows}))
            if not color and form != "dots":
                mark["mark-color"] = th.accent
        if refline:
            more["refline"] = [{"attr": "stroke-color", "value": th.ink_2}, {"attr": "stroke-size", "value": "1"},
                               {"attr": "fill-above", "value": "#00000000"}, {"attr": "fill-below", "value": "#00000000"}]

        line_like = form in ("area", "spotlight", "heatmap", "columns")
        filters = [(scope, "[none:scope:nk]", ['"period"' if line_like else '"range"']), (ok, "[none:Calculation_ok:nk]", ["true"])]
        if top_members:
            filters.append((q(f"none:{cats[0]}:nk"), f"[none:{cats[0]}:nk]", [f'"{m}"' for m in top_members]))
        finish_view(view, filters, (sort_on, "ASC" if viz.sort == "asc" else "DESC", measure) if sort_on else None)
        clean_style(table, th.card, {"axis": axis, **more}, ramp=(measure, ramp) if ramp else None)
        pane = _sub(_sub(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
        _sub(_sub(pane, "view"), "breakdown", {"value": "auto"})
        _sub(pane, "mark", {"class": FORM_MARKS.get(form, "Bar")})
        if color or text:
            enc = _sub(pane, "encodings")
            if color:
                _sub(enc, "color", {"column": color})
            if text:
                _sub(enc, "text", {"column": text})
        if refline:
            _sub(pane, "reference-line", {"axis-column": measure, "enable-instant-analytics": "true", "id": "refline0", "scope": "per-table",
                                          "tooltip-type": "none", "value-column": measure, "z-order": "1"} | refline)
        if label:
            label_runs(pane, label)
        mark_style(pane, mark, datalabel)
        _sub(table, "rows", text=rows or None)
        _sub(table, "cols", text=cols_shelf or None)
        _sub(ws, "simple-id", {"uuid": _uuid(seed + sheet)})

    # --- dashboard: a one-screen summary on top, the detail below ------------------------------
    dash_name = _sheet_name(spec.title, used)
    findings = [f for f in (findings or []) if f][:5]
    W, M, G = 1600, 24, 14  # width, outer margin, gap between cards (px)
    inner = W - 2 * M
    placed: list[tuple[int, int, int, int, dict[str, str], dict[str, str] | None, list[tuple[dict[str, str], str]] | None]] = []

    def place(x: int, y: int, w: int, h: int, attrs: dict[str, str], bg: str | None = None,
              runs: list[tuple[dict[str, str], str]] | None = None, pad: tuple[int, int, int, int] = (0, 0, 0, 0)) -> None:
        style = None
        if bg or any(pad):
            style = ({"background-color": bg} if bg else {}) | {"padding-top": str(pad[0]), "padding-right": str(pad[1]),
                                                               "padding-bottom": str(pad[2]), "padding-left": str(pad[3])}
        placed.append((x, y, w, h, attrs, style, runs))

    def wanted_height(r: VizResult) -> int:
        """Rows of bars, dots or heatmap cells get enough height that labels never overlap."""
        form = forms.get(r.viz_id, "")
        if form in ("area", "spotlight", "columns") or not r.rows or not r.columns:
            return 0
        key = next((c["key"] for c in r.columns if c["type"] == "string"), r.columns[0]["key"])
        n = len({row.get(key) for row in r.rows})
        return 96 + n * (30 if form == "heatmap" else 27 if form != "table" else 22)

    def header(x: int, y: int, w: int, dark: bool) -> int:
        ink, sub = (th.band_ink, th.band_muted) if dark else (th.ink, th.ink_2)
        runs = [({"fontcolor": th.band_muted if dark else th.accent, "fontname": th.font_strong, "fontsize": "9"}, ds_meta.name.upper() + "\n"),
                ({"fontcolor": ink, "fontname": th.font_strong, "fontsize": "22"}, spec.title + "\n"),
                ({"fontcolor": sub, "fontname": th.font, "fontsize": "10"}, f"{span}   ·   "),
                ({"fontcolor": "#ffb08a" if dark else th.bad, "fontname": th.font_strong, "fontsize": "9"}, "POC / SYNTHETIC DATA - not actual bank performance")]
        h = 112 if dark else 96
        place(x, y, w, h, {"type-v2": "text"}, th.band if dark else th.page, runs, (18, M, 8, M) if dark else (6, 0, 0, 0))
        return h

    def filter_strip(x: int, y: int, w: int) -> int:
        if not params:
            return 0
        h, cw = 58, min(280, w // len(params))
        for i, (pname, _d, _f) in enumerate(params):
            place(x + i * cw, y, cw, h, {"param": f"[Parameters].[{pname}]", "type-v2": "paramctrl", "mode": "compact"},
                  th.card, pad=(8, 10, 6, 16 if i == 0 else 10))
        if w - len(params) * cw > 0:
            place(x + len(params) * cw, y, w - len(params) * cw, h, {"type-v2": "empty"}, th.card)
        return h + G

    def findings_height(w: int) -> int:
        if not findings:
            return 0
        cpl = max(28, (w - 40) // 7)
        return 56 + 20 * sum(-(-len(f) // cpl) for f in findings) + 8 * len(findings)

    def findings_card(x: int, y: int, w: int, h: int | None = None) -> int:
        if not findings:
            return 0
        runs = [({"fontcolor": th.accent, "fontname": th.font_strong, "fontsize": "9"}, "WHAT STANDS OUT\n"),
                ({"fontcolor": th.muted, "fontname": th.font, "fontsize": "8"}, "Computed with every filter on All")]
        for f in findings:
            runs.append(({"fontcolor": th.accent, "fontname": th.font_strong, "fontsize": "11"}, "\n\n—  "))
            runs.append(({"fontcolor": th.ink, "fontname": th.font, "fontsize": "10"}, f))
        hh = max(h or 0, findings_height(w))
        place(x, y, w, hh, {"type-v2": "text"}, th.card, runs, (16, 18, 12, 18))
        return hh

    def kpi_card(x: int, y: int, w: int, h: int, r: VizResult, bg: str, pad_l: int = 18) -> None:
        trend = sheet_names.get(f"{r.viz_id}__trend")
        if trend:  # number on the left, sparkline on the right
            sw = max(120, int(w * 0.38))
            place(x, y, w - sw, h, {"name": sheet_names[r.viz_id], "show-title": "false"}, bg, pad=(14, 6, 6, pad_l))
            place(x + w - sw, y, sw, h, {"name": trend, "show-title": "false"}, bg, pad=(34, 18, 18, 6))
        else:
            place(x, y, w, h, {"name": sheet_names[r.viz_id], "show-title": "false"}, bg, pad=(14, 12, 6, pad_l))

    def chart_card(x: int, y: int, w: int, h: int, r: VizResult) -> None:
        sheet = sheet_names[r.viz_id]
        dim = series_dims.get(sheet)
        if dim:
            lw = 150
            place(x, y, w - lw, h, {"name": sheet}, th.card, pad=(14, 4, 12, 16))
            place(x + w - lw, y, lw, h, {"name": sheet, "param": q(f"none:{dim[0]}:nk"), "type-v2": "color"}, th.card, pad=(52, 10, 10, 4))
        else:
            place(x, y, w, h, {"name": sheet}, th.card, pad=(14, 18, 12, 16))

    def chart_grid(x: int, y: int, w: int, items: list[VizResult], per_row: int, h: int) -> int:
        y0 = y
        for start in range(0, len(items), per_row):
            row = items[start:start + per_row]
            rh = max([h] + [wanted_height(r) for r in row])  # one height per row keeps the cards aligned
            cw = (w - G * (len(row) - 1)) // len(row)
            for col, r in enumerate(row):
                cx = x + col * (cw + G)
                chart_card(cx, y, cw if col < len(row) - 1 else x + w - cx, rh, r)
            y += rh + G
        return y - y0

    # The hero is the trend (or the first chart); everything else is detail.
    hero = next((r for r in charts if r.viz_id == plan.hero), None)
    detail = [r for r in charts if r is not hero]
    kpi_h, hero_h, side_w = 118, 420, 400
    y = 0
    if recipe != "band":
        # Tableau stretches the zones' bounding box over the whole dashboard, so outer margins only
        # survive when something spans the full width: an empty strip on top, the footer at the bottom.
        place(0, 0, W, M, {"type-v2": "empty"})
        y = M
    if recipe == "band":
        y += header(0, 0, W, True)
        n = max(1, len(kpis))
        bw = W // n
        for i, r in enumerate(kpis):
            kpi_card(i * bw, y, bw if i < n - 1 else W - i * bw, kpi_h, r, th.band, M + 4 if i == 0 else 22)
        y += (kpi_h + 10 if kpis else 0) + G
        y += filter_strip(M, y, inner)
    else:
        y += header(M, y, inner, False)
        y += filter_strip(M, y, inner)
    if recipe == "rail" and kpis:  # KPIs stacked on the left, the hero beside them, findings on the right
        col_w = 360
        for i, r in enumerate(kpis):
            kpi_card(M, y + i * (kpi_h + G), col_w, kpi_h, r, th.card)
        stack = len(kpis) * (kpi_h + G) - G
        hh = max(hero_h, stack)
        mid_w = inner - col_w - G - (side_w + G if findings else 0)
        if hero:
            chart_card(M + col_w + G, y, mid_w, max(hh, wanted_height(hero)), hero)
        if findings:
            findings_card(M + inner - side_w, y, side_w, max(hh, wanted_height(hero) if hero else 0))
        y += max(hh, wanted_height(hero) if hero else 0) + G
    else:
        if recipe != "band" and kpis:
            n = len(kpis)
            bw = (inner - G * (n - 1)) // n
            for i, r in enumerate(kpis):
                kpi_card(M + i * (bw + G), y, bw if i < n - 1 else inner - i * (bw + G), kpi_h, r, th.card)
            y += kpi_h + G
        if hero:
            hh = max(hero_h, wanted_height(hero), findings_height(side_w) if findings else 0)
            chart_card(M, y, inner - (side_w + G if findings else 0), hh, hero)
            if findings:
                findings_card(M + inner - side_w, y, side_w, hh)
            y += hh + G
        elif findings:
            y += findings_card(M, y, inner) + G
    if detail:
        dims_txt = ", ".join(sorted({ds_meta.field(d).name for r in detail for v in spec.visualizations if v.id == r.viz_id
                                     for d in v.dimension_ids if d != ds_meta.date_field})) or "period"
        place(M, y + 6, inner, 52, {"type-v2": "text"}, None,
              [({"fontcolor": th.ink, "fontname": th.font_strong, "fontsize": "15"}, "Detail\n"),
               ({"fontcolor": th.muted, "fontname": th.font, "fontsize": "9"}, f"Every KPI broken down by {dims_txt}  ·  scroll for more")])
        y += 58 + 6
        per_row = 1 if spec.layout == "detailed_grid" or len(detail) == 1 else 2
        y += chart_grid(M, y, inner, detail, per_row, 340)
    foot_h = 30
    place(0, y, W, foot_h, {"type-v2": "text"}, None, [({"fontcolor": th.muted, "fontname": th.font, "fontsize": "8"}, footer)],
          (4, M, 0, M))
    height_px = y + foot_h

    dashboards = _sub(wb, "dashboards")
    dash = _sub(dashboards, "dashboard", {"name": dash_name})
    dstyle = _sub(dash, "style")
    for el, fmts in (("table", {"background-color": th.page}),
                     ("parameter-ctrl", {"background-color": th.card}),
                     ("parameter-ctrl-title", {"font-family": th.font_strong, "font-size": "9", "color": th.ink_2})):
        rule = _sub(dstyle, "style-rule", {"element": el})
        for k_, v_ in fmts.items():
            _sub(rule, "format", {"attr": k_, "value": v_})
    text_rule = _sub(dstyle, "style-rule", {"element": "dash-text"})  # text zones read top-down, not centred
    _sub(dash, "size", {"maxheight": str(height_px), "maxwidth": str(W), "minheight": str(height_px), "minwidth": str(W), "sizing-mode": "fixed"})
    zones = _sub(dash, "zones")
    root = _sub(zones, "zone", {"h": "100000", "id": "1", "type-v2": "layout-basic", "w": "100000", "x": "0", "y": "0"})

    def ux(px: int) -> int:
        return round(px * 100000 / W)

    def uy(px: int) -> int:
        return round(px * 100000 / height_px)

    for zid, (x, y0, w, h, attrs, style, runs) in enumerate(placed, start=2):
        z = _sub(root, "zone", {k_: str(v_) for k_, v_ in attrs.items()} | {
            "h": str(uy(y0 + h) - uy(y0)), "id": str(zid), "w": str(ux(x + w) - ux(x)), "x": str(ux(x)), "y": str(uy(y0))})
        if runs:
            _sub(text_rule, "format", {"attr": "vertical-align", "id": f"dash-text_{zid}", "value": "top"})
            ft = _sub(z, "formatted-text")
            for a, t in runs:
                _sub(ft, "run", a, t)
        if style:
            zs = _sub(z, "zone-style")
            for k_, v_ in ({"border-style": "none", "border-width": "0", "margin": "0"} | style).items():
                _sub(zs, "format", {"attr": k_, "value": v_})
    _sub(dash, "simple-id", {"uuid": _uuid(seed + "dashboard")})

    # Worksheets stay visible: Tableau publishes only visible sheets as views, and publish
    # verification reads the KPI sheets back to compare their values with the preview.
    windows = _sub(wb, "windows", {"source-height": "30"})
    for sheet in sheet_names.values():
        _sub(windows, "window", {"class": "worksheet", "name": sheet})
    dw = _sub(windows, "window", {"class": "dashboard", "maximized": "true", "name": dash_name})
    vps = _sub(dw, "viewpoints")
    for vid, sheet in sheet_names.items():
        r = next((x for x in results if x.viz_id == vid), None)
        _sub(_sub(vps, "viewpoint", {"name": sheet}), "zoom", {"type": "fit-width" if r and r.viz_type == "table" else "entire-view"})
    _sub(dw, "active", {"id": "-1"})

    ET.indent(wb, space="  ")
    xml = b"<?xml version='1.0' encoding='utf-8' ?>\n" + ET.tostring(wb, encoding="utf-8", xml_declaration=False)
    return xml, sheet_names, dash_name, kpi_expected


def csv_columns(dims: list[str]) -> list[tuple[str, str]]:
    return [("scope", "string"), ("period", "date"), *[(d, "string") for d in dims], ("metric_id", "string"), ("highlight", "string"),
            ("a", "real"), ("b", "real")]


def _parts_csv(spec: DashboardSpec, results: list[VizResult], dims: list[str], comp_rows: list[dict]) -> str:
    hl_viz = next((v for v in spec.visualizations if v.highlight_top_n), None)
    top: set[str] = set()
    hl_dim = None
    if hl_viz:
        hl_dim = next(d for d in hl_viz.dimension_ids if d in dims)
        res = next(r for r in results if r.viz_id == hl_viz.id)
        top = {r[hl_dim] for r in res.rows if r.get("highlight")}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=[c for c, _ in csv_columns(dims)], lineterminator="\n")
    w.writeheader()
    for r in comp_rows:
        hl = "" if not hl_viz else (f"Top {hl_viz.highlight_top_n}" if r[hl_dim] in top else "Other")
        w.writerow({**r, "highlight": hl, "a": "" if r["a"] is None else repr(r["a"]), "b": "" if r["b"] is None else repr(r["b"])})
    return buf.getvalue()


def build_twbx(
    spec: DashboardSpec,
    results: list[VizResult],
    catalog: Catalog,
    *,
    dims: list[str],
    components: list[dict],
    draft_id: str,
    version: int,
    footer: str,
    findings: list[str] | None = None,
) -> WorkbookArtifact:
    seed = f"{draft_id}:{version}"
    twb, sheets, dash_name, kpi_expected = build_twb(spec, results, catalog, dims, components, seed=seed, footer=footer, findings=findings)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", spec.title).strip("_")[:60] or "dashboard"
    name = f"PoC - {spec.title[:120]} ({draft_id[:8]})"  # draft suffix avoids overwriting other users' workbooks
    data = _parts_csv(spec, results, dims, components).encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, payload in sorted({f"{safe}.twb": twb, "Data/results/kpi_parts.csv": data}.items()):
            info = zipfile.ZipInfo(arc, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, payload)
    content = out.getvalue()
    return WorkbookArtifact(name, f"{safe}_v{version}.twbx", content, hashlib.sha256(content).hexdigest(), sheets, dash_name,
                            long_rows(spec, results, catalog), kpi_expected)
