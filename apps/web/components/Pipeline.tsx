"use client";

export const STAGES = ["Understanding request", "Checking definitions", "Building", "Validating", "Publishing"] as const;
export type Stage = (typeof STAGES)[number];

/** A real sequence, so the steps are numbered. `current` is the active step; earlier steps are complete. */
export function Pipeline({ current, done, failedAt, note }: { current: number; done?: boolean; failedAt?: number; note?: string }) {
  return (
    <div>
      <ol className="grid grid-cols-5 gap-1" aria-label="Progress">
        {STAGES.map((s, i) => {
          const state = failedAt === i ? "failed" : done || i < current ? "complete" : i === current ? "active" : "upcoming";
          return (
            <li key={s} aria-current={state === "active" ? "step" : undefined} className="min-w-0">
              <div
                className={`h-1.5 rounded-full ${
                  state === "complete" ? "bg-teal" : state === "active" ? "bg-teal step-active" : state === "failed" ? "bg-danger" : "bg-line"
                }`}
              />
              <div className={`mt-1.5 text-[11px] sm:text-xs leading-tight ${state === "upcoming" ? "text-ink-3" : state === "failed" ? "text-danger" : "text-ink"}`}>
                <span className="num font-semibold mr-1">{i + 1}</span>
                <span className="hidden sm:inline">{s}</span>
              </div>
            </li>
          );
        })}
      </ol>
      {note && <p className="mt-2 text-xs text-ink-3">{note}</p>}
    </div>
  );
}
