"""Cross-cutting helpers: per-host rate limiting and block detection.

Block detection is the quiet hero here — escalating layers is pointless if
we can't tell a real page from a challenge page. We look for the signatures
the big vendors leave behind rather than trusting the HTTP status alone
(many challenge pages return 200)."""

from __future__ import annotations

import random
import time
from urllib.parse import urlparse


class RateLimiter:
    """Sleeps just enough to keep a minimum gap between hits to the same host.

    Being polite per-host (not globally) means one slow site never throttles
    scraping of an unrelated one, while no single host gets hammered."""

    def __init__(self, min_delay: float, max_delay: float):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._last_hit: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        now = time.monotonic()
        last = self._last_hit.get(host)
        if last is not None:
            target_gap = random.uniform(self.min_delay, self.max_delay)
            elapsed = now - last
            if elapsed < target_gap:
                time.sleep(target_gap - elapsed)
        self._last_hit[host] = time.monotonic()


# Signatures that mean "you hit a wall, not the page you wanted."
_BLOCK_MARKERS = (
    "just a moment...",                     # Cloudflare interstitial
    "checking your browser before",         # Cloudflare legacy
    "cf-browser-verification",
    "cf_chl_opt",                           # Cloudflare challenge script
    "_cf_chl_",
    "attention required! | cloudflare",
    "please enable javascript and cookies", # generic JS-gate
    "px-captcha",                           # PerimeterX
    "captcha-delivery.com",                 # DataDome
    "datadome",
    "access denied",
    "request unsuccessful. incapsula",      # Imperva/Incapsula
    "are you a robot",
    "unusual traffic from your",
)


def looks_blocked(status: int | None, text: str) -> bool:
    """Best-effort guess at whether a response is a block/challenge page.

    Conservative on purpose: a false 'blocked' just wastes one escalation;
    a false 'ok' would hand the caller a challenge page as if it were data."""
    if status in (401, 403, 429, 503):
        return True
    if not text:
        return status is None or status >= 400
    head = text[:4000].lower()
    return any(marker in head for marker in _BLOCK_MARKERS)


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with jitter, capped. attempt is 0-indexed."""
    raw = base ** attempt
    jittered = raw * random.uniform(0.7, 1.3)
    return min(jittered, cap)


def pick_proxy(proxies: list[str], attempt: int) -> str | None:
    """Rotate deterministically over the pool by attempt number."""
    if not proxies:
        return None
    return proxies[attempt % len(proxies)]
