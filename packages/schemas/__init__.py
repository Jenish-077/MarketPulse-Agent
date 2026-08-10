"""Pydantic schemas for SignalWatch events, alerts, traces."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, HttpUrl


class PageLabel(str, Enum):
    pricing = "pricing"
    changelog = "changelog"
    blog = "blog"
    product = "product"
    other = "other"


class EventType(str, Enum):
    pricing_change = "pricing_change"
    plan_change = "plan_change"
    feature_launch = "feature_launch"
    feature_removal = "feature_removal"
    positioning_change = "positioning_change"
    blog_announcement = "blog_announcement"
    changelog_entry = "changelog_entry"
    other = "other"
    noise = "noise"


class FeedbackLabel(str, Enum):
    useful = "useful"
    meh = "meh"
    noise = "noise"


class ExtractedEntities(BaseModel):
    plan: str | None = None
    old_price: str | None = None
    new_price: str | None = None
    feature_area: str | None = None
    target_segment: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CompetitiveEvent(BaseModel):
    """Structured finding from a competitor page change."""

    id: UUID = Field(default_factory=uuid4)
    source: str = "competitor_page"
    competitor: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: EventType
    raw_snippet: str
    source_url: str
    extracted_entities: ExtractedEntities = Field(default_factory=ExtractedEntities)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    page_label: PageLabel | None = None
    changed_section: str | None = None


class ScoreBreakdown(BaseModel):
    event_type_weight: float = 0.0
    pricing_signal: float = 0.0
    feature_overlap: float = 0.0
    icp_relevance: float = 0.0
    user_importance: float = 0.0
    confidence_factor: float = 0.0
    corroboration_boost: float = 0.0
    noise_penalty: float = 0.0
    total: float = 0.0
    explanation: list[str] = Field(default_factory=list)
    weights_used: dict[str, float] = Field(default_factory=dict)


class AlertDraft(BaseModel):
    title: str
    what_changed: str
    why_it_matters: str
    suggested_action: str
    source_url: str
    quoted_snippet: str
    needs_human_review: bool = False
    critic_passed: bool = False
    critic_notes: str = ""
    company_context_used: list[str] = Field(default_factory=list)
    similar_past_moves: list[str] = Field(default_factory=list)


class TraceStep(BaseModel):
    step: str
    status: str  # ok | retry | degraded | skipped | error
    started_at: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: int = 0
    detail: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class RunCost(BaseModel):
    scrape_ms: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_calls: int = 0
    embedding_calls: int = 0


class CompetitorCreate(BaseModel):
    name: str
    website: str | None = None
    notes: str | None = None


class CompetitorURLCreate(BaseModel):
    url: str
    label: PageLabel = PageLabel.other
    enabled: bool = True


class CompetitorOut(BaseModel):
    id: UUID
    name: str
    website: str | None = None
    notes: str | None = None
    urls: list[dict[str, Any]] = Field(default_factory=list)


class CompanyContextIn(BaseModel):
    title: str
    content: str
    kind: str = "product_summary"  # product_summary | differentiators | icp | priorities


class FeedbackIn(BaseModel):
    alert_id: UUID
    label: FeedbackLabel
    note: str | None = None


class ImportanceFactorIn(BaseModel):
    name: str
    description: str = ""
    weight: float = 1.0
    keywords: list[str] = Field(default_factory=list)


class PolicyConfig(BaseModel):
    allowed_domains: list[str] = Field(default_factory=list)
    max_pages_per_run: int = 20
    disallow_login_paths: list[str] = Field(
        default_factory=lambda: ["/login", "/signin", "/auth", "/account", "/dashboard"]
    )
    request_delay_seconds: float = 1.0
    respect_robots: bool = True
