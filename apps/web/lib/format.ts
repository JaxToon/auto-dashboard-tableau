export function formatValue(value: number | null | undefined, unit?: string): string {
  if (value === null || value === undefined) return "–";
  if (unit === "IDR") {
    const abs = Math.abs(value);
    if (abs >= 1e12) return `Rp ${(value / 1e12).toFixed(2)} T`;
    if (abs >= 1e9) return `Rp ${(value / 1e9).toFixed(2)} M`;
    if (abs >= 1e6) return `Rp ${(value / 1e6).toFixed(2)} Jt`;
    return `Rp ${value.toLocaleString("en-US")}`;
  }
  if (unit === "%") return `${value.toFixed(2)}%`;
  return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
}

export function axisValue(value: number, unit?: string): string {
  if (unit === "IDR") {
    const abs = Math.abs(value);
    if (abs >= 1e12) return `${(value / 1e12).toFixed(1)} T`;
    if (abs >= 1e9) return `${(value / 1e9).toFixed(0)} M`;
    return `${(value / 1e6).toFixed(0)} Jt`;
  }
  if (unit === "%") return `${value.toFixed(1)}%`;
  return value.toLocaleString("en-US");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatPeriod(iso: string, grain = "month"): string {
  const [y, m] = iso.split("-").map(Number);
  return grain === "quarter" ? `Q${Math.floor((m - 1) / 3) + 1} ${y}` : `${MONTHS[m - 1]} ${y}`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function unitLabel(unit?: string): string {
  return unit === "IDR" ? "Rupiah" : unit === "%" ? "percent" : unit ?? "";
}
