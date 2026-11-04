"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { can, useSession } from "@/components/Shell";
import { Button, Empty, ErrorNote, PageHeader, StatusPill } from "@/components/ui";

type Rule = { ok: boolean; value?: number; threshold?: number; note?: string };
type Rec = {
  id: string;
  topic: string;
  dataset_name: string;
  metrics: { id: string; name: string }[];
  dimensions: { id: string; name: string }[];
  period_type: string;
  frequency_summary: string;
  rationale: string;
  evaluation: Record<string, Rule>;
  status: string;
  simulated: boolean;
  linked_draft_id: string | null;
};
type Cluster = { cluster_key: string; topic: string; count: number; qualifies: boolean; rules: Record<string, Rule>; simulated: boolean };

const RULE_NAMES: Record<string, string> = {
  min_events: "Similar questions",
  audience_diversity: "Different groups asking",
  time_recurrence: "Weeks with questions",
  stable_approved_kpis: "Uses approved KPIs",
  dashboard_suitable: "Suits a dashboard",
};

function RuleList({ rules }: { rules: Record<string, Rule> }) {
  return (
    <ul className="grid sm:grid-cols-2 gap-x-6 gap-y-1 text-sm">
      {Object.entries(rules).map(([k, r]) => (
        <li key={k} className={r.ok ? "text-ink" : "text-ink-3"}>
          <span className={r.ok ? "text-ok" : "text-danger"}>{r.ok ? "Met" : "Not met"}</span>: {RULE_NAMES[k] ?? k}
          {r.value !== undefined && <span className="num"> ({r.value} of {r.threshold} needed)</span>}
        </li>
      ))}
    </ul>
  );
}

export default function RecommendationsPage() {
  const { me } = useSession();
  const router = useRouter();
  const reviewer = can(me, "recommendation:review");
  const [recs, setRecs] = useState<Rec[] | null>(null);
  const [analysis, setAnalysis] = useState<{ thresholds: Record<string, number>; clusters: Cluster[] } | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => api<{ recommendations: Rec[] }>("/api/v1/recommendations").then((r) => setRecs(r.recommendations)).catch(setError), []);
  useEffect(() => {
    load();
  }, [load]);

  async function analyze() {
    setError(null);
    try {
      setAnalysis(await api("/api/v1/recommendations/analyze", { body: {} }));
      await load();
    } catch (e) {
      setError(e);
    }
  }

  async function decide(id: string, decision: string) {
    setError(null);
    try {
      const r = await api<{ draft_id?: string }>(`/api/v1/recommendations/${id}/decision`, { body: { decision } });
      if (r.draft_id) router.push(`/dashboards/${r.draft_id}`);
      else await load();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="max-w-5xl">
      <PageHeader
        title="Recommendations"
        intro="Questions this workspace asks again and again, and which ones may deserve a reusable dashboard. Built from normalized question patterns only: no question text and no names are kept."
      >
        {reviewer && <Button onClick={analyze}>Find recurring questions</Button>}
      </PageHeader>
      <div className="mb-5 rounded-md border-2 border-dashed border-signal bg-signal-tint px-4 py-2 text-sm">
        The question history on this page is <strong>simulated</strong> for the demo, plus any questions asked in this local app.
      </div>
      <ErrorNote error={error} />

      {recs && recs.length === 0 && (
        <Empty>{reviewer ? "No recommendations yet. Select Find recurring questions to analyze the last 30 days." : "No accepted recommendations for your datasets yet."}</Empty>
      )}

      <ul className="space-y-4">
        {recs?.map((r) => (
          <li key={r.id} className="rounded-lg border border-line bg-surface p-5">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={r.status} />
              {r.simulated && <span className="rounded bg-signal-tint px-2 py-0.5 text-xs font-semibold text-signal">Simulated events</span>}
              <span className="text-xs text-ink-3">{r.dataset_name}</span>
            </div>
            <h2 className="display mt-2 text-lg font-bold">{r.topic}</h2>
            <p className="mt-1 text-ink-2 max-w-prose">{r.rationale}</p>
            <p className="mt-2 text-sm"><span className="text-ink-3">KPIs:</span> {r.metrics.map((m) => m.name).join(", ")}<span className="text-ink-3 ml-3">Breakdown:</span> {r.dimensions.filter((d) => d.id !== "report_month").map((d) => d.name).join(", ") || "None"}{r.dimensions.some((d) => d.id === "report_month") ? ", monthly trend" : ""}</p>
            <p className="text-sm text-ink-3 num">{r.frequency_summary}</p>
            {open === r.id && <div className="mt-3 border-t border-line pt-3"><RuleList rules={r.evaluation} /></div>}
            <div className="mt-4 flex flex-wrap gap-2">
              {r.status !== "dismissed" && r.status !== "converted" && (reviewer || r.status === "accepted") && can(me, "dashboard:create") && (
                <Button onClick={() => decide(r.id, "create_draft")}>Create dashboard draft</Button>
              )}
              {r.linked_draft_id && <Button variant="secondary" onClick={() => router.push(`/dashboards/${r.linked_draft_id}`)}>Open draft</Button>}
              <Button variant="secondary" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "Hide reasons" : "Why recommended"}</Button>
              {reviewer && r.status === "candidate" && (
                <>
                  <Button variant="secondary" onClick={() => decide(r.id, "accept")}>Accept</Button>
                  <Button variant="danger" onClick={() => decide(r.id, "dismiss")}>Dismiss</Button>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>

      {analysis && (
        <section className="mt-10">
          <h2 className="font-semibold">All question patterns in the last {analysis.thresholds.window_days} days</h2>
          <p className="text-sm text-ink-2">Frequency alone is not enough: a pattern also needs different groups asking, repeats over several weeks, approved KPIs and a shape that suits a dashboard.</p>
          <ul className="mt-3 divide-y divide-line border-y border-line">
            {analysis.clusters.map((c) => (
              <li key={c.cluster_key} className="py-3">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-medium">{c.topic}</span>
                  <span className={`text-sm shrink-0 ${c.qualifies ? "text-ok" : "text-ink-3"}`}>{c.qualifies ? "Recommended" : "Not recommended"}</span>
                </div>
                <div className="mt-1"><RuleList rules={c.rules} /></div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
