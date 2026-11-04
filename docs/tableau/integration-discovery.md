# Tableau integration discovery

## Status

| Item | Status |
|---|---|
| Adapter for Tableau REST API via Tableau Server Client 0.42 (PAT sign-in, project check, publish, read-back verification) | Built; unit-tested with a fake client |
| Deterministic `.twbx` generation | Built; structural and determinism tests pass |
| Real Tableau environment | **Not available yet.** Nothing has been published. No URL, site or project ID was invented. |

## Discovery checklist (PRD §8.2) — to confirm with the Tableau administrator

| Question | Answer | Config |
|---|---|---|
| Tableau Server or Tableau Cloud? | _Open_ | `TABLEAU_SERVER_URL` |
| Version / build | _Open_ (shown by Admin, Test connection) | — |
| Site content URL | _Open_ | `TABLEAU_SITE_CONTENT_URL` (empty = default site) |
| Target PoC project ID | _Open_ | `TABLEAU_PROJECT_ID`, `TABLEAU_ALLOWED_PROJECT_IDS` |
| Approved authentication | _Open_. Adapter implements personal access token. Connected-app JWT would be a small addition. | `TABLEAU_AUTH_METHOD=pat`, `TABLEAU_PAT_NAME`, `TABLEAU_PAT_SECRET` |
| Dedicated least-privilege publisher identity? | _Open_. Needs Publish on the PoC project only. Not a site/server admin. | — |
| REST API publishing enabled? | _Open_ | — |
| Packaged workbooks with embedded text-file data allowed? | _Open_. **Key risk**, see below. | — |
| Extract vs published data source vs live | Current: embedded file data (results CSV). Fallback: Hyper extract. | — |
| Test-user licences (Viewer) for step 10 | _Open_ | — |
| External URL opening acceptable vs embedding | Assumed: opening the URL is enough (PRD §3.2) | — |
| TLS / custom CA | _Open_ | `TABLEAU_VERIFY_SSL` |

## How publishing works

1. Spec and results are validated again at publish time.
2. `tableau_artifact.build_twbx` writes `Data/results/results.csv` (long format: `viz_id, metric, period, category, series, highlight, value, value_label`) and a `.twb` with one worksheet per visualization (filtered on `viz_id`), one dashboard (title, synthetic banner, KPI row, chart grid, provenance footer). Workbook name: `PoC - <title> (<draft id prefix>)`.
3. `RestTableauPublisher.publish_dashboard` signs in with the PAT, checks the project is allow-listed and visible, and publishes with `PublishMode.Overwrite` (a new version of the same draft replaces the previous workbook for that draft).
4. `verify_publication` reads the workbook back by ID and checks: same ID, same project, same name, dashboard view present, all sheets present, and each KPI worksheet's CSV (rendered by Tableau) contains the same value label as the preview. URL = Tableau's `webpageUrl`; if a server does not return it, the URL is built from the verified dashboard view and marked `constructed_from_verified_view`.
5. Only if every check passes does the draft become Published with that URL.

## Risks and first actions once access exists

1. **Open the generated workbook in Tableau Desktop first.** Generate one with the mock mode flow (file in `var/artifacts/`) and open it. Fix any XML the Desktop version rejects before touching the server.
2. **Tableau Cloud and file data.** If the site refuses packaged workbooks with text-file data, switch to Option B: add `tableauhyperapi`, write the same long-format rows to `results.hyper`, and point the template connection at the extract (`class='hyper'`). The verification logic stays the same.
3. **Value read-back** uses `views.populate_csv` and needs view-data access for the publisher identity. If that is not allowed, the check fails and the draft is not Published. Adjust permissions or replace this check with a documented manual check.
4. **Workbook name collisions:** names include the draft ID prefix to avoid overwriting other users' workbooks.
