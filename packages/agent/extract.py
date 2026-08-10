"""LLM + heuristic event extraction from diffs."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from packages.schemas import CompetitiveEvent, EventType, ExtractedEntities, PageLabel
from packages.settings import get_settings

logger = logging.getLogger(__name__)

PRICE_RE = re.compile(
    r"(?P<plan>[A-Za-z][\w\s-]{0,24})?.{0,40}?\$?\s?(?P<price>\d{1,4}(?:\.\d{2})?)\s*(?:/\s*(mo|month|yr|year))?",
    re.I,
)


def heuristic_extract(
    competitor: str,
    source_url: str,
    changed_section: str,
    page_label: PageLabel | None = None,
) -> list[CompetitiveEvent]:
    text = changed_section
    events: list[CompetitiveEvent] = []
    low = text.lower()

    event_type = EventType.other
    confidence = 0.45
    ents = ExtractedEntities()

    if page_label == PageLabel.pricing or "price" in low or "$" in text:
        event_type = EventType.pricing_change
        confidence = 0.55
        prices = re.findall(r"\$\s?(\d{1,4}(?:\.\d{2})?)", text)
        if "REMOVED" in text and "ADDED" in text and len(prices) >= 2:
            ents.old_price = prices[0]
            ents.new_price = prices[1]
            confidence = 0.7
        elif prices:
            ents.new_price = prices[0]
        plan_m = re.search(r"\b(Free|Starter|Pro|Team|Business|Enterprise|Plus|Basic)\b", text, re.I)
        if plan_m:
            ents.plan = plan_m.group(1)

    if any(k in low for k in ["launch", "introducing", "now available", "new feature"]):
        event_type = EventType.feature_launch
        confidence = max(confidence, 0.6)
        ents.feature_area = _guess_feature(text)

    if page_label == PageLabel.changelog:
        event_type = EventType.changelog_entry
        confidence = max(confidence, 0.5)

    if page_label == PageLabel.blog and event_type == EventType.other:
        event_type = EventType.blog_announcement

    # Ignore pure noise
    if len(text.strip()) < 20:
        return []

    snippet = text[:500]
    events.append(
        CompetitiveEvent(
            source=f"competitor_{page_label.value if page_label else 'page'}",
            competitor=competitor,
            timestamp=datetime.utcnow(),
            event_type=event_type,
            raw_snippet=snippet,
            source_url=source_url,
            extracted_entities=ents,
            confidence=confidence,
            page_label=page_label,
            changed_section=changed_section[:4000],
        )
    )
    return events


def _guess_feature(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("REMOVED", "ADDED")) and len(line) < 80:
            return line[:80]
    return None


def llm_extract(
    competitor: str,
    source_url: str,
    changed_section: str,
    page_label: PageLabel | None = None,
) -> list[CompetitiveEvent]:
    settings = get_settings()
    if not settings.llm_enabled:
        return heuristic_extract(competitor, source_url, changed_section, page_label)

    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
    system = (
        "You extract structured competitive intelligence events from page diffs. "
        "Return ONLY valid JSON: {\"events\": [ {...} ]}. "
        "Each event: event_type (pricing_change|plan_change|feature_launch|feature_removal|"
        "positioning_change|blog_announcement|changelog_entry|other|noise), "
        "raw_snippet, confidence (0-1), extracted_entities "
        "{plan, old_price, new_price, feature_area, target_segment}. "
        "Do not speculate. If change is cookie/footer/date noise, use event_type=noise."
    )
    user = (
        f"Competitor: {competitor}\nURL: {source_url}\nPage label: {page_label}\n\n"
        f"DIFF:\n{changed_section[:5000]}"
    )
    try:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        out: list[CompetitiveEvent] = []
        for raw in data.get("events", []):
            et = raw.get("event_type", "other")
            try:
                event_type = EventType(et)
            except ValueError:
                event_type = EventType.other
            if event_type == EventType.noise:
                continue
            ents_raw = raw.get("extracted_entities") or {}
            out.append(
                CompetitiveEvent(
                    source=f"competitor_{page_label.value if page_label else 'page'}",
                    competitor=competitor,
                    event_type=event_type,
                    raw_snippet=(raw.get("raw_snippet") or changed_section)[:500],
                    source_url=source_url,
                    extracted_entities=ExtractedEntities(**{
                        k: ents_raw.get(k) for k in ("plan", "old_price", "new_price", "feature_area", "target_segment")
                    }),
                    confidence=float(raw.get("confidence", 0.5)),
                    page_label=page_label,
                    changed_section=changed_section[:4000],
                )
            )
        return out or heuristic_extract(competitor, source_url, changed_section, page_label)
    except Exception as e:
        logger.warning("LLM extract failed, using heuristics: %s", e)
        return heuristic_extract(competitor, source_url, changed_section, page_label)


def extract_events(
    competitor: str,
    source_url: str,
    changed_section: str,
    page_label: PageLabel | None = None,
) -> list[CompetitiveEvent]:
    return llm_extract(competitor, source_url, changed_section, page_label)
