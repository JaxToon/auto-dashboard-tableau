"""Audit repository: security and lifecycle events with minimized details.

Details must never contain raw prompts, result values, credentials or tokens.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db import AuditEvent
from .identity import UserContext

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
log = logging.getLogger("pbac.audit")

_FORBIDDEN_DETAIL_KEYS = {"question", "prompt", "token", "secret", "password", "pat_secret", "api_key"}


def record_event(
    db: Session,
    actor: UserContext | str,
    action: str,
    *,
    status: str = "success",
    resource_type: str = "",
    resource_id: str = "",
    policy_ref: str = "",
    details: dict | None = None,
    commit: bool = True,
) -> AuditEvent:
    clean = {k: v for k, v in (details or {}).items() if k.lower() not in _FORBIDDEN_DETAIL_KEYS}
    actor_id = actor.user_id if isinstance(actor, UserContext) else actor
    ev = AuditEvent(
        request_id=request_id_var.get(),
        actor_id=actor_id,
        actor_type="user" if isinstance(actor, UserContext) else "service",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        status=status,
        policy_ref=policy_ref,
        details=clean,
    )
    db.add(ev)
    if commit:
        db.commit()
    log.info("audit action=%s status=%s actor=%s resource=%s:%s request_id=%s", action, status, actor_id, resource_type, resource_id, ev.request_id)
    return ev


def query_events(db: Session, user: UserContext, *, limit: int = 200, action_prefix: str | tuple[str, ...] | None = None, own_only: bool = False) -> list[AuditEvent]:
    """Auditors see all events; everyone else sees only events they performed."""
    stmt = select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(min(limit, 1000))
    if own_only or not user.has("audit:view_all"):
        stmt = stmt.where(AuditEvent.actor_id == user.user_id)
    if action_prefix:
        prefixes = (action_prefix,) if isinstance(action_prefix, str) else action_prefix
        stmt = stmt.where(or_(*(AuditEvent.action.startswith(p) for p in prefixes)))
    return list(db.execute(stmt).scalars())


def serialize(ev: AuditEvent) -> dict:
    return {
        "id": ev.id,
        "timestamp": ev.timestamp.isoformat(),
        "request_id": ev.request_id,
        "actor_id": ev.actor_id,
        "actor_type": ev.actor_type,
        "action": ev.action,
        "resource_type": ev.resource_type,
        "resource_id": ev.resource_id,
        "status": ev.status,
        "policy_ref": ev.policy_ref,
        "details": ev.details,
    }
