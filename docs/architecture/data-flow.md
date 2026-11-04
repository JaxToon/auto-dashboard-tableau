# Data flow

```
Browser (Next.js) ──same-origin /api──▶ Next.js rewrite ──▶ FastAPI
                                                         │
  1. POST /analytics/interpret ── session → UserContext (roles from DB)
       RulesPlanner(question, catalog terms only) → Intent  (stored server-side, id returned)
       ANSWER/CHART: spec → policy.validate_spec → SyntheticDataAdapter → quick result
       telemetry: minimized QueryIntentEvent (no prompt, no values, pseudonymous group)
  2. POST /dashboards/plan(interpretation_id)
       RulesPlanner.generate_dashboard_spec → validate_spec → DashboardDraft v1 + version row
  3. POST /dashboards/{id}/validate
       validate_spec → execute_validated_plan (re-authorizes dataset) → validate_results
       (types, shape, row/series limits, reconciliation of additive KPIs)
  4. POST /dashboards/{id}/preview → validated results + provenance (dataset, KPI versions, freshness)
  5. PATCH (allowed props only) → new version, approval and validation reset
  6. submit-review → reviewer approve (not the owner) → Ready to Publish
  7. POST /dashboards/{id}/publish {confirm:true}
       blockers? → re-validate spec + results → claim status=Publishing
       build_twbx(validated results) → var/artifacts/<draft>/<name>_vN.twbx
       TableauPublisher.publish_dashboard (server-side PAT) → content id
       verify_publication: workbook by id, project, name, views, KPI values via CSV → URL
       Published + verified URL   |  Failed (draft preserved, no URL)
  8. Browser opens the Tableau URL; Tableau authenticates and authorizes the viewer.
```

## What crosses each boundary

| Boundary | Data | Never |
|---|---|---|
| Browser → API | question text, IDs, allowed edit fields | roles, formulas, SQL, Tableau target outside allowlist |
| API → planner | question text, catalog of entitled datasets | data values |
| Planner → API | typed Intent / DashboardSpec | SQL, code, workbook XML |
| API → data adapter | validated spec + user context | free-form queries |
| API → Tableau | generated `.twbx` (synthetic aggregates), PAT sign-in | admin credentials, customer data |
| API → browser | results, provenance, verified URL | tokens, PAT, stack traces |
| Audit / telemetry | action metadata, IDs, normalized intent keys | raw prompts, result values, secrets |
