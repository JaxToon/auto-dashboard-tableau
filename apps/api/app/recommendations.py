"""Recurring-question intelligence (P1, behind FEATURE_RECURRING_RECOMMENDATIONS).

Events hold only normalized intent metadata: no raw prompt, no result values, no
identity (an optional salted pseudonymous group). Frequency alone never creates a
recommendation: recurrence over time, audience diversity and dashboard suitability are
also required, and a Business Analytics reviewer decides.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .audit import record_event
from .dashboards import create_draft
from .db import DashboardRecommendation, QueryIntentEvent
from .errors import Conflict, Forbidden, NotFound
from .identity import UserContext
from .planner import _add_months, _period_label
from .runtime import Container
from .schema import Intent, Period

_process_salt = secrets.token_bytes(16)

# Deterministic SIMULATED demo events: (intent_key, dataset, metrics, dims, period_type, output, count, actors, spread_days)
SIMULATED = [
    ("total_outstanding__region__trend", "demo_sme_credit", ["total_outstanding"], ["region", "report_month"], "rolling_12_months", "answer", 14, 5, 27),
    ("target_achievement_pct__region__snapshot", "demo_sme_credit", ["target_achievement_pct"], ["region"], "single_month", "chart", 11, 4, 24),
    ("total_disbursement__product_segment__trend", "demo_sme_credit", ["total_disbursement"], ["product_segment", "report_month"], "rolling_12_months", "chart", 7, 2, 20),
    ("total_outstanding__total__snapshot", "demo_sme_credit", ["total_outstanding"], [], "single_month", "answer", 15, 6, 25),
    ("total_deposit_balance__region__trend", "demo_deposit_funding", ["total_deposit_balance"], ["region", "report_month"], "rolling_12_months", "chart", 12, 3, 26),
]


def actor_group(c: Container, user_id: str) -> str:
    salt = c.settings.telemetry_actor_salt.get_secret_value().encode() or _process_salt
    return "grp_" + hmac.new(salt, user_id.encode(), hashlib.sha256).hexdigest()[:12]


def record_intent_event(db: Session, c: Container, user: UserContext, intent: Intent, result_status: str = "success") -> None:
    if not (c.runtime.flag("feature_recurring_recommendations") and c.runtime.flag("feature_recommendation_telemetry")):
        return
    dims = list(intent.dimension_ids) + (["report_month"] if intent.time_grain else [])
    db.add(QueryIntentEvent(
        event_date_bucket=date.today().isoformat(),
        intent_key=intent.intent_key,
        dataset_id=intent.dataset_id,
        metric_ids=intent.metric_ids,
        dimension_ids=dims,
        period_type=intent.period.period_type,
        output_type={"ANSWER_ONLY": "answer", "CHART_OR_ANALYSIS": "chart"}.get(intent.output_decision, "dashboard"),
        result_status=result_status,
        actor_group=actor_group(c, user.user_id),
        simulated=False,
    ))
    db.commit()


def seed_simulated_events(db: Session) -> None:
    if db.execute(select(func.count()).select_from(QueryIntentEvent).where(QueryIntentEvent.simulated.is_(True))).scalar():
        return
    today = date.today()
    for key, ds, metrics, dims, ptype, out, count, actors, spread in SIMULATED:
        for i in range(count):
            db.add(QueryIntentEvent(
                event_date_bucket=(today - timedelta(days=(i * spread) // max(count - 1, 1))).isoformat(),
                intent_key=key, dataset_id=ds, metric_ids=metrics, dimension_ids=dims, period_type=ptype,
                output_type=out, result_status="success", actor_group=f"sim_group_{i % actors + 1}", simulated=True,
            ))
    db.commit()


def _topic(c: Container, ds_id: str, metrics: list[str], dims: list[str]) -> str:
    ds = c.catalog.dataset(ds_id)
    names = " & ".join(c.catalog.metric(m).name for m in metrics if c.catalog.metric(m))
    cats = [ds.field(d).name for d in dims if d != ds.date_field and ds.field(d)]
    shape = " monthly trend" if ds.date_field in dims else ""
    return f"{names}{' by ' + ' and '.join(cats) if cats else ''}{shape}"


def analyze(db: Session, c: Container, user: UserContext) -> dict:
    user.require("recommendation:review")
    c.runtime.require("feature_recurring_recommendations", "Recurring recommendations")
    min_events, window, min_actors = c.runtime.value("rec_min_events"), c.runtime.value("rec_window_days"), c.runtime.value("rec_min_actors")
    since = (date.today() - timedelta(days=window)).isoformat()
    events = db.execute(select(QueryIntentEvent).where(QueryIntentEvent.event_date_bucket >= since, QueryIntentEvent.result_status == "success")).scalars().all()

    clusters: dict[str, list[QueryIntentEvent]] = defaultdict(list)
    for e in events:
        clusters[f"{e.dataset_id}|{e.intent_key}|{','.join(sorted(e.metric_ids))}|{','.join(sorted(e.dimension_ids))}"].append(e)

    evaluated = []
    for key, evs in clusters.items():
        e0 = evs[0]
        ds = c.catalog.dataset(e0.dataset_id)
        actors = {e.actor_group for e in evs if e.actor_group}
        weeks = {date.fromisoformat(e.event_date_bucket).isocalendar()[:2] for e in evs}
        metrics_ok = ds is not None and all(c.catalog.metric(m) and c.catalog.metric(m).dataset_id == ds.id for m in e0.metric_ids)
        has_view = bool(e0.dimension_ids)
        rules = {
            "min_events": {"ok": len(evs) >= min_events, "value": len(evs), "threshold": min_events},
            "audience_diversity": {"ok": len(actors) >= min_actors, "value": len(actors), "threshold": min_actors},
            "time_recurrence": {"ok": len(weeks) >= 2, "value": len(weeks), "threshold": 2},
            "stable_approved_kpis": {"ok": metrics_ok},
            "dashboard_suitable": {"ok": has_view, "note": "Needs a trend or breakdown; single-value lookups are better answered in chat."},
        }
        qualifies = all(r["ok"] for r in rules.values())
        simulated = any(e.simulated for e in evs)
        topic = _topic(c, e0.dataset_id, e0.metric_ids, e0.dimension_ids) if metrics_ok else e0.intent_key
        item = {"cluster_key": key, "topic": topic, "dataset_id": e0.dataset_id, "count": len(evs), "qualifies": qualifies, "rules": rules, "simulated": simulated}
        evaluated.append(item)
        if not qualifies:
            continue
        summary = {"topic": topic, "count": len(evs), "window_days": window, "weeks": len(weeks), "actors": len(actors),
                   "trend": ds is not None and ds.date_field in e0.dimension_ids}
        rec = db.execute(select(DashboardRecommendation).where(DashboardRecommendation.cluster_key == key)).scalar_one_or_none()
        if rec is None:
            rec = DashboardRecommendation(cluster_key=key, topic=topic, intent_key=e0.intent_key, dataset_id=e0.dataset_id,
                                          metric_ids=e0.metric_ids, dimension_ids=e0.dimension_ids, period_type=e0.period_type,
                                          frequency_summary="", rationale="", evaluation={}, simulated=simulated)
            db.add(rec)
        rec.frequency_summary = f"{len(evs)} similar questions in {window} days, {len(weeks)} weeks, {len(actors)} groups"
        rec.rationale = c.planner.explain_recommendation(summary)
        rec.evaluation = rules
    record_event(db, user, "recommendation.analyze", commit=False, details={"clusters": len(clusters), "qualifying": sum(1 for e in evaluated if e["qualifies"])})
    db.commit()
    return {"thresholds": {"min_events": min_events, "window_days": window, "min_actors": min_actors}, "clusters": sorted(evaluated, key=lambda x: -x["count"])}


def serialize(rec: DashboardRecommendation, c: Container) -> dict:
    ds = c.catalog.dataset(rec.dataset_id)
    return {
        "id": rec.id,
        "topic": rec.topic,
        "intent_key": rec.intent_key,
        "dataset_id": rec.dataset_id,
        "dataset_name": ds.name if ds else rec.dataset_id,
        "metrics": [{"id": m, "name": c.catalog.metric(m).name} for m in rec.metric_ids if c.catalog.metric(m)],
        "dimensions": [{"id": d, "name": ds.field(d).name} for d in rec.dimension_ids if ds and ds.field(d)],
        "period_type": rec.period_type,
        "frequency_summary": rec.frequency_summary,
        "rationale": rec.rationale,
        "evaluation": rec.evaluation,
        "status": rec.status,
        "simulated": rec.simulated,
        "linked_draft_id": rec.linked_draft_id,
        "updated_at": rec.updated_at.isoformat(),
    }


def list_recommendations(db: Session, c: Container, user: UserContext) -> list[DashboardRecommendation]:
    user.require("recommendation:view")
    c.runtime.require("feature_recurring_recommendations", "Recurring recommendations")
    recs = db.execute(select(DashboardRecommendation).order_by(DashboardRecommendation.updated_at.desc())).scalars().all()
    visible = [r for r in recs if c.catalog.can_access(r.dataset_id, user.roles)]
    if not user.has("recommendation:review"):
        visible = [r for r in visible if r.status in ("accepted", "converted")]
    return visible


def decide(db: Session, c: Container, user: UserContext, rec_id: str, decision: str) -> dict:
    c.runtime.require("feature_recurring_recommendations", "Recurring recommendations")
    rec = db.get(DashboardRecommendation, rec_id)
    if rec is None or not c.catalog.can_access(rec.dataset_id, user.roles):
        raise NotFound("Recommendation not found.", code="RECOMMENDATION_NOT_FOUND")
    if decision in ("accept", "dismiss"):
        user.require("recommendation:review")
        rec.status = "accepted" if decision == "accept" else "dismissed"
        rec.decided_by = user.user_id
        record_event(db, user, f"recommendation.{decision}", resource_type="recommendation", resource_id=rec.id, commit=False)
        db.commit()
        return {"recommendation": serialize(rec, c)}

    # create_draft: same planning, validation, review and publishing controls as any question.
    if rec.status not in ("accepted", "converted") and not user.has("recommendation:review"):
        raise Forbidden("This recommendation has not been accepted by a reviewer yet.", code="RECOMMENDATION_NOT_ACCEPTED")
    if rec.status == "dismissed":
        raise Conflict("Dismissed recommendations cannot be converted.", code="RECOMMENDATION_DISMISSED")
    ds = c.catalog.dataset(rec.dataset_id)
    latest = c.coverage(ds.id)[1]
    single = rec.period_type == "single_month"
    start = latest if single else _add_months(latest, -11)
    trend = ds.date_field in rec.dimension_ids
    cats = [d for d in rec.dimension_ids if d != ds.date_field]
    intent = Intent(
        dataset_id=ds.id, metric_ids=rec.metric_ids, dimension_ids=cats, time_grain="month" if trend else None,
        period=Period(start=start, end=latest, period_type=rec.period_type, label=_period_label(start, latest)),
        output_decision="DASHBOARD_RECOMMENDATION", decision_reason="Created from an accepted recurring-question recommendation.",
        intent_key=rec.intent_key, summary=f"{rec.topic}, {_period_label(start, latest)} ({ds.name})",
        assumptions=["Created from a recurring-question recommendation" + (" based on SIMULATED events." if rec.simulated else ".")],
    )
    draft = create_draft(db, c, user, intent, force_dashboard=True, recommendation_id=rec.id)
    rec.status, rec.linked_draft_id = "converted", draft.id
    record_event(db, user, "recommendation.convert", resource_type="recommendation", resource_id=rec.id, commit=False, details={"draft_id": draft.id})
    db.commit()
    return {"recommendation": serialize(rec, c), "draft_id": draft.id}
