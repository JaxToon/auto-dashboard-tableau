# Assumptions and open questions

## Assumptions made in the PoC (to be confirmed)

| Area | Assumption |
|---|---|
| Domain | SME credit is the pilot domain (PRD example). A second, restricted deposit dataset only demonstrates entitlements. |
| KPI definitions | The 7 catalog KPIs are **placeholders written for the PoC**, not official bank definitions. Owner fields say "to be confirmed". |
| Growth semantics | By time: vs previous period. Without time: last month of the range vs the month before the range starts (e.g. Dec 2025 vs Dec 2024 for "2025"). |
| "Last N months" | Measured up to the latest month in the dataset (Dec 2025), not today's date, because the data is a static snapshot. |
| Default period | Last 12 available months when none is given (stated to the user as an assumption). |
| Output decision | Explicit dashboard/monitoring words, ≥2 KPIs, trend + breakdown, or ranking over time → dashboard; single value → answer; one trend or comparison → chart. |
| Review | BA reviewer approval required by default (`REVIEW_REQUIRED=true`); a reviewer cannot approve their own draft. |
| Recommendation thresholds | 10 events / 30 days / 3 groups / ≥2 weeks: local demo parameters, not bank policy. |
| Raw prompts | Not persisted anywhere (drafts store the interpretation summary instead). |
| Tableau URL | Opening the URL in Tableau is sufficient; no embedding. |

## Open questions (from PRD §28, still open)

**Business Analytics:** pilot domain; official KPI definitions and owners; manual baseline time; who reviews; test users.

**Existing conversational analytics tool / AI platform team:** existing capabilities (NL query, charts, history, semantic mapping); API contract; approved model/provider and deployment path; policy for query metadata.

**Data Management:** curated or synthetic-like dataset for pilot; data dictionary; metadata that may be shown to an app/model.

**Tableau:** see docs/tableau/integration-discovery.md.

**Security / Risk / Compliance:** approved environment; IAM requirements; logging and retention; approvals needed before internal metadata is used.
