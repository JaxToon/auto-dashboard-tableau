# Runbook: real Tableau end-to-end acceptance

The PoC is complete only when this passes against a real Tableau **non-production** project.

## 1. Ask the Tableau administrator for

- Server/Cloud URL and site content URL
- A dedicated PoC project (note its **project ID / LUID**)
- A least-privilege publisher identity with a personal access token (Publish on that project only, plus view-data access so values can be read back)
- One Viewer test user with access to the project

For a personal test, a free Tableau Cloud developer site (Tableau Developer Program) works; you are its site administrator.

## 2. Create a personal access token (PAT)

1. If *My Account Settings → Personal Access Tokens* says "Your site administrator has disabled PAT creation", a site administrator opens the site **Settings** (General or Authentication tab), enables personal access token creation and saves.
2. Avatar → **My Account Settings → Personal Access Tokens**: type a **Token Name** (you choose it; this is `TABLEAU_PAT_NAME`), select **Create**, then **Copy Secret** (shown once; this is `TABLEAU_PAT_SECRET`).
3. Paste both straight into the root `.env`. Do not send the secret over chat or commit it.

## 3. Configure (root `.env`, never committed)

```dotenv
TABLEAU_ENABLED=true
TABLEAU_MODE=rest
TABLEAU_SERVER_URL=https://<your-pod>.online.tableau.com
TABLEAU_SITE_CONTENT_URL=<site>          # the part after /site/ in the browser URL
TABLEAU_PAT_NAME=<token-name>
TABLEAU_PAT_SECRET=<token-secret>
TABLEAU_PROJECT_ID=                      # fill in after step 4
FEATURE_DASHBOARD_PUBLISH=true
```

## 4. Connect and choose the project

```bash
cd apps/api
.venv/Scripts/python -m app.tableau_check
```

It signs in, prints the Tableau version and every project with its ID, then signs out (the secret is never printed). Exit code 0 = connected and target project valid; 3 = connected but `TABLEAU_PROJECT_ID` is empty or wrong; 1/2 = sign-in failed or settings missing. Create a dedicated project (for example `Auto Dashboard PoC`) in Tableau, copy its ID into `TABLEAU_PROJECT_ID`, and run the check again. Until a project is set, the app can test the connection and list projects but blocks publishing.

## 5. Smoke test in the app

1. Optional: open a generated workbook (`var/artifacts/<draft>/<name>.twbx`) in Tableau Desktop.
2. Restart the API without `TABLEAU_MODE=mock` overrides, sign in as **Demo PoC Admin**, open Admin, select **Test connection**. It must say "Connected; target project validated."
3. As **Demo Business User**: ask a question, build and validate a draft, submit it; as **Demo BA Reviewer** approve it; back as the business user select **Publish to Tableau → Confirm publish**. Success shows **Open in Tableau** with the verified URL.

## 6. Run the acceptance test

```bash
cd apps/api
.venv/Scripts/python -m pytest tests_e2e -v -s
```

It asks the mandatory question, checks the dataset/KPI mapping, validates, compares preview values with `data/synthetic/expected_values.json`, gets reviewer approval, publishes, verifies through the Tableau API (including Tableau-rendered KPI values) and prints the URL.

## 7. Manual step 10

Open the printed URL in a browser, sign in as the Viewer test user and confirm:
- the dashboard opens in the PoC project;
- the KPI shows **Rp 11.95 T** total credit outstanding as of Dec 2025, and regional bars match the in-app preview;
- the synthetic-data banner is visible.

Record the result (date, Tableau version, URL, screenshot) in this file's log below.

## Log

| Date | Tableau version | Result | URL | By |
|---|---|---|---|---|
| | | | | |
