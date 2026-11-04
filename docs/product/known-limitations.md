# Known limitations

1. **Verified on one Tableau Cloud developer site only.** Publishing and value read-back passed there; Tableau Server and other sites still need their own run.
2. **Fixed dashboard template.** KPI cards are dashboard text zones; worksheets are also published as separate views so their values can be verified.
3. **Rules-based planner.** Understands catalog synonyms, periods, top-N, filters and comparisons in ID/EN, but not free-form phrasing outside those patterns (it then asks for clarification or refuses). No LLM is used.
4. **Mock login only.** No SSO.
5. **Single-instance state.** Interpretations and rate limits are in process memory; sessions/drafts are in the database.
6. **No Alembic migrations yet.** Tables are created on startup (`create_all`).
7. **Docker compose not verified.** `docker compose config` validates, but images were not built in this environment (Docker Desktop was not running).
8. **No browser E2E test suite.** The UI flow was checked manually with Playwright screenshots; API flows are covered by integration tests.
9. **Two chart families only** (line, bar) plus KPI cards and tables.
10. **Interactive filters live in the app, not in Tableau.** The review page lets any reader filter by period, region, segment and branch without changing the draft; saving a filter creates a new version that needs review. The published Tableau workbook contains pre-aggregated results for the saved view only, so it has no quick filters. Adding Tableau-side filters would need a finer-grained extract (Hyper) and recalculated KPIs in the workbook.
11. **Recommendations use simulated events** (clearly labelled) plus local questions; clustering is exact-match on normalized intent, not embeddings.
