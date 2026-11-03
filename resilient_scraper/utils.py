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
        # Guard against min > max (e.g. only --min-delay raised): uniform(10, 3)
        # would happily return gaps *below* the promised minimum.
        self.max_delay = max(min_delay, max_delay)
        self._last_hit: dict[str, float] = {}

    def wait(self, url: str) -> None:
        """Block until this host is due for another request. Call before
        *every* outgoing request (retries and escalations included)."""
        host = urlparse(url).netloc
        now = time.monotonic()
        last = self._last_hit.get(host)
        if last is not None:
            target_gap = random.uniform(self.min_delay, self.max_delay)
            elapsed = now - last
            if elapsed < target_gap:
                time.sleep(target_gap - elapsed)
        self._last_hit[host] = time.monotonic()

    def mark(self, url: str) -> None:
        """Record that a request to this host just *finished*, so the next
        gap is measured from the end of a (possibly slow) request."""
        self._last_hit[urlparse(url).netloc] = time.monotonic()


# NB: keep markers specific to *challenge pages*. Bare vendor names such as
# "datadome" also appear on normal pages (the vendor's JS tag sits in <head>),
# and phrases like "access denied" show up in ordinary article text.
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
    "<title>access denied",                 # Akamai / generic WAF block page
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
        # An empty 404/500 is an HTTP error, not a bot wall — the fetcher
        # handles it via is_http_error (no pointless browser escalation).
        return False
    head = text[:4000].lower()
    return any(marker in head for marker in _BLOCK_MARKERS)


def is_http_error(status: int | None) -> bool:
    """True for a 4xx/5xx status — never usable content, even with a body."""
    return status is not None and status >= 400


def is_retryable_status(status: int | None) -> bool:
    """5xx and 408 are usually transient; other 4xx (404, 410, ...) are not."""
    return status is not None and (status >= 500 or status == 408)


# Error text that points at the proxy itself (not the target or local setup).
_PROXY_ERROR_MARKERS = (
    "proxy", "407", "tunnel", "connect tunnel", "err_proxy", "err_tunnel",
    "err_no_supported_proxies",
)


def is_proxy_error(error: str | None, status: int | None = None) -> bool:
    if status == 407:
        return True
    if not error:
        return False
    low = error.lower()
    return any(m in low for m in _PROXY_ERROR_MARKERS)


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with jitter, capped. attempt is 0-indexed."""
    raw = base ** attempt
    jittered = raw * random.uniform(0.7, 1.3)
    return min(jittered, cap)
