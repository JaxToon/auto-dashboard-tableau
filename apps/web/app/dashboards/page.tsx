"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { can, useSession } from "@/components/Shell";
import { Empty, ErrorNote, PageHeader, StatusPill } from "@/components/ui";

type Row = {
  id: string;
  title: string;
  status: string;
  owner_id: string;
  dataset_name: string;
  catalog_version: string;
  current_version: number;
  created_at: string;
  target_project_id: string | null;
  verified_url: string | null;
  publications: { status: string }[];
};

export default function DashboardsPage() {
  const { me } = useSession();
  const reviewer = can(me, "dashboard:review");
  const [scope, setScope] = useState<"mine" | "review">("mine");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    setRows(null);
    api<{ dashboards: Row[] }>(`/api/v1/dashboards?scope=${scope}`).then((r) => setRows(r.dashboards)).catch(setError);
  }, [scope]);

  return (
    <div className="max-w-6xl">
      <PageHeader title={scope === "mine" ? "My Dashboards" : "Review queue"} intro="Drafts, review status and verified Tableau links.">
        {reviewer && (
          <div className="flex rounded-md border border-line bg-surface p-0.5 text-sm" role="tablist">
            {(["mine", "review"] as const).map((s) => (
              <button key={s} role="tab" aria-selected={scope === s} onClick={() => setScope(s)} className={`rounded px-3 py-1.5 ${scope === s ? "bg-ink text-white" : "text-ink-2"}`}>
                {s === "mine" ? "My drafts" : "Submitted for review"}
              </button>
            ))}
          </div>
        )}
      </PageHeader>
      <ErrorNote error={error} />
      {rows && rows.length === 0 && (
        <Empty>{scope === "mine" ? <>No dashboards yet. <Link href="/" className="text-teal underline">Ask a question</Link> to create your first draft.</> : "Nothing is waiting for review."}</Empty>
      )}
      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-ink-2">
                <th className="px-4 py-2.5 font-medium">Dashboard</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Created by</th>
                <th className="px-4 py-2.5 font-medium">Created</th>
                <th className="px-4 py-2.5 font-medium">Dataset / KPI catalog</th>
                <th className="px-4 py-2.5 font-medium">Tableau</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id} className="border-b border-line-2 last:border-0 hover:bg-paper/60">
                  <td className="px-4 py-3">
                    <Link href={`/dashboards/${d.id}`} className="font-semibold text-ink hover:text-teal">{d.title}</Link>
                    <div className="text-xs text-ink-3 num">Version {d.current_version}</div>
                  </td>
                  <td className="px-4 py-3"><StatusPill status={d.status} /></td>
                  <td className="px-4 py-3 text-ink-2">{d.owner_id}</td>
                  <td className="px-4 py-3 text-ink-2 whitespace-nowrap">{formatTime(d.created_at)}</td>
                  <td className="px-4 py-3 text-ink-2">{d.dataset_name}<div className="text-xs text-ink-3">{d.catalog_version}</div></td>
                  <td className="px-4 py-3">
                    {d.verified_url ? (
                      <a href={d.verified_url} target="_blank" rel="noopener noreferrer" className="text-teal font-medium hover:underline">Open in Tableau</a>
                    ) : d.publications[0] ? (
                      <StatusPill status={d.publications[0].status} />
                    ) : (
                      <span className="text-ink-3">{d.target_project_id ? "Not published" : "Not configured"}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
