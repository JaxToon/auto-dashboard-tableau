"use client";

import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axisValue, formatPeriod, formatValue, unitLabel } from "@/lib/format";

export type Column = { key: string; label: string; type: "number" | "string" | "date" | "boolean"; unit?: string; grain?: string; metric_id?: string };
export type VizResult = { viz_id: string; type: "kpi_card" | "line" | "bar" | "table"; title: string; columns: Column[]; rows: Record<string, any>[]; warnings: string[] };

const SERIES = ["#0b6e6e", "#e8590c", "#3b5ba5", "#8c6d1f", "#7a3e9d", "#2f8f5b", "#b23a48", "#4a7f8c", "#c27c0e", "#5c6b73", "#9c4f2e", "#2e4a62"];

function metricCols(r: VizResult) {
  return r.columns.filter((c) => c.type === "number");
}

export function KpiStrip({ results }: { results: VizResult[] }) {
  if (!results.length) return null;
  return (
    <div
      className="grid grid-cols-1 sm:[grid-template-columns:var(--kpi-cols)] border-y border-line divide-y sm:divide-y-0 sm:divide-x divide-line"
      style={{ "--kpi-cols": `repeat(${results.length}, minmax(0, 1fr))` } as React.CSSProperties}
    >
      {results.map((r) => {
        const m = metricCols(r)[0];
        const v = r.rows[0]?.[m.key];
        return (
          <div key={r.viz_id} className="py-4 sm:px-5 first:sm:pl-0">
            <div className="text-sm text-ink-2 leading-snug">{r.title}</div>
            <div className="display num mt-1 text-[28px] font-bold text-ink whitespace-nowrap">{formatValue(v, m.unit)}</div>
          </div>
        );
      })}
    </div>
  );
}

function ChartFrame({ r, children }: { r: VizResult; children: React.ReactNode }) {
  const m = metricCols(r)[0];
  return (
    <figure className="bg-surface border border-line rounded-lg p-4">
      <figcaption className="flex items-baseline justify-between gap-3">
        <span className="font-semibold text-[15px]">{r.title}</span>
        {m && <span className="text-xs text-ink-3 shrink-0">{unitLabel(m.unit)}</span>}
      </figcaption>
      <div className="mt-3 h-72">{children}</div>
      {r.warnings.length > 0 && (
        <ul className="mt-2 text-xs text-warn space-y-0.5">
          {r.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
    </figure>
  );
}

function tooltipFormatter(unit?: string) {
  return (value: any) => formatValue(typeof value === "number" ? value : null, unit);
}

export function LineViz({ r }: { r: VizResult }) {
  const metrics = metricCols(r);
  const seriesCol = r.columns.find((c) => c.type === "string");
  const grain = r.columns.find((c) => c.key === "period")?.grain ?? "month";
  let data: Record<string, any>[] = [];
  let keys: { key: string; label: string }[] = [];
  if (seriesCol) {
    const m = metrics[0];
    const byPeriod = new Map<string, Record<string, any>>();
    const names = new Set<string>();
    for (const row of r.rows) {
      const p = row.period;
      const s = String(row[seriesCol.key]);
      names.add(s);
      byPeriod.set(p, { ...(byPeriod.get(p) ?? { period: p }), [s]: row[m.key] });
    }
    data = [...byPeriod.values()];
    keys = [...names].sort().map((n) => ({ key: n, label: n }));
  } else {
    data = r.rows;
    keys = metrics.map((m) => ({ key: m.key, label: m.label }));
  }
  const unit = metrics[0]?.unit;
  return (
    <ChartFrame r={r}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 12, bottom: 4, left: 4 }}>
          <CartesianGrid stroke="#e6ecea" vertical={false} />
          <XAxis dataKey="period" tickFormatter={(v) => formatPeriod(v, grain)} tick={{ fontSize: 11, fill: "#6b7f82" }} minTickGap={16} />
          <YAxis tickFormatter={(v) => axisValue(v, unit)} tick={{ fontSize: 11, fill: "#6b7f82" }} width={64} />
          <Tooltip formatter={tooltipFormatter(unit)} labelFormatter={(v) => formatPeriod(String(v), grain)} />
          {keys.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {keys.map((k, i) => (
            <Line key={k.key} type="linear" dataKey={k.key} name={k.label} stroke={SERIES[i % SERIES.length]} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function BarViz({ r }: { r: VizResult }) {
  const metrics = metricCols(r);
  const m = metrics[0];
  const strings = r.columns.filter((c) => c.type === "string");
  const hasPeriod = r.columns.some((c) => c.key === "period");
  const grain = r.columns.find((c) => c.key === "period")?.grain ?? "month";
  const catKey = hasPeriod ? "period" : strings[0]?.key;
  const seriesCol = hasPeriod ? strings[0] : strings[1];
  const highlight = r.columns.some((c) => c.key === "highlight");

  if (seriesCol) {
    const byCat = new Map<string, Record<string, any>>();
    const names = new Set<string>();
    for (const row of r.rows) {
      const cat = String(row[catKey]);
      const s = String(row[seriesCol.key]);
      names.add(s);
      byCat.set(cat, { ...(byCat.get(cat) ?? { cat }), [s]: row[m.key] });
    }
    const data = [...byCat.values()];
    return (
      <ChartFrame r={r}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <CartesianGrid stroke="#e6ecea" vertical={false} />
            <XAxis dataKey="cat" tickFormatter={(v) => (hasPeriod ? formatPeriod(v, grain) : v)} tick={{ fontSize: 11, fill: "#6b7f82" }} />
            <YAxis tickFormatter={(v) => axisValue(v, m.unit)} tick={{ fontSize: 11, fill: "#6b7f82" }} width={64} />
            <Tooltip formatter={tooltipFormatter(m.unit)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {[...names].sort().map((n, i) => (
              <Bar key={n} dataKey={n} fill={SERIES[i % SERIES.length]} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </ChartFrame>
    );
  }

  if (hasPeriod) {
    return (
      <ChartFrame r={r}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={r.rows}>
            <CartesianGrid stroke="#e6ecea" vertical={false} />
            <XAxis dataKey="period" tickFormatter={(v) => formatPeriod(v, grain)} tick={{ fontSize: 11, fill: "#6b7f82" }} />
            <YAxis tickFormatter={(v) => axisValue(v, m.unit)} tick={{ fontSize: 11, fill: "#6b7f82" }} width={64} />
            <Tooltip formatter={tooltipFormatter(m.unit)} labelFormatter={(v) => formatPeriod(String(v), grain)} />
            {metrics.map((mc, i) => (
              <Bar key={mc.key} dataKey={mc.key} name={mc.label} fill={SERIES[i]} isAnimationActive={false} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </ChartFrame>
    );
  }

  const height = Math.max(r.rows.length * 30 + 20, 160);
  return (
    <ChartFrame r={r}>
      <div className="h-full overflow-y-auto">
        <div style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={r.rows} layout="vertical" margin={{ left: 8, right: 16 }}>
              <CartesianGrid stroke="#e6ecea" horizontal={false} />
              <XAxis type="number" tickFormatter={(v) => axisValue(v, m.unit)} tick={{ fontSize: 11, fill: "#6b7f82" }} />
              <YAxis type="category" dataKey={catKey} width={130} tick={{ fontSize: 12, fill: "#10262b" }} interval={0} />
              <Tooltip formatter={tooltipFormatter(m.unit)} />
              <Bar dataKey={m.key} name={m.label} isAnimationActive={false} radius={[0, 3, 3, 0]}>
                {r.rows.map((row, i) => (
                  <Cell key={i} fill={highlight ? (row.highlight ? "#e8590c" : "#9fb9b7") : "#0b6e6e"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      {highlight && (
        <p className="sr-only">
          Highlighted: {r.rows.filter((x) => x.highlight).map((x) => x[catKey]).join(", ")}
        </p>
      )}
    </ChartFrame>
  );
}

export function TableViz({ r }: { r: VizResult }) {
  const grain = r.columns.find((c) => c.key === "period")?.grain ?? "month";
  return (
    <figure className="bg-surface border border-line rounded-lg p-4 overflow-x-auto">
      <figcaption className="font-semibold text-[15px]">{r.title}</figcaption>
      <table className="mt-3 w-full text-sm num">
        <thead>
          <tr className="border-b border-line text-left text-ink-2">
            {r.columns.filter((c) => c.type !== "boolean").map((c) => (
              <th key={c.key} className={`py-2 pr-4 font-medium ${c.type === "number" ? "text-right" : ""}`}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {r.rows.map((row, i) => (
            <tr key={i} className="border-b border-line-2">
              {r.columns.filter((c) => c.type !== "boolean").map((c) => (
                <td key={c.key} className={`py-1.5 pr-4 ${c.type === "number" ? "text-right" : ""}`}>
                  {c.type === "number" ? formatValue(row[c.key], c.unit) : c.type === "date" ? formatPeriod(row[c.key], grain) : row[c.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

export function DashboardPreview({ results, layout = "executive_overview" }: { results: VizResult[]; layout?: string }) {
  const kpis = results.filter((r) => r.type === "kpi_card");
  const charts = results.filter((r) => r.type !== "kpi_card");
  return (
    <div className="space-y-5">
      <KpiStrip results={kpis} />
      <div className={`grid gap-4 ${layout === "detailed_grid" || charts.length === 1 ? "grid-cols-1" : "grid-cols-1 xl:grid-cols-2"}`}>
        {charts.map((r) =>
          r.type === "line" ? <LineViz key={r.viz_id} r={r} /> : r.type === "bar" ? <BarViz key={r.viz_id} r={r} /> : <TableViz key={r.viz_id} r={r} />,
        )}
      </div>
    </div>
  );
}
