"""Identity, roles and session handling.

Roles are always derived server-side from the user store; the frontend never supplies a
role. Mock login exists only for isolated local development and is labelled as mock.
A pilot must replace MockIdentityProvider with the bank's approved SSO and role mapping.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionToken, User, UserRole, utcnow
from .errors import Forbidden, NotAuthenticated, RateLimited

SESSION_COOKIE = "pbac_session"

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "business_user": {"analytics:ask", "dashboard:create", "dashboard:publish", "recommendation:view"},
    "ba_reviewer": {
        "analytics:ask",
        "dashboard:create",
        "dashboard:publish",
        "dashboard:review",
        "recommendation:view",
        "recommendation:review",
    },
    "data_steward": {"analytics:ask", "catalog:view_all"},
    "admin": {"admin:settings", "admin:integrations"},
    "auditor": {"audit:view_all"},
}

MOCK_USERS: list[tuple[str, str, list[str]]] = [
    ("demo.business", "Demo Business User", ["business_user"]),
    ("demo.business2", "Demo Business User 2", ["business_user"]),
    ("demo.reviewer", "Demo BA Reviewer", ["ba_reviewer"]),
    ("demo.steward", "Demo Data Steward", ["data_steward"]),
    ("demo.admin", "Demo PoC Admin", ["admin"]),
    ("demo.auditor", "Demo Auditor", ["auditor"]),
]


@dataclass(frozen=True)
class UserContext:
    user_id: str
    display_name: str
    roles: list[str]
    is_mock: bool = True
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.has(permission):
            raise Forbidden("Your role does not allow this action.", code="PERMISSION_DENIED", details={"permission": permission})

    def public(self) -> dict:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "roles": self.roles,
            "permissions": sorted(self.permissions),
            "is_mock": self.is_mock,
        }


def build_context(user: User) -> UserContext:
    roles = sorted(r.role for r in user.roles)
    perms = frozenset().union(*(ROLE_PERMISSIONS.get(r, set()) for r in roles)) if roles else frozenset()
    return UserContext(user.id, user.display_name, roles, user.is_mock, perms)


def seed_mock_users(db: Session) -> None:
    for uid, name, roles in MOCK_USERS:
        if db.get(User, uid) is None:
            u = User(id=uid, display_name=name, is_mock=True)
            u.roles = [UserRole(role=r) for r in roles]
            db.add(u)
    db.commit()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user_id: str, ttl_minutes: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(SessionToken(token_hash=_hash(token), user_id=user_id, expires_at=utcnow() + timedelta(minutes=ttl_minutes)))
    db.commit()
    return token


def revoke_session(db: Session, token: str | None) -> None:
    if token:
        row = db.get(SessionToken, _hash(token))
        if row:
            db.delete(row)
            db.commit()


def resolve_session(db: Session, token: str | None) -> UserContext:
    if not token:
        raise NotAuthenticated("Please sign in.")
    row = db.get(SessionToken, _hash(token))
    if row is None:
        raise NotAuthenticated("Session expired. Please sign in again.")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=utcnow().tzinfo)
    if expires < utcnow():
        db.delete(row)
        db.commit()
        raise NotAuthenticated("Session expired. Please sign in again.")
    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one()
    return build_context(user)


class RateLimiter:
    """Simple in-process sliding-window limiter (per user). Replace with a shared store for multi-instance."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_s: float = 60.0) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            raise RateLimited("Too many requests. Please wait a moment and try again.")
        q.append(now)


rate_limiter = RateLimiter()
