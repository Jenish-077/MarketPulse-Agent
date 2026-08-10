"""LangGraph competitive intelligence pipeline."""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from packages.agent.actions import deliver_alert
from packages.agent.extract import extract_events
from packages.agent.summarize import critic_check, summarize_event
from packages.connectors import fetch_page
from packages.db.models import (
    AgentRun,
    Alert,
    AuditLog,
    Competitor,
    CompetitorURL,
    EventRecord,
    ImportanceFactor,
    NoiseRule,
    ScoringWeights,
    Snapshot,
)
from packages.diffing import cheap_text_diff
from packages.rag import relevance_scores
from packages.schemas import PageLabel, TraceStep
from packages.scoring import gate_decision, score_event
from packages.settings import get_settings

logger = logging.getLogger(__name__)


class IntelState(TypedDict, total=False):
    run_id: str
    planned_urls: list[dict[str, Any]]
    fetch_results: list[dict[str, Any]]
    events: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    cost: dict[str, Any]
    errors: list[str]
    status: str


def _add_trace(state: IntelState, step: str, status: str, detail: str = "", **meta: Any) -> None:
    state.setdefault("trace", []).append(
        TraceStep(step=step, status=status, detail=detail, meta=meta).model_dump(mode="json")
    )


def plan_node(state: IntelState, db: Session) -> IntelState:
    settings = get_settings()
    rows = (
        db.query(CompetitorURL, Competitor)
        .join(Competitor, CompetitorURL.competitor_id == Competitor.id)
        .filter(CompetitorURL.enabled.is_(True))
        .all()
    )
    planned = []
    for url_row, comp in rows[: settings.max_pages_per_run]:
        planned.append(
            {
                "url_id": str(url_row.id),
                "url": url_row.url,
                "label": url_row.label,
                "competitor_id": str(comp.id),
                "competitor": comp.name,
            }
        )
    state["planned_urls"] = planned
    _add_trace(state, "plan", "ok", f"planned {len(planned)} URLs", count=len(planned))
    return state


def collect_node(state: IntelState, db: Session) -> IntelState:
    results = []
    scrape_ms = 0
    for item in state.get("planned_urls", []):
        t0 = time.perf_counter()
        fr = fetch_page(item["url"])
        elapsed = int((time.perf_counter() - t0) * 1000)
        scrape_ms += fr.elapsed_ms or elapsed

        db.add(
            AuditLog(
                action="fetch",
                url=item["url"],
                detail=fr.error or f"{fr.method} status={fr.status_code}",
                meta={"ok": fr.ok, "method": fr.method, "hash": fr.content_hash},
            )
        )

        status = "ok" if fr.ok else "degraded"
        _add_trace(
            state,
            "fetch",
            status if fr.ok else "retry",
            f"{item['url']} via {fr.method}",
            ok=fr.ok,
            error=fr.error,
        )

        # Persist snapshot only on success
        if fr.ok:
            url_uuid = uuid.UUID(item["url_id"])
            snap = Snapshot(
                url_id=url_uuid,
                content_hash=fr.content_hash,
                normalized_text=fr.normalized_text,
                raw_html=fr.raw_html[:500000] if fr.raw_html else None,
                fetch_method=fr.method,
                http_status=fr.status_code,
            )
            db.add(snap)
            url_row = db.get(CompetitorURL, url_uuid)
            if url_row:
                url_row.last_fetched_at = datetime.utcnow()
                url_row.last_status = "ok"
        else:
            url_row = db.get(CompetitorURL, uuid.UUID(item["url_id"]))
            if url_row:
                url_row.last_status = f"failed: {fr.error}"
            state.setdefault("errors", []).append(f"{item['url']}: {fr.error}")

        results.append({**item, "fetch": fr.__dict__})
        db.commit()

    state["fetch_results"] = results
    state.setdefault("cost", {})
    state["cost"]["scrape_ms"] = scrape_ms
    _add_trace(state, "collect", "ok", f"fetched {len(results)} pages", scrape_ms=scrape_ms)
    return state


def diff_extract_node(state: IntelState, db: Session) -> IntelState:
    noise_patterns = [r.pattern for r in db.query(NoiseRule).filter(NoiseRule.active.is_(True)).all()]
    events_out: list[dict[str, Any]] = []

    for item in state.get("fetch_results", []):
        fr = item["fetch"]
        if not fr.get("ok"):
            _add_trace(state, "diff", "degraded", f"skip diff — fetch failed for {item['url']}")
            continue

        url_id = uuid.UUID(item["url_id"])
        snaps = (
            db.query(Snapshot)
            .filter(Snapshot.url_id == url_id)
            .order_by(Snapshot.fetched_at.desc())
            .limit(2)
            .all()
        )
        if len(snaps) < 2:
            _add_trace(state, "diff", "skipped", f"baseline only for {item['url']}")
            continue

        new_snap, old_snap = snaps[0], snaps[1]
        if new_snap.content_hash == old_snap.content_hash:
            _add_trace(state, "diff", "ok", f"no change {item['url']}")
            continue

        diff = cheap_text_diff(old_snap.normalized_text, new_snap.normalized_text, noise_patterns)
        if diff.is_noise_only or not diff.changed:
            _add_trace(
                state,
                "diff",
                "ok",
                f"noise/ignored change on {item['url']}",
                reasons=diff.noise_reasons,
            )
            continue

        _add_trace(
            state,
            "diff",
            "ok",
            f"change_ratio={diff.change_ratio} on {item['url']}",
            added=len(diff.added_lines),
            removed=len(diff.removed_lines),
        )

        label = None
        try:
            label = PageLabel(item.get("label") or "other")
        except ValueError:
            label = PageLabel.other

        for section in diff.changed_sections:
            extracted = extract_events(item["competitor"], item["url"], section, label)
            for ev in extracted:
                events_out.append(ev.model_dump(mode="json"))
                _add_trace(
                    state,
                    "extract",
                    "ok",
                    f"{ev.event_type.value} conf={ev.confidence}",
                    competitor=item["competitor"],
                )

    state["events"] = events_out
    return state


def score_summarize_act_node(state: IntelState, db: Session) -> IntelState:
    settings = get_settings()
    weights_row = db.query(ScoringWeights).order_by(ScoringWeights.id.desc()).first()
    weights = weights_row.weights if weights_row else {}
    factors = db.query(ImportanceFactor).all()
    keywords = [kw for f in factors for kw in (f.keywords or [])]

    alerts_out: list[dict[str, Any]] = []
    token_est = 0

    from packages.schemas import CompetitiveEvent

    for raw in state.get("events", []):
        event = CompetitiveEvent.model_validate(raw)
        event_text = f"{event.event_type} {event.raw_snippet} {event.extracted_entities.model_dump()}"
        feature_ov, icp, snippets = relevance_scores(db, event_text, keywords)

        user_imp = 0.0
        low = event_text.lower()
        for f in factors:
            if any(kw.lower() in low for kw in (f.keywords or [])) or f.name.lower() in low:
                user_imp = max(user_imp, min(1.0, f.weight))

        breakdown = score_event(
            event,
            weights,
            feature_overlap=feature_ov,
            icp_relevance=icp,
            user_importance=user_imp,
        )
        decision = gate_decision(
            breakdown.total,
            event.confidence,
            settings.alert_score_threshold,
            settings.review_band_low,
            settings.review_band_high,
        )
        _add_trace(
            state,
            "score",
            "ok",
            f"{event.competitor} score={breakdown.total} → {decision}",
            breakdown=breakdown.model_dump(),
        )

        if decision == "suppress":
            continue

        needs_review = decision == "review"
        draft = summarize_event(event, breakdown, snippets, needs_review)
        draft = critic_check(draft, event)
        _add_trace(
            state,
            "critique",
            "ok" if draft.critic_passed else "error",
            draft.critic_notes,
        )

        if not draft.critic_passed and not draft.needs_human_review:
            draft.needs_human_review = True

        # Persist event + alert
        comp = db.query(Competitor).filter(Competitor.name == event.competitor).first()
        ev_row = EventRecord(
            competitor_id=comp.id if comp else None,
            run_id=uuid.UUID(state["run_id"]),
            event_type=event.event_type.value,
            payload=event.model_dump(mode="json"),
            confidence=event.confidence,
            source_url=event.source_url,
        )
        db.add(ev_row)
        db.flush()

        delivery = {"channels": ["none"]}
        status = "review" if needs_review or draft.needs_human_review else "sent"
        # Always attempt delivery so Slack/console get the signal (review tagged in text)
        delivery = deliver_alert(draft, breakdown.total)
        if status == "sent":
            _add_trace(state, "act", "ok", f"delivered via {delivery['channels']}")
        else:
            _add_trace(state, "act", "ok", f"review alert delivered via {delivery['channels']}")

        bd = breakdown.model_dump()
        bd["event_type"] = event.event_type.value
        alert = Alert(
            event_id=ev_row.id,
            competitor_id=comp.id if comp else None,
            run_id=uuid.UUID(state["run_id"]),
            title=draft.title,
            what_changed=draft.what_changed,
            why_it_matters=draft.why_it_matters,
            suggested_action=draft.suggested_action,
            source_url=draft.source_url,
            quoted_snippet=draft.quoted_snippet,
            score=breakdown.total,
            score_breakdown=bd,
            needs_human_review=draft.needs_human_review or needs_review,
            critic_passed=draft.critic_passed,
            critic_notes=draft.critic_notes,
            status=status,
            delivered_via=",".join(delivery.get("channels", [])),
        )
        db.add(alert)
        db.commit()
        alerts_out.append(
            {
                "id": str(alert.id),
                "title": alert.title,
                "score": alert.score,
                "status": alert.status,
                "breakdown": alert.score_breakdown,
            }
        )
        token_est += 800  # rough for observability without exact usage

    state["alerts"] = alerts_out
    state.setdefault("cost", {})
    state["cost"]["llm_calls"] = len(state.get("events", []))
    state["cost"]["llm_tokens_in"] = token_est
    state["cost"]["llm_tokens_out"] = token_est // 4
    return state


def build_graph(db: Session):
    def plan(s: IntelState) -> IntelState:
        return plan_node(s, db)

    def collect(s: IntelState) -> IntelState:
        return collect_node(s, db)

    def diff_extract(s: IntelState) -> IntelState:
        return diff_extract_node(s, db)

    def score_act(s: IntelState) -> IntelState:
        return score_summarize_act_node(s, db)

    g = StateGraph(IntelState)
    g.add_node("plan", plan)
    g.add_node("collect", collect)
    g.add_node("diff_extract", diff_extract)
    g.add_node("score_act", score_act)
    g.set_entry_point("plan")
    g.add_edge("plan", "collect")
    g.add_edge("collect", "diff_extract")
    g.add_edge("diff_extract", "score_act")
    g.add_edge("score_act", END)
    return g.compile()


def run_intel_cycle(db: Session) -> AgentRun:
    run = AgentRun(status="running", trace=[], cost={})
    db.add(run)
    db.commit()
    db.refresh(run)

    graph = build_graph(db)
    initial: IntelState = {
        "run_id": str(run.id),
        "planned_urls": [],
        "fetch_results": [],
        "events": [],
        "alerts": [],
        "trace": [],
        "cost": {},
        "errors": [],
        "status": "running",
    }
    try:
        final = graph.invoke(initial)
        run.trace = final.get("trace", [])
        run.cost = final.get("cost", {})
        n_alerts = len(final.get("alerts", []))
        n_events = len(final.get("events", []))
        run.summary = f"events={n_events} alerts={n_alerts} errors={len(final.get('errors', []))}"
        run.status = "completed" if not final.get("errors") else "completed_with_errors"
    except Exception as e:
        logger.exception("Intel cycle failed")
        run.status = "failed"
        run.summary = str(e)
        run.trace = initial.get("trace", []) + [
            TraceStep(step="fatal", status="error", detail=str(e)).model_dump(mode="json")
        ]
    run.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(run)
    return run
