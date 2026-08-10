"""Safeguards / policy configuration."""
from __future__ import annotations

from urllib.parse import urlparse

from packages.schemas import PolicyConfig
from packages.settings import get_settings

# Speculative / ungrounded language the critic rejects
BANNED_PHRASES = [
    "probably",
    "might be trying to",
    "could indicate desperation",
    "they are failing",
    "rumor",
    "i believe",
    "likely means they are",
    "secretly",
    "on the verge of",
    "doom",
    "crushing them",
]

DEFAULT_NOISE_PATTERNS = [
    r"cookie",
    r"consent",
    r"privacy policy",
    r"terms of (service|use)",
    r"all rights reserved",
    r"©\s*\d{4}",
    r"last updated:?\s*\w+",
    r"careers?",
    r"we('re| are) hiring",
    r"subscribe to (our )?newsletter",
]


def load_policy(allowed_domains: list[str] | None = None) -> PolicyConfig:
    settings = get_settings()
    return PolicyConfig(
        allowed_domains=allowed_domains or [],
        max_pages_per_run=settings.max_pages_per_run,
        request_delay_seconds=settings.request_delay_seconds,
    )


def is_login_path(url: str, policy: PolicyConfig | None = None) -> bool:
    policy = policy or load_policy()
    path = urlparse(url).path.lower()
    return any(disallowed in path for disallowed in policy.disallow_login_paths)


def domain_allowed(url: str, policy: PolicyConfig) -> bool:
    if not policy.allowed_domains:
        return True
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in policy.allowed_domains)


def contains_banned_language(text: str) -> list[str]:
    lower = text.lower()
    return [p for p in BANNED_PHRASES if p in lower]
