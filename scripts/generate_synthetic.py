"""Generate the deterministic synthetic PoC datasets.

All values are SYNTHETIC. They are produced from closed-form formulas and an
integer hash (no `random` module), so the output is byte-identical on every
machine and Python version. No production data is used as input.

Usage:  python scripts/generate_synthetic.py
Writes: data/synthetic/demo_sme_credit.csv
        data/synthetic/demo_deposit_funding.csv
        data/synthetic/expected_values.json   (independently computed checks)
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "synthetic"

REGIONS = [
    "Jakarta",
    "Jawa Barat",
    "Jawa Tengah",
    "Jawa Timur",
    "Sumatera",
    "Kalimantan",
    "Sulawesi",
    "Bali & Nusa Tenggara",
]
# Monthly growth per region (synthetic, expressed in basis points per month).
REGION_GROWTH_BP = [95, 120, 80, 105, 140, 70, 160, 110]
REGION_SCALE = [1.60, 1.30, 1.00, 1.20, 0.90, 0.70, 0.60, 0.50]
SEGMENTS = ["Mikro", "Kecil", "Menengah"]
SEGMENT_SCALE = [0.55, 1.00, 1.85]
SEGMENT_AVG_LOAN = [45_000_000, 350_000_000, 2_400_000_000]
BRANCHES_PER_REGION = 3
DEPOSIT_PRODUCTS = ["Giro", "Tabungan", "Deposito"]
DEPOSIT_SCALE = [0.9, 1.4, 1.1]
DEPOSIT_AVG_BALANCE = [180_000_000, 12_000_000, 450_000_000]

MONTHS = [(2024 + m // 12, m % 12 + 1) for m in range(24)]  # 2024-01 .. 2025-12
MILLION = 1_000_000


def noise(*parts: int) -> float:
    """Deterministic pseudo-noise in [0, 1) from integer parts."""
    h = 2166136261
    for p in parts:
        h ^= (p + 0x9E3779B9) & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 13
    return (h % 10_000) / 10_000


def month_str(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}-01"


def sme_rows() -> list[dict]:
    rows = []
    for t, (y, m) in enumerate(MONTHS):
        season = 1 + 0.015 * math.sin(2 * math.pi * (m - 3) / 12)
        for r, region in enumerate(REGIONS):
            g = REGION_GROWTH_BP[r] / 10_000
            for b in range(BRANCHES_PER_REGION):
                branch = f"SYN-{r + 1:02d}{b + 1}"
                branch_scale = 0.8 + 0.4 * noise(r, b, 7)
                for s, segment in enumerate(SEGMENTS):
                    base = 120_000 * MILLION * REGION_SCALE[r] * SEGMENT_SCALE[s] * branch_scale
                    jitter = 1 + 0.02 * (noise(t, r, b, s) - 0.5)
                    outstanding = round(base * (1 + g) ** t * season * jitter / MILLION) * MILLION
                    target = round(base * 1.02 * (1.011) ** t / MILLION) * MILLION
                    disb = round(outstanding * (0.035 + 0.02 * noise(t, r, b, s, 3)) / MILLION) * MILLION
                    accounts = max(1, round(outstanding / SEGMENT_AVG_LOAN[s]))
                    rows.append(
                        {
                            "report_month": month_str(y, m),
                            "region": region,
                            "branch_code": branch,
                            "product_segment": segment,
                            "outstanding_amount": outstanding,
                            "disbursement_amount": disb,
                            "target_amount": target,
                            "account_count": accounts,
                        }
                    )
    return rows


def deposit_rows() -> list[dict]:
    rows = []
    for t, (y, m) in enumerate(MONTHS):
        for r, region in enumerate(REGIONS):
            g = (REGION_GROWTH_BP[(r + 3) % 8] * 0.7) / 10_000
            for p, product in enumerate(DEPOSIT_PRODUCTS):
                base = 900_000 * MILLION * REGION_SCALE[r] * DEPOSIT_SCALE[p]
                jitter = 1 + 0.03 * (noise(t, r, p, 11) - 0.5)
                balance = round(base * (1 + g) ** t * jitter / MILLION) * MILLION
                accounts = max(1, round(balance / DEPOSIT_AVG_BALANCE[p]))
                rows.append(
                    {
                        "report_month": month_str(y, m),
                        "region": region,
                        "product_type": product,
                        "balance_amount": balance,
                        "account_count": accounts,
                    }
                )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def expected(sme: list[dict]) -> dict:
    """Plain-arithmetic expected values, deliberately not using the app engine."""
    dec25 = [r for r in sme if r["report_month"] == "2025-12-01"]
    dec24 = [r for r in sme if r["report_month"] == "2024-12-01"]
    by_region_dec25: dict[str, int] = {}
    for r in dec25:
        by_region_dec25[r["region"]] = by_region_dec25.get(r["region"], 0) + r["outstanding_amount"]
    monthly_2025: dict[str, int] = {}
    for r in sme:
        if r["report_month"].startswith("2025"):
            monthly_2025[r["report_month"]] = monthly_2025.get(r["report_month"], 0) + r["outstanding_amount"]
    out_dec25 = sum(r["outstanding_amount"] for r in dec25)
    out_dec24 = sum(r["outstanding_amount"] for r in dec24)
    return {
        "_note": "SYNTHETIC expected values computed with plain sums in the generator, independent of the app engine.",
        "demo_sme_credit": {
            "total_outstanding_2025_12": out_dec25,
            "total_outstanding_by_region_2025_12": dict(sorted(by_region_dec25.items())),
            "total_outstanding_by_month_2025": dict(sorted(monthly_2025.items())),
            "total_disbursement_2025": sum(r["disbursement_amount"] for r in sme if r["report_month"].startswith("2025")),
            "target_achievement_pct_2025_12": round(out_dec25 / sum(r["target_amount"] for r in dec25) * 100, 4),
            "outstanding_growth_pct_2025_vs_2024_12": round((out_dec25 / out_dec24 - 1) * 100, 4),
            "row_count": len(sme),
        },
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sme = sme_rows()
    dep = deposit_rows()
    write_csv(OUT / "demo_sme_credit.csv", sme)
    write_csv(OUT / "demo_deposit_funding.csv", dep)
    (OUT / "expected_values.json").write_text(json.dumps(expected(sme), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(sme)} SME rows, {len(dep)} deposit rows to {OUT}")


if __name__ == "__main__":
    main()
