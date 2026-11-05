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
from .tableau_theme import choose_style

USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)
REMOTE_TYPES = {"string": "129", "real": "5", "date": "7"}
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MARKS = {"kpi_card": "Text", "line": "Line", "bar": "Bar", "table": "Text"}
MAX_END_LABELS = 6  # line charts with more series than this get a colour legend instead of end labels


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
    recipe, th = choose_style(spec.recipe, spec.theme, len(kpis), len(charts))
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

    # Fixed colours per member: top-N emphasis, and the theme's categorical palette for series
    # (largest series first, so the biggest line gets the accent colour).
    palettes: dict[str, dict[str, str]] = {}
    hl_viz = next((v for v in spec.visualizations if v.highlight_top_n), None)
    if hl_viz:
        palettes["none:highlight:nk"] = {f"Top {hl_viz.highlight_top_n}": th.highlight, "Other": th.other}
    for res in charts:
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        cats = [d for d in viz.dimension_ids if d != ds_meta.date_field]
        dim = cats[0] if res.viz_type == "line" and cats else cats[1] if res.viz_type == "bar" and len(cats) > 1 and viz is not hl_viz else None
        if not dim:
            continue
        mid = viz.metric_ids[0]
        totals: dict[str, float] = {}
        for r in res.rows:
            if r.get(dim) is not None:
                totals[str(r[dim])] = totals.get(str(r[dim]), 0.0) + (r.get(mid) or 0.0)
        pal = palettes.setdefault(f"none:{dim}:nk", {})
        for member in sorted(totals, key=lambda x: (-totals[x], x)):
            pal.setdefault(member, th.series[len(pal) % len(th.series)])
    if palettes:
        # Tableau applies a data-source colour map only to a column instance declared on the data source.
        for fld in palettes:
            col = fld.split(":")[1]
            _sub(ds_el, "column-instance", {"column": f"[{col}]", "derivation": "None", "name": f"[{fld}]", "pivot": "key", "type": "nominal"})
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

    def new_sheet(sheet: str, title: str | None = None) -> tuple[ET.Element, ET.Element, ET.Element]:
        ws = _sub(worksheets, "worksheet", {"name": sheet})
        if title:
            ft = _sub(_sub(_sub(ws, "layout-options"), "title"), "formatted-text")
            _sub(ft, "run", {"fontcolor": th.ink, "fontname": th.font_strong, "fontsize": "11"}, title)
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

    def clean_style(table: ET.Element, bg: str, more: dict[str, list[dict[str, str]]] | None = None) -> None:
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
        has_time = ds_meta.date_field in viz.dimension_ids
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
            runs = [({"fontcolor": lab, "fontname": th.font, "fontsize": "10"}, res.title + "\n")]
            runs += field_runs({"fontcolor": ink, "fontname": th.font_strong, "fontsize": "24"}, value)
            if up:
                up_c, down_c = (good, bad) if k["good_up"] else (bad, good)
                runs += [({"fontsize": "6"}, "Æ\n")]  # Tableau's line-break marker; a bare newline run is dropped
                runs += field_runs({"fontcolor": up_c, "fontname": th.font_strong, "fontsize": "10"}, up)
                runs += field_runs({"fontcolor": down_c, "fontname": th.font_strong, "fontsize": "10"}, down)
                runs += [({"fontcolor": lab, "fontname": th.font, "fontsize": "9"}, k["vs"])]
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

        ws, table, view, deps = new_sheet(sheet, res.title)
        calc_column(deps, mc)
        measure = inst(deps, mc.name, "User", f"usr:{mc.name}:qk", "quantitative")
        scope = inst(deps, "scope", "None", "none:scope:nk", "nominal")
        ok = inst(deps, "Calculation_ok", "None", "none:Calculation_ok:nk", "nominal")
        rows = cols_shelf = ""
        color = text = sort_on = None
        top_members: list[str] = []
        axis: list[dict[str, str]] = []
        mark: dict[str, str] = {}
        datalabel: dict[str, str] = {"font-size": "9"}
        label: list[tuple[dict[str, str], str]] = []

        if res.viz_type == "line":
            period = inst(deps, "period", trunc, time_qk, "quantitative")
            rows, cols_shelf = measure, period
            axis = [{"attr": "title", "class": "0", "field": measure, "scope": "rows", "value": ""},
                    {"attr": "title", "class": "0", "field": period, "scope": "cols", "value": ""}]
            mark = {"mark-labels-show": "true", "mark-labels-mode": "line-ends", "mark-labels-line-first": "false",
                    "mark-labels-cull": "true", "mark-markers-mode": "none"}
            if cats:
                color = inst(deps, cats[0], "None", f"none:{cats[0]}:nk", "nominal")
                n_series = len({r[cats[0]] for r in res.rows})
                series_dims[sheet] = (cats[0], n_series)
                if n_series <= MAX_END_LABELS:  # label each line at its end; more lines get a legend instead
                    text = color
                    datalabel["color-mode"] = "match"
                else:
                    mark["mark-labels-show"] = "false"
                if viz.top_n:
                    top_members = sorted({r[cats[0]] for r in res.rows})
            else:
                text = measure
                mark["mark-color"] = th.accent
            label = field_runs({}, text) if text else []
        elif res.viz_type == "bar":
            mark = {"mark-labels-show": "true"}
            datalabel["color"] = th.ink_2
            if has_time and not cats:
                period = inst(deps, "period", trunc.replace("-Trunc", ""), time_qk.replace("t", "", 1).replace(":qk", ":ok"), "ordinal")
                cols_shelf, rows = period, measure
                axis = [{"attr": "display", "class": "0", "field": measure, "scope": "rows", "value": "false"}]
            else:
                cat = inst(deps, cats[0], "None", f"none:{cats[0]}:nk", "nominal")
                rows, cols_shelf = cat, measure
                axis = [{"attr": "display", "class": "0", "field": measure, "scope": "cols", "value": "false"}]
                sort_on = cat if viz.sort != "none" else None
                if viz.top_n:
                    top_members = sorted({r[cats[0]] for r in res.rows})
                if viz.highlight_top_n and viz is hl_viz:
                    color = inst(deps, "highlight", "None", "none:highlight:nk", "nominal")
                elif len(cats) > 1:
                    color = inst(deps, cats[1], "None", f"none:{cats[1]}:nk", "nominal")
                    series_dims[sheet] = (cats[1], len({r[cats[1]] for r in res.rows}))
            if not color:
                mark["mark-color"] = th.accent
        else:  # table
            rows = inst(deps, cats[0], "None", f"none:{cats[0]}:nk", "nominal")
            text = measure

        line_like = res.viz_type == "line" or (has_time and not cats)
        filters = [(scope, "[none:scope:nk]", ['"period"' if line_like else '"range"']), (ok, "[none:Calculation_ok:nk]", ["true"])]
        if top_members:
            filters.append((q(f"none:{cats[0]}:nk"), f"[none:{cats[0]}:nk]", [f'"{m}"' for m in top_members]))
        finish_view(view, filters, (sort_on, "DESC" if viz.sort == "desc" else "ASC", measure) if sort_on else None)
        clean_style(table, th.card, {"axis": axis})
        pane = _sub(_sub(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
        _sub(_sub(pane, "view"), "breakdown", {"value": "auto"})
        _sub(pane, "mark", {"class": MARKS[res.viz_type]})
        if color or text:
            enc = _sub(pane, "encodings")
            if color:
                _sub(enc, "color", {"column": color})
            if text:
                _sub(enc, "text", {"column": text})
        if label:
            label_runs(pane, label)
        if mark:
            mark_style(pane, mark, datalabel)
        _sub(table, "rows", text=rows or None)
        _sub(table, "cols", text=cols_shelf or None)
        _sub(ws, "simple-id", {"uuid": _uuid(seed + sheet)})

    # --- dashboard -------------------------------------------------------------------------
    dash_name = _sheet_name(spec.title, used)
    findings = [f for f in (findings or []) if f][:5]
    W, M, G = 1400, 20, 12  # width, outer margin, gap between cards (px)
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
        """Bars and tables grow with their row count so labels never overlap."""
        if r.viz_type == "line" or not r.rows or not r.columns:
            return 0
        n = len({row.get(r.columns[0]["key"]) for row in r.rows})
        return 70 + n * (26 if r.viz_type == "bar" else 22)

    def header(x: int, y: int, w: int, dark: bool) -> int:
        ink, sub = (th.band_ink, th.band_muted) if dark else (th.ink, th.ink_2)
        runs = [({"fontcolor": ink, "fontname": th.font_strong, "fontsize": "20"}, spec.title)]
        if spec.description:
            runs.append(({"fontcolor": sub, "fontname": th.font, "fontsize": "10"}, "\n" + spec.description))
        runs.append(({"fontcolor": "#ffb08a" if dark else th.bad, "fontname": th.font_strong, "fontsize": "9"},
                     "\nPOC / SYNTHETIC DATA - not actual bank performance"))
        h = 118 if dark else 100
        place(x, y, w, h, {"type-v2": "text"}, th.band if dark else th.page, runs, (16, M, 8, M) if dark else (6, 0, 0, 0))
        return h

    def filter_strip(x: int, y: int, w: int) -> int:
        if not params:
            return 0
        h, cw = 56, min(260, w // len(params))
        for i, (pname, _d, _f) in enumerate(params):
            place(x + i * cw, y, cw, h, {"param": f"[Parameters].[{pname}]", "type-v2": "paramctrl", "mode": "compact"},
                  th.card, pad=(6, 8, 6, 14 if i == 0 else 8))
        if w - len(params) * cw > 0:
            place(x + len(params) * cw, y, w - len(params) * cw, h, {"type-v2": "empty"}, th.card)
        return h + G

    def findings_card(x: int, y: int, w: int, h: int | None = None) -> int:
        if not findings:
            return 0
        cpl = max(30, (w - 36) // 7)
        lines = sum(-(-len(f) // cpl) for f in findings)
        need = 48 + 19 * lines + 6 * len(findings)
        runs = [({"fontcolor": th.ink, "fontname": th.font_strong, "fontsize": "11"}, "Key findings")]
        runs.append(({"fontcolor": th.muted, "fontname": th.font, "fontsize": "9"}, "  all filters on All"))
        for f in findings:
            runs.append(({"fontcolor": th.accent, "fontname": th.font_strong, "fontsize": "10"}, "\n● "))
            runs.append(({"fontcolor": th.ink_2, "fontname": th.font, "fontsize": "10"}, f))
        hh = max(h or 0, need)
        place(x, y, w, hh, {"type-v2": "text"}, th.card, runs, (14, 16, 12, 16))
        return hh

    def findings_height(w: int) -> int:
        if not findings:
            return 0
        cpl = max(30, (w - 36) // 7)
        return 48 + 19 * sum(-(-len(f) // cpl) for f in findings) + 6 * len(findings)

    def kpi_card(x: int, y: int, w: int, h: int, r: VizResult, bg: str, pad_l: int = 18) -> None:
        trend = sheet_names.get(f"{r.viz_id}__trend")
        th_h = 44 if trend else 0
        place(x, y, w, h - th_h, {"name": sheet_names[r.viz_id], "show-title": "false"}, bg, pad=(14, 12, 0, pad_l))
        if trend:
            place(x, y + h - th_h, w, th_h, {"name": trend, "show-title": "false"}, bg, pad=(0, 18, 10, pad_l))

    def chart_card(x: int, y: int, w: int, h: int, r: VizResult) -> None:
        sheet = sheet_names[r.viz_id]
        dim = series_dims.get(sheet)
        if dim and (r.viz_type != "line" or dim[1] > MAX_END_LABELS):  # few lines are labelled at their ends instead
            lw = 150
            place(x, y, w - lw, h, {"name": sheet}, th.card, pad=(12, 4, 10, 14))
            place(x + w - lw, y, lw, h, {"name": sheet, "param": q(f"none:{dim[0]}:nk"), "type-v2": "color"}, th.card, pad=(40, 10, 10, 4))
        else:
            place(x, y, w, h, {"name": sheet}, th.card, pad=(12, 16, 10, 14))

    def chart_grid(x: int, y: int, w: int, per_row: int, h: int) -> int:
        if not charts:
            return 0
        y0 = y
        for start in range(0, len(charts), per_row):
            row = charts[start:start + per_row]
            rh = max([h] + [wanted_height(r) for r in row])  # one height per row keeps the cards aligned
            cw = (w - G * (len(row) - 1)) // len(row)
            for col, r in enumerate(row):
                cx = x + col * (cw + G)
                chart_card(cx, y, cw if col < len(row) - 1 else x + w - cx, rh, r)
            y += rh + G
        return y - y0

    grid_cols = 1 if spec.layout == "detailed_grid" or len(charts) == 1 else 2
    kpi_h = 150
    y = 0
    if recipe != "band":
        # Tableau stretches the zones' bounding box over the whole dashboard, so outer margins only
        # survive when something spans the full width: an empty strip on top, the footer at the bottom.
        place(0, 0, W, M, {"type-v2": "empty"})
    if recipe == "band":
        y += header(0, 0, W, True)
        n = max(1, len(kpis))
        bw = W // n
        for i, r in enumerate(kpis):
            kpi_card(i * bw, y, bw if i < n - 1 else W - i * bw, kpi_h - 10, r, th.band, M + 4 if i == 0 else 18)
        y += kpi_h - 10 + G + 4
        y += filter_strip(M, y, inner)
        if findings:
            y += findings_card(M, y, inner) + G
        y += chart_grid(M, y, inner, grid_cols, 330)
    elif recipe == "rail":
        y = M
        y += header(M, y, inner, False)
        y += filter_strip(M, y, inner)
        rail_w = 320
        ry = y
        for r in kpis:
            kpi_card(M, ry, rail_w, kpi_h, r, th.card)
            ry += kpi_h + G
        if findings:
            ry += findings_card(M, ry, rail_w) + G
        cx, cw = M + rail_w + G, inner - rail_w - G
        per_row = 1 if spec.layout == "detailed_grid" or len(charts) <= 2 else 2
        ch = max(300, (ry - y - G * (-(-len(charts) // per_row) - 1)) // max(1, -(-len(charts) // per_row))) if charts else 0
        cy = y + chart_grid(cx, y, cw, per_row, ch)
        y = max(ry, cy)
    else:  # executive
        y = M
        y += header(M, y, inner, False)
        y += filter_strip(M, y, inner)
        side = bool(findings) and len(charts) == 1
        if kpis:
            n = len(kpis)
            bw = 280 if findings and not side else (inner - G * (n - 1)) // n
            row_h = max(kpi_h, findings_height(inner - n * (bw + G))) if findings and not side else kpi_h
            for i, r in enumerate(kpis):
                kpi_card(M + i * (bw + G), y, bw, row_h, r, th.card)
            if findings and not side:
                findings_card(M + n * (bw + G), y, inner - n * (bw + G), row_h)
            y += row_h + G
        elif findings and not side:
            y += findings_card(M, y, inner) + G
        if side:
            fw, sh = 380, max(440, wanted_height(charts[0]), findings_height(380))
            chart_card(M, y, inner - fw - G, sh, charts[0])
            findings_card(M + inner - fw, y, fw, sh)
            y += sh + G
        else:
            y += chart_grid(M, y, inner, grid_cols, 340 if grid_cols == 2 else 380)
    foot_h = 26 + M // 2
    place(0, y, W, foot_h, {"type-v2": "text"}, None, [({"fontcolor": th.muted, "fontname": th.font, "fontsize": "8"}, footer)],
          (2, M, 0, M))
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
