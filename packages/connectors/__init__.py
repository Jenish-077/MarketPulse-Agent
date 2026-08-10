"""Page fetchers: httpx first, Playwright fallback. Polite delays + retries."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from packages.policy import is_login_path, load_policy
from packages.settings import get_settings

logger = logging.getLogger(__name__)

UA = "SignalWatchBot/0.1 (+https://github.com/signalwatch; competitive-intel; polite)"


@dataclass
class FetchResult:
    url: str
    ok: bool
    status_code: int | None
    raw_html: str
    normalized_text: str
    content_hash: str
    method: str
    error: str | None = None
    elapsed_ms: int = 0


def normalize_html(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    # Drop common noise regions by role/class hints
    for sel in [
        "[id*='cookie']",
        "[class*='cookie']",
        "[class*='consent']",
        "footer",
        "nav",
        "[role='navigation']",
        "[aria-label*='cookie' i]",
    ]:
        for el in soup.select(sel):
            el.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def robots_allows(url: str) -> bool:
    policy = load_policy()
    if not policy.respect_robots:
        return True
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = RobotFileParser()
    try:
        # urllib's rp.read() often fails silently on Windows/TLS; fetch via httpx instead
        with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": UA}) as client:
            resp = client.get(robots_url)
            if resp.status_code >= 400 or not resp.text.strip():
                logger.warning("robots.txt missing/empty for %s — allowing with caution", robots_url)
                return True
            rp.parse(resp.text.splitlines())
        allowed = rp.can_fetch(UA, url)
        if not allowed:
            # Also try generic agent token — some parsers mishandle custom UAs
            allowed = rp.can_fetch("*", url)
        return allowed
    except Exception:
        logger.warning("robots.txt unreadable for %s — allowing with caution", url)
        return True


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
def _httpx_get(url: str, timeout: int) -> httpx.Response:
    with httpx.Client(follow_redirects=True, timeout=timeout, headers={"User-Agent": UA}) as client:
        return client.get(url)


def fetch_httpx(url: str) -> FetchResult:
    settings = get_settings()
    t0 = time.perf_counter()
    try:
        resp = _httpx_get(url, settings.scrape_timeout_seconds)
        html = resp.text
        norm = normalize_html(html)
        return FetchResult(
            url=url,
            ok=resp.is_success and bool(norm),
            status_code=resp.status_code,
            raw_html=html,
            normalized_text=norm,
            content_hash=content_hash(norm),
            method="httpx",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            error=None if resp.is_success else f"HTTP {resp.status_code}",
        )
    except Exception as e:
        return FetchResult(
            url=url,
            ok=False,
            status_code=None,
            raw_html="",
            normalized_text="",
            content_hash="",
            method="httpx",
            error=str(e),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )


def fetch_playwright(url: str) -> FetchResult:
    settings = get_settings()
    t0 = time.perf_counter()
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=UA)
            page.goto(url, wait_until="domcontentloaded", timeout=settings.scrape_timeout_seconds * 1000)
            page.wait_for_timeout(800)
            html = page.content()
            browser.close()
        norm = normalize_html(html)
        return FetchResult(
            url=url,
            ok=bool(norm),
            status_code=200,
            raw_html=html,
            normalized_text=norm,
            content_hash=content_hash(norm),
            method="playwright",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as e:
        return FetchResult(
            url=url,
            ok=False,
            status_code=None,
            raw_html="",
            normalized_text="",
            content_hash="",
            method="playwright",
            error=str(e),
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )


def fetch_fixture(url: str) -> FetchResult:
    """Load local eval/demo HTML via fixture://name or fixture://name/after."""
    import pathlib

    t0 = time.perf_counter()
    # fixture://acme_pricing/before → evals/fixtures/pages/acme_pricing_before.html
    slug = url.split("://", 1)[1].strip("/")
    parts = slug.split("/")
    if len(parts) == 2:
        name, version = parts
        filename = f"{name}_{version}.html"
    else:
        filename = f"{parts[0]}.html"
    root = pathlib.Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "pages"
    path = root / filename
    if not path.exists():
        return FetchResult(
            url=url,
            ok=False,
            status_code=404,
            raw_html="",
            normalized_text="",
            content_hash="",
            method="fixture",
            error=f"Fixture not found: {path}",
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
        )
    html = path.read_text(encoding="utf-8")
    norm = normalize_html(html)
    return FetchResult(
        url=url,
        ok=True,
        status_code=200,
        raw_html=html,
        normalized_text=norm,
        content_hash=content_hash(norm),
        method="fixture",
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
    )


def fetch_page(url: str, prefer_playwright: bool = False) -> FetchResult:
    """Collect a public page with policy checks, delay, retry, and Playwright fallback."""
    settings = get_settings()
    policy = load_policy()

    if url.startswith("fixture://"):
        return fetch_fixture(url)

    if is_login_path(url, policy):
        return FetchResult(
            url=url,
            ok=False,
            status_code=None,
            raw_html="",
            normalized_text="",
            content_hash="",
            method="blocked",
            error="Disallowed login/auth path by policy",
        )

    if not robots_allows(url):
        return FetchResult(
            url=url,
            ok=False,
            status_code=None,
            raw_html="",
            normalized_text="",
            content_hash="",
            method="blocked",
            error="Blocked by robots.txt",
        )

    time.sleep(settings.request_delay_seconds)

    if prefer_playwright:
        result = fetch_playwright(url)
        if result.ok:
            return result
        return fetch_httpx(url)

    result = fetch_httpx(url)
    # Retry with Playwright if thin/empty or failed
    if not result.ok or len(result.normalized_text) < 200:
        pw = fetch_playwright(url)
        if pw.ok:
            return pw
        if not result.ok:
            return result
    return result
