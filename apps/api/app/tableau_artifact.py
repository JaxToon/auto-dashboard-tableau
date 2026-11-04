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

USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)
REMOTE_TYPES = {"string": "129", "real": "5", "date": "7"}
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MARKS = {"kpi_card": "Text", "line": "Line", "bar": "Bar", "table": "Text"}
INK, INK_2, TEAL, SIGNAL, MUTED, PANEL = "#10262b", "#3d5559", "#0b6e6e", "#e8590c", "#9fb9b7", "#f3f6f5"
PAD = 1200  # left/right dashboard margin, in zone units (1/100000 of the width)


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

    for name, typ in columns:
        physical(ds_el, name, typ)
    ok_column(ds_el)
    for c in calcs.values():
        calc_column(ds_el, c)

    # Highlight colours (fixed): top-N members in signal orange, the rest muted.
    hl_viz = next((v for v in spec.visualizations if v.highlight_top_n), None)
    if hl_viz:
        enc = _sub(_sub(_sub(ds_el, "style"), "style-rule", {"element": "mark"}), "encoding",
                   {"attr": "color", "field": "[none:highlight:nk]", "type": "palette"})
        _sub(_sub(enc, "map", {"to": SIGNAL}), "bucket", text=f'"Top {hl_viz.highlight_top_n}"')
        _sub(_sub(enc, "map", {"to": MUTED}), "bucket", text='"Other"')

    used: set[str] = set()
    sheet_names: dict[str, str] = {}
    series_dims: dict[str, str] = {}
    kpi_expected: dict[str, float] = {}
    worksheets = _sub(wb, "worksheets")
    for res in results:
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        mc = calcs[viz.metric_ids[0]]
        cats = [d for d in viz.dimension_ids if d != ds_meta.date_field]
        has_time = ds_meta.date_field in viz.dimension_ids
        sheet = _sheet_name(res.title, used)
        sheet_names[viz.id] = sheet
        ws = _sub(worksheets, "worksheet", {"name": sheet})
        title = _sub(_sub(_sub(ws, "layout-options"), "title"), "formatted-text")
        if res.viz_type == "kpi_card":
            _sub(title, "run", {"fontcolor": INK_2, "fontsize": "10"}, res.title)
        else:
            _sub(title, "run", {"bold": "true", "fontcolor": INK, "fontsize": "12"}, res.title)
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
        calc_column(deps, mc)

        def inst(col: str, derivation: str, iname: str, itype: str, deps: ET.Element = deps) -> str:
            _sub(deps, "column-instance", {"column": f"[{col}]", "derivation": derivation, "name": f"[{iname}]", "pivot": "key", "type": itype})
            return q(iname)

        measure = inst(mc.name, "User", f"usr:{mc.name}:qk", "quantitative")
        scope = inst("scope", "None", "none:scope:nk", "nominal")
        ok = inst("Calculation_ok", "None", "none:Calculation_ok:nk", "nominal")
        time_inst = "tqr:period:qk" if spec.time_range.grain == "quarter" else "tmn:period:qk"
        derivation = "Quarter-Trunc" if spec.time_range.grain == "quarter" else "Month-Trunc"
        rows = cols_shelf = ""
        color = text = sort_on = None
        labels = hide_row_label = False
        top_members: list[str] = []

        if res.viz_type == "kpi_card":
            text = measure
            kpi_value = res.rows[0].get(mc.metric.id) if res.rows else None
            if kpi_value is not None:
                kpi_expected[sheet] = kpi_value / mc.divisor
        elif res.viz_type == "line":
            rows, cols_shelf = measure, inst("period", derivation, time_inst, "quantitative")
            if cats:
                color = inst(cats[0], "None", f"none:{cats[0]}:nk", "nominal")
                series_dims[sheet] = cats[0]
                if viz.top_n:
                    top_members = sorted({r[cats[0]] for r in res.rows})
        elif res.viz_type == "bar":
            labels = True
            if has_time and not cats:
                cols_shelf, rows = inst("period", derivation.replace("-Trunc", ""), time_inst.replace("t", "", 1).replace(":qk", ":ok"), "ordinal"), measure
            else:
                cat = inst(cats[0], "None", f"none:{cats[0]}:nk", "nominal")
                rows, cols_shelf, hide_row_label = cat, measure, True
                sort_on = cat if viz.sort != "none" else None
                if viz.top_n:
                    top_members = sorted({r[cats[0]] for r in res.rows})
                if viz.highlight_top_n and viz is hl_viz:
                    color = inst("highlight", "None", "none:highlight:nk", "nominal")
                elif len(cats) > 1:
                    color = inst(cats[1], "None", f"none:{cats[1]}:nk", "nominal")
                    series_dims[sheet] = cats[1]
        else:  # table
            rows, hide_row_label = inst(cats[0], "None", f"none:{cats[0]}:nk", "nominal"), True
            text = measure

        _member_filter(view, scope, "[none:scope:nk]", ['"period"' if res.viz_type == "line" or (has_time and not cats) else '"range"'])
        _member_filter(view, ok, "[none:Calculation_ok:nk]", ["true"])
        if top_members:
            _member_filter(view, q(f"none:{cats[0]}:nk"), f"[none:{cats[0]}:nk]", [f'"{m}"' for m in top_members])
        if sort_on:
            _sub(view, "computed-sort", {"column": sort_on, "direction": "DESC" if viz.sort == "desc" else "ASC", "using": measure})
        slices = _sub(view, "slices")
        for col in [scope, ok] + ([q(f"none:{cats[0]}:nk")] if top_members else []):
            _sub(slices, "column", text=col)
        _sub(view, "aggregation", {"value": "true"})

        tstyle = _sub(table, "style")
        if hide_row_label:
            _sub(_sub(tstyle, "style-rule", {"element": "worksheet"}), "format", {"attr": "display-field-labels", "scope": "rows", "value": "false"})
        if res.viz_type == "kpi_card":
            # Big, bold number in a wide cell; Rupiah KPIs get an "Rp ... T/M" number format.
            cell = _sub(tstyle, "style-rule", {"element": "cell"})
            _sub(cell, "format", {"attr": "width", "value": "420"})
            _sub(cell, "format", {"attr": "height", "value": "46"})
            _sub(cell, "format", {"attr": "text-align", "value": "left"})
            _sub(cell, "format", {"attr": "font-size", "value": "24"})
            _sub(cell, "format", {"attr": "font-weight", "value": "bold"})
            _sub(cell, "format", {"attr": "color", "value": INK})
            if mc.prefix:
                _sub(cell, "format", {"attr": "text-format", "field": measure, "value": f'*"{mc.prefix}"#,##0.00"{mc.suffix}"'})
        pane = _sub(_sub(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
        _sub(_sub(pane, "view"), "breakdown", {"value": "auto"})
        _sub(pane, "mark", {"class": MARKS[res.viz_type]})
        if color or text:
            enc = _sub(pane, "encodings")
            if color:
                _sub(enc, "color", {"column": color})
            if text:
                _sub(enc, "text", {"column": text})
        if labels or (res.viz_type == "bar" and not color):
            rule = _sub(_sub(pane, "style"), "style-rule", {"element": "mark"})
            if labels:
                _sub(rule, "format", {"attr": "mark-labels-show", "value": "true"})
            if not color:
                _sub(rule, "format", {"attr": "mark-color", "value": TEAL})
        _sub(table, "rows", text=rows or None)
        _sub(table, "cols", text=cols_shelf or None)
        _sub(ws, "simple-id", {"uuid": _uuid(seed + sheet)})

    # --- dashboard -------------------------------------------------------------------
    dash_name = _sheet_name(spec.title, used)
    kpis = [r for r in results if r.viz_type == "kpi_card"]
    charts = [r for r in results if r.viz_type != "kpi_card"]
    findings = [f for f in (findings or []) if f][:5]
    height_px = 1200 if len(charts) > 2 else 950
    dashboards = _sub(wb, "dashboards")
    dash = _sub(dashboards, "dashboard", {"name": dash_name})
    _sub(dash, "style")
    _sub(dash, "size", {"maxheight": str(height_px), "maxwidth": "1400", "minheight": str(height_px), "minwidth": "1400", "sizing-mode": "fixed"})
    zones = _sub(dash, "zones")
    root = _sub(zones, "zone", {"h": "100000", "id": "1", "type-v2": "layout-basic", "w": "100000", "x": "0", "y": "0"})
    zid = [1]
    inner = 100000 - 2 * PAD

    def zone(attrs: dict) -> ET.Element:
        zid[0] += 1
        return _sub(root, "zone", {k: str(v) for k, v in attrs.items()} | {"id": str(zid[0])})

    def row_of(items: int, y: int, h: int) -> list[tuple[int, int]]:
        w = inner // items
        return [(PAD + w * i, w if i < items - 1 else inner - w * i) for i in range(items)]

    unit = 24 * 100000 // height_px  # one ~10pt text line (24 px), in zone units
    y = unit // 2
    head_h = 4 * unit
    ft = _sub(zone({"h": head_h, "w": inner, "x": PAD, "y": y, "type-v2": "text"}), "formatted-text")
    _sub(ft, "run", {"bold": "true", "fontcolor": INK, "fontsize": "18"}, spec.title)
    _sub(ft, "run", {"fontcolor": INK_2, "fontsize": "10"}, "\n" + (spec.description or ""))
    _sub(ft, "run", {"fontcolor": "#b42318", "fontsize": "9"}, "\nPOC / SYNTHETIC DATA - not actual bank performance")
    y += head_h
    if params:
        ph = 3 * unit
        for (x, w), (pname, _d, _f) in zip(row_of(max(len(params), 4), y, ph), params, strict=False):
            zone({"h": ph, "w": w, "x": x, "y": y, "param": f"[Parameters].[{pname}]", "type-v2": "paramctrl", "mode": "compact"})
        y += ph
    if findings:
        fh = (len(findings) + 1) * unit + unit // 2
        fft = _sub(zone({"h": fh, "w": inner, "x": PAD, "y": y, "type-v2": "text"}), "formatted-text")
        _sub(fft, "run", {"bold": "true", "fontcolor": INK, "fontsize": "11"}, "Key findings (all filters on All)")
        for f in findings:
            _sub(fft, "run", {"fontcolor": INK, "fontsize": "10"}, "\n- " + f)
        y += fh
    if kpis:
        kh = 4 * unit
        for (x, w), r in zip(row_of(len(kpis), y, kh), kpis, strict=True):
            zone({"h": kh, "w": w, "x": x, "y": y, "name": sheet_names[r.viz_id]})
        y += kh
    foot_h = 2 * unit
    if charts:
        per_row = 1 if spec.layout == "detailed_grid" or len(charts) == 1 else 2
        n_rows = -(-len(charts) // per_row)
        ch = (100000 - y - foot_h) // n_rows
        for i, r in enumerate(charts):
            row, col = divmod(i, per_row)
            in_row = min(per_row, len(charts) - row * per_row)
            x0, width = row_of(in_row, 0, 0)[col]
            sheet = sheet_names[r.viz_id]
            if sheet in series_dims:
                lw = width // 6  # colour legend to the right of the chart
                zone({"h": ch, "w": width - lw, "x": x0, "y": y + row * ch, "name": sheet})
                zone({"h": ch, "w": lw, "x": x0 + width - lw, "y": y + row * ch, "name": sheet,
                      "param": q(f"none:{series_dims[sheet]}:nk"), "type-v2": "color"})
            else:
                zone({"h": ch, "w": width, "x": x0, "y": y + row * ch, "name": sheet})
        y += ch * n_rows
    _sub(_sub(zone({"h": 100000 - y, "w": inner, "x": PAD, "y": y, "type-v2": "text"}), "formatted-text"), "run",
         {"fontcolor": INK_2, "fontsize": "8"}, footer)
    _sub(dash, "simple-id", {"uuid": _uuid(seed + "dashboard")})

    # Worksheets stay visible: Tableau publishes only visible sheets as views, and publish
    # verification reads the KPI sheets back to compare their values with the preview.
    windows = _sub(wb, "windows", {"source-height": "30"})
    for sheet in sheet_names.values():
        _sub(windows, "window", {"class": "worksheet", "name": sheet})
    dw = _sub(windows, "window", {"class": "dashboard", "maximized": "true", "name": dash_name})
    vps = _sub(dw, "viewpoints")
    for sheet in sheet_names.values():
        _sub(vps, "viewpoint", {"name": sheet})
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
