"use client";

import { ApiError } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  Draft: "bg-line-2 text-ink-2",
  "Needs Review": "bg-warn-tint text-warn",
  "Ready to Publish": "bg-teal-tint text-teal-dark",
  Publishing: "bg-teal-tint text-teal-dark",
  Published: "bg-ok-tint text-ok",
  Failed: "bg-danger-tint text-danger",
  Archived: "bg-line-2 text-ink-3",
  MockPublished: "bg-signal-tint text-signal",
  candidate: "bg-warn-tint text-warn",
  accepted: "bg-teal-tint text-teal-dark",
  converted: "bg-ok-tint text-ok",
  dismissed: "bg-line-2 text-ink-3",
};

export function StatusPill({ status }: { status: string }) {
  const label = status === "MockPublished" ? "Mock publish (not real)" : status;
  return <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap ${STATUS_STYLE[status] ?? "bg-line-2"}`}>{label}</span>;
}

export function Button({ variant = "primary", className = "", ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "quiet" | "danger" }) {
  const styles = {
    primary: "bg-teal text-white hover:bg-teal-dark disabled:bg-line disabled:text-ink-3",
    secondary: "bg-surface text-ink border border-line hover:border-teal disabled:text-ink-3",
    quiet: "text-teal hover:underline underline-offset-2 disabled:text-ink-3",
    danger: "bg-surface text-danger border border-danger/40 hover:bg-danger-tint",
  }[variant];
  return <button {...props} className={`rounded-md px-3.5 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed ${styles} ${className}`} />;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  const blockers = (e.details?.blockers as string[] | undefined) ?? [];
  const fields = (e.details?.errors as { message: string }[] | undefined) ?? [];
  return (
    <div role="alert" className="rounded-md border border-danger/30 bg-danger-tint px-4 py-3 text-sm text-danger">
      <div className="font-semibold">{e.message}</div>
      {[...blockers, ...fields.map((f) => f.message)].length > 1 && (
        <ul className="mt-1 list-disc pl-5">
          {[...blockers, ...fields.map((f) => f.message)].map((b) => (
            <li key={b}>{b}</li>
          ))}
        </ul>
      )}
      {e.requestId && <div className="mt-1 text-xs opacity-70">Reference: {e.requestId}</div>}
    </div>
  );
}

export function PageHeader({ title, intro, children }: { title: string; intro?: string; children?: React.ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
      <div>
        <h1 className="display text-2xl md:text-[28px] font-bold leading-tight">{title}</h1>
        {intro && <p className="mt-1.5 text-ink-2 max-w-prose">{intro}</p>}
      </div>
      {children}
    </header>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line px-6 py-10 text-center text-ink-2">{children}</div>;
}
