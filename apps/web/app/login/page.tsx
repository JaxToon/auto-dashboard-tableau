"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/components/Shell";

type MockUser = { user_id: string; display_name: string; roles: string[] };

const ROLE_HELP: Record<string, string> = {
  business_user: "Asks questions, builds drafts, publishes approved dashboards.",
  ba_reviewer: "Reviews KPI meaning and approves drafts. Sees the restricted deposit dataset.",
  data_steward: "Reads the full catalog.",
  admin: "Tests the Tableau connection and controls feature switches. No data access.",
  auditor: "Reads every audit event.",
};

export default function LoginPage() {
  const router = useRouter();
  const { refresh } = useSession();
  const [users, setUsers] = useState<MockUser[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ users: MockUser[] }>("/api/v1/auth/mock-users")
      .then((r) => setUsers(r.users))
      .catch((e: ApiError) => setError(e.status === 404 ? "Mock login is turned off. Sign in through your organisation's single sign-on." : e.message));
  }, []);

  async function signIn(id: string) {
    try {
      await api("/api/v1/auth/mock-login", { body: { user_id: id } });
      await refresh();
      router.replace("/");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-12">
      <h1 className="display text-3xl font-bold">Proactive Business Analytics Copilot</h1>
      <p className="mt-3 text-ink-2 max-w-prose">
        Ask a business question, review the dashboard it proposes, and publish it to Tableau once a Business Analytics reviewer approves.
      </p>
      <div className="mt-8 rounded-lg border-2 border-dashed border-signal bg-signal-tint px-4 py-3 text-sm">
        <strong>Mock login.</strong> For isolated local development only. A pilot uses the bank's approved single sign-on and role mapping.
      </div>
      {error && <p className="mt-6 text-danger">{error}</p>}
      <h2 className="mt-8 text-sm font-semibold text-ink-2">Sign in as</h2>
      <ul className="mt-2 divide-y divide-line border-y border-line">
        {users.map((u) => (
          <li key={u.user_id}>
            <button onClick={() => signIn(u.user_id)} className="w-full text-left py-3 px-2 hover:bg-teal-tint flex flex-col sm:flex-row sm:items-baseline sm:gap-4">
              <span className="font-semibold sm:w-56">{u.display_name}</span>
              <span className="text-sm text-ink-2">{ROLE_HELP[u.roles[0]]}</span>
            </button>
          </li>
        ))}
      </ul>
    </main>
  );
}
