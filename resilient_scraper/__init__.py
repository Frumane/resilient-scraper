"""resilient-scraper — a layered, polite scraper for JS-rendered and
lightly anti-bot-protected *public* pages.

Philosophy: don't fight the wall, take the cheapest door that works.
Each request walks a chain of strategies, cheapest first:

    1. curl_cffi  — real-browser TLS/HTTP2 fingerprint, no browser needed
    2. Playwright — a real headless browser for JS-rendered / fingerprinted
                    pages (optional dependency, loaded only if installed)

The orchestrator tries a layer, checks the response for block/challenge
signatures, and escalates to the next layer only when needed.

Public data only. This is not a tool for defeating login walls or
harvesting personal data — see README.
"""

from .fetcher import Fetcher
from .result import FetchResult
from .config import ScraperConfig

__all__ = ["Fetcher", "FetchResult", "ScraperConfig"]
__version__ = "0.1.0"
