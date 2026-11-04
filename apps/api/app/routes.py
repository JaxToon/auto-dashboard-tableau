"""HTTP API (PRD section 17). Every endpoint except health requires a server-side session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import dashboards as dsvc
from . import recommendations as rsvc
from .audit import query_events, record_event
from .audit import serialize as audit_dict
from .db import User, get_db
from .errors import AppError, Forbidden, NotFound, ValidationFailed
from .identity import MOCK_USERS, SESSION_COOKIE, UserContext, create_session, rate_limiter, resolve_session, revoke_session
from .runtime import Container
from .schema import ApproveRequest, DraftPatch, InterpretRequest, PublishRequest, RecommendationDecision, SettingsPatch
from .tableau_artifact import format_value

router = APIRouter()


def container(request: Request) -> Container:
    return request.app.state.container


def current_user(request: Request, db: Session = Depends(get_db)) -> UserContext:
    return resolve_session(db, request.cookies.get(SESSION_COOKIE))


# --- auth ------------------------------------------------------------------------------
class MockLogin(BaseModel):
    user_id: str


@router.get("/api/v1/auth/mock-users", tags=["auth"])
def mock_users(c: Container = Depends(container)):
    if c.settings.auth_mode != "mock":
        raise NotFound("Mock login is disabled.", code="MOCK_LOGIN_DISABLED")
    return {"mock": True, "notice": "MOCK LOGIN - isolated local development only.", "users": [{"user_id": u, "display_name": n, "roles": r} for u, n, r in MOCK_USERS]}


@router.post("/api/v1/auth/mock-login", tags=["auth"])
def mock_login(body: MockLogin, response: Response, db: Session = Depends(get_db), c: Container = Depends(container)):
    if c.settings.auth_mode != "mock":
        raise NotFound("Mock login is disabled.", code="MOCK_LOGIN_DISABLED")
    user = db.get(User, body.user_id)
    if user is None:
        raise NotFound("Unknown mock user.", code="UNKNOWN_USER")
    token = create_session(db, user.id, c.settings.session_ttl_minutes)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=c.settings.app_env not in ("local", "test"), max_age=c.settings.session_ttl_minutes * 60, path="/")
    record_event(db, user.id, "auth.login", resource_type="session", details={"method": "mock"})
    return {"ok": True}


@router.post("/api/v1/auth/logout", tags=["auth"])
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/v1/me", tags=["auth"])
def me(user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return {
        "user": user.public(),
        "app": {
            "env": c.settings.app_env,
            "mode": c.settings.app_mode,
            "synthetic": c.settings.app_mode == "synthetic",
            "auth_mode": c.settings.auth_mode,
            "llm_provider": c.planner.name,
            "tableau_mode": c.publisher.mode,
            "flags": {k: v["effective"] for k, v in c.runtime.snapshot()["flags"].items()},
        },
    }


# --- catalog ---------------------------------------------------------------------------
def _dataset_dict(c: Container, ds) -> dict:
    return {
        **ds.model_dump(exclude={"source_file", "synonyms"}),
        "fields": [f.model_dump(exclude={"synonyms"}) for f in ds.fields],
        "metrics": [m.id for m in c.catalog.metrics_for(ds.id)],
        "freshness": c.data.get_data_freshness(ds.id),
    }


@router.get("/api/v1/catalog/datasets", tags=["catalog"])
def list_datasets(user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return {"catalog_version": c.catalog.catalog_version, "datasets": [_dataset_dict(c, d) for d in c.data.list_available_datasets(user)]}


@router.get("/api/v1/catalog/metrics", tags=["catalog"])
def list_metrics(user: UserContext = Depends(current_user), c: Container = Depends(container)):
    ids = {d.id for d in c.catalog.accessible_datasets(user.roles)}
    return {"catalog_version": c.catalog.catalog_version, "metrics": [m.model_dump() for m in c.catalog.metrics if m.dataset_id in ids]}


# --- analytics -------------------------------------------------------------------------
def _resolved(c: Container, intent) -> dict:
    ds = c.catalog.dataset(intent.dataset_id)
    return {
        "dataset": {"id": ds.id, "name": ds.name, "version": ds.version, "classification": ds.classification},
        "metrics": [{"id": m.id, "name": m.name, "definition": m.definition, "formula_display": m.formula_display, "unit": m.unit, "version": m.version} for m in (c.catalog.metric(x) for x in intent.metric_ids)],
        "dimensions": [{"id": d, "name": ds.field(d).name} for d in intent.dimension_ids],
        "filters": [{"field": f.field, "name": ds.field(f.field).name, "values": f.values} for f in intent.filters],
        "period": intent.period.model_dump(mode="json"),
        "time_grain": intent.time_grain,
    }


@router.post("/api/v1/analytics/interpret", tags=["analytics"])
def interpret(body: InterpretRequest, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    user.require("analytics:ask")
    rate_limiter.check(f"interpret:{user.user_id}", c.settings.rate_limit_per_minute)
    if len(body.question) > c.settings.max_prompt_chars:
        raise ValidationFailed(f"Questions are limited to {c.settings.max_prompt_chars} characters.", code="PROMPT_TOO_LONG")
    if not body.question.strip():
        raise ValidationFailed("Please enter a question.", code="PROMPT_EMPTY")
    if body.dataset_id:
        c.catalog.require_dataset(body.dataset_id, user.roles)
    result = c.planner.classify_intent(body.question, c.semantic_context(user, body.dataset_id), body.clarifications)
    out = result.model_dump(mode="json")
    details = {"status": result.status, "flags": result.flags, "planner": result.planner}
    if result.intent:
        intent = result.intent
        out["interpretation_id"] = dsvc.interpretations.put(user.user_id, intent)
        out["resolved"] = _resolved(c, intent)
        details |= {"dataset_id": intent.dataset_id, "metrics": intent.metric_ids, "dimensions": intent.dimension_ids,
                    "output_decision": intent.output_decision, "summary": intent.summary}
        if intent.output_decision != "DASHBOARD_RECOMMENDATION":
            spec = c.planner.generate_dashboard_spec(intent, c.semantic_context(user))
            report, spec = dsvc._validate(c, spec, user)
            if report.valid:
                results = c.data.execute_validated_plan(spec, user)
                quick = {"spec": spec.model_dump(mode="json"), "results": [r.as_dict() for r in results]}
                if intent.output_decision == "ANSWER_ONLY":
                    parts = []
                    for r in results:
                        mid = r.columns[0]["key"]
                        m = c.catalog.metric(mid)
                        parts.append(f"{r.title}: {format_value(r.rows[0].get(mid) if r.rows else None, m.unit)}")
                    quick["answer_text"] = "; ".join(parts) + ". (Synthetic data.)"
                out["quick_result"] = quick
        rsvc.record_intent_event(db, c, user, intent)
    record_event(db, user, "analytics.interpret", status="success" if result.status == "ok" else result.status, details=details)
    return out


class PlanBody(BaseModel):
    interpretation_id: str
    force_dashboard: bool = False


@router.post("/api/v1/dashboards/plan", tags=["dashboards"])
def plan(body: PlanBody, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    intent = dsvc.interpretations.get(body.interpretation_id, user.user_id)
    draft = dsvc.create_draft(db, c, user, intent, force_dashboard=body.force_dashboard)
    return dsvc.serialize(draft, c, db)


@router.get("/api/v1/dashboards", tags=["dashboards"])
def list_dashboards(scope: str = "mine", db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return {"dashboards": [dsvc.serialize(d, c, db) for d in dsvc.list_drafts(db, c, user, scope)]}


@router.get("/api/v1/dashboards/{draft_id}", tags=["dashboards"])
def get_dashboard(draft_id: str, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return dsvc.serialize(dsvc.get_draft(db, c, draft_id, user), c, db, include_versions=True)


@router.patch("/api/v1/dashboards/{draft_id}", tags=["dashboards"])
def patch_dashboard(draft_id: str, body: DraftPatch, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    draft = dsvc.edit_draft(db, c, dsvc.get_draft(db, c, draft_id, user), user, body)
    return dsvc.serialize(draft, c, db, include_versions=True)


@router.post("/api/v1/dashboards/{draft_id}/validate", tags=["dashboards"])
def validate_dashboard(draft_id: str, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    draft = dsvc.get_draft(db, c, draft_id, user)
    report = dsvc.validate_draft(db, c, draft, user)
    return {"report": report, "draft": dsvc.serialize(draft, c, db)}


@router.post("/api/v1/dashboards/{draft_id}/preview", tags=["dashboards"])
def preview_dashboard(draft_id: str, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return dsvc.preview(db, c, dsvc.get_draft(db, c, draft_id, user), user)


@router.post("/api/v1/dashboards/{draft_id}/submit-review", tags=["dashboards"])
def submit_review(draft_id: str, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return dsvc.serialize(dsvc.submit_review(db, c, dsvc.get_draft(db, c, draft_id, user), user), c, db)


@router.post("/api/v1/dashboards/{draft_id}/approve", tags=["dashboards"])
def approve(draft_id: str, body: ApproveRequest, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return dsvc.serialize(dsvc.review(db, c, dsvc.get_draft(db, c, draft_id, user), user, body.decision, body.comment), c, db)


@router.post("/api/v1/dashboards/{draft_id}/publish", tags=["dashboards"])
def publish(draft_id: str, body: PublishRequest, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    if not body.confirm:
        raise ValidationFailed("Publishing requires explicit confirmation.", code="CONFIRMATION_REQUIRED")
    return dsvc.publish(db, c, dsvc.get_draft(db, c, draft_id, user), user, body.target_project_id)


@router.post("/api/v1/dashboards/{draft_id}/archive", tags=["dashboards"])
def archive(draft_id: str, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    draft = dsvc.archive(db, dsvc.get_draft(db, c, draft_id, user), user)
    return dsvc.serialize(draft, c, db)


# --- recommendations -------------------------------------------------------------------
@router.post("/api/v1/recommendations/analyze", tags=["recommendations"])
def analyze(db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return rsvc.analyze(db, c, user)


@router.get("/api/v1/recommendations", tags=["recommendations"])
def recommendations(db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return {"recommendations": [rsvc.serialize(r, c) for r in rsvc.list_recommendations(db, c, user)]}


@router.post("/api/v1/recommendations/{rec_id}/decision", tags=["recommendations"])
def recommendation_decision(rec_id: str, body: RecommendationDecision, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    return rsvc.decide(db, c, user, rec_id, body.decision)


# --- integrations / admin --------------------------------------------------------------
@router.post("/api/v1/integrations/tableau/test", tags=["admin"])
def tableau_test(db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    user.require("admin:integrations")
    result = c.publisher.test_connection()
    record_event(db, user, "tableau.test", status="success" if result.get("ok") else "failure", details={"mode": result.get("mode"), "ok": result.get("ok")})
    return result


@router.get("/api/v1/integrations/tableau/projects", tags=["admin"])
def tableau_projects(user: UserContext = Depends(current_user), c: Container = Depends(container)):
    user.require("admin:integrations")
    return {"projects": c.publisher.list_projects(), "allowed_project_ids": c.settings.allowed_project_ids}


@router.get("/api/v1/admin/settings", tags=["admin"])
def admin_settings(user: UserContext = Depends(current_user), c: Container = Depends(container)):
    user.require("admin:settings")
    s = c.settings
    return {
        **c.runtime.snapshot(),
        "connectors": {
            "data": {"adapter": "SyntheticDataAdapter", "datasets": [c.data.get_data_freshness(d.id) for d in c.catalog.datasets]},
            "llm": {"provider": c.planner.name, "model": s.llm_model, "external_calls": False},
            "tableau": {
                "mode": c.publisher.mode,
                "configured": s.tableau_configured,
                "server_url": s.tableau_server_url or None,
                "site_content_url": s.tableau_site_content_url or None,
                "project_id": s.tableau_project_id or None,
                "allowed_project_ids": s.allowed_project_ids,
                "auth_method": s.tableau_auth_method,
                "pat_name_set": bool(s.tableau_pat_name),
                "pat_secret_set": bool(s.tableau_pat_secret.get_secret_value()),
            },
            "conversational_analytics": {"enabled": s.feature_conversational_analytics_integration, "status": "Not implemented (P2) - awaiting approved API contract."},
        },
        "limits": {"max_prompt_chars": s.max_prompt_chars, "max_result_rows": s.max_result_rows, "max_series": s.max_series, "rate_limit_per_minute": s.rate_limit_per_minute},
    }


@router.patch("/api/v1/admin/settings", tags=["admin"])
def update_settings(body: SettingsPatch, db: Session = Depends(get_db), user: UserContext = Depends(current_user), c: Container = Depends(container)):
    user.require("admin:settings")
    changes = body.model_dump(exclude_none=True)
    blocked = [k for k, v in changes.items() if k.startswith("feature_") and v and not getattr(c.settings, k)]
    if blocked:
        raise Forbidden("These features are disabled by the deployment configuration and cannot be enabled here: " + ", ".join(blocked), code="ENV_CEILING")
    applied = c.runtime.update(db, changes, user.user_id)
    record_event(db, user, "admin.settings_change", details={"changed": applied})
    return c.runtime.snapshot()


# --- audit / activity ------------------------------------------------------------------
@router.get("/api/v1/audit/events", tags=["audit"])
def audit_events(limit: int = 200, action: str | None = None, db: Session = Depends(get_db), user: UserContext = Depends(current_user)):
    return {"scope": "all" if user.has("audit:view_all") else "own", "events": [audit_dict(e) for e in query_events(db, user, limit=limit, action_prefix=action)]}


@router.get("/api/v1/activity", tags=["audit"])
def activity(db: Session = Depends(get_db), user: UserContext = Depends(current_user)):
    events = query_events(db, user, limit=100, own_only=True, action_prefix=("analytics.", "dashboard.", "recommendation."))
    return {"events": [audit_dict(e) for e in events]}


# --- health ----------------------------------------------------------------------------
@router.get("/health/live", tags=["health"])
def live():
    return {"status": "ok"}


@router.get("/health/ready", tags=["health"])
def ready(c: Container = Depends(container), db: Session = Depends(get_db)):
    try:
        db.execute(User.__table__.select().limit(1))
        db_ok = True
    except AppError:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "datasets_loaded": len(c.data._data), "tableau_configured": c.settings.tableau_configured}
