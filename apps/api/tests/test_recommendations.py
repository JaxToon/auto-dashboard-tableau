"""Recurring-question detection on SIMULATED events."""

from tests.test_api_flow import Q, ask


def analyze(clients):
    r = clients.as_user("demo.reviewer").post("/api/v1/recommendations/analyze")
    assert r.status_code == 200, r.text
    return r.json()


def test_thresholds_and_suitability(clients):
    out = analyze(clients)
    by = {c["cluster_key"].split("|")[1]: c for c in out["clusters"]}
    assert by["total_outstanding__region__trend"]["qualifies"]
    assert by["target_achievement_pct__region__snapshot"]["qualifies"]
    assert by["total_deposit_balance__region__trend"]["qualifies"]
    assert not by["total_disbursement__product_segment__trend"]["qualifies"]  # below count / audience
    single = by["total_outstanding__total__snapshot"]
    assert not single["qualifies"] and not single["rules"]["dashboard_suitable"]["ok"]  # frequent but not dashboard-worthy
    assert all(c["simulated"] for c in out["clusters"])


def test_configurable_thresholds(clients):
    clients.as_user("demo.admin").patch("/api/v1/admin/settings", json={"rec_min_events": 5, "rec_min_actors": 2})
    by = {c["cluster_key"].split("|")[1]: c for c in analyze(clients)["clusters"]}
    assert by["total_disbursement__product_segment__trend"]["qualifies"]


def test_no_raw_prompts_or_identities_exposed(clients):
    ask(clients.as_user("demo.business"), Q)
    out = analyze(clients)
    recs = clients.as_user("demo.reviewer").get("/api/v1/recommendations").json()
    text = str(out) + str(recs)
    assert Q not in text and "demo.business" not in text


def test_review_flow_and_visibility(clients):
    analyze(clients)
    rev, biz = clients.as_user("demo.reviewer"), clients.as_user("demo.business")
    recs = rev.get("/api/v1/recommendations").json()["recommendations"]
    assert {r["status"] for r in recs} == {"candidate"} and all(r["simulated"] for r in recs)
    assert biz.get("/api/v1/recommendations").json()["recommendations"] == []  # not yet accepted
    credit = next(r for r in recs if r["intent_key"] == "total_outstanding__region__trend")
    deposit = next(r for r in recs if r["dataset_id"] == "demo_deposit_funding")
    assert biz.post(f"/api/v1/recommendations/{credit['id']}/decision", json={"decision": "accept"}).status_code == 403
    rev.post(f"/api/v1/recommendations/{credit['id']}/decision", json={"decision": "accept"})
    rev.post(f"/api/v1/recommendations/{deposit['id']}/decision", json={"decision": "accept"})
    visible = biz.get("/api/v1/recommendations").json()["recommendations"]
    assert [r["id"] for r in visible] == [credit["id"]]  # deposit dataset not entitled
    out = biz.post(f"/api/v1/recommendations/{credit['id']}/decision", json={"decision": "create_draft"}).json()
    draft = biz.get(f"/api/v1/dashboards/{out['draft_id']}").json()
    assert draft["status"] == "Draft" and draft["recommendation_id"] == credit["id"]
    assert any("SIMULATED" in a for a in draft["spec"]["assumptions"])
    assert out["recommendation"]["status"] == "converted"


def test_dismissed_cannot_be_converted(clients):
    analyze(clients)
    rev = clients.as_user("demo.reviewer")
    rec = rev.get("/api/v1/recommendations").json()["recommendations"][0]
    rev.post(f"/api/v1/recommendations/{rec['id']}/decision", json={"decision": "dismiss"})
    r = rev.post(f"/api/v1/recommendations/{rec['id']}/decision", json={"decision": "create_draft"})
    assert r.status_code == 409


def test_feature_flag_disables_recommendations(clients):
    clients.as_user("demo.admin").patch("/api/v1/admin/settings", json={"feature_recurring_recommendations": False})
    assert clients.as_user("demo.reviewer").get("/api/v1/recommendations").status_code == 503
