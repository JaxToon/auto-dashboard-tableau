"""Deterministic key findings computed only from validated results (no model, no invented numbers)."""

from __future__ import annotations

from datetime import date

from .catalog import Catalog
from .data_provider import VizResult
from .schema import DashboardSpec

MAX_FINDINGS = 5


def _fmt(value: float, unit: str) -> str:
    from .tableau_artifact import format_value  # local import avoids a cycle

    return format_value(value, unit)


def _month(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%b %Y")


def key_findings(spec: DashboardSpec, results: list[VizResult], catalog: Catalog) -> list[str]:
    ds = catalog.dataset(spec.dataset_id)
    out: list[str] = []
    for res in results:
        viz = next((v for v in spec.visualizations if v.id == res.viz_id), None)
        if viz is None or res.viz_type == "kpi_card" or not res.rows:
            continue
        m = catalog.metric(viz.metric_ids[0])
        cats = [d for d in viz.dimension_ids if d != ds.date_field]
        has_time = ds.date_field in viz.dimension_ids
        dim_name = ds.field(cats[0]).name.lower() if cats else ""

        if res.viz_type == "bar" and len(cats) == 1 and not has_time:
            vals = [(r[cats[0]], r[m.id]) for r in res.rows if r.get(m.id) is not None]
            if len(vals) < 2:
                continue
            vals.sort(key=lambda kv: kv[1], reverse=True)
            (top, tv), (low, lv) = vals[0], vals[-1]
            if m.formula.type == "ratio":
                below = [k for k, v in vals if v < 100]
                if below:
                    names = ", ".join(below[:4]) + (f" and {len(below) - 4} more" if len(below) > 4 else "")
                    out.append(f"{len(below)} of {len(vals)} {dim_name} values are below 100% on {m.name}: {names}. Highest is {top} ({_fmt(tv, m.unit)}).")
                else:
                    out.append(f"Every {dim_name} is at or above 100% on {m.name}; {top} leads with {_fmt(tv, m.unit)}.")
            elif m.formula.type == "growth":
                out.append(f"{m.name}: {top} grew fastest ({_fmt(tv, m.unit)}) and {low} slowest ({_fmt(lv, m.unit)}).")
            else:
                total = sum(v for _, v in vals)
                share = f", {tv / total:.0%} of the total" if total and not viz.top_n else ""
                out.append(f"{top} has the highest {m.name} ({_fmt(tv, m.unit)}{share}); {low} is lowest ({_fmt(lv, m.unit)}).")

        elif res.viz_type == "line" and has_time:
            series_key = cats[0] if cats else None
            groups: dict[str, list[tuple[str, float]]] = {}
            for r in res.rows:
                if r.get(m.id) is None:
                    continue
                groups.setdefault(r[series_key] if series_key else "", []).append((r["period"], r[m.id]))
            if not groups:
                continue
            if m.formula.type == "growth":
                latest = {k: sorted(v)[-1] for k, v in groups.items()}
                if series_key and len(latest) > 1:
                    best = max(latest.items(), key=lambda kv: kv[1][1])
                    out.append(f"In {_month(best[1][0])}, {best[0]} had the strongest {m.name} ({_fmt(best[1][1], m.unit)}).")
                else:
                    pts = sorted(next(iter(groups.values())))
                    avg = sum(v for _, v in pts) / len(pts)
                    out.append(f"{m.name} averaged {_fmt(avg, m.unit)} per period; latest {_month(pts[-1][0])} was {_fmt(pts[-1][1], m.unit)}.")
                continue
            growth_bar = any(
                v.type == "bar" and v.dimension_ids == cats and catalog.metric(v.metric_ids[0]).formula.type == "growth"
                and catalog.metric(v.metric_ids[0]).formula.field == m.formula.field
                for v in spec.visualizations
            )
            if growth_bar:
                continue  # the growth chart already states growth with the catalog definition; avoid a second, different figure
            changes = {}
            for k, pts in groups.items():
                pts.sort()
                if len(pts) >= 2 and pts[0][1]:
                    changes[k] = (pts[0], pts[-1], (pts[-1][1] / pts[0][1] - 1) * 100)
            if not changes:
                continue
            if series_key and len(changes) > 1:
                fast = max(changes.items(), key=lambda kv: kv[1][2])
                slow = min(changes.items(), key=lambda kv: kv[1][2])
                first, last, _ = fast[1]
                out.append(f"From {_month(first[0])} to {_month(last[0])}, {fast[0]} rose most ({fast[1][2]:+.1f}%) and {slow[0]} least ({slow[1][2]:+.1f}%) on {m.name}.")
            else:
                first, last, pct = next(iter(changes.values()))
                out.append(f"{m.name} moved from {_fmt(first[1], m.unit)} in {_month(first[0])} to {_fmt(last[1], m.unit)} in {_month(last[0])} ({pct:+.1f}%).")
        if len(out) >= MAX_FINDINGS:
            break
    return list(dict.fromkeys(out))[:MAX_FINDINGS]
