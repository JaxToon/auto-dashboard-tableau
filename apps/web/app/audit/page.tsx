"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AuditEvent, EventTable } from "@/components/EventTable";
import { ErrorNote, PageHeader } from "@/components/ui";

const FILTERS = [
  { value: "", label: "All events" },
  { value: "dashboard.publish", label: "Publishing" },
  { value: "dashboard.review", label: "Reviews" },
  { value: "authz", label: "Access denied" },
  { value: "admin", label: "Settings changes" },
];

export default function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    api<{ events: AuditEvent[] }>(`/api/v1/audit/events?limit=300${filter ? `&action=${filter}` : ""}`).then((r) => setEvents(r.events)).catch(setError);
  }, [filter]);
  return (
    <div className="max-w-7xl">
      <PageHeader title="Audit trail" intro="Security and lifecycle events with actor, time, request reference and policy. Details are minimized: no question text, result values or credentials.">
        <select value={filter} onChange={(e) => setFilter(e.target.value)} className="rounded-md border border-line bg-surface px-2 py-1.5 text-sm" aria-label="Filter events">
          {FILTERS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
        </select>
      </PageHeader>
      <ErrorNote error={error} />
      <EventTable events={events} showActor technical />
    </div>
  );
}
