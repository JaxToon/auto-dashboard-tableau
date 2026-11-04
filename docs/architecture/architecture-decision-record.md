# Architecture decision record

Status for all entries: **Accepted for PoC**, revisit before internal pilot.

## ADR-001 Stack: FastAPI + Next.js
- **Decision:** Python 3.11 / FastAPI / Pydantic v2 backend; Next.js 15 / React 19 / TypeScript / Tailwind 4 / Recharts frontend.
- **Why:** PRD §15.2 default; empty repository; Python gives first-party Tableau tooling (TSC, Hyper API) and strict Pydantic schemas for the spec contract.
- **Note:** No third-party component library was added; the handful of controls are hand-built with visible focus states and native form elements. Revisit if the UI grows.

## ADR-002 One backend, modular services
- **Decision:** PRD §25 lists separate `services/*` packages. For a PoC they are modules inside `apps/api/app` with interface boundaries: `LLMProvider` (planner.py), `DataProvider` (data_provider.py), `TableauPublisher` (tableau_publisher.py), identity (identity.py), audit (audit.py), policy (policy.py), recommendations.
- **Why:** fewer moving parts; boundaries still allow swapping adapters.

## ADR-003 Planner: deterministic rules provider
- **Decision:** `RulesPlanner` interprets ID/EN questions by matching approved catalog synonyms only. It produces a typed `Intent` and a `DashboardSpec`; it never emits SQL, code or workbook XML.
- **Why:** no approved model gateway (PRD §11.3). The `LLMProvider` interface is where an approved gateway would plug in; its output would pass through the same strict schema and policy validation.

## ADR-004 Persistence: SQLAlchemy, SQLite locally, PostgreSQL in compose
- **Decision:** SQLAlchemy 2 models; SQLite file for the isolated prototype, PostgreSQL via `DATABASE_URL` (docker-compose).
- **Migration path:** schema is created with `create_all`. Before pilot, introduce Alembic with an initial migration generated from these models.
- **Catalog storage:** the semantic catalog is a versioned JSON file (`data/seeds/catalog.json`) rather than tables, so KPI changes go through code review. Move to tables when a catalog-editing workflow is needed.

## ADR-005 KPI semantics: stock vs flow
- **Decision:** each measure declares `time_aggregation`. Balances (`period_end`) take the last month of a period and are never summed across months; flows (`sum`) are summed. Formula types: `sum`, `ratio` (sum-then-divide), `growth` (period over previous period).
- **Why:** summing month-end balances across a year is the most common way a dashboard "looks right but is wrong".

## ADR-006 Tableau artifact strategy: Option A variant (template + embedded validated results)
- **Decision:** generate a deterministic `.twbx` containing (a) a CSV of the **validated preview results** in long format and (b) a fixed `.twb` template: one worksheet per visualization filtered by `viz_id`, one dashboard, a synthetic-data banner and a provenance footer. Publish with TSC `workbooks.publish` (overwrite within the per-draft workbook name), then verify by reading the workbook and views back, and comparing KPI labels rendered by Tableau (`views.populate_csv`) with the preview.
- **Why:** Tableau only sums single pre-computed rows, so published values equal the preview by construction; no KPI formulas are re-implemented in Tableau calculations; no live connection credentials are embedded.
- **Risks / alternatives:** the XML is unverified in real Tableau; Tableau Cloud may require extracts for file data. Option B (Hyper extract via Hyper API) is the fallback. See docs/tableau/integration-discovery.md.

## ADR-007 Interpretations held server-side
- **Decision:** `/analytics/interpret` stores the intent server-side (30-minute TTL, bound to the user) and returns an ID; `/dashboards/plan` takes only that ID.
- **Why:** clients cannot hand-craft intents. In-process storage is a PoC limitation; use a shared store for multiple instances.

## ADR-008 Publish safety
- Published only after Tableau confirms and read-back verification passes. Mock mode records `MockPublished`, returns no URL and never changes a draft to Published.
- Project allowlist, owner-only publish, BA approval of the exact version (configurable), explicit confirmation, re-validation at publish time, conditional status update against double publishing, and idempotency per version.

## ADR-009 Sessions and identity
- Opaque random session token in an HttpOnly cookie; only its SHA-256 hash is stored. Roles come from the user store, never from the client. Next.js proxies `/api` so the cookie is first-party.
- Replace `MockIdentityProvider` with the bank's SSO (OIDC/SAML) and role mapping before pilot.
