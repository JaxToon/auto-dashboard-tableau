"use client";

import { useEffect, useRef } from "react";
import { formatPeriod } from "@/lib/format";
import { Button } from "@/components/ui";

export type FilterField = { id: string; name: string; values: string[] };
export type ViewState = { filters: Record<string, string[]>; start: string; end: string };

function summary(selected: string[], all: string[]) {
  if (!selected.length || selected.length === all.length) return "All";
  if (selected.length <= 2) return selected.join(", ");
  return `${selected.length} selected`;
}

function MultiSelect({ field, selected, onChange }: { field: FilterField; selected: string[]; onChange: (v: string[]) => void }) {
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (ref.current?.open && !ref.current.contains(e.target as Node)) ref.current.open = false;
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const active = selected.length > 0 && selected.length < field.values.length;
  return (
    <details ref={ref} className="relative">
      <summary
        className={`list-none cursor-pointer rounded-md border px-3 py-1.5 text-sm whitespace-nowrap [&::-webkit-details-marker]:hidden ${
          active ? "border-teal bg-teal-tint text-teal-dark" : "border-line bg-surface"
        }`}
      >
        <span className="text-ink-2">{field.name}:</span> <span className="font-medium">{summary(selected, field.values)}</span>
        <span aria-hidden className="ml-1.5 text-ink-3">▾</span>
      </summary>
      <div className="absolute z-20 mt-1 w-64 max-h-80 overflow-y-auto rounded-md border border-line bg-surface p-2 shadow-lg">
        <div className="flex justify-between px-1 pb-1.5 text-xs">
          <button type="button" className="text-teal hover:underline" onClick={() => onChange([])}>All</button>
          <span className="text-ink-3">{selected.length ? `${selected.length} of ${field.values.length}` : "No filter"}</span>
        </div>
        {field.values.map((v) => (
          <label key={v} className="flex items-center gap-2 rounded px-1 py-1 text-sm hover:bg-paper">
            <input
              type="checkbox"
              className="accent-teal"
              checked={selected.includes(v)}
              onChange={(e) => onChange(e.target.checked ? [...selected, v] : selected.filter((x) => x !== v))}
            />
            {v}
          </label>
        ))}
      </div>
    </details>
  );
}

/** Viewer-side filters: change what the preview shows without changing the saved draft. */
export function FilterBar({
  fields,
  months,
  view,
  customized,
  loading,
  canSave,
  onChange,
  onReset,
  onSave,
}: {
  fields: FilterField[];
  months: string[];
  view: ViewState;
  customized: boolean;
  loading: boolean;
  canSave: boolean;
  onChange: (v: ViewState) => void;
  onReset: () => void;
  onSave: () => void;
}) {
  return (
    <section aria-label="Filters" className="mb-5 rounded-lg border border-line bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-ink-2">From</span>
          <select value={view.start} onChange={(e) => onChange({ ...view, start: e.target.value })} className="rounded-md border border-line bg-surface px-2 py-1.5">
            {months.map((m) => <option key={m} value={m} disabled={m > view.end}>{formatPeriod(m)}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-ink-2">to</span>
          <select value={view.end} onChange={(e) => onChange({ ...view, end: e.target.value })} className="rounded-md border border-line bg-surface px-2 py-1.5">
            {months.map((m) => <option key={m} value={m} disabled={m < view.start}>{formatPeriod(m)}</option>)}
          </select>
        </label>
        {fields.map((f) => (
          <MultiSelect key={f.id} field={f} selected={view.filters[f.id] ?? []} onChange={(v) => onChange({ ...view, filters: { ...view.filters, [f.id]: v } })} />
        ))}
        {loading && <span className="text-xs text-ink-3">Updating…</span>}
      </div>
      {customized && (
        <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line pt-3 text-sm">
          <span className="text-ink-2">You are viewing a custom selection. The saved dashboard has not changed.</span>
          <Button variant="secondary" onClick={onReset}>Reset to saved view</Button>
          {canSave && <Button onClick={onSave}>Save as dashboard filter</Button>}
          {canSave && <span className="text-xs text-ink-3">Saving creates a new version that needs validation and review again.</span>}
        </div>
      )}
    </section>
  );
}
