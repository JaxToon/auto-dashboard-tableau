"use client";

// App preview of the Tableau dashboard. Every decision (arrangement, theme, chart form, headline)
// comes from the API's design plan, the same one the .twbx generator uses, so the preview and the
// published dashboard look alike.

import { Area, AreaChart, Bar, BarChart, Cell, LabelList, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axisValue, formatPeriod, formatValue } from "@/lib/format";
import { BarViz, TableViz, type VizResult } from "./Charts";

export type DesignTheme = {
  name: string; page: string; card: string; ink: string; ink_2: string; muted: string; accent: string; accent_soft: string;
  highlight: string; other: string; good: string; bad: string; band: string; band_ink: string; band_muted: string;
  series: string[]; ramp: string[];
};
export type Design = {
  recipe: "executive" | "band" | "rail";
  span: string;
  hero: string | null;
  theme: DesignTheme;
  vizzes: Record<string, { form: string; headline: string; subtitle: string }>;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const metricCol = (r: VizResult) => r.columns.find((c) => c.type === "number")!;
const catCol = (r: VizResult) => r.columns.find((c) => c.type === "string");
const grainOf = (r: VizResult) => r.columns.find((c) => c.key === "period")?.grain ?? "month";
const shortPeriod = (iso: string, grain: string) => (grain === "quarter" ? formatPeriod(iso, grain) : MONTHS[Number(iso.split("-")[1]) - 1]);

/** Short number for a cell or bar label; `scale` keeps one unit (T or M) across a whole chart. */
function compact(v: number | null | undefined, unit?: string, scale?: number): string {
  if (v === null || v === undefined) return "–";
  if (unit === "IDR") {
    const div = scale ?? (Math.abs(v) >= 1e12 ? 1e12 : 1e9);
    return (v / div).toFixed(div === 1e12 ? 2 : 1);
  }
  if (unit === "%") return `${v.toFixed(1)}%`;
  return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

function lerp(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  return "#" + pa.map((x, i) => Math.round(x + (pb[i] - x) * t).toString(16).padStart(2, "0")).join("");
}

function rampColor(ramp: string[], t: number): string {
  const x = Math.min(1, Math.max(0, t)) * (ramp.length - 1);
  const i = Math.min(ramp.length - 2, Math.floor(x));
  return lerp(ramp[i], ramp[i + 1], x - i);
}

function Card({ t, head, sub, children, className = "" }: { t: DesignTheme; head: string; sub?: string; children: React.ReactNode; className?: string }) {
  return (
    <figure className={`rounded-xl p-5 shadow-[0_1px_2px_rgba(16,24,40,0.05)] ${className}`} style={{ background: t.card }}>
      <figcaption>
        <div className="font-semibold text-[15px] leading-snug" style={{ color: t.ink }}>{head}</div>
        {sub && <div className="mt-0.5 text-xs" style={{ color: t.muted }}>{sub}</div>}
      </figcaption>
      <div className="mt-4">{children}</div>
    </figure>
  );
}

function KpiCard({ r, d, dark }: { r: VizResult; d: Design; dark?: boolean }) {
  const t = d.theme;
  const m = metricCol(r);
  return (
    <div className={`rounded-xl px-5 py-4 ${dark ? "" : "shadow-[0_1px_2px_rgba(16,24,40,0.05)]"}`} style={{ background: dark ? "transparent" : t.card }}>
      <div className="text-sm font-semibold" style={{ color: dark ? t.band_muted : t.ink_2 }}>{d.vizzes[r.viz_id]?.headline ?? r.title}</div>
      <div className="mt-1 text-[28px] font-bold leading-tight whitespace-nowrap" style={{ color: dark ? t.band_ink : t.ink }}>{formatValue(r.rows[0]?.[m.key], m.unit)}</div>
      <div className="mt-1 text-xs" style={{ color: dark ? t.band_muted : t.muted }}>{d.span}</div>
    </div>
  );
}

/** One row per member: ranked bars, bars against a target, or dots against the average. */
function RowMarks({ r, t, form }: { r: VizResult; t: DesignTheme; form: string }) {
  const m = metricCol(r);
  const c = catCol(r)!;
  const rows = r.rows.filter((x) => x[m.key] !== null && x[m.key] !== undefined);
  const sorted = form === "ranked" || form === "highlight" ? rows : [...rows].sort((a, b) => b[m.key] - a[m.key]);
  const values = sorted.map((x) => x[m.key] as number);
  const avg = values.reduce((a, b) => a + b, 0) / Math.max(1, values.length);
  const ref = form === "target" ? { v: 100, label: "Target 100%" } : form === "dots" ? { v: avg, label: `Average ${formatValue(avg, m.unit)}` } : null;
  const lo = Math.min(0, ...values);
  const hi = Math.max(...values, ref?.v ?? -Infinity) * 1.32;  // room for the value label
  const pct = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const rank = new Map([...rows].sort((a, b) => b[m.key] - a[m.key]).map((x, i) => [x[c.key], i]));
  const colour = (x: Record<string, any>) =>
    form === "target" ? (x[m.key] >= 100 ? t.accent : t.highlight)
      : form === "highlight" ? (x.highlight ? t.highlight : t.other)
        : (rank.get(x[c.key]) ?? 9) < 3 ? t.accent : t.accent_soft;
  return (
    <div className="relative">
      <div className="space-y-1.5">
        {sorted.map((x) => (
          <div key={String(x[c.key])} className="flex items-center gap-3 text-[13px]">
            <div className="w-40 shrink-0 truncate text-right" style={{ color: t.ink_2 }} title={String(x[c.key])}>{x[c.key]}</div>
            <div className="relative h-6 flex-1">
              {form === "dots" ? (
                <>
                  <div className="absolute top-1/2 h-[2px] -translate-y-1/2" style={{ left: `${pct(Math.min(0, x[m.key]))}%`, width: `${Math.abs(pct(x[m.key]) - pct(0))}%`, background: t.accent_soft }} />
                  <div className="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full" style={{ left: `${pct(x[m.key])}%`, background: t.accent }} />
                  <span className="absolute top-1/2 -translate-y-1/2 whitespace-nowrap pl-3 text-xs" style={{ left: `${pct(x[m.key])}%`, color: t.ink_2 }}>{formatValue(x[m.key], m.unit)}</span>
                </>
              ) : (
                <>
                  <div className="absolute inset-y-0.5 rounded-r-sm" style={{ left: `${pct(0)}%`, width: `${pct(x[m.key]) - pct(0)}%`, background: colour(x) }} />
                  <span className="absolute top-1/2 -translate-y-1/2 whitespace-nowrap pl-1.5 text-xs" style={{ left: `${pct(x[m.key])}%`, color: t.ink_2 }}>{formatValue(x[m.key], m.unit)}</span>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
      {ref && (
        <div className="pointer-events-none absolute inset-y-0 left-[10.75rem] right-0">
          <div className="absolute inset-y-0 border-l border-dashed" style={{ left: `${pct(ref.v)}%`, borderColor: t.ink_2 }} />
          <span className="absolute -bottom-5 whitespace-nowrap pl-1 text-[11px]" style={{ left: `${pct(ref.v)}%`, color: t.ink_2 }}>{ref.label}</span>
        </div>
      )}
      {ref && <div className="h-5" />}
    </div>
  );
}

function Heatmap({ r, t }: { r: VizResult; t: DesignTheme }) {
  const m = metricCol(r);
  const c = catCol(r)!;
  const grain = grainOf(r);
  const periods = [...new Set(r.rows.map((x) => x.period as string))].sort();
  const last = periods[periods.length - 1];
  const members = [...new Set(r.rows.map((x) => String(x[c.key])))];
  const at = new Map(r.rows.map((x) => [`${x[c.key]}|${x.period}`, x[m.key] as number]));
  members.sort((a, b) => (at.get(`${b}|${last}`) ?? 0) - (at.get(`${a}|${last}`) ?? 0));
  const vals = [...at.values()].filter((v) => v !== null && v !== undefined);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const scale = Math.max(...vals.map(Math.abs)) >= 1e12 ? 1e12 : 1e9;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-separate border-spacing-[2px] text-[12px]">
        <thead>
          <tr>
            <th />
            {periods.map((p) => <th key={p} className="pb-1 font-normal" style={{ color: t.muted }}>{shortPeriod(p, grain)}</th>)}
          </tr>
        </thead>
        <tbody>
          {members.map((mem) => (
            <tr key={mem}>
              <td className="whitespace-nowrap pr-3 text-[13px]" style={{ color: t.ink_2 }}>{mem}</td>
              {periods.map((p) => {
                const v = at.get(`${mem}|${p}`);
                return (
                  <td key={p} title={`${mem}, ${formatPeriod(p, grain)}: ${formatValue(v, m.unit)}`} className="h-8 rounded-[3px] text-center tabular-nums"
                    style={{ background: v === undefined ? "transparent" : rampColor(t.ramp, (v - lo) / Math.max(1e-9, hi - lo)), color: t.ink }}>
                    {compact(v, m.unit, scale)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Spotlight({ r, t }: { r: VizResult; t: DesignTheme }) {
  const m = metricCol(r);
  const c = catCol(r)!;
  const grain = grainOf(r);
  const byPeriod = new Map<string, Record<string, any>>();
  const totals = new Map<string, number>();
  for (const x of r.rows) {
    const s = String(x[c.key]);
    totals.set(s, (totals.get(s) ?? 0) + (x[m.key] ?? 0));
    byPeriod.set(x.period, { ...(byPeriod.get(x.period) ?? { period: x.period }), [s]: x[m.key] });
  }
  const data = [...byPeriod.values()].sort((a, b) => a.period.localeCompare(b.period));
  const order = [...totals.keys()].sort((a, b) => (totals.get(b) ?? 0) - (totals.get(a) ?? 0));
  const colourOf = (i: number) => (i === 0 ? t.accent : i === 1 ? t.highlight : t.other);
  return (
    <div className="h-80">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 120, bottom: 4, left: 4 }}>
          <XAxis dataKey="period" tickFormatter={(v) => shortPeriod(v, grain)} tick={{ fontSize: 11, fill: t.muted }} axisLine={false} tickLine={false} />
          <YAxis tickFormatter={(v) => axisValue(v, m.unit)} tick={{ fontSize: 11, fill: t.muted }} width={56} axisLine={false} tickLine={false} />
          <Tooltip formatter={(v: any) => formatValue(v, m.unit)} labelFormatter={(v) => formatPeriod(String(v), grain)} />
          {[...order].reverse().map((s) => {
            const i = order.indexOf(s);
            return (
              <Line key={s} dataKey={s} stroke={colourOf(i)} strokeWidth={i < 2 ? 2.6 : 1.6} dot={false} isAnimationActive={false}>
                <LabelList dataKey={s} content={(p: any) => (p.index === data.length - 1 ? <text x={p.x + 6} y={p.y + 4} fontSize={12} fill={colourOf(i)}>{s}</text> : null)} />
              </Line>
            );
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function AreaTrend({ r, t }: { r: VizResult; t: DesignTheme }) {
  const m = metricCol(r);
  const grain = grainOf(r);
  const data = [...r.rows].sort((a, b) => a.period.localeCompare(b.period));
  return (
    <div className="h-80">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 16, right: 64, bottom: 4, left: 4 }}>
          <XAxis dataKey="period" tickFormatter={(v) => shortPeriod(v, grain)} tick={{ fontSize: 11, fill: t.muted }} axisLine={false} tickLine={false} />
          <YAxis hide domain={["auto", "auto"]} />
          <Tooltip formatter={(v: any) => formatValue(v, m.unit)} labelFormatter={(v) => formatPeriod(String(v), grain)} />
          <Area dataKey={m.key} stroke={t.accent} strokeWidth={2.4} fill={t.accent} fillOpacity={0.18} isAnimationActive={false}>
            <LabelList dataKey={m.key} content={(p: any) => (p.index === data.length - 1 ? <text x={p.x + 8} y={p.y + 4} fontSize={12} fontWeight={600} fill={t.ink}>{formatValue(p.value, m.unit)}</text> : null)} />
          </Area>
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function Columns({ r, t }: { r: VizResult; t: DesignTheme }) {
  const m = metricCol(r);
  const grain = grainOf(r);
  const data = [...r.rows].sort((a, b) => a.period.localeCompare(b.period));
  const scale = Math.max(...data.map((x) => Math.abs(x[m.key] ?? 0))) >= 1e12 ? 1e12 : 1e9;
  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 20, right: 8, bottom: 4, left: 8 }}>
          <XAxis dataKey="period" tickFormatter={(v) => shortPeriod(v, grain)} tick={{ fontSize: 11, fill: t.muted }} axisLine={false} tickLine={false} />
          <YAxis hide />
          <Tooltip formatter={(v: any) => formatValue(v, m.unit)} labelFormatter={(v) => formatPeriod(String(v), grain)} />
          <Bar dataKey={m.key} isAnimationActive={false} radius={[3, 3, 0, 0]}>
            {data.map((_, i) => <Cell key={i} fill={i === data.length - 1 ? t.accent : t.accent_soft} />)}
            <LabelList dataKey={m.key} position="top" formatter={(v: any) => compact(v, m.unit, scale)} style={{ fontSize: 11, fill: t.ink_2 }} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChartBody({ r, d }: { r: VizResult; d: Design }) {
  const t = d.theme;
  const form = d.vizzes[r.viz_id]?.form ?? r.type;
  if (form === "heatmap") return <Heatmap r={r} t={t} />;
  if (form === "spotlight") return <Spotlight r={r} t={t} />;
  if (form === "area") return <AreaTrend r={r} t={t} />;
  if (form === "columns") return <Columns r={r} t={t} />;
  if (["ranked", "target", "dots", "highlight"].includes(form)) return <RowMarks r={r} t={t} form={form} />;
  return null;
}

function ChartCard({ r, d, className }: { r: VizResult; d: Design; className?: string }) {
  const v = d.vizzes[r.viz_id];
  if (!v || v.form === "series_bars") return <BarViz r={r} />;
  if (v.form === "table") return <TableViz r={r} />;
  return <Card t={d.theme} head={v.headline} sub={v.subtitle} className={className}><ChartBody r={r} d={d} /></Card>;
}

function Findings({ insights, t }: { insights: string[]; t: DesignTheme }) {
  if (!insights.length) return null;
  return (
    <aside className="rounded-xl p-5 shadow-[0_1px_2px_rgba(16,24,40,0.05)]" style={{ background: t.card }}>
      <div className="text-[11px] font-semibold tracking-wider" style={{ color: t.accent }}>WHAT STANDS OUT</div>
      <ul className="mt-3 space-y-3 text-sm leading-snug">
        {insights.map((f) => (
          <li key={f} className="flex gap-2" style={{ color: t.ink }}><span aria-hidden style={{ color: t.accent }}>—</span><span>{f}</span></li>
        ))}
      </ul>
    </aside>
  );
}

export function DesignedDashboard({ results, insights, design, layout }: { results: VizResult[]; insights: string[]; design: Design; layout?: string }) {
  const t = design.theme;
  const kpis = results.filter((r) => r.type === "kpi_card");
  const charts = results.filter((r) => r.type !== "kpi_card");
  const hero = charts.find((r) => r.viz_id === design.hero);
  const detail = charts.filter((r) => r !== hero);
  const twoUp = layout !== "detailed_grid" && detail.length > 1;
  const heroRow = (
    <div className="flex flex-wrap gap-4">
      {hero && <div className="min-w-0 flex-[2_1_520px]"><ChartCard r={hero} d={design} /></div>}
      {insights.length > 0 && <div className="min-w-0 flex-[1_1_280px]"><Findings insights={insights} t={t} /></div>}
    </div>
  );
  return (
    <div className="space-y-4 rounded-2xl p-4 sm:p-5" style={{ background: t.page }}>
      <div className="flex flex-wrap items-center gap-2 text-xs" style={{ color: t.muted }}>
        <span className="h-2.5 w-2.5 rounded-full" style={{ background: t.accent }} />
        Tableau design: {design.recipe} arrangement, {t.name} theme
      </div>
      {design.recipe === "band" && kpis.length > 0 && (
        <div className="grid gap-2 rounded-xl p-2 sm:[grid-template-columns:var(--cols)]" style={{ background: t.band, "--cols": `repeat(${kpis.length}, minmax(0, 1fr))` } as React.CSSProperties}>
          {kpis.map((r) => <KpiCard key={r.viz_id} r={r} d={design} dark />)}
        </div>
      )}
      {design.recipe === "rail" && kpis.length > 0 ? (
        <div className="flex flex-wrap gap-4">
          <div className="min-w-0 flex-[1_1_220px] space-y-4">{kpis.map((r) => <KpiCard key={r.viz_id} r={r} d={design} />)}</div>
          {hero && <div className="min-w-0 flex-[3_1_480px]"><ChartCard r={hero} d={design} /></div>}
          {insights.length > 0 && <div className="min-w-0 flex-[1_1_260px]"><Findings insights={insights} t={t} /></div>}
        </div>
      ) : (
        <>
          {design.recipe !== "band" && kpis.length > 0 && (
            <div className="grid gap-4 sm:[grid-template-columns:var(--cols)]" style={{ "--cols": `repeat(${kpis.length}, minmax(0, 1fr))` } as React.CSSProperties}>
              {kpis.map((r) => <KpiCard key={r.viz_id} r={r} d={design} />)}
            </div>
          )}
          {heroRow}
        </>
      )}
      {detail.length > 0 && (
        <>
          <div className="pt-2">
            <div className="text-lg font-semibold" style={{ color: t.ink }}>Detail</div>
            <div className="text-xs" style={{ color: t.muted }}>Every KPI broken down in its own chart</div>
          </div>
          <div className="grid gap-4" style={{ gridTemplateColumns: twoUp ? "repeat(auto-fit, minmax(420px, 1fr))" : "1fr" }}>
            {detail.map((r) => <ChartCard key={r.viz_id} r={r} d={design} />)}
          </div>
        </>
      )}
    </div>
  );
}
