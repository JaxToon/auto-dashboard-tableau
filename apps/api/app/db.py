"""Persistence: SQLAlchemy models and session handling.

SQLite is used for the isolated local prototype; the same models run on PostgreSQL
via DATABASE_URL (see docs/architecture/architecture-decision-record.md, ADR-004).
Tableau tokens and model API keys are never stored in these tables.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    roles: Mapped[list[UserRole]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(40))
    user: Mapped[User] = relationship(back_populates="roles")


class SessionToken(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DashboardDraft(Base):
    __tablename__ = "dashboard_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="Draft", index=True)
    dataset_id: Mapped[str] = mapped_column(String(80))
    dataset_version: Mapped[str] = mapped_column(String(20))
    catalog_version: Mapped[str] = mapped_column(String(20))
    metric_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    spec: Mapped[dict] = mapped_column(JSON)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    interpretation_summary: Mapped[str] = mapped_column(Text, default="")
    output_decision: Mapped[str] = mapped_column(String(40), default="DASHBOARD_RECOMMENDATION")
    validation_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validated_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recommendation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class DashboardVersion(Base):
    __tablename__ = "dashboard_versions"
    __table_args__ = (UniqueConstraint("draft_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    draft_id: Mapped[str] = mapped_column(ForeignKey("dashboard_drafts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DashboardPublication(Base):
    __tablename__ = "dashboard_publications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    draft_id: Mapped[str] = mapped_column(ForeignKey("dashboard_drafts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16))  # "rest" (real) or "mock"
    status: Mapped[str] = mapped_column(String(32))  # Publishing | Published | Failed | MockPublished
    server_url: Mapped[str] = mapped_column(String(300), default="")
    site_content_url: Mapped[str] = mapped_column(String(120), default="")
    project_id: Mapped[str] = mapped_column(String(80), default="")
    project_name: Mapped[str] = mapped_column(String(200), default="")
    content_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    content_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    publisher_ref: Mapped[str] = mapped_column(String(120), default="")
    requested_by: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewAction(Base):
    __tablename__ = "review_actions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    draft_id: Mapped[str] = mapped_column(ForeignKey("dashboard_drafts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(20))  # approved | changes_requested
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueryIntentEvent(Base):
    """Minimized telemetry: no raw prompt, no result values, no identity."""

    __tablename__ = "query_intent_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_date_bucket: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    intent_key: Mapped[str] = mapped_column(String(120))
    dataset_id: Mapped[str] = mapped_column(String(80))
    metric_ids: Mapped[list] = mapped_column(JSON)
    dimension_ids: Mapped[list] = mapped_column(JSON)
    period_type: Mapped[str] = mapped_column(String(40))
    output_type: Mapped[str] = mapped_column(String(40))
    result_status: Mapped[str] = mapped_column(String(20))
    actor_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)


class DashboardRecommendation(Base):
    __tablename__ = "dashboard_recommendations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cluster_key: Mapped[str] = mapped_column(String(300), unique=True)
    topic: Mapped[str] = mapped_column(String(200))
    intent_key: Mapped[str] = mapped_column(String(120))
    dataset_id: Mapped[str] = mapped_column(String(80))
    metric_ids: Mapped[list] = mapped_column(JSON)
    dimension_ids: Mapped[list] = mapped_column(JSON)
    period_type: Mapped[str] = mapped_column(String(40))
    frequency_summary: Mapped[str] = mapped_column(String(200))
    rationale: Mapped[str] = mapped_column(Text)
    evaluation: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="candidate")  # candidate|accepted|dismissed|converted
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    linked_draft_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    actor_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_type: Mapped[str] = mapped_column(String(20), default="user")  # user | service
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(40), default="")
    resource_id: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(20))  # success | denied | failure
    policy_ref: Mapped[str] = mapped_column(String(80), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class RuntimeSetting(Base):
    __tablename__ = "runtime_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> None:
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, **kwargs)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    Base.metadata.create_all(_engine)


def get_db() -> Iterator[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None
    return _SessionLocal
