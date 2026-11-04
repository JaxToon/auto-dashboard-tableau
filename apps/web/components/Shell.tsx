"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export type Me = {
  user: { user_id: string; display_name: string; roles: string[]; permissions: string[]; is_mock: boolean };
  app: { env: string; mode: string; synthetic: boolean; auth_mode: string; llm_provider: string; tableau_mode: string; flags: Record<string, boolean> };
};

const SessionCtx = createContext<{ me: Me | null; refresh: () => Promise<void> }>({ me: null, refresh: async () => {} });
export const useSession = () => useContext(SessionCtx);
export const can = (me: Me | null, perm: string) => !!me?.user.permissions.includes(perm);

const NAV = [
  { href: "/", label: "Ask & Build", perm: "analytics:ask" },
  { href: "/dashboards", label: "My Dashboards", perm: "dashboard:create" },
  { href: "/recommendations", label: "Recommendations", perm: "recommendation:view" },
  { href: "/catalog", label: "Data & Metrics", perm: null },
  { href: "/activity", label: "Activity", perm: null },
  { href: "/admin", label: "Admin", perm: "admin:settings" },
  { href: "/audit", label: "Audit", perm: "audit:view_all" },
];

const ROLE_NAMES: Record<string, string> = {
  business_user: "Business User",
  ba_reviewer: "BA Reviewer",
  data_steward: "Data Steward",
  admin: "PoC Admin",
  auditor: "Auditor",
};

export function SyntheticBand() {
  return (
    <div className="synthetic-band text-white text-xs font-semibold tracking-wide px-4 py-1 flex items-center justify-between gap-4" role="status">
      <span>POC / SYNTHETIC DATA</span>
      <span className="font-normal hidden sm:inline">Values are generated for demonstration and are not real bank performance.</span>
    </div>
  );
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const isLogin = pathname === "/login";

  const refresh = useCallback(async () => {
    try {
      setMe(await api<Me>("/api/v1/me"));
    } catch (e) {
      setMe(null);
      if (e instanceof ApiError && e.status === 401 && !isLogin) router.replace("/login");
    } finally {
      setLoading(false);
    }
  }, [isLogin, router]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  if (isLogin) {
    return (
      <SessionCtx.Provider value={{ me, refresh }}>
        <SyntheticBand />
        {children}
      </SessionCtx.Provider>
    );
  }

  async function logout() {
    await api("/api/v1/auth/logout", { method: "POST", body: {} });
    router.replace("/login");
  }

  return (
    <SessionCtx.Provider value={{ me, refresh }}>
      <div className="sticky top-0 z-30">
        <SyntheticBand />
      </div>
      <div className="flex min-h-[calc(100vh-24px)] flex-col md:flex-row">
        <aside className="md:w-60 md:shrink-0 bg-ink text-white md:min-h-full">
          <div className="px-5 pt-5 pb-4">
            <div className="display text-[15px] font-bold leading-tight">Proactive Business Analytics Copilot</div>
            <div className="text-xs text-white/60 mt-1">Business Analytics PoC</div>
            {me && (
              <div className="md:hidden mt-2 flex items-center gap-3 text-xs text-white/80">
                <span>{me.user.display_name}{me.user.is_mock ? " (mock login)" : ""}</span>
                <button onClick={logout} className="underline underline-offset-2">Switch user</button>
              </div>
            )}
          </div>
          <nav aria-label="Main" className="flex md:flex-col gap-0.5 overflow-x-auto px-2 pb-2">
            {NAV.filter((n) => !n.perm || can(me, n.perm)).map((n) => {
              const active = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className={`whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors ${active ? "bg-white text-ink font-semibold" : "text-white/80 hover:bg-white/10"}`}
                >
                  {n.label}
                </Link>
              );
            })}
          </nav>
          {me && (
            <div className="hidden md:block mx-4 mt-6 border-t border-white/15 pt-4 text-xs text-white/70 space-y-1">
              <div className="text-white text-sm font-medium">{me.user.display_name}</div>
              <div>{me.user.roles.map((r) => ROLE_NAMES[r] ?? r).join(", ")}</div>
              {me.user.is_mock && <div className="inline-block mt-1 rounded bg-signal px-1.5 py-0.5 text-[11px] font-semibold text-white">Mock login</div>}
              <div className="pt-2 space-y-0.5">
                <div>Planner: {me.app.llm_provider === "mock-rules" ? "rules-based (no external AI)" : me.app.llm_provider}</div>
                <div>Tableau: {me.app.tableau_mode === "rest" ? "real REST API" : me.app.tableau_mode === "mock" ? "MOCK (not real)" : "not configured"}</div>
              </div>
              <button onClick={logout} className="mt-3 text-white underline underline-offset-2 hover:text-white/80">
                Switch user
              </button>
            </div>
          )}
        </aside>
        <main className="flex-1 min-w-0 px-4 py-6 md:px-10 md:py-8">{loading && !me ? <p className="text-ink-3">Loading…</p> : children}</main>
      </div>
    </SessionCtx.Provider>
  );
}
