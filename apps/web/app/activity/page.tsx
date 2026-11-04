"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AuditEvent, EventTable } from "@/components/EventTable";
import { Empty, ErrorNote, PageHeader } from "@/components/ui";

export default function ActivityPage() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    api<{ events: AuditEvent[] }>("/api/v1/activity").then((r) => setEvents(r.events)).catch(setError);
  }, []);
  return (
    <div className="max-w-6xl">
      <PageHeader title="Activity" intro="Your recent requests and dashboard actions. Questions are stored as their interpretation, not as the text you typed." />
      <ErrorNote error={error} />
      {events && events.length === 0 && <Empty>No activity yet.</Empty>}
      {events && events.length > 0 && <EventTable events={events} />}
    </div>
  );
}
