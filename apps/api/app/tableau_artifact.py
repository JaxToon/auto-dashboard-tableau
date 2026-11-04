"""Deterministic Tableau workbook (.twbx) generation from a validated specification.

The planner never produces workbook XML. This module renders a fixed, tested template:
one text-file data source holding the *validated preview results* in long format, one
worksheet per visualization (filtered by viz_id) and one dashboard. Because Tableau
only sums single pre-computed rows, published values equal the in-app preview.

Identical inputs produce byte-identical output (fixed zip timestamps, sorted entries).
NOTE: the XML follows the Tableau 18.1 workbook format but must be confirmed by opening
it in Tableau Desktop / publishing to the PoC site (see docs/tableau/integration-discovery.md).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date

from .catalog import Catalog
from .data_provider import VizResult
from .schema import DashboardSpec

USER_NS = "http://www.tableausoftware.com/xml/user"
ET.register_namespace("user", USER_NS)
CSV_COLUMNS = [
    ("viz_id", "string"),
    ("viz_title", "string"),
    ("metric_id", "string"),
    ("metric_name", "string"),
    ("unit", "string"),
    ("period", "date"),
    ("category", "string"),
    ("series", "string"),
    ("highlight", "string"),
    ("value", "real"),
    ("value_label", "string"),
    ("idr_t", "real"),
    ("idr_b", "real"),
    ("pct", "real"),
    ("num", "real"),
]
MEASURE_COLS = ("idr_t", "idr_b", "pct", "num")
CAPTIONS = {
    "period": "Period", "category": "Breakdown", "series": "Series", "highlight": "Highlight", "value": "Value (raw)",
    "value_label": "Value", "idr_t": "Rupiah (triliun)", "idr_b": "Rupiah (miliar)", "pct": "Percent", "num": "Count",
}
FORMATS = {"idr_t": "n#,##0.00", "idr_b": "n#,##0.0", "pct": "p0.0%", "num": "n#,##0"}
INK, INK_2, TEAL, SIGNAL, MUTED = "#10262b", "#3d5559", "#0b6e6e", "#e8590c", "#9fb9b7"
REMOTE_TYPES = {"string": "129", "real": "5", "date": "7"}
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MARKS = {"kpi_card": "Text", "line": "Line", "bar": "Bar", "table": "Text"}


@dataclass
class WorkbookArtifact:
    name: str
    filename: str
    content: bytes
    sha256: str
    sheet_names: dict[str, str]  # viz_id -> sheet name
    dashboard_name: str
    csv_rows: list[dict]


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


def measure_column(res: VizResult, metric_id: str, unit: str) -> tuple[str, float]:
    """Display column and divisor for a metric in one visualization (one scale per chart axis)."""
    if unit == "IDR":
        peak = max((abs(r[metric_id]) for r in res.rows if r.get(metric_id) is not None), default=0)
        return ("idr_t", 1e12) if peak >= 1e12 else ("idr_b", 1e9)
    if unit == "%":
        return "pct", 100.0
    return "num", 1.0


def long_rows(spec: DashboardSpec, results: list[VizResult], catalog: Catalog) -> list[dict]:
    """Flatten validated results into one row per (viz, metric, period/category, series)."""
    ds = catalog.dataset(spec.dataset_id)
    out: list[dict] = []
    for res in results:
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        cats = [d for d in viz.dimension_ids if d != ds.date_field]
        metric_ids = viz.metric_ids
        scales = {mid: measure_column(res, mid, catalog.metric(mid).unit) for mid in metric_ids}
        for row in res.rows:
            for mid in metric_ids:
                m = catalog.metric(mid)
                val = row.get(mid)
                mcol, div = scales[mid]
                rec = {
                    "viz_id": res.viz_id,
                    "viz_title": res.title,
                    "metric_id": mid,
                    "metric_name": m.name,
                    "unit": m.unit,
                    "period": row.get("period", ""),
                    "category": "",
                    "series": "",
                    "highlight": "",
                    "value": "" if val is None else repr(float(val)),
                    "value_label": format_value(val, m.unit),
                } | {c: "" for c in MEASURE_COLS}
                if val is not None:
                    rec[mcol] = repr(round(float(val) / div, 9))
                if res.viz_type == "line":
                    rec["series"] = row.get(cats[0], "") if cats else (m.name if len(metric_ids) > 1 else "")
                elif res.viz_type in ("bar", "table"):
                    if row.get("period") and not cats:
                        rec["category"] = _period_text(row["period"], spec.time_range.grain)
                    else:
                        rec["category"] = " / ".join(str(row.get(d, "")) for d in cats[:1]) if cats else ""
                    if len(cats) > 1:
                        rec["series"] = str(row.get(cats[1], ""))
                    elif row.get("period") and cats:
                        rec["series"] = str(row.get(cats[0], ""))
                        rec["category"] = _period_text(row["period"], spec.time_range.grain)
                    elif len(metric_ids) > 1:
                        rec["series"] = m.name
                    if "highlight" in row:
                        rec["highlight"] = f"Top {viz.highlight_top_n}" if row["highlight"] else "Other"
                out.append(rec)
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


def build_twb(
    spec: DashboardSpec, results: list[VizResult], catalog: Catalog, *, seed: str, footer: str, findings: list[str] | None = None
) -> tuple[bytes, dict[str, str], str]:
    h = hashlib.sha256(seed.encode()).hexdigest()
    DS = f"federated.{h[:28]}"
    CONN = f"textscan.{h[12:40]}"
    q = lambda inst: f"[{DS}].[{inst}]"  # noqa: E731
    flat_all = long_rows(spec, results, catalog)

    wb = ET.Element("workbook", {"original-version": "18.1", "source-platform": "win", "version": "18.1"})
    _sub(wb, "preferences")
    dss = _sub(wb, "datasources")
    ds_el = _sub(dss, "datasource", {"caption": "PoC Results (Synthetic)", "inline": "true", "name": DS, "version": "18.1"})
    conn = _sub(ds_el, "connection", {"class": "federated"})
    named = _sub(_sub(conn, "named-connections"), "named-connection", {"caption": "results", "name": CONN})
    _sub(named, "connection", {"class": "textscan", "directory": "Data/results", "filename": "results.csv", "password": "", "server": ""})
    rel = _sub(conn, "relation", {"connection": CONN, "name": "results.csv", "table": "[results#csv]", "type": "table"})
    cols = _sub(rel, "columns", {"character-set": "UTF-8", "header": "yes", "locale": "en_US", "separator": ","})
    for i, (name, typ) in enumerate(CSV_COLUMNS):
        _sub(cols, "column", {"datatype": typ, "name": name, "ordinal": str(i)})
    recs = _sub(conn, "metadata-records")
    for i, (name, typ) in enumerate(CSV_COLUMNS):
        r = _sub(recs, "metadata-record", {"class": "column"})
        _sub(r, "remote-name", text=name)
        _sub(r, "remote-type", text=REMOTE_TYPES[typ])
        _sub(r, "local-name", text=f"[{name}]")
        _sub(r, "parent-name", text="[results.csv]")
        _sub(r, "remote-alias", text=name)
        _sub(r, "ordinal", text=str(i))
        _sub(r, "local-type", text=typ)
        _sub(r, "aggregation", text="Sum" if typ == "real" else "Year" if typ == "date" else "Count")
        _sub(r, "contains-null", text="true")
    _sub(ds_el, "aliases", {"enabled": "yes"})
    for name, typ in CSV_COLUMNS:
        role = "measure" if typ == "real" else "dimension"
        kind = "quantitative" if typ == "real" else "ordinal" if typ == "date" else "nominal"
        attrs = {"caption": CAPTIONS.get(name, name.replace("_", " ").title()), "datatype": typ, "name": f"[{name}]", "role": role, "type": kind}
        if name in FORMATS:
            attrs["default-format"] = FORMATS[name]
        _sub(ds_el, "column", attrs)
    highlight_labels = sorted({r["highlight"] for r in flat_all if r["highlight"]})
    if highlight_labels:
        # Fixed colours: highlighted bars in signal orange, the rest muted.
        enc = _sub(_sub(_sub(ds_el, "style"), "style-rule", {"element": "mark"}), "encoding",
                   {"attr": "color", "field": "[none:highlight:nk]", "type": "palette"})
        for label in highlight_labels:
            _sub(_sub(enc, "map", {"to": MUTED if label == "Other" else SIGNAL}), "bucket", text=f'"{label}"')

    ds_meta = catalog.dataset(spec.dataset_id)
    used: set[str] = set()
    sheet_names: dict[str, str] = {}
    series_sheets: set[str] = set()
    worksheets = _sub(wb, "worksheets")
    for res in results:
        viz = next(v for v in spec.visualizations if v.id == res.viz_id)
        metric = catalog.metric(viz.metric_ids[0])
        mcol, _ = measure_column(res, metric.id, metric.unit)
        flat = [r for r in flat_all if r["viz_id"] == res.viz_id]
        sheet = _sheet_name(res.title, used)
        sheet_names[viz.id] = sheet
        ws = _sub(worksheets, "worksheet", {"name": sheet})
        title = _sub(_sub(_sub(ws, "layout-options"), "title"), "formatted-text")
        if res.viz_type == "kpi_card":
            _sub(title, "run", {"fontcolor": INK_2, "fontsize": "10"}, res.title)
        else:
            _sub(title, "run", {"bold": "true", "fontcolor": INK, "fontsize": "11"}, res.title)
        table = _sub(ws, "table")
        view = _sub(table, "view")
        _sub(_sub(view, "datasources"), "datasource", {"caption": "PoC Results (Synthetic)", "name": DS})
        deps = _sub(view, "datasource-dependencies", {"datasource": DS})

        def dep(col: str, typ: str, derivation: str, inst: str, ityp: str, deps: ET.Element = deps) -> None:
            role = "measure" if typ == "real" else "dimension"
            kind = "quantitative" if typ == "real" else "ordinal" if typ == "date" else "nominal"
            if deps.find(f"column[@name='[{col}]']") is None:
                attrs = {"caption": CAPTIONS.get(col, col), "datatype": typ, "name": f"[{col}]", "role": role, "type": kind}
                if col in FORMATS:
                    attrs["default-format"] = FORMATS[col]
                _sub(deps, "column", attrs)
            _sub(deps, "column-instance", {"column": f"[{col}]", "derivation": derivation, "name": f"[{inst}]", "pivot": "key", "type": ityp})

        time_inst = "tqr:period:qk" if spec.time_range.grain == "quarter" else "tmn:period:qk"
        measure = q(f"sum:{mcol}:qk")
        rows, cols_shelf, color, text, sort_using, labels, hide_row_label = "", "", None, None, None, False, False
        dep("viz_id", "string", "None", "none:viz_id:nk", "nominal")
        if res.viz_type != "kpi_card":
            dep(mcol, "real", "Sum", f"sum:{mcol}:qk", "quantitative")
        if res.viz_type == "kpi_card":
            dep("value_label", "string", "None", "none:value_label:nk", "nominal")
            text = q("none:value_label:nk")
        elif res.viz_type == "line":
            dep("period", "date", "Month-Trunc" if spec.time_range.grain == "month" else "Quarter-Trunc", time_inst, "quantitative")
            rows, cols_shelf = measure, q(time_inst)
            if any(r["series"] for r in flat):
                dep("series", "string", "None", "none:series:nk", "nominal")
                color = q("none:series:nk")
                series_sheets.add(sheet)
        elif res.viz_type == "bar":
            dep("category", "string", "None", "none:category:nk", "nominal")
            labels = True
            time_first = bool(viz.dimension_ids) and viz.dimension_ids[0] == ds_meta.date_field
            if time_first:
                dep("period", "date", "Month-Trunc" if spec.time_range.grain == "month" else "Quarter-Trunc", time_inst.replace(":qk", ":ok"), "ordinal")
                cols_shelf, rows = q(time_inst.replace(":qk", ":ok")), measure
            else:
                rows, cols_shelf = q("none:category:nk"), measure
                hide_row_label = True
                if viz.sort != "none":
                    sort_using = viz.sort
            if any(r["highlight"] for r in flat):
                dep("highlight", "string", "None", "none:highlight:nk", "nominal")
                color = q("none:highlight:nk")
            elif any(r["series"] for r in flat):
                dep("series", "string", "None", "none:series:nk", "nominal")
                color = q("none:series:nk")
                series_sheets.add(sheet)
        else:  # table
            dep("category", "string", "None", "none:category:nk", "nominal")
            dep("metric_name", "string", "None", "none:metric_name:nk", "nominal")
            dep("value_label", "string", "None", "none:value_label:nk", "nominal")
            rows, cols_shelf = q("none:category:nk"), q("none:metric_name:nk")
            text = q("none:value_label:nk")
            hide_row_label = True

        flt = _sub(view, "filter", {"class": "categorical", "column": q("none:viz_id:nk")})
        _sub(flt, "groupfilter", {
            "function": "member", "level": "[none:viz_id:nk]", "member": f'"{res.viz_id}"',
            f"{{{USER_NS}}}ui-domain": "database", f"{{{USER_NS}}}ui-enumeration": "inclusive", f"{{{USER_NS}}}ui-marker": "enumerate",
        })
        if sort_using:
            _sub(view, "computed-sort", {"column": q("none:category:nk"), "direction": "DESC" if sort_using == "desc" else "ASC", "using": measure})
        _sub(_sub(view, "slices"), "column", text=q("none:viz_id:nk"))
        _sub(view, "aggregation", {"value": "true"})
        tstyle = _sub(table, "style")
        if hide_row_label:
            _sub(_sub(tstyle, "style-rule", {"element": "worksheet"}), "format", {"attr": "display-field-labels", "scope": "rows", "value": "false"})
        pane = _sub(_sub(table, "panes"), "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
        _sub(_sub(pane, "view"), "breakdown", {"value": "auto"})
        _sub(pane, "mark", {"class": MARKS[res.viz_type]})
        if color or text:
            enc = _sub(pane, "encodings")
            if color:
                _sub(enc, "color", {"column": color})
            if text:
                _sub(enc, "text", {"column": text})
        if res.viz_type == "kpi_card":
            # Big number; the small metric name and period come from the sheet title.
            cl = _sub(_sub(pane, "customized-label"), "formatted-text")
            _sub(cl, "run", {"bold": "true", "fontcolor": INK, "fontsize": "22"}, f"<{q('none:value_label:nk')}>")
        if labels or (res.viz_type == "bar" and not color):
            rule = _sub(_sub(pane, "style"), "style-rule", {"element": "mark"})
            if labels:
                _sub(rule, "format", {"attr": "mark-labels-show", "value": "true"})
            if not color:
                _sub(rule, "format", {"attr": "mark-color", "value": TEAL})
        _sub(table, "rows", text=rows or None)
        _sub(table, "cols", text=cols_shelf or None)
        _sub(ws, "simple-id", {"uuid": _uuid(seed + sheet)})

    # Dashboard layout (zone coordinates are in 1/100000 of the dashboard size).
    dash_name = _sheet_name(spec.title, used)
    kpis = [r for r in results if r.viz_type == "kpi_card"]
    charts = [r for r in results if r.viz_type != "kpi_card"]
    findings = [f for f in (findings or []) if f][:5]
    height_px = 1250 if len(charts) > 2 else 950
    dashboards = _sub(wb, "dashboards")
    dash = _sub(dashboards, "dashboard", {"name": dash_name})
    _sub(dash, "style")
    height = str(height_px)
    _sub(dash, "size", {"maxheight": height, "maxwidth": "1400", "minheight": height, "minwidth": "1400", "sizing-mode": "fixed"})
    zones = _sub(dash, "zones")
    root = _sub(zones, "zone", {"h": "100000", "id": "1", "type-v2": "layout-basic", "w": "100000", "x": "0", "y": "0"})
    zid = [1]

    def zone(attrs: dict) -> ET.Element:
        zid[0] += 1
        return _sub(root, "zone", {k: str(v) for k, v in attrs.items()} | {"id": str(zid[0])})

    unit = 24 * 100000 // height_px  # one ~10pt text line (24 px), in zone units
    head_h = 4 * unit
    head = zone({"h": head_h, "w": 100000, "x": 0, "y": 0, "type-v2": "text"})
    ft = _sub(head, "formatted-text")
    _sub(ft, "run", {"bold": "true", "fontcolor": INK, "fontsize": "18"}, spec.title)
    _sub(ft, "run", {"fontcolor": INK_2, "fontsize": "10"}, "\n" + (spec.description or ""))
    _sub(ft, "run", {"fontcolor": "#b42318", "fontsize": "9"}, "\nPOC / SYNTHETIC DATA - not actual bank performance")
    y = head_h
    if findings:
        fh = (len(findings) + 1) * unit + unit // 2
        fz = zone({"h": fh, "w": 100000, "x": 0, "y": y, "type-v2": "text"})
        fft = _sub(fz, "formatted-text")
        _sub(fft, "run", {"bold": "true", "fontcolor": INK, "fontsize": "11"}, "Key findings")
        for f in findings:
            _sub(fft, "run", {"fontcolor": INK, "fontsize": "10"}, "\n- " + f)
        y += fh
    if kpis:
        # KPI cards are text zones (small label, big number) for a predictable layout. The KPI
        # worksheets still exist as views so publish verification can read their values back.
        kh = 3 * unit
        w = 100000 // len(kpis)
        for i, r in enumerate(kpis):
            viz = next(v for v in spec.visualizations if v.id == r.viz_id)
            m = catalog.metric(viz.metric_ids[0])
            value = r.rows[0].get(m.id) if r.rows else None
            kz = zone({"h": kh, "w": w if i < len(kpis) - 1 else 100000 - w * i, "x": w * i, "y": y, "type-v2": "text"})
            kft = _sub(kz, "formatted-text")
            _sub(kft, "run", {"fontcolor": INK_2, "fontsize": "10"}, r.title)
            _sub(kft, "run", {"bold": "true", "fontcolor": INK, "fontsize": "22"}, "\n" + format_value(value, m.unit))
        y += kh
    foot_h = 3 * unit
    if charts:
        per_row = 1 if spec.layout == "detailed_grid" or len(charts) == 1 else 2
        n_rows = -(-len(charts) // per_row)
        ch = (100000 - y - foot_h) // n_rows
        for i, r in enumerate(charts):
            row, col = divmod(i, per_row)
            in_row = min(per_row, len(charts) - row * per_row)
            cw = 100000 // in_row
            x0, width = cw * col, (cw if col < in_row - 1 else 100000 - cw * col)
            sheet = sheet_names[r.viz_id]
            if sheet in series_sheets:
                lw = width // 6  # colour legend to the right of the chart
                zone({"h": ch, "w": width - lw, "x": x0, "y": y + row * ch, "name": sheet})
                zone({"h": ch, "w": lw, "x": x0 + width - lw, "y": y + row * ch, "name": sheet, "param": q("none:series:nk"), "type-v2": "color"})
            else:
                zone({"h": ch, "w": width, "x": x0, "y": y + row * ch, "name": sheet})
        y += ch * n_rows
    foot = zone({"h": 100000 - y, "w": 100000, "x": 0, "y": y, "type-v2": "text"})
    _sub(_sub(foot, "formatted-text"), "run", {"fontcolor": INK_2, "fontsize": "8"}, footer)
    _sub(dash, "simple-id", {"uuid": _uuid(seed + "dashboard")})

    # Worksheets stay visible: Tableau publishes only visible sheets as views, and publish
    # verification reads the KPI sheets back to compare their values with the preview.
    windows = _sub(wb, "windows", {"source-height": "30"})
    for sheet in sheet_names.values():
        _sub(windows, "window", {"class": "worksheet", "name": sheet})
    dw = _sub(windows, "window", {"class": "dashboard", "maximized": "true", "name": dash_name})
    vps = _sub(dw, "viewpoints")
    for r in charts:
        _sub(vps, "viewpoint", {"name": sheet_names[r.viz_id]})
    _sub(dw, "active", {"id": "-1"})

    ET.indent(wb, space="  ")
    xml = b"<?xml version='1.0' encoding='utf-8' ?>\n" + ET.tostring(wb, encoding="utf-8", xml_declaration=False)
    return xml, sheet_names, dash_name


def build_twbx(
    spec: DashboardSpec, results: list[VizResult], catalog: Catalog, *, draft_id: str, version: int, footer: str, findings: list[str] | None = None
) -> WorkbookArtifact:
    seed = f"{draft_id}:{version}"
    rows = long_rows(spec, results, catalog)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=[c for c, _ in CSV_COLUMNS], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    twb, sheets, dash_name = build_twb(spec, results, catalog, seed=seed, footer=footer, findings=findings)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", spec.title).strip("_")[:60] or "dashboard"
    name = f"PoC - {spec.title[:120]} ({draft_id[:8]})"  # draft suffix avoids overwriting other users' workbooks
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, data in sorted({f"{safe}.twb": twb, "Data/results/results.csv": buf.getvalue().encode("utf-8")}.items()):
            info = zipfile.ZipInfo(arc, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    content = out.getvalue()
    return WorkbookArtifact(name, f"{safe}_v{version}.twbx", content, hashlib.sha256(content).hexdigest(), sheets, dash_name, rows)
