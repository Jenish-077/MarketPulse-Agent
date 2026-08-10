"""FastAPI backend for SignalWatch."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

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
    NoiseRule,
    ScoringWeights,
    get_db,
    init_db,
)
from packages.rag import upsert_context
from packages.schemas import (
    CompanyContextIn,
    CompetitorCreate,
    CompetitorURLCreate,
    FeedbackIn,
    FeedbackLabel,
    ImportanceFactorIn,
)
from packages.scoring import propose_weight_update
from packages.settings import get_settings

app = FastAPI(title="SignalWatch API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    try:
        init_db()
    except Exception as e:
        print(f"DB init deferred: {e}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "signalwatch"}


# --- Competitors CRUD ---


@app.get("/competitors")
def list_competitors(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.query(Competitor).all()
    out = []
    for c in rows:
        out.append(
            {
                "id": str(c.id),
                "name": c.name,
                "website": c.website,
                "notes": c.notes,
                "urls": [
                    {"id": str(u.id), "url": u.url, "label": u.label, "enabled": u.enabled, "last_status": u.last_status}
                    for u in c.urls
                ],
            }
        )
    return out


@app.post("/competitors")
def create_competitor(body: CompetitorCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    if db.query(Competitor).filter(Competitor.name == body.name).first():
        raise HTTPException(400, "Competitor already exists")
    c = Competitor(name=body.name, website=body.website, notes=body.notes)
    db.add(c)
    db.commit()
    db.refresh(c)
    return {"id": str(c.id), "name": c.name}


@app.post("/competitors/{competitor_id}/urls")
def add_url(competitor_id: uuid.UUID, body: CompetitorURLCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    c = db.get(Competitor, competitor_id)
    if not c:
        raise HTTPException(404, "Competitor not found")
    u = CompetitorURL(competitor_id=c.id, url=body.url, label=body.label.value, enabled=body.enabled)
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"id": str(u.id), "url": u.url, "label": u.label}


@app.delete("/competitors/{competitor_id}")
def delete_competitor(competitor_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, str]:
    c = db.get(Competitor, competitor_id)
    if not c:
        raise HTTPException(404, "Not found")
    db.delete(c)
    db.commit()
    return {"status": "deleted"}


# --- Company context / RAG ---


@app.post("/company-context")
def add_context(body: CompanyContextIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = upsert_context(db, body.title, body.content, body.kind)
    return {"id": str(row.id), "title": row.title, "kind": row.kind}


@app.get("/company-context")
def list_context(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {"id": str(r.id), "title": r.title, "kind": r.kind, "content": r.content[:500]}
        for r in db.query(CompanyContext).order_by(CompanyContext.created_at.desc()).all()
    ]


# --- Importance factors / noise ---


@app.post("/importance-factors")
def add_factor(body: ImportanceFactorIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    f = ImportanceFactor(name=body.name, description=body.description, weight=body.weight, keywords=body.keywords)
    db.add(f)
    db.commit()
    db.refresh(f)
    return {"id": str(f.id), "name": f.name}


@app.get("/importance-factors")
def list_factors(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {"id": str(f.id), "name": f.name, "weight": f.weight, "keywords": f.keywords, "description": f.description}
        for f in db.query(ImportanceFactor).all()
    ]


@app.post("/noise-rules")
def add_noise_rule(pattern: str, description: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    r = NoiseRule(pattern=pattern, description=description)
    db.add(r)
    db.commit()
    return {"id": str(r.id), "pattern": r.pattern}


# --- Runs / alerts / feedback ---


@app.post("/runs/trigger")
def trigger_run(db: Session = Depends(get_db)) -> dict[str, Any]:
    run = run_intel_cycle(db)
    return {
        "id": str(run.id),
        "status": run.status,
        "summary": run.summary,
        "trace": run.trace,
        "cost": run.cost,
    }


@app.get("/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
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


@app.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> dict[str, Any]:
    r = db.get(AgentRun, run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    return {
        "id": str(r.id),
        "status": r.status,
        "summary": r.summary,
        "trace": r.trace,
        "cost": r.cost,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
    }


@app.get("/alerts")
def list_alerts(limit: int = 50, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
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
            "feedback": [{"label": f.label, "weight_deltas": f.weight_deltas, "note": f.note} for f in a.feedback],
        }
        for a in rows
    ]


@app.post("/feedback")
def submit_feedback(body: FeedbackIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    alert = db.get(Alert, body.alert_id)
    if not alert:
        raise HTTPException(404, "Alert not found")

    weights_row = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).first()
    current = weights_row.weights if weights_row else {}
    event_type = (alert.score_breakdown or {}).get("event_type") or "other"

    new_w, deltas, notes = propose_weight_update(current, body.label, event_type, alert.score_breakdown)
    fb = Feedback(alert_id=alert.id, label=body.label.value, note=body.note, weight_deltas={"deltas": deltas, "notes": notes, "before": current, "after": new_w})
    db.add(fb)
    db.add(ScoringWeights(weights=new_w, reason=f"feedback:{body.label.value}:{alert.id}"))
    db.commit()
    return {
        "feedback_id": str(fb.id),
        "label": body.label.value,
        "before_breakdown": alert.score_breakdown,
        "weight_change": {"deltas": deltas, "notes": notes, "after": new_w},
    }


@app.get("/weights")
def get_weights(db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).first()
    history = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).limit(10).all()
    return {
        "current": row.weights if row else {},
        "updated_at": row.updated_at.isoformat() if row else None,
        "history": [{"id": h.id, "reason": h.reason, "updated_at": h.updated_at.isoformat(), "weights": h.weights} for h in history],
    }


@app.get("/metrics/quality")
def quality_metrics(db: Session = Depends(get_db)) -> dict[str, Any]:
    """% useful alerts over time for dashboard chart."""
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


@app.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
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


@app.get("/settings/public")
def public_settings() -> dict[str, Any]:
    s = get_settings()
    return {
        "alert_score_threshold": s.alert_score_threshold,
        "review_band": [s.review_band_low, s.review_band_high],
        "max_pages_per_run": s.max_pages_per_run,
        "llm_enabled": s.llm_enabled,
        "model": s.openai_model,
    }
