"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatPeriod } from "@/lib/format";
import { Empty, ErrorNote, PageHeader } from "@/components/ui";

type Dataset = {
  id: string; name: string; description: string; domain: string; owner: string; classification: string; permitted_use: string;
  grain: string; refresh: string; calendar: string; version: string; status: string;
  fields: { id: string; name: string; type: string; role: string; unit?: string; values?: string[]; time_aggregation?: string }[];
  freshness: { earliest_period: string; latest_period: string; row_count: number };
};
type Metric = {
  id: string; dataset_id: string; name: string; definition: string; formula_display: string; calculation_notes: string; unit: string;
  allowed_dimensions: string[]; null_handling: string; comparison_rules: string; owner: string; version: string; effective_date: string;
};

const ROLE_NAMES: Record<string, string> = { time: "Time", dimension: "Breakdown", measure: "Measure" };

export default function CatalogPage() {
  const [data, setData] = useState<{ version: string; datasets: Dataset[]; metrics: Metric[] } | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([api("/api/v1/catalog/datasets"), api("/api/v1/catalog/metrics")])
      .then(([d, m]) => setData({ version: d.catalog_version, datasets: d.datasets, metrics: m.metrics }))
      .catch(setError);
  }, []);

  return (
    <div className="max-w-5xl">
      <PageHeader title="Data & Metrics" intro={`Approved datasets and KPI definitions you can use. Catalog version ${data?.version ?? "…"}. Definitions are PoC placeholders until the Business Analytics owner approves them.`} />
      <ErrorNote error={error} />
      {data && data.datasets.length === 0 && <Empty>Your role has no dataset entitlements. Ask the data steward if you need access.</Empty>}
      {data?.datasets.map((ds) => (
        <section key={ds.id} className="mb-12">
          <h2 className="display text-xl font-bold">{ds.name}</h2>
          <p className="mt-1 text-ink-2 max-w-prose">{ds.description}</p>
          <dl className="mt-4 grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-3 text-sm">
            <div><dt className="text-ink-3">Classification</dt><dd>{ds.classification}</dd></div>
            <div><dt className="text-ink-3">Owner</dt><dd>{ds.owner}</dd></div>
            <div><dt className="text-ink-3">Grain</dt><dd>{ds.grain}</dd></div>
            <div><dt className="text-ink-3">Coverage</dt><dd className="num">{formatPeriod(ds.freshness.earliest_period)} – {formatPeriod(ds.freshness.latest_period)}, {ds.freshness.row_count.toLocaleString()} rows</dd></div>
            <div><dt className="text-ink-3">Permitted use</dt><dd>{ds.permitted_use}</dd></div>
            <div><dt className="text-ink-3">Refresh</dt><dd>{ds.refresh}</dd></div>
            <div><dt className="text-ink-3">Calendar</dt><dd>{ds.calendar}</dd></div>
            <div><dt className="text-ink-3">Version</dt><dd>{ds.version}</dd></div>
          </dl>

          <h3 className="mt-6 font-semibold">KPI definitions</h3>
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {data.metrics.filter((m) => m.dataset_id === ds.id).map((m) => (
              <li key={m.id} className="py-4 grid md:grid-cols-[220px_1fr] gap-x-6 gap-y-1 text-sm">
                <div>
                  <div className="font-semibold text-[15px]">{m.name}</div>
                  <div className="text-ink-3">v{m.version}, effective {m.effective_date}</div>
                </div>
                <div className="space-y-1">
                  <p>{m.definition}</p>
                  <p><span className="text-ink-3">Formula:</span> {m.formula_display}</p>
                  <p><span className="text-ink-3">Calculation:</span> {m.calculation_notes}</p>
                  <p><span className="text-ink-3">Breakdowns allowed:</span> {m.allowed_dimensions.map((d) => ds.fields.find((f) => f.id === d)?.name ?? d).join(", ")}</p>
                  <p><span className="text-ink-3">Nulls:</span> {m.null_handling}</p>
                  <p className="text-ink-3">Owner: {m.owner}</p>
                </div>
              </li>
            ))}
          </ul>

          <h3 className="mt-6 font-semibold">Fields</h3>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-line text-left text-ink-2"><th className="py-2 pr-4 font-medium">Field</th><th className="py-2 pr-4 font-medium">Use</th><th className="py-2 pr-4 font-medium">Type</th><th className="py-2 font-medium">Values or behaviour</th></tr></thead>
              <tbody>
                {ds.fields.map((f) => (
                  <tr key={f.id} className="border-b border-line-2">
                    <td className="py-2 pr-4">{f.name}</td>
                    <td className="py-2 pr-4">{ROLE_NAMES[f.role]}</td>
                    <td className="py-2 pr-4 text-ink-2">{f.type}{f.unit ? `, ${f.unit}` : ""}</td>
                    <td className="py-2 text-ink-2">{f.values?.join(", ") ?? (f.time_aggregation === "period_end" ? "Balance: last month of a period" : f.time_aggregation === "sum" ? "Flow: summed over months" : "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
