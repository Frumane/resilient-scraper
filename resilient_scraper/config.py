"""All tunables in one place. Every knob has a sane default so the tool
works out of the box, but nothing is hardcoded deep in the logic."""

from __future__ import annotations

from dataclasses import dataclass, field


# A small pool of real, current browser identities. curl_cffi's `impersonate`
# handles the TLS/HTTP2 fingerprint; we rotate the matching User-Agent string
# so the header and the fingerprint tell the same story (a mismatch is itself
# a bot signal).
# Each entry pairs a curl_cffi impersonate target with a matching User-Agent
# so the TLS/HTTP2 fingerprint and the UA header agree (a mismatch is itself a
# bot signal). Only targets curl_cffi actually ships are listed — check yours
# with `list(BrowserTypeLiteral)` if you pin a different version.
BROWSER_PROFILES: list[tuple[str, str]] = [
    (
        "chrome131",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ),
    (
        "chrome124",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ),
    (
        "chrome120",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ),
    (
        "safari18_0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    ),
]


@dataclass
class ScraperConfig:
    # --- politeness / rate limiting ---
    min_delay: float = 1.0            # min seconds between requests to one host
    max_delay: float = 3.0            # max seconds (a random value in [min, max])
    timeout: float = 20.0            # per-request timeout, seconds

    # --- retries ---
    max_retries: int = 3              # attempts per layer before escalating
    backoff_base: float = 1.5         # exponential backoff multiplier
    backoff_cap: float = 30.0         # never wait longer than this between retries

    # --- layers ---
    use_playwright_fallback: bool = True   # escalate to a real browser if installed
    headless: bool = True                  # run Playwright headless

    # --- Layer 3: nodriver (undetected real Chrome via raw CDP) ---
    use_nodriver_fallback: bool = True     # last resort for managed challenges
    nodriver_headless: bool = False        # headful clears Cloudflare far more often
    nodriver_settle: float = 6.0           # seconds to let a challenge auto-solve

    # --- proxy (optional) — a single URL or a list to rotate over ---
    proxies: list[str] = field(default_factory=list)

    # --- default headers merged into every request (browser-like order) ---
    base_headers: dict[str, str] = field(
        default_factory=lambda: {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
    )
