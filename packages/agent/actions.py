"""Outbound actions: Slack webhook and/or email, console fallback."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from packages.schemas import AlertDraft
from packages.settings import get_settings

logger = logging.getLogger(__name__)


def format_alert_text(draft: AlertDraft, score: float) -> str:
    review = " ⚠️ NEEDS HUMAN REVIEW" if draft.needs_human_review else ""
    return (
        f"*{draft.title}*{review}\n"
        f"*What changed:* {draft.what_changed}\n"
        f"*Why it matters:* {draft.why_it_matters}\n"
        f"*Suggested action:* {draft.suggested_action}\n"
        f"*Score:* {score:.2f}\n"
        f"*Source:* {draft.source_url}\n"
        f"> {draft.quoted_snippet[:300]}"
    )


def send_slack(text: str) -> bool:
    settings = get_settings()
    if not settings.slack_webhook_url:
        return False
    try:
        r = httpx.post(settings.slack_webhook_url, json={"text": text}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Slack delivery failed: %s", e)
        return False


def send_email(subject: str, body: str) -> bool:
    settings = get_settings()
    if not settings.resend_api_key or not settings.alert_to_email:
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={
                "from": settings.alert_from_email,
                "to": [settings.alert_to_email],
                "subject": subject,
                "text": body,
            },
            timeout=15,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error("Email delivery failed: %s", e)
        return False


def deliver_alert(draft: AlertDraft, score: float) -> dict[str, Any]:
    text = format_alert_text(draft, score)
    channels: list[str] = []
    if send_slack(text):
        channels.append("slack")
    if send_email(draft.title, text.replace("*", "")):
        channels.append("email")
    if not channels:
        print("\n=== SignalWatch Alert (console fallback) ===")
        print(text.replace("*", ""))
        print("=== end ===\n")
        channels.append("console")
    return {"channels": channels, "preview": text}
