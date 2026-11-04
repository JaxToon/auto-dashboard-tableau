"""API integration: question -> draft -> validate -> preview -> review -> publish.

Publishing here uses MOCK or a FAKE Tableau client and is labelled as such. It does not
satisfy the PoC acceptance criterion; see tests/e2e/test_tableau_real_e2e.py.
"""

from pydantic import SecretStr

from app.tableau_publisher import RestTableauPublisher
from tests.conftest import EXPECTED, FakeTableau

Q = "Show monthly SME credit outstanding by region for 2025."
REAL_LIKE = dict(feature_dashboard_publish=True, tableau_enabled=True, tableau_mode="rest", tableau_server_url="https://tableau.example.test",
                 tableau_site_content_url="poc", tableau_project_id="proj-poc", tableau_pat_name="poc-publisher", tableau_pat_secret=SecretStr("pat-secret-xyz"))


def ask(client, q=Q, **kw):
    r = client.post("/api/v1/analytics/interpret", json={"question": q, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def build_ready_draft(clients, q=Q):
    biz, rev = clients.as_user("demo.business"), clients.as_user("demo.reviewer")
    interp = ask(biz, q)
    draft = biz.post("/api/v1/dashboards/plan", json={"interpretation_id": interp["interpretation_id"]}).json()
    did = draft["id"]
    assert biz.post(f"/api/v1/dashboards/{did}/validate").json()["report"]["valid"]
    assert biz.post(f"/api/v1/dashboards/{did}/submit-review").json()["status"] == "Needs Review"
    r = rev.post(f"/api/v1/dashboards/{did}/approve", json={"decision": "approved", "comment": "KPI definitions OK"})
    assert r.json()["status"] == "Ready to Publish", r.text
    return did


def test_health(clients):
    assert clients.anonymous().get("/health/live").json() == {"status": "ok"}
    ready = clients.anonymous().get("/health/ready").json()
    assert ready["database"] is True and "secret" not in str(ready).lower()


def test_unauthenticated_is_rejected(clients):
    r = clients.anonymous().post("/api/v1/analytics/interpret", json={"question": Q})
    assert r.status_code == 401 and r.json()["error"]["code"] == "NOT_AUTHENTICATED"


def test_interpret_returns_resolution_and_correlation_id(clients):
    biz = clients.as_user("demo.business")
    r = biz.post("/api/v1/analytics/interpret", json={"question": Q}, headers={"X-Request-ID": "req-123"})
    assert r.headers["X-Request-ID"] == "req-123"
    body = r.json()
    assert body["status"] == "ok"
    assert body["resolved"]["dataset"]["id"] == "demo_sme_credit"
    assert [m["id"] for m in body["resolved"]["metrics"]] == ["total_outstanding"]
    assert body["intent"]["output_decision"] == "DASHBOARD_RECOMMENDATION"


def test_answer_only_returns_quick_answer(clients):
    body = ask(clients.as_user("demo.business"), "Berapa total outstanding kredit SME Desember 2025?")
    assert body["intent"]["output_decision"] == "ANSWER_ONLY"
    assert "Rp 11.95 T" in body["quick_result"]["answer_text"]


def test_empty_and_oversized_prompts(clients):
    biz = clients.as_user("demo.business")
    assert biz.post("/api/v1/analytics/interpret", json={"question": ""}).status_code == 422
    r = biz.post("/api/v1/analytics/interpret", json={"question": "x" * 1001})
    assert r.status_code == 422 and r.json()["error"]["code"] == "PROMPT_TOO_LONG"


def test_full_flow_preview_matches_fixture(clients):
    biz = clients.as_user("demo.business")
    interp = ask(biz)
    draft = biz.post("/api/v1/dashboards/plan", json={"interpretation_id": interp["interpretation_id"]}).json()
    assert draft["status"] == "Draft" and draft["metric_versions"] == {"total_outstanding": "1.0.0", "outstanding_growth_pct": "1.0.0", "target_achievement_pct": "1.0.0"}
    did = draft["id"]
    rep = biz.post(f"/api/v1/dashboards/{did}/validate").json()["report"]
    assert rep["valid"] and any(c["name"].startswith("reconcile:") for c in rep["checks"])
    pv = biz.post(f"/api/v1/dashboards/{did}/preview").json()
    assert pv["provenance"]["synthetic"] is True
    res = {r["viz_id"]: r for r in pv["results"]}
    assert res["kpi_total_outstanding"]["rows"][0]["total_outstanding"] == EXPECTED["demo_sme_credit"]["total_outstanding_2025_12"]
    bars = {r["region"]: r["total_outstanding"] for r in res["comparison"]["rows"]}
    assert bars == EXPECTED["demo_sme_credit"]["total_outstanding_by_region_2025_12"]


def test_edit_creates_version_and_invalidates_approval(clients):
    did = build_ready_draft(clients)
    biz = clients.as_user("demo.business")
    d = biz.patch(f"/api/v1/dashboards/{did}", json={"title": "SME Credit Monitor", "chart_types": {"comparison": "table"}}).json()
    assert d["current_version"] == 2 and d["status"] == "Draft" and d["approved_version"] is None
    assert d["title"] == "SME Credit Monitor" and len(d["versions"]) == 2
    assert not d["can_publish"]


def test_edit_cannot_break_chart_rules(clients):
    biz = clients.as_user("demo.business")
    did = biz.post("/api/v1/dashboards/plan", json={"interpretation_id": ask(biz)["interpretation_id"]}).json()["id"]
    r = biz.patch(f"/api/v1/dashboards/{did}", json={"chart_types": {"trend": "kpi_card"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "EDIT_INVALID"


def test_publish_blocked_when_tableau_not_configured(make_app):
    _, clients = make_app(feature_dashboard_publish=True)
    did = build_ready_draft(clients)
    r = clients.as_user("demo.business").post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True})
    assert r.status_code == 409
    assert "Tableau integration not configured." in r.json()["error"]["details"]["blockers"]
    d = clients.as_user("demo.business").get(f"/api/v1/dashboards/{did}").json()
    assert d["status"] == "Ready to Publish" and d["verified_url"] is None


def test_publish_requires_confirmation(make_app):
    _, clients = make_app(feature_dashboard_publish=True, tableau_enabled=True, tableau_mode="mock")
    did = build_ready_draft(clients)
    r = clients.as_user("demo.business").post(f"/api/v1/dashboards/{did}/publish", json={"confirm": False})
    assert r.json()["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_mock_publish_is_never_published(make_app):
    _, clients = make_app(feature_dashboard_publish=True, tableau_enabled=True, tableau_mode="mock")
    did = build_ready_draft(clients)
    out = clients.as_user("demo.business").post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True}).json()
    assert out["publication"]["status"] == "MockPublished"
    assert out["publication"]["content_url"] is None and out["publication"]["verified"] is False
    assert out["draft"]["status"] != "Published" and out["draft"]["verified_url"] is None


def test_fake_rest_publish_verified(make_app):
    app, clients = make_app(**REAL_LIKE)
    fake = FakeTableau()
    app.state.container.publisher = RestTableauPublisher(app.state.container.settings, tsc=fake)
    did = build_ready_draft(clients)
    biz = clients.as_user("demo.business")
    out = biz.post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True}).json()
    assert out["publication"]["status"] == "Published" and out["publication"]["verified"]
    assert out["draft"]["status"] == "Published"
    assert out["draft"]["verified_url"] == "https://tableau.example.test/#/site/poc/workbooks/1"
    # idempotent: publishing the same version again does not create a duplicate workbook
    again = biz.post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True}).json()
    assert again["duplicate"] is True and len(fake.published) == 1


def test_fake_rest_publish_failure_preserves_draft(make_app):
    app, clients = make_app(**REAL_LIKE)
    app.state.container.publisher = RestTableauPublisher(app.state.container.settings, tsc=FakeTableau(fail_publish=True))
    did = build_ready_draft(clients)
    biz = clients.as_user("demo.business")
    out = biz.post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True}).json()
    assert out["publication"]["status"] == "Failed" and out["publication"]["content_url"] is None
    assert out["draft"]["status"] == "Failed" and out["draft"]["spec"]["title"]
    # retry after the issue is fixed
    app.state.container.publisher = RestTableauPublisher(app.state.container.settings, tsc=FakeTableau())
    retry = biz.post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True}).json()
    assert retry["draft"]["status"] == "Published"


def test_verification_failure_is_not_published(make_app):
    app, clients = make_app(**REAL_LIKE)
    app.state.container.publisher = RestTableauPublisher(app.state.container.settings, tsc=FakeTableau(drop_views=True))
    did = build_ready_draft(clients)
    out = clients.as_user("demo.business").post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True}).json()
    assert out["publication"]["status"] == "Failed" and out["publication"]["error_code"] == "VERIFICATION_FAILED"
    assert out["draft"]["verified_url"] is None


def test_review_not_required_mode(make_app):
    app, clients = make_app(review_required=False)
    biz = clients.as_user("demo.business")
    did = biz.post("/api/v1/dashboards/plan", json={"interpretation_id": ask(biz)["interpretation_id"]}).json()["id"]
    biz.post(f"/api/v1/dashboards/{did}/validate")
    assert biz.post(f"/api/v1/dashboards/{did}/submit-review").json()["status"] == "Ready to Publish"


def test_my_dashboards_and_review_queue(clients):
    did = build_ready_draft(clients)
    mine = clients.as_user("demo.business").get("/api/v1/dashboards").json()["dashboards"]
    assert did in [d["id"] for d in mine]
    queue = clients.as_user("demo.reviewer").get("/api/v1/dashboards?scope=review").json()["dashboards"]
    assert did in [d["id"] for d in queue]


def test_audit_covers_lifecycle(make_app):
    _, clients = make_app(feature_dashboard_publish=True, tableau_enabled=True, tableau_mode="mock")
    did = build_ready_draft(clients)
    clients.as_user("demo.business").post(f"/api/v1/dashboards/{did}/publish", json={"confirm": True})
    events = clients.as_user("demo.auditor").get("/api/v1/audit/events").json()["events"]
    actions = {e["action"] for e in events if e["resource_id"] in (did, "")}
    for a in ("analytics.interpret", "dashboard.plan", "dashboard.validate", "dashboard.submit_review", "dashboard.review.approved",
              "dashboard.publish.attempt", "dashboard.publish.mock"):
        assert a in actions, a
    assert all(e["request_id"] for e in events)
    assert Q not in str(events)  # raw prompt never persisted
