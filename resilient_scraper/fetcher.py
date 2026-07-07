"""The orchestrator. Owns the escalation ladder:

    for each layer (cheapest first):
        try up to max_retries times, with backoff + proxy rotation
        if we get usable, non-blocked content -> return it
        if the layer errors or looks blocked -> escalate to the next layer

The point is to spend the least effort that works: a 200ms fingerprinted
HTTP call beats a 3s headless browser, so we only reach for the browser when
the cheap call actually failed."""

from __future__ import annotations

import time

from .config import ScraperConfig
from .layers import CurlCffiLayer, NodriverLayer, PlaywrightLayer
from .proxies import ProxyPool
from .result import FetchResult
from .utils import RateLimiter, backoff_delay, looks_blocked


class Fetcher:
    def __init__(self, config: ScraperConfig | None = None, verbose: bool = False):
        self.config = config or ScraperConfig()
        self.verbose = verbose
        self.rate_limiter = RateLimiter(self.config.min_delay, self.config.max_delay)
        self.proxies = ProxyPool(self.config.proxies) if self.config.proxies else None

        # Build the ladder, cheapest first. Layer 1 (curl_cffi) is always
        # present; the browser layers are added only if enabled AND installed.
        self._layers = [CurlCffiLayer(self.config)]
        if self.config.use_playwright_fallback and PlaywrightLayer.available():
            self._layers.append(PlaywrightLayer(self.config))
        if self.config.use_nodriver_fallback and NodriverLayer.available():
            self._layers.append(NodriverLayer(self.config))

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[resilient-scraper] {msg}")

    def _report(self, proxy: str | None, ok: bool) -> None:
        if self.proxies is not None:
            self.proxies.report(proxy, ok)

    def fetch(self, url: str) -> FetchResult:
        """Fetch one URL, escalating through layers until one succeeds."""
        self.rate_limiter.wait(url)
        attempts: list[str] = []
        last_result: FetchResult | None = None

        for layer in self._layers:
            for attempt in range(self.config.max_retries):
                proxy = self.proxies.get() if self.proxies else None
                tag = f"{layer.name}#{attempt + 1}" + ("+proxy" if proxy else "")
                attempts.append(tag)

                result = layer.fetch(url, proxy=proxy)
                last_result = result

                if result.error:
                    self._report(proxy, ok=False)
                    self._log(f"{tag} errored: {result.error}")
                elif looks_blocked(result.status, result.text):
                    result.blocked = True
                    self._report(proxy, ok=False)
                    self._log(f"{tag} blocked ({result.status})")
                else:
                    self._report(proxy, ok=True)
                    result.ok = True
                    result.attempts = attempts
                    self._log(f"{tag} OK — {result.short()}")
                    return result

                # Not the last try for this layer? back off and retry.
                if attempt < self.config.max_retries - 1:
                    delay = backoff_delay(
                        attempt, self.config.backoff_base, self.config.backoff_cap,
                    )
                    self._log(f"{tag} retrying in {delay:.1f}s")
                    time.sleep(delay)

            self._log(f"'{layer.name}' exhausted, escalating")

        # Nothing worked — hand back the last thing we saw, flagged.
        # NB: use an explicit None check, not `or`. A blocked result is a
        # real, informative response (e.g. 403 + challenge body) but is falsy
        # because FetchResult.__bool__ returns .ok — `or` would throw it away.
        result = last_result if last_result is not None else FetchResult(
            url=url, error="no layers ran",
        )
        result.attempts = attempts
        result.ok = False
        self._log(f"gave up after {len(attempts)} attempts")
        return result

    def fetch_all(self, urls: list[str]) -> list[FetchResult]:
        """Sequential fetch of many URLs (rate-limited per host). Kept simple
        and synchronous on purpose — predictable and polite beats fast-and-banned
        for most freelance jobs."""
        return [self.fetch(u) for u in urls]
