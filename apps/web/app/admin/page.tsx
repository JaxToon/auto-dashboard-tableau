"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatPeriod } from "@/lib/format";
import { Button, ErrorNote, PageHeader } from "@/components/ui";

type Settings = {
  flags: Record<string, { effective: boolean; env_ceiling: boolean }>;
  values: Record<string, number | boolean>;
  connectors: {
    data: { adapter: string; datasets: { dataset_id: string; row_count: number; latest_period: string }[] };
    llm: { provider: string; model: string; external_calls: boolean };
    tableau: { mode: string; configured: boolean; server_url: string | null; site_content_url: string | null; project_id: string | null; allowed_project_ids: string[]; auth_method: string; pat_name_set: boolean; pat_secret_set: boolean };
    conversational_analytics: { enabled: boolean; status: string };
  };
  limits: Record<string, number>;
};

const FLAG_NAMES: Record<string, string> = {
  feature_ai_planning: "Question interpretation and planning",
  feature_data_access: "Data access",
  feature_dashboard_publish: "Publishing to Tableau",
  feature_recurring_recommendations: "Recurring-question recommendations",
  feature_recommendation_telemetry: "Recording minimized question patterns",
};

export default function AdminPage() {
  const [s, setS] = useState<Settings | null>(null);
  const [test, setTest] = useState<Record<string, unknown> | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string; allowed: boolean }[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => api<Settings>("/api/v1/admin/settings").then(setS).catch(setError), []);
  useEffect(() => {
    load();
  }, [load]);

  async function patch(body: Record<string, unknown>) {
    setError(null);
    try {
      await api("/api/v1/admin/settings", { method: "PATCH", body });
      await load();
    } catch (e) {
      setError(e);
    }
  }

  async function runTest() {
    setBusy(true);
    setError(null);
    try {
      setTest(await api("/api/v1/integrations/tableau/test", { body: {} }));
      setProjects((await api<{ projects: { id: string; name: string; allowed: boolean }[] }>("/api/v1/integrations/tableau/projects")).projects);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (!s) return error ? <ErrorNote error={error} /> : <p className="text-ink-3">Loading…</p>;
  const t = s.connectors.tableau;

  return (
    <div className="max-w-5xl">
      <PageHeader title="Admin" intro="Connector health, kill switches and recommendation thresholds. Secrets are configured on the server and are never shown here." />
      <div className="mb-4"><ErrorNote error={error} /></div>

      <section className="rounded-lg border-2 border-ink bg-surface p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="display text-lg font-bold">Tableau</h2>
          <Button onClick={runTest} disabled={busy}>{busy ? "Testing…" : "Test connection"}</Button>
        </div>
        <p className={`mt-1 text-sm font-semibold ${t.mode === "rest" ? "text-ok" : t.mode === "mock" ? "text-signal" : "text-danger"}`}>
          {t.mode === "rest" ? "Real Tableau REST API configured" : t.mode === "mock" ? "MOCK mode: nothing is published to Tableau" : "Tableau integration not configured"}
        </p>
        <dl className="mt-3 grid sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div><dt className="text-ink-3">Server</dt><dd className="break-all">{t.server_url ?? "Not set (TABLEAU_SERVER_URL)"}</dd></div>
          <div><dt className="text-ink-3">Site</dt><dd>{t.site_content_url ?? "Default site"}</dd></div>
          <div><dt className="text-ink-3">Target project</dt><dd>{t.project_id ?? "Not set (TABLEAU_PROJECT_ID)"}</dd></div>
          <div><dt className="text-ink-3">Allow-listed projects</dt><dd>{t.allowed_project_ids.join(", ") || "None"}</dd></div>
          <div><dt className="text-ink-3">Authentication</dt><dd>{t.auth_method}: token name {t.pat_name_set ? "set" : "missing"}, secret {t.pat_secret_set ? "set" : "missing"}</dd></div>
        </dl>
        {test && (
          <div className={`mt-4 rounded-md px-4 py-3 text-sm ${test.ok ? "bg-ok-tint text-ok" : test.signed_in ? "bg-warn-tint text-warn" : "bg-danger-tint text-danger"}`}>
            <div className="font-semibold">{String(test.message)}</div>
            {"product_version" in test && test.product_version ? <div>Tableau {String(test.product_version)}, REST API {String(test.rest_api_version)}</div> : null}
          </div>
        )}
        {projects && (
          <ul className="mt-3 text-sm">
            {projects.map((p) => <li key={p.id}>{p.name} <span className="text-ink-3">{p.id}</span> {p.allowed && <span className="text-ok font-medium">allow-listed</span>}</li>)}
          </ul>
        )}
      </section>

      <section className="mt-6 rounded-lg border border-line bg-surface p-5">
        <h2 className="font-semibold">Kill switches</h2>
        <p className="text-sm text-ink-2">You can turn a feature off at any time. A feature the deployment disabled cannot be turned on here.</p>
        <ul className="mt-3 divide-y divide-line-2">
          {Object.entries(s.flags).map(([k, f]) => (
            <li key={k} className="flex items-center justify-between gap-4 py-2.5">
              <div>
                <div className="font-medium">{FLAG_NAMES[k] ?? k}</div>
                {!f.env_ceiling && <div className="text-xs text-ink-3">Disabled by deployment ({k.toUpperCase()}=false)</div>}
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={f.effective} disabled={!f.env_ceiling} onChange={(e) => patch({ [k]: e.target.checked })} className="h-4 w-4 accent-teal" />
                {f.effective ? "On" : "Off"}
              </label>
            </li>
          ))}
          <li className="flex items-center justify-between gap-4 py-2.5">
            <div className="font-medium">Require Business Analytics reviewer approval before publishing</div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={!!s.values.review_required} onChange={(e) => patch({ review_required: e.target.checked })} className="h-4 w-4 accent-teal" />
              {s.values.review_required ? "On" : "Off"}
            </label>
          </li>
        </ul>
      </section>

      <section className="mt-6 rounded-lg border border-line bg-surface p-5">
        <h2 className="font-semibold">Recommendation thresholds</h2>
        <p className="text-sm text-ink-2">Local demo test parameters, not bank policy.</p>
        <form
          className="mt-3 flex flex-wrap items-end gap-4 text-sm"
          onSubmit={(e) => {
            e.preventDefault();
            const fd = new FormData(e.currentTarget);
            patch({ rec_min_events: Number(fd.get("rec_min_events")), rec_window_days: Number(fd.get("rec_window_days")), rec_min_actors: Number(fd.get("rec_min_actors")) });
          }}
        >
          {[["rec_min_events", "Minimum similar questions"], ["rec_window_days", "Window (days)"], ["rec_min_actors", "Minimum distinct groups"]].map(([k, label]) => (
            <label key={k} className="flex flex-col"><span className="text-ink-2">{label}</span>
              <input name={k} type="number" min={1} defaultValue={Number(s.values[k])} className="mt-1 w-28 rounded-md border border-line px-2 py-1.5 num" /></label>
          ))}
          <Button type="submit" variant="secondary">Save thresholds</Button>
        </form>
      </section>

      <section className="mt-6 grid md:grid-cols-3 gap-4 text-sm">
        <div className="rounded-lg border border-line bg-surface p-4">
          <h3 className="font-semibold">Data adapter</h3>
          <p className="text-ink-2">{s.connectors.data.adapter}</p>
          <ul className="mt-1 text-ink-2">{s.connectors.data.datasets.map((d) => <li key={d.dataset_id}>{d.dataset_id}: {d.row_count} rows to {formatPeriod(d.latest_period)}</li>)}</ul>
        </div>
        <div className="rounded-lg border border-line bg-surface p-4">
          <h3 className="font-semibold">Planner</h3>
          <p className="text-ink-2">{s.connectors.llm.provider} ({s.connectors.llm.model})</p>
          <p className="text-ink-2">{s.connectors.llm.external_calls ? "Calls an external model" : "No external model calls"}</p>
        </div>
        <div className="rounded-lg border border-line bg-surface p-4">
          <h3 className="font-semibold">Conversational analytics integration</h3>
          <p className="text-ink-2">{s.connectors.conversational_analytics.status}</p>
        </div>
      </section>
    </div>
  );
}
