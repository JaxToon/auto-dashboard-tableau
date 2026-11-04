"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { formatPeriod, formatTime } from "@/lib/format";
import { can, useSession } from "@/components/Shell";
import { Pipeline } from "@/components/Pipeline";
import { DashboardPreview, VizResult } from "@/components/Charts";
import { Button, ErrorNote, StatusPill } from "@/components/ui";

type Dataset = { id: string; name: string; description: string; example_prompts: string[]; freshness: { latest_period: string } };
type Interp = {
  status: "ok" | "needs_clarification" | "unsupported";
  message: string;
  flags: string[];
  interpretation_id?: string;
  intent?: { output_decision: string; decision_reason: string; assumptions: string[]; summary: string; top_n: number | null; top_order: string };
  clarification?: { id: string; question: string; options: { value: string; label: string; description: string }[] };
  resolved?: {
    dataset: { id: string; name: string; version: string };
    metrics: { id: string; name: string; definition: string; formula_display: string; version: string }[];
    dimensions: { id: string; name: string }[];
    filters: { name: string; values: string[] }[];
    period: { label: string; period_type: string };
    time_grain: string | null;
  };
  quick_result?: { results: VizResult[]; answer_text?: string };
};
type Draft = { id: string; title: string; status: string; dataset_name: string; updated_at: string; validation_report?: { valid: boolean; warnings: string[] } };

const DECISION_COPY: Record<string, string> = {
  ANSWER_ONLY: "Direct answer",
  CHART_OR_ANALYSIS: "Single chart",
  DASHBOARD_RECOMMENDATION: "Dashboard",
};

export default function AskPage() {
  const { me } = useSession();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [question, setQuestion] = useState("");
  const [clar, setClar] = useState<Record<string, string>>({});
  const [interp, setInterp] = useState<Interp | null>(null);
  const [stage, setStage] = useState(-1);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [recent, setRecent] = useState<Draft[]>([]);
  const [error, setError] = useState<unknown>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<{ datasets: Dataset[] }>("/api/v1/catalog/datasets").then((r) => setDatasets(r.datasets)).catch(setError);
    if (can(me, "dashboard:create")) api<{ dashboards: Draft[] }>("/api/v1/dashboards").then((r) => setRecent(r.dashboards.slice(0, 5))).catch(() => {});
  }, [me]);

  const examples = datasets.filter((d) => !datasetId || d.id === datasetId).flatMap((d) => d.example_prompts);

  async function ask(q = question, clarifications = clar) {
    if (!q.trim()) return;
    setError(null);
    setDraft(null);
    setStage(0);
    try {
      const r = await api<Interp>("/api/v1/analytics/interpret", { body: { question: q, dataset_id: datasetId || null, clarifications } });
      setStage(r.status === "ok" ? 1 : 0);
      setInterp(r);
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    } catch (e) {
      setError(e);
      setStage(-1);
    }
  }

  async function build(force: boolean) {
    if (!interp?.interpretation_id) return;
    setError(null);
    try {
      setStage(2);
      const d = await api<Draft>("/api/v1/dashboards/plan", { body: { interpretation_id: interp.interpretation_id, force_dashboard: force } });
      setStage(3);
      const v = await api<{ draft: Draft }>(`/api/v1/dashboards/${d.id}/validate`, { body: {} });
      setDraft(v.draft);
      setStage(4);
      setRecent((prev) => [v.draft, ...prev.filter((x) => x.id !== d.id)].slice(0, 5));
    } catch (e) {
      setError(e);
    }
  }

  function pickClarification(id: string, value: string) {
    const next = { ...clar, [id]: value };
    setClar(next);
    ask(question, next);
  }

  function reset(q: string) {
    setQuestion(q);
    setClar({});
    setInterp(null);
    setDraft(null);
    setStage(-1);
  }

  if (me && !can(me, "analytics:ask")) {
    return (
      <div className="max-w-prose">
        <h1 className="display text-2xl font-bold">Ask & Build</h1>
        <p className="mt-2 text-ink-2">Your role does not ask questions of the data. Use the menu to reach the pages your role can use.</p>
      </div>
    );
  }

  const r = interp?.resolved;
  return (
    <div className="max-w-6xl">
      <section className="max-w-3xl">
        <h1 className="display text-3xl md:text-[40px] font-bold leading-[1.1]">What would you like to understand?</h1>
        <p className="mt-3 text-ink-2 max-w-prose">
          Ask in Bahasa Indonesia or English. The answer uses only approved datasets and KPI definitions. Dashboards stay drafts until a reviewer approves them and you publish.
        </p>
        <form
          className="mt-6"
          onSubmit={(e) => {
            e.preventDefault();
            setClar({});
            ask(question, {});
          }}
        >
          <label htmlFor="q" className="sr-only">Your business question</label>
          <textarea
            id="q"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                setClar({});
                ask(question, {});
              }
            }}
            maxLength={1000}
            rows={3}
            placeholder="e.g. Tampilkan tren pertumbuhan kredit SME per bulan, bandingkan antar-region"
            className="w-full resize-y rounded-lg border-2 border-ink/80 bg-surface px-4 py-3 text-lg leading-snug placeholder:text-ink-3 focus:border-teal focus:outline-none"
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={!question.trim() || stage === 0}>Ask</Button>
            <label className="flex items-center gap-2 text-sm text-ink-2">
              Dataset
              <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)} className="rounded-md border border-line bg-surface px-2 py-1.5 text-ink">
                <option value="">Choose for me</option>
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            </label>
            <span className="text-xs text-ink-3 num">{question.length}/1000</span>
          </div>
        </form>

        {!interp && examples.length > 0 && (
          <div className="mt-8">
            <h2 className="text-sm font-semibold text-ink-2">Try a question from the approved catalog</h2>
            <ul className="mt-2 space-y-1">
              {examples.map((q) => (
                <li key={q}>
                  <button onClick={() => reset(q)} className="text-left text-[15px] text-teal hover:underline underline-offset-2">{q}</button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {(stage >= 0 || interp) && (
        <section ref={resultRef} className="mt-10 scroll-mt-12">
          <Pipeline current={Math.max(stage, 0)} done={false} note={stage >= 4 ? "Step 5 happens on the review page after approval." : undefined} />
        </section>
      )}

      <div className="mt-6 space-y-4 max-w-4xl">
        <ErrorNote error={error} />

        {interp?.status === "unsupported" && (
          <div className="rounded-lg border-l-4 border-danger bg-surface px-5 py-4">
            <div className="font-semibold">This question can't be answered with approved data</div>
            <p className="mt-1 text-ink-2">{interp.message}</p>
          </div>
        )}

        {interp?.status === "needs_clarification" && interp.clarification && (
          <div className="rounded-lg border-l-4 border-warn bg-surface px-5 py-4">
            <div className="font-semibold">{interp.clarification.question}</div>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {interp.clarification.options.map((o) => (
                <button key={o.value} onClick={() => pickClarification(interp.clarification!.id, o.value)} className="text-left rounded-md border border-line px-3 py-2 hover:border-teal hover:bg-teal-tint">
                  <div className="font-medium">{o.label}</div>
                  <div className="text-xs text-ink-2 mt-0.5">{o.description}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {interp?.status === "ok" && r && interp.intent && (
          <div className="rounded-lg bg-surface border border-line">
            <div className="px-5 py-4 border-b border-line">
              <h2 className="text-sm font-semibold text-ink-2">How the question was read</h2>
              <p className="mt-1 text-[17px] leading-snug">{interp.intent.summary}</p>
            </div>
            <dl className="grid sm:grid-cols-2 gap-x-8 gap-y-3 px-5 py-4 text-sm">
              <div>
                <dt className="text-ink-3">Dataset</dt>
                <dd className="font-medium">{r.dataset.name} <span className="text-ink-3 font-normal">v{r.dataset.version}</span></dd>
              </div>
              <div>
                <dt className="text-ink-3">Period</dt>
                <dd className="font-medium">{r.period.label}{r.time_grain ? `, by ${r.time_grain}` : ""}</dd>
              </div>
              <div>
                <dt className="text-ink-3">Breakdown</dt>
                <dd className="font-medium">{r.dimensions.map((d) => d.name).join(", ") || "None"}{interp.intent.top_n ? ` (${interp.intent.top_order === "asc" ? "bottom" : "top"} ${interp.intent.top_n} highlighted)` : ""}</dd>
              </div>
              <div>
                <dt className="text-ink-3">Filters</dt>
                <dd className="font-medium">{r.filters.map((f) => `${f.name}: ${f.values.join(", ")}`).join("; ") || "None"}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-ink-3">KPI definitions used</dt>
                <dd>
                  <ul className="mt-1 space-y-1.5">
                    {r.metrics.map((m) => (
                      <li key={m.id}>
                        <span className="font-medium">{m.name}</span> <span className="text-ink-3">v{m.version}</span>
                        <div className="text-ink-2">{m.definition} <span className="text-ink-3">{m.formula_display}</span></div>
                      </li>
                    ))}
                  </ul>
                </dd>
              </div>
            </dl>
            {(interp.intent.assumptions.length > 0 || interp.flags.length > 0) && (
              <ul className="mx-5 mb-4 rounded-md bg-warn-tint px-4 py-2 text-sm text-warn space-y-0.5">
                {interp.intent.assumptions.map((a) => (
                  <li key={a}>{a}</li>
                ))}
              </ul>
            )}
            <div className="px-5 py-4 border-t border-line bg-paper/60 rounded-b-lg">
              <div className="text-sm">
                <span className="font-semibold">Recommended output: {DECISION_COPY[interp.intent.output_decision]}.</span>{" "}
                <span className="text-ink-2">{interp.intent.decision_reason}</span>
              </div>
              {interp.quick_result?.answer_text && <p className="display mt-3 text-2xl font-bold num">{interp.quick_result.answer_text}</p>}
              {interp.quick_result && !interp.quick_result.answer_text && (
                <div className="mt-4"><DashboardPreview results={interp.quick_result.results} /></div>
              )}
              {can(me, "dashboard:create") && !draft && (
                <div className="mt-4 flex flex-wrap gap-3">
                  {interp.intent.output_decision === "DASHBOARD_RECOMMENDATION" ? (
                    <Button onClick={() => build(false)} disabled={stage >= 2}>Build dashboard draft</Button>
                  ) : (
                    <Button variant="secondary" onClick={() => build(true)} disabled={stage >= 2}>Build a dashboard instead</Button>
                  )}
                  <Button variant="quiet" onClick={() => document.getElementById("q")?.focus()}>Rephrase the question</Button>
                </div>
              )}
            </div>
          </div>
        )}

        {draft && (
          <div className="rounded-lg border-2 border-teal bg-surface px-5 py-4 flex flex-col sm:flex-row sm:items-center gap-4 justify-between">
            <div>
              <div className="flex items-center gap-2"><StatusPill status={draft.status} /><span className="text-xs text-ink-3">Validated draft</span></div>
              <div className="display mt-1 text-lg font-bold">{draft.title}</div>
              <div className="text-sm text-ink-2">{draft.dataset_name}</div>
            </div>
            <Link href={`/dashboards/${draft.id}`} className="rounded-md bg-teal px-4 py-2 text-sm font-semibold text-white hover:bg-teal-dark text-center">Review dashboard</Link>
          </div>
        )}
      </div>

      {recent.length > 0 && (
        <section className="mt-14 max-w-4xl">
          <h2 className="text-sm font-semibold text-ink-2">Your recent drafts</h2>
          <ul className="mt-2 divide-y divide-line border-y border-line">
            {recent.map((d) => (
              <li key={d.id}>
                <Link href={`/dashboards/${d.id}`} className="flex items-center justify-between gap-4 py-2.5 hover:bg-surface px-1">
                  <span className="truncate">{d.title}</span>
                  <span className="flex items-center gap-3 shrink-0 text-xs text-ink-3">{formatTime(d.updated_at)}<StatusPill status={d.status} /></span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="sr-only">{datasets[0] ? `Latest data: ${formatPeriod(datasets[0].freshness.latest_period)}` : ""}</p>
    </div>
  );
}
