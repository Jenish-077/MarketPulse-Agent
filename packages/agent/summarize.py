"""Summarizer + critic (fact-check) agents."""
from __future__ import annotations

import json
import logging
import re

from packages.policy import contains_banned_language
from packages.schemas import AlertDraft, CompetitiveEvent, ScoreBreakdown
from packages.settings import get_settings

logger = logging.getLogger(__name__)


ACTION_TEMPLATES = {
    "pricing_change": "Update pricing comparison for {plan} and brief GTM on competitor move.",
    "feature_launch": "Investigate [{feature}] launch vs our roadmap; decide respond / ignore / match.",
    "feature_removal": "Note removal of [{feature}] — check if customers cited this in losses.",
    "plan_change": "Refresh competitive battlecard for plan packaging changes.",
    "blog_announcement": "Skim announcement and log strategic implication if any.",
    "changelog_entry": "Review changelog entry for overlap with our product.",
    "default": "Review signal and decide: investigate, update collateral, or dismiss.",
}


def _suggested_action(event: CompetitiveEvent) -> str:
    et = event.event_type.value
    tmpl = ACTION_TEMPLATES.get(et, ACTION_TEMPLATES["default"])
    return tmpl.format(
        plan=event.extracted_entities.plan or "affected plan",
        feature=event.extracted_entities.feature_area or "feature",
    )


def summarize_heuristic(
    event: CompetitiveEvent,
    score: ScoreBreakdown,
    company_snippets: list[str],
    needs_review: bool,
) -> AlertDraft:
    ents = event.extracted_entities
    what = f"{event.competitor} — {event.event_type.value.replace('_', ' ')}"
    details = []
    if ents.plan:
        details.append(f"plan={ents.plan}")
    if ents.old_price or ents.new_price:
        details.append(f"price {ents.old_price or '?'} → {ents.new_price or '?'}")
    if ents.feature_area:
        details.append(f"feature={ents.feature_area}")
    if details:
        what += ": " + ", ".join(details)

    why_parts = []
    if company_snippets:
        why_parts.append(
            "Relevant to our context: " + company_snippets[0][:220].replace("\n", " ")
        )
    else:
        why_parts.append(
            "May affect competitive positioning based on the observed page change (no company context retrieved)."
        )
    why_parts.append(f"Score {score.total:.2f}: " + "; ".join(score.explanation[:3]))

    return AlertDraft(
        title=f"[SignalWatch] {event.competitor}: {event.event_type.value}",
        what_changed=what,
        why_it_matters=" ".join(why_parts),
        suggested_action=_suggested_action(event),
        source_url=event.source_url,
        quoted_snippet=event.raw_snippet[:400],
        needs_human_review=needs_review,
        company_context_used=company_snippets[:3],
    )


def summarize_event(
    event: CompetitiveEvent,
    score: ScoreBreakdown,
    company_snippets: list[str],
    needs_review: bool,
) -> AlertDraft:
    settings = get_settings()
    draft = summarize_heuristic(event, score, company_snippets, needs_review)
    if not settings.llm_enabled:
        return draft

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    prompt = {
        "event": event.model_dump(mode="json"),
        "score_explanation": score.explanation,
        "company_context_snippets": company_snippets,
        "instructions": (
            "Write a short CI alert. Fields: title, what_changed, why_it_matters, suggested_action. "
            "why_it_matters MUST cite company context snippets and the competitor evidence. "
            "No speculation or FUD. Keep under 120 words total body."
        ),
    }
    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You write source-grounded competitive intel alerts."},
                {"role": "user", "content": json.dumps(prompt)},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        draft.title = data.get("title") or draft.title
        draft.what_changed = data.get("what_changed") or draft.what_changed
        draft.why_it_matters = data.get("why_it_matters") or draft.why_it_matters
        draft.suggested_action = data.get("suggested_action") or draft.suggested_action
    except Exception as e:
        logger.warning("Summarizer LLM failed: %s", e)
    return draft


def critic_check(draft: AlertDraft, event: CompetitiveEvent) -> AlertDraft:
    """Reject ungrounded / speculative claims; require snippet grounding."""
    notes: list[str] = []
    banned = contains_banned_language(
        f"{draft.what_changed} {draft.why_it_matters} {draft.suggested_action}"
    )
    if banned:
        notes.append(f"banned speculative phrases: {banned}")
        # Neutralize
        draft.why_it_matters = (
            "Grounded summary only: change observed on competitor page; "
            "see quoted snippet and source link. Speculative language removed by critic."
        )

    snippet = (event.raw_snippet or "").lower()
    # Require at least some token overlap between claims and snippet
    claim_tokens = set(re.findall(r"[a-z0-9]{4,}", draft.what_changed.lower()))
    snip_tokens = set(re.findall(r"[a-z0-9]{4,}", snippet))
    overlap = claim_tokens & snip_tokens
    if len(claim_tokens) >= 3 and len(overlap) < 1:
        notes.append("weak grounding: what_changed lacks snippet token overlap")
        draft.needs_human_review = True

    if not draft.quoted_snippet or not draft.source_url:
        notes.append("missing mandatory source link or snippet")
        draft.critic_passed = False
        draft.critic_notes = "; ".join(notes)
        return draft

    draft.critic_passed = len([n for n in notes if "banned" in n or "missing" in n]) == 0
    draft.critic_notes = "; ".join(notes) if notes else "ok"
    if banned:
        draft.critic_passed = False
        draft.needs_human_review = True
    return draft
