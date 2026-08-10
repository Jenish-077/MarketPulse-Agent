"""In-process backend for Streamlit Cloud (no separate FastAPI process required)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func

from packages.agent import run_intel_cycle
from packages.db.models import (
    AgentRun,
    Alert,
    AuditLog,
    CompanyContext,
    Competitor,
    CompetitorURL,
    Feedback,
    ImportanceFactor,
    ScoringWeights,
    get_session_factory,
    init_db,
)
from packages.rag import upsert_context
from packages.schemas import FeedbackLabel
from packages.scoring import propose_weight_update
from packages.settings import clear_settings_cache, get_settings


def ensure_db() -> None:
    init_db()


def list_competitors() -> list[dict[str, Any]]:
    Session = get_session_factory()
    with Session() as db:
        rows = db.query(Competitor).all()
        return [
            {
                "id": str(c.id),
                "name": c.name,
                "website": c.website,
                "notes": c.notes,
                "urls": [
                    {
                        "id": str(u.id),
                        "url": u.url,
                        "label": u.label,
                        "enabled": u.enabled,
                        "last_status": u.last_status,
                    }
                    for u in c.urls
                ],
            }
            for c in rows
        ]


def create_competitor(name: str, website: str | None = None) -> dict[str, Any] | None:
    Session = get_session_factory()
    with Session() as db:
        if db.query(Competitor).filter(Competitor.name == name).first():
            return None
        c = Competitor(name=name, website=website)
        db.add(c)
        db.commit()
        db.refresh(c)
        return {"id": str(c.id), "name": c.name}


def add_competitor_url(competitor_id: str, url: str, label: str = "other", enabled: bool = True) -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        u = CompetitorURL(
            competitor_id=uuid.UUID(competitor_id),
            url=url,
            label=label,
            enabled=enabled,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return {"id": str(u.id), "url": u.url, "label": u.label}


def list_company_context() -> list[dict[str, Any]]:
    Session = get_session_factory()
    with Session() as db:
        return [
            {
                "id": str(r.id),
                "title": r.title,
                "kind": r.kind,
                "content": r.content[:2000],
            }
            for r in db.query(CompanyContext).order_by(CompanyContext.created_at.desc()).all()
        ]


def add_company_context(title: str, content: str, kind: str = "product_summary") -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        row = upsert_context(db, title, content, kind)
        return {"id": str(row.id), "title": row.title, "kind": row.kind}


def list_importance_factors() -> list[dict[str, Any]]:
    Session = get_session_factory()
    with Session() as db:
        return [
            {
                "id": str(f.id),
                "name": f.name,
                "weight": f.weight,
                "keywords": f.keywords,
                "description": f.description,
            }
            for f in db.query(ImportanceFactor).all()
        ]


def add_importance_factor(name: str, keywords: list[str], weight: float = 1.0, description: str = "") -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        f = ImportanceFactor(name=name, description=description, weight=weight, keywords=keywords)
        db.add(f)
        db.commit()
        db.refresh(f)
        return {"id": str(f.id), "name": f.name}


def trigger_run() -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        run = run_intel_cycle(db)
        return {
            "id": str(run.id),
            "status": run.status,
            "summary": run.summary,
            "trace": run.trace,
            "cost": run.cost,
        }


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    Session = get_session_factory()
    with Session() as db:
        rows = db.query(AgentRun).order_by(AgentRun.started_at.desc()).limit(limit).all()
        return [
            {
                "id": str(r.id),
                "status": r.status,
                "summary": r.summary,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "cost": r.cost,
                "trace": r.trace,
            }
            for r in rows
        ]


def list_alerts(limit: int = 50) -> list[dict[str, Any]]:
    Session = get_session_factory()
    with Session() as db:
        rows = db.query(Alert).order_by(Alert.created_at.desc()).limit(limit).all()
        return [
            {
                "id": str(a.id),
                "title": a.title,
                "what_changed": a.what_changed,
                "why_it_matters": a.why_it_matters,
                "suggested_action": a.suggested_action,
                "source_url": a.source_url,
                "quoted_snippet": a.quoted_snippet,
                "score": a.score,
                "score_breakdown": a.score_breakdown,
                "needs_human_review": a.needs_human_review,
                "critic_passed": a.critic_passed,
                "status": a.status,
                "delivered_via": a.delivered_via,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "feedback": [
                    {"label": f.label, "weight_deltas": f.weight_deltas, "note": f.note} for f in a.feedback
                ],
            }
            for a in rows
        ]


def submit_feedback(alert_id: str, label: str, note: str | None = None) -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        alert = db.get(Alert, uuid.UUID(alert_id))
        if not alert:
            raise ValueError("Alert not found")
        weights_row = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).first()
        current = weights_row.weights if weights_row else {}
        event_type = (alert.score_breakdown or {}).get("event_type") or "other"
        fb_label = FeedbackLabel(label)
        new_w, deltas, notes = propose_weight_update(current, fb_label, event_type, alert.score_breakdown)
        fb = Feedback(
            alert_id=alert.id,
            label=fb_label.value,
            note=note,
            weight_deltas={"deltas": deltas, "notes": notes, "before": current, "after": new_w},
        )
        db.add(fb)
        db.add(ScoringWeights(weights=new_w, reason=f"feedback:{fb_label.value}:{alert.id}"))
        db.commit()
        return {
            "feedback_id": str(fb.id),
            "label": fb_label.value,
            "before_breakdown": alert.score_breakdown,
            "weight_change": {"deltas": deltas, "notes": notes, "after": new_w},
        }


def get_weights() -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        row = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).first()
        history = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).limit(10).all()
        return {
            "current": row.weights if row else {},
            "updated_at": row.updated_at.isoformat() if row else None,
            "history": [
                {
                    "id": h.id,
                    "reason": h.reason,
                    "updated_at": h.updated_at.isoformat(),
                    "weights": h.weights,
                }
                for h in history
            ],
        }


def quality_metrics() -> dict[str, Any]:
    Session = get_session_factory()
    with Session() as db:
        rows = (
            db.query(Feedback.label, func.date_trunc("day", Feedback.created_at).label("day"), func.count())
            .group_by(Feedback.label, "day")
            .order_by("day")
            .all()
        )
        by_day: dict[str, dict[str, int]] = {}
        for label, day, count in rows:
            key = day.date().isoformat() if hasattr(day, "date") else str(day)
            by_day.setdefault(key, {"useful": 0, "meh": 0, "noise": 0, "total": 0})
            by_day[key][label] = count
            by_day[key]["total"] += count
        series = []
        for day, counts in sorted(by_day.items()):
            pct = (counts["useful"] / counts["total"] * 100) if counts["total"] else 0
            series.append({"day": day, "useful_pct": round(pct, 1), **counts})
        total_fb = db.query(Feedback).count()
        useful = db.query(Feedback).filter(Feedback.label == FeedbackLabel.useful.value).count()
        return {
            "series": series,
            "overall_useful_pct": round((useful / total_fb * 100) if total_fb else 0, 1),
            "total_feedback": total_fb,
        }


def audit_log(limit: int = 100) -> list[dict[str, Any]]:
    Session = get_session_factory()
    with Session() as db:
        rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
        return [
            {
                "id": str(r.id),
                "action": r.action,
                "url": r.url,
                "detail": r.detail,
                "meta": r.meta,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def public_settings() -> dict[str, Any]:
    clear_settings_cache()
    s = get_settings()
    return {
        "alert_score_threshold": s.alert_score_threshold,
        "review_band": [s.review_band_low, s.review_band_high],
        "max_pages_per_run": s.max_pages_per_run,
        "llm_enabled": s.llm_enabled,
        "model": s.openai_model,
    }
