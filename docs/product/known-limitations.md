# Known limitations

1. **No real Tableau publication yet.** The REST adapter and workbook generator are tested against fakes only. The mandatory E2E fails until a real site is configured.
2. **Workbook XML unverified in Tableau.** Generated to the 18.1 format; may need adjustments once opened in Tableau Desktop. KPI cards use the default text size.
3. **Rules-based planner.** Understands catalog synonyms, periods, top-N, filters and comparisons in ID/EN, but not free-form phrasing outside those patterns (it then asks for clarification or refuses). No LLM is used.
4. **Mock login only.** No SSO.
5. **Single-instance state.** Interpretations and rate limits are in process memory; sessions/drafts are in the database.
6. **No Alembic migrations yet.** Tables are created on startup (`create_all`).
7. **Docker compose not verified.** `docker compose config` validates, but images were not built in this environment (Docker Desktop was not running).
8. **No browser E2E test suite.** The UI flow was checked manually with Playwright screenshots; API flows are covered by integration tests.
9. **Two chart families only** (line, bar) plus KPI cards and tables.
10. **Recommendations use simulated events** (clearly labelled) plus local questions; clustering is exact-match on normalized intent, not embeddings.
