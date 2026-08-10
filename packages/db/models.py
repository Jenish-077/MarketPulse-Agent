"""SQLAlchemy models + session for SignalWatch."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from packages.settings import get_settings

EMBED_DIM = 1536


class Base(DeclarativeBase):
    pass


class Competitor(Base):
    __tablename__ = "competitors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    website: Mapped[str | None] = mapped_column(String(1024))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    urls: Mapped[list[CompetitorURL]] = relationship(back_populates="competitor", cascade="all, delete-orphan")


class CompetitorURL(Base):
    __tablename__ = "competitor_urls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    competitor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitors.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    label: Mapped[str] = mapped_column(String(64), default="other")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_status: Mapped[str | None] = mapped_column(String(64))

    competitor: Mapped[Competitor] = relationship(back_populates="urls")
    snapshots: Mapped[list[Snapshot]] = relationship(back_populates="url_ref", cascade="all, delete-orphan")


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("competitor_urls.id", ondelete="CASCADE"))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_html: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    fetch_method: Mapped[str] = mapped_column(String(32), default="httpx")
    http_status: Mapped[int | None] = mapped_column(Integer)

    url_ref: Mapped[CompetitorURL] = relationship(back_populates="snapshots")


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competitors.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source_url: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    competitor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("competitors.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(512))
    what_changed: Mapped[str] = mapped_column(Text)
    why_it_matters: Mapped[str] = mapped_column(Text)
    suggested_action: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(2048))
    quoted_snippet: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    critic_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    critic_notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(64), default="sent")  # sent | review | suppressed
    delivered_via: Mapped[str] = mapped_column(String(64), default="console")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    feedback: Mapped[list[Feedback]] = relationship(back_populates="alert", cascade="all, delete-orphan")


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    weight_deltas: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    alert: Mapped[Alert] = relationship(back_populates="feedback")


class ScoringWeights(Base):
    __tablename__ = "scoring_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reason: Mapped[str] = mapped_column(String(255), default="init")


class CompanyContext(Base):
    __tablename__ = "company_context"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(64), default="product_summary")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ImportanceFactor(Base):
    __tablename__ = "importance_factors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    keywords: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(64), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    trace: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    cost: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(2048))
    detail: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NoiseRule(Base):
    __tablename__ = "noise_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pattern: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session_factory():
    get_engine()
    return _SessionLocal


def get_db():
    Session = get_session_factory()
    db = Session()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize schema on Postgres+pgvector (docker compose db service)."""
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        raise RuntimeError(
            "SignalWatch requires PostgreSQL + pgvector (e.g. Supabase). "
            "Set DATABASE_URL in .env to a postgresql+psycopg:// connection string."
        )
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(bind=engine)

    # Seed default scoring weights if empty
    Session = get_session_factory()
    with Session() as db:
        existing = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).first()
        if not existing:
            from packages.scoring.weights import DEFAULT_WEIGHTS

            db.add(ScoringWeights(weights=DEFAULT_WEIGHTS, reason="init"))
            db.commit()
    print("Database initialized.")
