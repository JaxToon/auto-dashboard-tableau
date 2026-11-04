"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatPeriod, formatTime } from "@/lib/format";
import { can, useSession } from "@/components/Shell";
import { DashboardPreview, VizResult } from "@/components/Charts";
import { Button, ErrorNote, StatusPill } from "@/components/ui";
import { FilterBar, FilterField, ViewState } from "@/components/FilterBar";

type Viz = { id: string; type: string; title: string; metric_ids: string[]; dimension_ids: string[] };
type Spec = {
  title: string;
  description: string;
  dataset_id: string;
  layout: string;
  time_range: { start: string; end: string; grain: string; field: string };
  filters: { field: string; op: string; values: string[] }[];
  visualizations: Viz[];
  assumptions: string[];
  warnings: string[];
};
type Pub = { id: string; version: number; mode: string; status: string; content_url: string | null; verified: boolean; error_message: string | null; project_name: string; project_id: string; created_at: string; verification?: { checks?: { name: string; ok: boolean }[] } };
type Draft = {
  id: string;
  title: string;
  status: string;
  owner_id: string;
  dataset_name: string;
  dataset_version: string;
  catalog_version: string;
  metric_versions: Record<string, string>;
  spec: Spec;
  current_version: number;
  validated_version: number | null;
  approved_version: number | null;
  validation_report: { valid: boolean; errors: { message: string }[]; warnings: string[]; checks: { name: string; status: string; detail: string }[] } | null;
  review_required: boolean;
  can_publish: boolean;
  publish_blockers: string[];
  target_project_id: string | null;
  tableau_mode: string;
  publications: Pub[];
  verified_url: string | null;
  reviews: { version: number; reviewer_id: string; decision: string; comment: string; created_at: string }[];
  versions: { version: number; change_summary: string; created_by: string; created_at: string }[];
  interpretation_summary: string;
};
type Preview = {
  view: { customized: boolean; filters: { field: string; values: string[] }[]; time_range: { start: string; end: string } };
  results: VizResult[];
  result_validation: { valid: boolean; warnings: string[] };
  provenance: {
    dataset_name: string;
    classification: string;
    catalog_version: string;
    freshness: { latest_period: string; earliest_period: string; snapshot_loaded_at: string; refresh: string };
    metrics: { id: string; name: string; definition: string; formula_display: string; calculation_notes: string; null_handling: string; version: string; owner: string; effective_date: string }[];
  };
};
type Field = { id: string; name: string; role: string; values?: string[] };

const CHART_NAMES: Record<string, string> = { line: "Line", bar: "Bar", table: "Table", kpi_card: "KPI card" };

function monthsBetween(start: string, end: string) {
  const out: string[] = [];
  let [y, m] = start.split("-").map(Number);
  const [ey, em] = end.split("-").map(Number);
  while (y < ey || (y === ey && m <= em)) {
    out.push(`${y}-${String(m).padStart(2, "0")}-01`);
    m++;
    if (m > 12) { m = 1; y++; }
  }
  return out;
}

export default function DashboardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useSession();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [fields, setFields] = useState<Field[]>([]);
  const [coverage, setCoverage] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [comment, setComment] = useState("");
  const [form, setForm] = useState<{ title: string; description: string; layout: string; charts: Record<string, string>; remove: string[] }>();
  const [view, setView] = useState<ViewState | null>(null);
  const [viewLoading, setViewLoading] = useState(false);

  const savedView = (s: Spec): ViewState => ({
    filters: Object.fromEntries(s.filters.map((f) => [f.field, f.values])),
    start: s.time_range.start,
    end: s.time_range.end,
  });

  const load = useCallback(async () => {
    try {
      const d = await api<Draft>(`/api/v1/dashboards/${id}`);
      setDraft(d);
      setView(savedView(d.spec));
      const ds = await api<{ datasets: { id: string; fields: Field[]; freshness: { earliest_period: string; latest_period: string } }[] }>("/api/v1/catalog/datasets");
      const meta = ds.datasets.find((x) => x.id === d.spec.dataset_id);
      if (meta) {
        setFields(meta.fields);
        setCoverage(monthsBetween(meta.freshness.earliest_period, meta.freshness.latest_period));
      }
      setPreview(await api<Preview>(`/api/v1/dashboards/${id}/preview`, { body: {} }));
    } catch (e) {
      setError(e);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const viewBody = (v: ViewState) => ({
    filters: Object.entries(v.filters).filter(([, vals]) => vals.length).map(([field, values]) => ({ field, op: "in", values })),
    time_range_start: v.start,
    time_range_end: v.end,
  });

  async function changeView(v: ViewState) {
    setView(v);
    setViewLoading(true);
    setError(null);
    try {
      setPreview(await api<Preview>(`/api/v1/dashboards/${id}/preview`, { body: viewBody(v) }));
    } catch (e) {
      setError(e);
    } finally {
      setViewLoading(false);
    }
  }

  async function saveView() {
    if (!view) return;
    const b = viewBody(view);
    await act("save-view", async () => {
      await api(`/api/v1/dashboards/${id}`, { method: "PATCH", body: b });
      await api(`/api/v1/dashboards/${id}/validate`, { body: {} });
    });
  }

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e);
      await load().catch(() => {});
    } finally {
      setBusy(null);
    }
  }

  function startEdit() {
    if (!draft) return;
    const s = draft.spec;
    setForm({
      title: s.title,
      description: s.description,
      layout: s.layout,
      charts: Object.fromEntries(s.visualizations.map((v) => [v.id, v.type])),
      remove: [],
    });
    setEditing(true);
  }

  async function saveEdit() {
    if (!draft || !form) return;
    const s = draft.spec;
    const patch: Record<string, unknown> = {};
    if (form.title !== s.title) patch.title = form.title;
    if (form.description !== s.description) patch.description = form.description;
    if (form.layout !== s.layout) patch.layout = form.layout;
    const charts = Object.fromEntries(Object.entries(form.charts).filter(([vid, t]) => s.visualizations.find((v) => v.id === vid)?.type !== t));
    if (Object.keys(charts).length) patch.chart_types = charts;
    if (form.remove.length) patch.remove_visualizations = form.remove;
    await act("save", async () => {
      await api(`/api/v1/dashboards/${id}`, { method: "PATCH", body: patch });
      await api(`/api/v1/dashboards/${id}/validate`, { body: {} });
      setEditing(false);
    });
  }

  if (!draft) {
    return error ? <ErrorNote error={error} /> : <p className="text-ink-3">Loading dashboard…</p>;
  }

  const isOwner = me?.user.user_id === draft.owner_id;
  const isReviewer = can(me, "dashboard:review") && !isOwner;
  const validated = draft.validated_version === draft.current_version;
  const approved = draft.approved_version === draft.current_version;
  const latestPub = draft.publications[0];
  const steps = [
    { label: "Draft", done: true },
    { label: "Validated", done: validated },
    { label: draft.review_required ? "Approved by BA reviewer" : "Review not required", done: !draft.review_required || approved },
    { label: "Published to Tableau", done: draft.status === "Published" && !!draft.verified_url },
  ];
  const filterable: FilterField[] = fields.filter((f) => f.role === "dimension" && f.values?.length).map((f) => ({ id: f.id, name: f.name, values: f.values! }));
  const shownRange = preview?.view.time_range ?? draft.spec.time_range;
  const shownFilters = preview?.view.filters ?? draft.spec.filters;

  return (
    <div className="max-w-[1400px]">
      <div className="text-sm"><Link href="/dashboards" className="text-teal hover:underline underline-offset-2">My Dashboards</Link></div>
      <header className="mt-2 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill status={draft.status} />
            <span className="text-xs text-ink-3 num">Version {draft.current_version}</span>
          </div>
          <h1 className="display mt-1 text-2xl md:text-[30px] font-bold leading-tight">{draft.title}</h1>
          <p className="mt-1 text-ink-2 max-w-prose">{draft.spec.description}</p>
        </div>
        <ol className="flex flex-wrap gap-x-5 gap-y-1 text-sm shrink-0" aria-label="Lifecycle">
          {steps.map((s, i) => (
            <li key={s.label} className={`flex items-center gap-1.5 ${s.done ? "text-ink" : "text-ink-3"}`}>
              <span className={`num inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${s.done ? "bg-teal text-white" : "border border-line"}`}>{s.done ? "✓" : i + 1}</span>
              {s.label}
            </li>
          ))}
        </ol>
      </header>

      <div className="mt-4"><ErrorNote error={error} /></div>

      <div className="mt-6 grid gap-8 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section aria-label="Dashboard preview" className="min-w-0">
          {view && coverage.length > 0 && (
            <FilterBar
              fields={filterable}
              months={coverage}
              view={view}
              customized={!!preview?.view.customized}
              loading={viewLoading}
              canSave={isOwner && !["Archived", "Publishing"].includes(draft.status) && !busy}
              onChange={changeView}
              onReset={() => changeView(savedView(draft.spec))}
              onSave={saveView}
            />
          )}
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 text-sm text-ink-2">
            <span>
              {draft.dataset_name}, {formatPeriod(shownRange.start)} – {formatPeriod(shownRange.end)}
              {shownFilters.length > 0 && <>, filtered to {shownFilters.map((f) => f.values.join(", ")).join("; ")}</>}
            </span>
            {preview && <span className="text-xs text-ink-3">Data up to {formatPeriod(preview.provenance.freshness.latest_period)} (synthetic snapshot)</span>}
          </div>
          {preview ? <DashboardPreview results={preview.results} layout={draft.spec.layout} /> : <p className="text-ink-3">Building preview…</p>}

          {preview && (
            <details className="mt-8 rounded-lg border border-line bg-surface" open>
              <summary className="cursor-pointer px-5 py-3 font-semibold">KPI definitions and data source</summary>
              <div className="px-5 pb-5 text-sm space-y-4">
                <dl className="grid sm:grid-cols-3 gap-3">
                  <div><dt className="text-ink-3">Source</dt><dd>{preview.provenance.dataset_name} v{draft.dataset_version}</dd></div>
                  <div><dt className="text-ink-3">Classification</dt><dd>{preview.provenance.classification}</dd></div>
                  <div><dt className="text-ink-3">KPI catalog</dt><dd>{preview.provenance.catalog_version}</dd></div>
                </dl>
                <ul className="divide-y divide-line-2">
                  {preview.provenance.metrics.map((m) => (
                    <li key={m.id} className="py-3">
                      <div className="font-semibold">{m.name} <span className="font-normal text-ink-3">v{m.version}, effective {m.effective_date}</span></div>
                      <p className="text-ink-2">{m.definition}</p>
                      <p className="mt-1"><span className="text-ink-3">Formula:</span> {m.formula_display}</p>
                      <p><span className="text-ink-3">Calculation:</span> {m.calculation_notes}</p>
                      <p><span className="text-ink-3">Nulls:</span> {m.null_handling}</p>
                      <p className="text-ink-3">Owner: {m.owner}</p>
                    </li>
                  ))}
                </ul>
                {[...draft.spec.assumptions, ...draft.spec.warnings, ...preview.result_validation.warnings].length > 0 && (
                  <div>
                    <div className="font-semibold">Assumptions and warnings</div>
                    <ul className="mt-1 list-disc pl-5 text-ink-2">
                      {[...new Set([...draft.spec.assumptions, ...draft.spec.warnings, ...preview.result_validation.warnings])].map((a) => <li key={a}>{a}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            </details>
          )}
        </section>

        <aside className="space-y-5">
          {/* Publish */}
          <section className="rounded-lg border-2 border-ink bg-surface p-5">
            <h2 className="display text-lg font-bold">Publish to Tableau</h2>
            {draft.verified_url ? (
              <div className="mt-3">
                <p className="text-sm text-ok font-semibold">Published and verified in Tableau.</p>
                <a href={draft.verified_url} target="_blank" rel="noopener noreferrer" className="mt-3 inline-block rounded-md bg-teal px-4 py-2 text-sm font-semibold text-white hover:bg-teal-dark">Open in Tableau</a>
                <p className="mt-2 text-xs text-ink-3 break-all">{draft.verified_url}</p>
                <p className="mt-1 text-xs text-ink-3">Tableau asks you to sign in and applies its own permissions.</p>
              </div>
            ) : (
              <div className="mt-2 text-sm">
                <p className="text-ink-2">
                  Target project: <span className="font-medium text-ink">{draft.target_project_id ?? (draft.tableau_mode === "mock" ? "MOCK project" : "not configured")}</span>
                </p>
                {draft.tableau_mode === "mock" && <p className="mt-2 rounded bg-signal-tint px-2 py-1 text-signal text-xs font-semibold">Mock mode: publishing writes a local workbook only. It is not a Tableau publication.</p>}
                {draft.publish_blockers.length > 0 && (
                  <ul className="mt-3 space-y-1">
                    {draft.publish_blockers.map((b) => <li key={b} className="flex gap-2 text-ink-2"><span aria-hidden className="text-warn">●</span>{b}</li>)}
                  </ul>
                )}
                {isOwner && !confirming && (
                  <Button className="mt-4 w-full" disabled={!draft.can_publish || !!busy} onClick={() => setConfirming(true)}>Publish to Tableau</Button>
                )}
                {confirming && (
                  <div className="mt-4 rounded-md border border-line p-3">
                    <p>Publish version {draft.current_version} to <strong>{draft.target_project_id ?? "the configured project"}</strong> using the PoC publisher account?</p>
                    <div className="mt-3 flex gap-2">
                      <Button disabled={!!busy} onClick={() => act("publish", async () => { await api(`/api/v1/dashboards/${id}/publish`, { body: { confirm: true } }); setConfirming(false); })}>
                        {busy === "publish" ? "Publishing…" : "Confirm publish"}
                      </Button>
                      <Button variant="secondary" onClick={() => setConfirming(false)}>Cancel</Button>
                    </div>
                  </div>
                )}
              </div>
            )}
            {latestPub && (
              <div className="mt-4 border-t border-line pt-3 text-sm">
                <div className="flex items-center justify-between"><span className="text-ink-3">Last attempt, v{latestPub.version}</span><StatusPill status={latestPub.status} /></div>
                {latestPub.error_message && <p className="mt-1 text-danger">{latestPub.error_message}</p>}
                {latestPub.verification?.checks && (
                  <ul className="mt-2 text-xs space-y-0.5">
                    {latestPub.verification.checks.map((c) => <li key={c.name} className={c.ok ? "text-ok" : "text-danger"}>{c.ok ? "Pass" : "Fail"}: {c.name.replace(/_/g, " ")}</li>)}
                  </ul>
                )}
              </div>
            )}
          </section>

          {/* Workflow actions */}
          <section className="rounded-lg border border-line bg-surface p-5 space-y-3">
            <h2 className="font-semibold">Review</h2>
            {isOwner && (
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" disabled={!!busy} onClick={() => act("validate", () => api(`/api/v1/dashboards/${id}/validate`, { body: {} }))}>{busy === "validate" ? "Validating…" : "Validate"}</Button>
                <Button disabled={!!busy || !validated || !["Draft", "Failed"].includes(draft.status)} onClick={() => act("submit", () => api(`/api/v1/dashboards/${id}/submit-review`, { body: {} }))}>
                  {draft.review_required ? "Submit for review" : "Mark ready to publish"}
                </Button>
              </div>
            )}
            {isReviewer && draft.status === "Needs Review" && (
              <div className="space-y-2">
                <label className="block text-sm">
                  <span className="text-ink-2">Reviewer comment</span>
                  <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} maxLength={1000} className="mt-1 w-full rounded-md border border-line px-2 py-1.5" />
                </label>
                <div className="flex gap-2">
                  <Button disabled={!!busy} onClick={() => act("approve", () => api(`/api/v1/dashboards/${id}/approve`, { body: { decision: "approved", comment } }))}>Approve</Button>
                  <Button variant="secondary" disabled={!!busy} onClick={() => act("changes", () => api(`/api/v1/dashboards/${id}/approve`, { body: { decision: "changes_requested", comment } }))}>Request changes</Button>
                </div>
              </div>
            )}
            {draft.validation_report && (
              <div className="text-sm">
                <div className={draft.validation_report.valid ? "text-ok font-medium" : "text-danger font-medium"}>
                  {draft.validation_report.valid ? `Validation passed for version ${draft.validated_version}` : "Validation failed"}
                </div>
                <details className="mt-1">
                  <summary className="cursor-pointer text-ink-2">{draft.validation_report.checks.length} checks</summary>
                  <ul className="mt-1 text-xs space-y-0.5">
                    {draft.validation_report.checks.map((c) => <li key={c.name} className={c.status === "pass" ? "text-ok" : "text-danger"}>{c.status === "pass" ? "Pass" : "Fail"}: {c.name.replace(/_/g, " ")}</li>)}
                    {draft.validation_report.errors.map((e) => <li key={e.message} className="text-danger">{e.message}</li>)}
                  </ul>
                </details>
              </div>
            )}
            {draft.reviews.length > 0 && (
              <ul className="text-sm border-t border-line pt-2 space-y-1">
                {draft.reviews.map((r, i) => (
                  <li key={i}><span className="font-medium">{r.decision === "approved" ? "Approved" : "Changes requested"}</span> <span className="text-ink-3">v{r.version}, {r.reviewer_id}, {formatTime(r.created_at)}</span>{r.comment && <div className="text-ink-2">{r.comment}</div>}</li>
                ))}
              </ul>
            )}
          </section>

          {/* Edit */}
          {isOwner && draft.status !== "Archived" && (
            <section className="rounded-lg border border-line bg-surface p-5">
              <div className="flex items-center justify-between">
                <h2 className="font-semibold">Edit draft</h2>
                {!editing && <Button variant="quiet" onClick={startEdit}>Edit</Button>}
              </div>
              {!editing && <p className="mt-1 text-sm text-ink-2">Change the title, description, chart types and layout. Set the period and filters with the filter bar above the preview. KPI formulas are protected. Each save creates a new version that needs validation and review again.</p>}
              {editing && form && (
                <div className="mt-3 space-y-3 text-sm">
                  <label className="block"><span className="text-ink-2">Title</span>
                    <input value={form.title} maxLength={120} onChange={(e) => setForm({ ...form, title: e.target.value })} className="mt-1 w-full rounded-md border border-line px-2 py-1.5" /></label>
                  <label className="block"><span className="text-ink-2">Description</span>
                    <textarea value={form.description} maxLength={500} rows={2} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1 w-full rounded-md border border-line px-2 py-1.5" /></label>
                  <label className="block"><span className="text-ink-2">Layout</span>
                    <select value={form.layout} onChange={(e) => setForm({ ...form, layout: e.target.value })} className="mt-1 w-full rounded-md border border-line px-2 py-1.5">
                      <option value="executive_overview">Executive overview (two columns)</option>
                      <option value="detailed_grid">Detailed (one column)</option>
                    </select></label>
                  <fieldset>
                    <legend className="text-ink-2">Charts</legend>
                    <ul className="mt-1 space-y-1.5">
                      {draft.spec.visualizations.map((v) => (
                        <li key={v.id} className={`flex items-center gap-2 ${form.remove.includes(v.id) ? "opacity-40" : ""}`}>
                          <span className="flex-1 truncate" title={v.title}>{v.title}</span>
                          {v.type === "kpi_card" ? <span className="text-ink-3 text-xs">KPI card</span> : (
                            <select value={form.charts[v.id]} onChange={(e) => setForm({ ...form, charts: { ...form.charts, [v.id]: e.target.value } })} className="rounded border border-line px-1 py-0.5 text-xs">
                              {["line", "bar", "table"].map((t) => <option key={t} value={t}>{CHART_NAMES[t]}</option>)}
                            </select>
                          )}
                          <button type="button" className="text-xs text-danger" onClick={() => setForm({ ...form, remove: form.remove.includes(v.id) ? form.remove.filter((x) => x !== v.id) : [...form.remove, v.id] })}>
                            {form.remove.includes(v.id) ? "Keep" : "Remove"}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </fieldset>
                  <div className="flex gap-2 pt-1">
                    <Button disabled={!!busy} onClick={saveEdit}>{busy === "save" ? "Saving…" : "Save new version"}</Button>
                    <Button variant="secondary" onClick={() => setEditing(false)}>Cancel</Button>
                  </div>
                </div>
              )}
            </section>
          )}

          <section className="rounded-lg border border-line bg-surface p-5 text-sm">
            <h2 className="font-semibold">Version history</h2>
            <ul className="mt-2 space-y-1">
              {draft.versions.map((v) => (
                <li key={v.version}><span className="num font-medium">v{v.version}</span> {v.change_summary} <span className="text-ink-3">{formatTime(v.created_at)}</span></li>
              ))}
            </ul>
            {isOwner && !["Archived", "Publishing"].includes(draft.status) && (
              <button className="mt-3 text-sm font-semibold text-teal hover:underline underline-offset-2" onClick={() => act("archive", () => api(`/api/v1/dashboards/${id}/archive`, { body: {} }))}>Archive this dashboard</button>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
