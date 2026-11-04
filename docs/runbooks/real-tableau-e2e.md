# Runbook: real Tableau end-to-end acceptance

The PoC is complete only when this passes against a real Tableau **non-production** project.

## 1. Ask the Tableau administrator for

- Server/Cloud URL and site content URL
- A dedicated PoC project (note its **project ID / LUID**)
- A least-privilege publisher identity with a personal access token (Publish on that project only, plus view-data access so values can be read back)
- One Viewer test user with access to the project

## 2. Configure (root `.env`, never committed)

```dotenv
TABLEAU_ENABLED=true
TABLEAU_MODE=rest
TABLEAU_SERVER_URL=https://<your-tableau-host>
TABLEAU_SITE_CONTENT_URL=<site>
TABLEAU_PROJECT_ID=<project-luid>
TABLEAU_PAT_NAME=<token-name>
TABLEAU_PAT_SECRET=<token-secret>
FEATURE_DASHBOARD_PUBLISH=true
```

## 3. Smoke test

1. Optional: generate a workbook in mock mode first and open `var/artifacts/<draft>/<name>.twbx` in Tableau Desktop.
2. Start the app, sign in as **Demo PoC Admin**, open Admin, select **Test connection**. It must say "Connected; target project validated."

## 4. Run the acceptance test

```bash
cd apps/api
.venv/Scripts/python -m pytest tests_e2e -v -s
```

It asks the mandatory question, checks the dataset/KPI mapping, validates, compares preview values with `data/synthetic/expected_values.json`, gets reviewer approval, publishes, verifies through the Tableau API (including Tableau-rendered KPI values) and prints the URL.

## 5. Manual step 10

Open the printed URL in a browser, sign in as the Viewer test user and confirm:
- the dashboard opens in the PoC project;
- the KPI shows **Rp 11.95 T** total credit outstanding as of Dec 2025, and regional bars match the in-app preview;
- the synthetic-data banner is visible.

Record the result (date, Tableau version, URL, screenshot) in this file's log below.

## Log

| Date | Tableau version | Result | URL | By |
|---|---|---|---|---|
| | | | | |
