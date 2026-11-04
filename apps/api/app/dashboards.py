"""Dashboard draft lifecycle: plan, validate, preview, edit, review and publish.

Statuses: Draft, Needs Review, Ready to Publish, Publishing, Published, Failed, Archived.
A draft becomes Published only after Tableau confirms the publish AND an independent
read-back verification succeeds. Mock publishes never change a draft to Published.
"""

from __future__ import annotations

import secrets
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .audit import record_event
from .db import DashboardDraft, DashboardPublication, DashboardVersion, ReviewAction, utcnow
from .errors import Conflict, Forbidden, NotFound, ValidationFailed
from .identity import UserContext
from .planner import _fmt_month, _period_label
from .policy import validate_results, validate_spec
from .runtime import Container
from .schema import DashboardSpec, DraftPatch, Intent, PreviewRequest
from .tableau_artifact import build_twbx

EDITABLE_STATUSES = {"Draft", "Needs Review", "Ready to Publish", "Failed", "Published"}
REVIEWABLE_VISIBLE = {"Needs Review", "Ready to Publish", "Publishing", "Published", "Failed"}


# --- interpretations are kept server-side so the client cannot tamper with an intent ----
class InterpretationStore:
    def __init__(self, ttl_s: int = 1800):
        self.ttl_s = ttl_s
        self._items: dict[str, tuple[str, float, Intent]] = {}

    def put(self, user_id: str, intent: Intent) -> str:
        now = time.monotonic()
        self._items = {k: v for k, v in self._items.items() if now - v[1] < self.ttl_s}
        key = secrets.token_urlsafe(12)
        self._items[key] = (user_id, now, intent)
        return key

    def get(self, key: str, user_id: str) -> Intent:
        item = self._items.get(key)
        if not item or item[0] != user_id or time.monotonic() - item[1] > self.ttl_s:
            raise NotFound("Interpretation expired. Please ask the question again.", code="INTERPRETATION_NOT_FOUND")
        return item[2]


interpretations = InterpretationStore()


# --- access ----------------------------------------------------------------------------
def can_read(draft: DashboardDraft, user: UserContext) -> bool:
    return draft.owner_id == user.user_id or (user.has("dashboard:review") and draft.status in REVIEWABLE_VISIBLE)


def get_draft(db: Session, c: Container, draft_id: str, user: UserContext) -> DashboardDraft:
    draft = db.get(DashboardDraft, draft_id)
    if draft is None or not can_read(draft, user):
        if draft is not None:
            record_event(db, user, "authz.denied", status="denied", resource_type="dashboard", resource_id=draft_id, policy_ref="draft_read")
        raise NotFound("Dashboard not found.", code="DASHBOARD_NOT_FOUND")  # do not reveal other users' drafts
    c.catalog.require_dataset(draft.dataset_id, user.roles)
    return draft


def require_owner(db: Session, draft: DashboardDraft, user: UserContext, action: str) -> None:
    if draft.owner_id != user.user_id:
        record_event(db, user, "authz.denied", status="denied", resource_type="dashboard", resource_id=draft.id, policy_ref=f"owner_only:{action}")
        raise Forbidden("Only the draft owner can do this.", code="NOT_DRAFT_OWNER")


def serialize(draft: DashboardDraft, c: Container, db: Session | None = None, *, include_versions: bool = False) -> dict:
    pubs = []
    reviews = []
    versions = []
    if db is not None:
        pubs = [publication_dict(p) for p in db.execute(select(DashboardPublication).where(DashboardPublication.draft_id == draft.id).order_by(DashboardPublication.created_at.desc())).scalars()]
        reviews = [{"version": r.version, "reviewer_id": r.reviewer_id, "decision": r.decision, "comment": r.comment, "created_at": r.created_at.isoformat()}
                   for r in db.execute(select(ReviewAction).where(ReviewAction.draft_id == draft.id).order_by(ReviewAction.created_at.desc())).scalars()]
        if include_versions:
            versions = [{"version": v.version, "change_summary": v.change_summary, "created_by": v.created_by, "created_at": v.created_at.isoformat()}
                        for v in db.execute(select(DashboardVersion).where(DashboardVersion.draft_id == draft.id).order_by(DashboardVersion.version.desc())).scalars()]
    ds = c.catalog.dataset(draft.dataset_id)
    latest_verified = next((p for p in pubs if p["status"] == "Published" and p["verified"]), None)
    return {
        "id": draft.id,
        "title": draft.title,
        "description": draft.description,
        "status": draft.status,
        "owner_id": draft.owner_id,
        "dataset_id": draft.dataset_id,
        "dataset_name": ds.name if ds else draft.dataset_id,
        "dataset_version": draft.dataset_version,
        "catalog_version": draft.catalog_version,
        "metric_versions": draft.metric_versions,
        "spec": draft.spec,
        "current_version": draft.current_version,
        "validated_version": draft.validated_version,
        "approved_version": draft.approved_version,
        "validation_report": draft.validation_report,
        "interpretation_summary": draft.interpretation_summary,
        "output_decision": draft.output_decision,
        "recommendation_id": draft.recommendation_id,
        "review_required": c.runtime.value("review_required"),
        "can_publish": publish_blockers(draft, c) == [],
        "publish_blockers": publish_blockers(draft, c),
        "target_project_id": c.settings.tableau_project_id or None,
        "tableau_mode": c.publisher.mode,
        "publications": pubs,
        "verified_url": latest_verified["content_url"] if latest_verified else None,
        "reviews": reviews,
        "versions": versions,
        "created_at": draft.created_at.isoformat(),
        "updated_at": draft.updated_at.isoformat(),
    }


def publication_dict(p: DashboardPublication) -> dict:
    return {
        "id": p.id,
        "version": p.version,
        "mode": p.mode,
        "status": p.status,
        "project_id": p.project_id,
        "project_name": p.project_name,
        "content_id": p.content_id,
        "content_url": p.content_url if p.verified else None,
        "verified": p.verified,
        "verification": p.verification,
        "error_code": p.error_code,
        "error_message": p.error_message,
        "requested_by": p.requested_by,
        "created_at": p.created_at.isoformat(),
        "completed_at": p.completed_at.isoformat() if p.completed_at else None,
    }


def publish_blockers(draft: DashboardDraft, c: Container) -> list[str]:
    out = []
    if draft.validated_version != draft.current_version:
        out.append("Validate the current version.")
    if c.runtime.value("review_required") and draft.approved_version != draft.current_version:
        out.append("Business Analytics reviewer approval is required for the current version.")
    if draft.status not in ("Ready to Publish", "Failed"):
        out.append(f"Status must be Ready to Publish (currently {draft.status}).")
    if not c.runtime.flag("feature_dashboard_publish"):
        out.append("Dashboard publishing is disabled (FEATURE_DASHBOARD_PUBLISH).")
    if c.publisher.mode == "not_configured":
        out.append("Tableau integration not configured.")
    elif c.publisher.mode == "rest" and not c.settings.tableau_project_id:
        out.append("Choose a target Tableau project: set TABLEAU_PROJECT_ID (Admin, Test connection lists the project IDs).")
    return out


# --- planning ---------------------------------------------------------------------------
def _validate(c: Container, spec: dict | DashboardSpec, user: UserContext):
    ds_id = spec.dataset_id if isinstance(spec, DashboardSpec) else spec.get("dataset_id", "")
    cov = c.coverage(ds_id) if c.catalog.dataset(ds_id) else None
    return validate_spec(spec, c.catalog, user, cardinality=c.cardinality, coverage=cov,
                         max_series=c.settings.max_series, max_categories=c.settings.max_categories)


def create_draft(db: Session, c: Container, user: UserContext, intent: Intent, *, force_dashboard: bool, recommendation_id: str | None = None) -> DashboardDraft:
    user.require("dashboard:create")
    c.catalog.require_dataset(intent.dataset_id, user.roles)
    spec = c.planner.generate_dashboard_spec(intent, c.semantic_context(user), force_dashboard)
    report, spec = _validate(c, spec, user)
    if not report.valid:
        record_event(db, user, "dashboard.plan", status="failure", details={"errors": [e["code"] for e in report.errors]})
        raise ValidationFailed("The generated specification did not pass validation.", code="SPEC_INVALID", details=report.as_dict())
    ds = c.catalog.dataset(spec.dataset_id)
    draft = DashboardDraft(
        owner_id=user.user_id,
        title=spec.title,
        description=spec.description,
        status="Draft",
        dataset_id=spec.dataset_id,
        dataset_version=ds.version,
        catalog_version=c.catalog.catalog_version,
        metric_versions={m.metric_id: c.catalog.metric(m.metric_id).version for m in spec.metrics},
        spec=spec.model_dump(mode="json"),
        interpretation_summary=intent.summary,
        output_decision="DASHBOARD_RECOMMENDATION" if force_dashboard else intent.output_decision,
        recommendation_id=recommendation_id,
    )
    db.add(draft)
    db.flush()
    db.add(DashboardVersion(draft_id=draft.id, version=1, spec=draft.spec, change_summary="Generated from question" if not recommendation_id else "Generated from recommendation", created_by=user.user_id))
    record_event(db, user, "dashboard.plan", resource_type="dashboard", resource_id=draft.id, commit=False,
                 details={"dataset_id": draft.dataset_id, "metrics": list(draft.metric_versions), "planner": c.planner.name, "recommendation_id": recommendation_id})
    db.commit()
    return draft


def validate_draft(db: Session, c: Container, draft: DashboardDraft, user: UserContext) -> dict:
    report, spec = _validate(c, draft.spec, user)
    result_report = None
    if report.valid:
        results = c.data.execute_validated_plan(spec, user)
        result_report = validate_results(spec, results, c.catalog, max_rows=c.settings.max_result_rows, max_series=c.settings.max_series)
    full = report.as_dict()
    if result_report:
        full["errors"] += result_report.errors
        full["warnings"] += [w for w in result_report.warnings if w not in full["warnings"]]
        full["checks"] += result_report.checks
        full["valid"] = report.valid and result_report.valid
    full["validated_at"] = datetime.now(UTC).isoformat()
    full["version"] = draft.current_version
    draft.validation_report = full
    draft.validated_version = draft.current_version if full["valid"] else None
    record_event(db, user, "dashboard.validate", status="success" if full["valid"] else "failure", resource_type="dashboard", resource_id=draft.id, commit=False,
                 details={"version": draft.current_version, "valid": full["valid"], "error_codes": [e["code"] for e in full["errors"]]})
    db.commit()
    return full


def preview(db: Session, c: Container, draft: DashboardDraft, user: UserContext, view: PreviewRequest | None = None) -> dict:
    """Run the draft. `view` applies viewer-side filters and period for exploration only; the draft is unchanged."""
    raw = DashboardSpec.model_validate(draft.spec).model_dump(mode="json")
    customized = False
    if view is not None:
        if view.filters is not None:
            raw["filters"] = [f.model_dump() for f in view.filters]
        if view.time_range_start is not None:
            raw["time_range"]["start"] = view.time_range_start.isoformat()
        if view.time_range_end is not None:
            raw["time_range"]["end"] = view.time_range_end.isoformat()
        customized = raw["filters"] != draft.spec.get("filters", []) or raw["time_range"] != draft.spec["time_range"]
    report, spec = _validate(c, raw, user)
    if not report.valid:
        if customized:
            raise ValidationFailed("That filter is not allowed: " + report.errors[0]["message"], code="VIEW_FILTER_INVALID", details=report.as_dict())
        raise ValidationFailed("The specification is not valid; fix it before previewing.", code="SPEC_INVALID", details=report.as_dict())
    results = c.data.execute_validated_plan(spec, user)
    saved = DashboardSpec.model_validate(draft.spec).time_range
    if (saved.start, saved.end) != (spec.time_range.start, spec.time_range.end):
        # Titles carry the saved period; relabel them so a custom view never shows a stale period.
        relabel = {
            _period_label(saved.start, saved.end): _period_label(spec.time_range.start, spec.time_range.end),
            f"as of {_fmt_month(saved.end)}": f"as of {_fmt_month(spec.time_range.end)}",
        }
        for r in results:
            for old, new in relabel.items():
                r.title = r.title.replace(old, new)
    rr = validate_results(spec, results, c.catalog, max_rows=c.settings.max_result_rows, max_series=c.settings.max_series)
    ds = c.catalog.dataset(spec.dataset_id)
    record_event(db, user, "dashboard.preview", resource_type="dashboard", resource_id=draft.id,
                 details={"version": draft.current_version, "result_valid": rr.valid, "custom_view": customized})
    return {
        "draft_id": draft.id,
        "version": draft.current_version,
        "view": {"customized": customized, "filters": [f.model_dump() for f in spec.filters], "time_range": spec.time_range.model_dump(mode="json")},
        "results": [r.as_dict() for r in results],
        "result_validation": rr.as_dict(),
        "provenance": {
            "dataset_id": ds.id,
            "dataset_name": ds.name,
            "dataset_version": ds.version,
            "classification": ds.classification,
            "catalog_version": c.catalog.catalog_version,
            "freshness": c.data.get_data_freshness(ds.id),
            "metrics": [c.catalog.metric(m.metric_id).model_dump() for m in spec.metrics],
            "filters": [f.model_dump() for f in spec.filters],
            "time_range": spec.time_range.model_dump(mode="json"),
            "synthetic": True,
        },
    }


def _new_version(db: Session, draft: DashboardDraft, spec: dict, user: UserContext, summary: str) -> None:
    draft.current_version += 1
    draft.spec = spec
    draft.title = spec["title"]
    draft.description = spec.get("description", "")
    draft.validated_version = None
    draft.validation_report = None
    draft.approved_version = None
    if draft.status in ("Needs Review", "Ready to Publish", "Failed", "Published"):
        draft.status = "Draft"
    db.add(DashboardVersion(draft_id=draft.id, version=draft.current_version, spec=spec, change_summary=summary, created_by=user.user_id))


def edit_draft(db: Session, c: Container, draft: DashboardDraft, user: UserContext, patch: DraftPatch) -> DashboardDraft:
    require_owner(db, draft, user, "edit")
    if draft.status not in EDITABLE_STATUSES:
        raise Conflict(f"A {draft.status} dashboard cannot be edited.", code="NOT_EDITABLE")
    spec = DashboardSpec.model_validate(draft.spec).model_dump(mode="json")
    changes = []
    if patch.title is not None:
        spec["title"] = patch.title
        changes.append("title")
    if patch.description is not None:
        spec["description"] = patch.description
        changes.append("description")
    if patch.layout is not None:
        spec["layout"] = patch.layout
        changes.append("layout")
    if patch.time_range_start is not None:
        spec["time_range"]["start"] = patch.time_range_start.isoformat()
        changes.append("time range")
    if patch.time_range_end is not None:
        spec["time_range"]["end"] = patch.time_range_end.isoformat()
        changes.append("time range")
    if patch.filters is not None:
        spec["filters"] = [f.model_dump() for f in patch.filters]
        changes.append("filters")
    viz_by_id = {v["id"]: v for v in spec["visualizations"]}
    for vid, t in (patch.chart_types or {}).items():
        if vid not in viz_by_id:
            raise ValidationFailed("Unknown visualization.", code="UNKNOWN_VISUALIZATION")
        viz_by_id[vid]["type"] = t
        if t != "bar":
            viz_by_id[vid]["highlight_top_n"] = None
        changes.append("chart type")
    for vid, title in (patch.visualization_titles or {}).items():
        if vid not in viz_by_id or not title.strip() or len(title) > 120:
            raise ValidationFailed("Invalid visualization title.", code="INVALID_VISUALIZATION_TITLE")
        viz_by_id[vid]["title"] = title.strip()
        changes.append("chart title")
    if patch.remove_visualizations:
        spec["visualizations"] = [v for v in spec["visualizations"] if v["id"] not in set(patch.remove_visualizations)]
        changes.append("removed charts")
    if not changes:
        return draft
    report, _ = _validate(c, spec, user)
    if not report.valid:
        raise ValidationFailed("That change is not allowed: " + report.errors[0]["message"], code="EDIT_INVALID", details=report.as_dict())
    _new_version(db, draft, spec, user, "Edited " + ", ".join(dict.fromkeys(changes)))
    record_event(db, user, "dashboard.edit", resource_type="dashboard", resource_id=draft.id, commit=False, details={"version": draft.current_version, "changed": list(dict.fromkeys(changes))})
    db.commit()
    return draft


def submit_review(db: Session, c: Container, draft: DashboardDraft, user: UserContext) -> DashboardDraft:
    require_owner(db, draft, user, "submit_review")
    if draft.validated_version != draft.current_version:
        raise Conflict("Validate the current version before submitting.", code="NOT_VALIDATED")
    if draft.status not in ("Draft", "Failed"):
        raise Conflict(f"Cannot submit a {draft.status} dashboard.", code="INVALID_STATUS")
    draft.status = "Needs Review" if c.runtime.value("review_required") else "Ready to Publish"
    record_event(db, user, "dashboard.submit_review", resource_type="dashboard", resource_id=draft.id, commit=False, details={"version": draft.current_version, "status": draft.status})
    db.commit()
    return draft


def review(db: Session, c: Container, draft: DashboardDraft, user: UserContext, decision: str, comment: str) -> DashboardDraft:
    user.require("dashboard:review")
    if draft.owner_id == user.user_id:
        record_event(db, user, "authz.denied", status="denied", resource_type="dashboard", resource_id=draft.id, policy_ref="separation_of_duties")
        raise Forbidden("Reviewers cannot approve their own dashboards.", code="SELF_APPROVAL_NOT_ALLOWED")
    if draft.status != "Needs Review":
        raise Conflict("Only dashboards awaiting review can be reviewed.", code="INVALID_STATUS")
    db.add(ReviewAction(draft_id=draft.id, version=draft.current_version, reviewer_id=user.user_id, decision=decision, comment=comment))
    if decision == "approved":
        draft.status = "Ready to Publish"
        draft.approved_version = draft.current_version
    else:
        draft.status = "Draft"
        draft.approved_version = None
    record_event(db, user, f"dashboard.review.{decision}", resource_type="dashboard", resource_id=draft.id, commit=False, details={"version": draft.current_version})
    db.commit()
    return draft


def archive(db: Session, draft: DashboardDraft, user: UserContext) -> DashboardDraft:
    require_owner(db, draft, user, "archive")
    if draft.status == "Publishing":
        raise Conflict("Cannot archive while publishing.", code="INVALID_STATUS")
    draft.status = "Archived"
    record_event(db, user, "dashboard.archive", resource_type="dashboard", resource_id=draft.id)
    return draft


# --- publishing -------------------------------------------------------------------------
def publish(db: Session, c: Container, draft: DashboardDraft, user: UserContext, target_project_id: str | None) -> dict:
    user.require("dashboard:publish")
    require_owner(db, draft, user, "publish")
    c.runtime.require("feature_dashboard_publish", "Dashboard publishing")
    target = target_project_id or c.settings.tableau_project_id or ("mock-project" if c.publisher.mode == "mock" else "")
    allowed = c.settings.allowed_project_ids + (["mock-project"] if c.publisher.mode == "mock" else [])
    if target_project_id and target_project_id not in allowed:
        record_event(db, user, "authz.denied", status="denied", resource_type="dashboard", resource_id=draft.id, policy_ref="tableau_project_allowlist")
        raise Forbidden("Target Tableau project is not allow-listed.", code="TABLEAU_PROJECT_NOT_ALLOWED")

    existing = db.execute(select(DashboardPublication).where(
        DashboardPublication.draft_id == draft.id, DashboardPublication.version == draft.current_version,
        DashboardPublication.status == "Published", DashboardPublication.verified.is_(True))).scalars().first()
    if existing:
        return {"publication": publication_dict(existing), "draft": serialize(draft, c, db), "duplicate": True}

    blockers = publish_blockers(draft, c)
    if blockers:
        raise Conflict(blockers[0], code="PUBLISH_BLOCKED", details={"blockers": blockers})

    # Re-validate everything at publish time; never trust earlier state alone.
    report, spec = _validate(c, draft.spec, user)
    if not report.valid:
        raise ValidationFailed("The specification no longer passes validation.", code="SPEC_INVALID", details=report.as_dict())
    results = c.data.execute_validated_plan(spec, user)
    rr = validate_results(spec, results, c.catalog, max_rows=c.settings.max_result_rows, max_series=c.settings.max_series)
    if not rr.valid:
        raise ValidationFailed("Result validation failed; publishing was stopped.", code="RESULT_INVALID", details=rr.as_dict())

    # Concurrency guard: only one publish at a time per draft.
    prev_status = draft.status
    claimed = db.execute(update(DashboardDraft).where(DashboardDraft.id == draft.id, DashboardDraft.status.in_(["Ready to Publish", "Failed"])).values(status="Publishing")).rowcount
    db.commit()
    if not claimed:
        raise Conflict("This dashboard is already being published.", code="PUBLISH_IN_PROGRESS")
    db.refresh(draft)

    ds = c.catalog.dataset(spec.dataset_id)
    footer = (f"POC / SYNTHETIC DATA | Source: {ds.name} v{ds.version} | KPI catalog {c.catalog.catalog_version} | "
              f"Spec v{draft.current_version} | Draft {draft.id[:8]} | Generated by Proactive Business Analytics Copilot (PoC)")
    artifact = build_twbx(spec, results, c.catalog, draft_id=draft.id, version=draft.current_version, footer=footer)
    path = Path(c.settings.artifact_dir) / draft.id / artifact.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(artifact.content)

    pub = DashboardPublication(draft_id=draft.id, version=draft.current_version, mode=c.publisher.mode, status="Publishing",
                               server_url=c.settings.tableau_server_url, site_content_url=c.settings.tableau_site_content_url,
                               project_id=target, publisher_ref=c.settings.tableau_pat_name or c.publisher.mode,
                               requested_by=user.user_id, artifact_sha256=artifact.sha256)
    db.add(pub)
    record_event(db, user, "dashboard.publish.attempt", resource_type="dashboard", resource_id=draft.id, commit=False,
                 details={"version": draft.current_version, "mode": c.publisher.mode, "project_id": target, "artifact_sha256": artifact.sha256})
    db.commit()

    try:
        result = c.publisher.publish_dashboard(artifact, path, target)
        pub.content_id = result.content_id
        if c.publisher.mode == "mock":
            pub.status = "MockPublished"
            pub.verification = {"verified": False, "reason": "Mock mode - not a real Tableau publication."}
            draft.status = prev_status if prev_status == "Ready to Publish" else "Ready to Publish"
            record_event(db, user, "dashboard.publish.mock", resource_type="dashboard", resource_id=draft.id, commit=False, details={"content_id": result.content_id})
        else:
            v = c.publisher.verify_publication(result.content_id, artifact, target)
            pub.verification = {"checks": v.checks, "views": v.views, "url_source": v.url_source, "reason": v.reason}
            pub.project_name = v.project_name
            if v.verified and v.content_url:
                pub.status, pub.verified, pub.content_url = "Published", True, v.content_url
                draft.status = "Published"
                record_event(db, user, "dashboard.publish.success", resource_type="dashboard", resource_id=draft.id, commit=False,
                             details={"content_id": result.content_id, "project_id": target, "version": draft.current_version})
            else:
                pub.status, pub.error_code, pub.error_message = "Failed", "VERIFICATION_FAILED", v.reason or "Verification failed."
                draft.status = "Failed"
                record_event(db, user, "dashboard.publish.failure", status="failure", resource_type="dashboard", resource_id=draft.id, commit=False,
                             details={"reason": "verification_failed", "content_id": result.content_id})
    except Exception as exc:  # noqa: BLE001 - any failure must leave the draft safe and not Published
        code = getattr(exc, "code", "PUBLISH_FAILED")
        msg = getattr(exc, "message", "Publishing failed unexpectedly. The draft was preserved.")
        pub.status, pub.error_code, pub.error_message = "Failed", code, msg
        draft.status = "Failed"
        record_event(db, user, "dashboard.publish.failure", status="failure", resource_type="dashboard", resource_id=draft.id, commit=False, details={"error_code": code})
    pub.completed_at = utcnow()
    db.commit()
    return {"publication": publication_dict(pub), "draft": serialize(draft, c, db), "duplicate": False}


def list_drafts(db: Session, c: Container, user: UserContext, scope: str = "mine") -> list[DashboardDraft]:
    stmt = select(DashboardDraft).order_by(DashboardDraft.updated_at.desc())
    if scope == "review" and user.has("dashboard:review"):
        stmt = stmt.where(DashboardDraft.status.in_(REVIEWABLE_VISIBLE))
    else:
        stmt = stmt.where(DashboardDraft.owner_id == user.user_id)
    return [d for d in db.execute(stmt).scalars() if c.catalog.can_access(d.dataset_id, user.roles)]
