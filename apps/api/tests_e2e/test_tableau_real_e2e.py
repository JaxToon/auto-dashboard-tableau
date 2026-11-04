"""MANDATORY end-to-end acceptance test against a REAL Tableau non-production site (PRD 20).

This test FAILS (never skips) when Tableau is not configured or is in mock mode, because a
mocked publish does not complete the PoC. Run it explicitly once the Tableau admin has
provisioned the PoC project and publisher PAT (see docs/runbooks/real-tableau-e2e.md):

    cd apps/api && .venv/Scripts/python -m pytest tests_e2e -v -s

Step 10 (a human opening the URL after Tableau sign-in) is printed at the end and must be
confirmed manually or with a Playwright session that holds a Tableau test-user login.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import REPO_ROOT, get_settings
from app.main import create_app

PROMPT = "Show monthly SME credit outstanding by region for 2025."
EXPECTED = json.loads((REPO_ROOT / "data" / "synthetic" / "expected_values.json").read_text())["demo_sme_credit"]


def test_question_to_verified_tableau_dashboard():
    s = get_settings()
    assert s.tableau_mode == "rest", "E2E must use the real REST publisher; TABLEAU_MODE=mock does not complete the PoC."
    assert s.tableau_configured, "Tableau integration not configured (TABLEAU_ENABLED, SERVER_URL, PROJECT_ID, PAT name/secret)."
    assert s.feature_dashboard_publish, "FEATURE_DASHBOARD_PUBLISH must be true for the E2E run."

    app = create_app(s)
    with TestClient(app) as biz:
        assert app.state.container.publisher.mode == "rest"
        rev = TestClient(app)
        assert biz.post("/api/v1/auth/mock-login", json={"user_id": "demo.business"}).status_code == 200
        assert rev.post("/api/v1/auth/mock-login", json={"user_id": "demo.reviewer"}).status_code == 200

        # 1-4: ask and verify dataset / KPI mapping
        it = biz.post("/api/v1/analytics/interpret", json={"question": PROMPT}).json()
        assert it["status"] == "ok"
        assert it["resolved"]["dataset"]["id"] == "demo_sme_credit"
        assert [m["id"] for m in it["resolved"]["metrics"]] == ["total_outstanding"]

        # 5-7: spec, validation, preview vs independent fixture values
        draft = biz.post("/api/v1/dashboards/plan", json={"interpretation_id": it["interpretation_id"]}).json()
        did = draft["id"]
        assert biz.post(f"/api/v1/dashboards/{did}/validate").json()["report"]["valid"]
        pv = biz.post(f"/api/v1/dashboards/{did}/preview").json()
        res = {r["viz_id"]: r for r in pv["results"]}
        assert res["kpi_total_outstanding"]["rows"][0]["total_outstanding"] == EXPECTED["total_outstanding_2025_12"]
        assert {r["region"]: r["total_outstanding"] for r in res["comparison"]["rows"]} == EXPECTED["total_outstanding_by_region_2025_12"]

        # review gate
        assert biz.post(f"/api/v1/dashboards/{did}/submit-review").status_code == 200
        assert rev.post(f"/api/v1/dashboards/{did}/approve", json={"decision": "approved", "comment": "E2E"}).json()["status"] == "Ready to Publish"

        # 8-9: publish to the configured project and verify through the Tableau API
        conn = TestClient(app)
        conn.post("/api/v1/auth/mock-login", json={"user_id": "demo.admin"})
        test = conn.post("/api/v1/integrations/tableau/test").json()
        assert test["ok"] and test["target_project_found"], test

        out = biz.post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True}).json()
        pub = out["publication"]
        assert pub["mode"] == "rest"
        assert pub["status"] == "Published", pub
        assert pub["verified"] is True and pub["content_id"] and pub["content_url"]
        checks = {c["name"]: c["ok"] for c in pub["verification"]["checks"]}
        assert all(checks.values()), checks
        assert any(k.startswith("value:") for k in checks), "Tableau-rendered KPI values were not checked"

        # independent read-back of the URL from Tableau
        url = app.state.container.publisher.get_content_url(pub["content_id"])
        assert url, "Tableau did not return a webpage URL for the workbook"

        # 10: human/browser confirmation
        print("\n=== MANUAL STEP 10 ===")
        print(f"Open in Tableau (sign in as an authorized test user): {pub['content_url']}")
        print(f"Expected KPI 'Total Credit Outstanding (as of Dec 2025)': Rp {EXPECTED['total_outstanding_2025_12'] / 1e12:,.2f} T")
