"use client";

import { formatTime } from "@/lib/format";

export type AuditEvent = {
  id: string; timestamp: string; request_id: string; actor_id: string; action: string; resource_type: string; resource_id: string;
  status: string; policy_ref: string; details: Record<string, unknown>;
};

const ACTION_NAMES: Record<string, string> = {
  "analytics.interpret": "Asked a question",
  "dashboard.plan": "Created a draft",
  "dashboard.validate": "Validated",
  "dashboard.preview": "Previewed",
  "dashboard.edit": "Edited",
  "dashboard.submit_review": "Submitted for review",
  "dashboard.review.approved": "Approved",
  "dashboard.review.changes_requested": "Requested changes",
  "dashboard.publish.attempt": "Started publishing",
  "dashboard.publish.success": "Published to Tableau",
  "dashboard.publish.failure": "Publishing failed",
  "dashboard.publish.mock": "Mock publish (not real)",
  "dashboard.archive": "Archived",
  "recommendation.analyze": "Analyzed recurring questions",
  "recommendation.accept": "Accepted a recommendation",
  "recommendation.dismiss": "Dismissed a recommendation",
  "recommendation.convert": "Created a draft from a recommendation",
  "authz.denied": "Access denied",
  "auth.login": "Signed in",
  "admin.settings_change": "Changed settings",
  "tableau.test": "Tested Tableau connection",
};

export function EventTable({ events, showActor, technical }: { events: AuditEvent[]; showActor?: boolean; technical?: boolean }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-ink-2">
            <th className="px-4 py-2.5 font-medium">When</th>
            {showActor && <th className="px-4 py-2.5 font-medium">Actor</th>}
            <th className="px-4 py-2.5 font-medium">What happened</th>
            <th className="px-4 py-2.5 font-medium">Result</th>
            <th className="px-4 py-2.5 font-medium">Detail</th>
            {technical && <th className="px-4 py-2.5 font-medium">Request</th>}
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="border-b border-line-2 last:border-0 align-top">
              <td className="px-4 py-2 whitespace-nowrap text-ink-2">{formatTime(e.timestamp)}</td>
              {showActor && <td className="px-4 py-2">{e.actor_id}</td>}
              <td className="px-4 py-2">{ACTION_NAMES[e.action] ?? e.action}{technical && <div className="text-xs text-ink-3">{e.action}{e.policy_ref ? `, policy ${e.policy_ref}` : ""}</div>}</td>
              <td className={`px-4 py-2 ${e.status === "success" ? "text-ok" : e.status === "denied" ? "text-danger" : "text-warn"}`}>{e.status}</td>
              <td className="px-4 py-2 text-ink-2 max-w-md">
                {typeof e.details.summary === "string" ? e.details.summary : e.resource_type ? `${e.resource_type} ${e.resource_id.slice(0, 8)}` : ""}
                {technical && Object.keys(e.details).length > 0 && <div className="text-xs text-ink-3 break-all">{JSON.stringify(e.details)}</div>}
              </td>
              {technical && <td className="px-4 py-2 text-xs text-ink-3">{e.request_id.slice(0, 12)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
