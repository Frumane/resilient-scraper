"""The fetch strategies, cheapest first. Each layer is a small class with a
single `.fetch(url, ...)` method returning a FetchResult. The orchestrator
(fetcher.py) owns the retry/escalation logic; layers just try once and report.

Layer 1 — CurlCffiLayer: real-browser TLS + HTTP/2 fingerprint via curl_cffi.
          No browser process, fast, beats the *first* line of many CDNs.
Layer 2 — PlaywrightLayer: a real headless browser that executes JS. Slower,
          heavier, but renders SPA content and clears fingerprint checks that
          a bare HTTP client can't. Optional — only used if playwright is
          installed and enabled.
Layer 3 — NodriverLayer: undetected real Chrome over raw CDP, last resort for
          managed challenges. Optional.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import random
import sys
import time
from urllib.parse import urlsplit

from .config import BROWSER_PROFILES, ScraperConfig
from .result import FetchResult
from .utils import looks_blocked


def split_proxy(proxy: str) -> tuple[str, str | None, str | None]:
    """Split "scheme://user:pass@host:port" into (server, username, password).
    Browsers take credentials separately from the proxy server address."""
    parts = urlsplit(proxy if "://" in proxy else f"http://{proxy}")
    host = parts.hostname or ""
    if ":" in host:                      # IPv6 literal
        host = f"[{host}]"
    server = f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")
    return server, parts.username, parts.password


def run_coro_sync(coro):  # noqa: ANN001, ANN201
    """Run a coroutine to completion from sync code. asyncio.run() refuses to
    start inside an already-running loop (Jupyter, async web handlers), so in
    that case run it on a fresh loop in a worker thread instead."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# Chrome's own network-error interstitial (DNS failure, refused connection,
# proxy failure...). Loading it is a failed navigation, not page content.
_CHROME_ERROR_MARKERS = ('id="main-frame-error"', "neterror", "chrome-error://")


def _is_chrome_error_page(html: str) -> bool:
    head = html[:20000]
    return any(m in head for m in _CHROME_ERROR_MARKERS)


class CurlCffiLayer:
    """HTTP client that impersonates a real browser's TLS/JA3 + HTTP2 profile."""

    name = "curl_cffi"

    def __init__(self, config: ScraperConfig):
        self.config = config
        # Lazy import so the package imports even if the extra isn't present.
        from curl_cffi import requests as cffi_requests  # noqa: WPS433
        self._requests = cffi_requests

    def fetch(self, url: str, proxy: str | None = None) -> FetchResult:
        impersonate, user_agent = random.choice(BROWSER_PROFILES)
        headers = {**self.config.base_headers, "User-Agent": user_agent}
        proxies = {"http": proxy, "https": proxy} if proxy else None

        start = time.monotonic()
        try:
            resp = self._requests.get(
                url,
                headers=headers,
                impersonate=impersonate,
                timeout=self.config.timeout,
                proxies=proxies,
                allow_redirects=True,
            )
            elapsed = time.monotonic() - start
            return FetchResult(
                url=url,
                status=resp.status_code,
                text=resp.text,
                layer=self.name,
                elapsed=elapsed,
            )
        except Exception as exc:  # noqa: BLE001 - report, let orchestrator decide
            return FetchResult(
                url=url,
                layer=self.name,
                elapsed=time.monotonic() - start,
                error=f"{type(exc).__name__}: {exc}",
            )


class PlaywrightLayer:
    """Real headless browser. Renders JS and presents a genuine browser
    fingerprint, at the cost of speed. Applies light stealth (hides the
    navigator.webdriver flag) — the single most common headless tell."""

    name = "playwright"

    # Injected before any page script runs; masks the most obvious automation
    # signal. Not a full stealth suite — honest about its ceiling.
    _STEALTH_JS = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

    def __init__(self, config: ScraperConfig):
        self.config = config

    @staticmethod
    def available() -> bool:
        try:
            import playwright  # noqa: F401, WPS433
            return True
        except ImportError:
            return False

    def fetch(self, url: str, proxy: str | None = None) -> FetchResult:
        from playwright.sync_api import sync_playwright  # noqa: WPS433

        _, user_agent = random.choice(BROWSER_PROFILES)
        start = time.monotonic()
        try:
            with sync_playwright() as p:
                launch_kwargs: dict = {"headless": self.config.headless}
                if proxy:
                    # Playwright ignores user:pass embedded in `server`;
                    # credentials must be passed as separate keys.
                    server, username, password = split_proxy(proxy)
                    proxy_cfg: dict = {"server": server}
                    if username:
                        proxy_cfg["username"] = username
                        proxy_cfg["password"] = password or ""
                    launch_kwargs["proxy"] = proxy_cfg
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(user_agent=user_agent)
                context.add_init_script(self._STEALTH_JS)
                page = context.new_page()
                try:
                    response = page.goto(url, timeout=int(self.config.timeout * 1000),
                                         wait_until="domcontentloaded")
                    # Give client-side rendering a beat to populate the DOM.
                    page.wait_for_timeout(1500)
                    html = page.content()
                    # goto() does not raise on 4xx/5xx — report the real status
                    # so block/HTTP-error detection works for this layer too.
                    status = response.status if response is not None else 200
                finally:
                    browser.close()
            return FetchResult(
                url=url,
                status=status,
                text=html,
                layer=self.name,
                elapsed=time.monotonic() - start,
            )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                url=url,
                layer=self.name,
                elapsed=time.monotonic() - start,
                error=f"{type(exc).__name__}: {exc}",
            )


_PROACTOR_PATCHED = False


def _silence_proactor_del_noise() -> None:
    """Swallow the harmless 'unclosed transport' / 'closed pipe' errors that
    Windows' Proactor event loop prints from transport.__del__ at GC after a
    nodriver run. The work is already done and returned; this is pure noise.
    Patched once, only on Windows."""
    global _PROACTOR_PATCHED
    if _PROACTOR_PATCHED or sys.platform != "win32":
        return
    def _wrap_del(cls) -> None:
        original = cls.__del__

        def _quiet_del(self, _orig=original):  # noqa: ANN001
            try:
                _orig(self)
            except (RuntimeError, ValueError):
                pass

        cls.__del__ = _quiet_del

    try:
        from asyncio.base_subprocess import BaseSubprocessTransport
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _wrap_del(_ProactorBasePipeTransport)
        _wrap_del(BaseSubprocessTransport)
    except Exception:  # noqa: BLE001 - best effort; noise is cosmetic anyway
        pass
    _PROACTOR_PATCHED = True


class NodriverLayer:
    """Last resort: a real Chrome driven over raw CDP via `nodriver`.

    Unlike Selenium/Playwright, nodriver leaves almost no automation tells
    (no navigator.webdriver, no CDP runtime leaks), so it clears the managed
    Cloudflare / DataDome challenges the lighter layers can't. It drives the
    actual installed Chrome, so its fingerprint is a genuine browser's — the
    'use the real thing' principle taken to its conclusion.

    Headful (a visible window) clears challenges far more reliably than
    headless, so that's the default."""

    name = "nodriver"

    def __init__(self, config: ScraperConfig):
        self.config = config

    @staticmethod
    def available() -> bool:
        try:
            import nodriver  # noqa: F401, WPS433
            return True
        except ImportError:
            return False

    async def _run(self, url: str, proxy: str | None) -> FetchResult:
        import nodriver as uc  # noqa: WPS433

        start = time.monotonic()
        browser = None
        try:
            browser_args = []
            if proxy:
                server, username, _ = split_proxy(proxy)
                if username:
                    # Chrome's --proxy-server flag has no credentials field;
                    # passing user:pass would just break navigation.
                    return FetchResult(
                        url=url, layer=self.name, elapsed=0.0,
                        error="nodriver layer cannot use credentialed (user:pass) "
                              "proxies; use an IP-whitelisted endpoint",
                    )
                browser_args.append(f"--proxy-server={server}")
            browser = await uc.start(
                headless=self.config.nodriver_headless,
                browser_args=browser_args or None,
            )
            page = await browser.get(url)

            # Poll while the challenge auto-solves, up to nodriver_settle secs.
            # Always read at least once (nodriver_settle <= 0 would otherwise
            # return an empty body as success).
            html = await page.get_content()
            deadline = time.monotonic() + self.config.nodriver_settle
            while looks_blocked(200, html) and time.monotonic() < deadline:
                await page.sleep(1)
                html = await page.get_content()

            # nodriver's get() swallows Page.navigate's errorText, so a DNS
            # failure / refused connection / dead proxy shows up as Chrome's
            # own error page. That is a failed fetch, not content.
            if _is_chrome_error_page(html):
                return FetchResult(
                    url=url, layer=self.name,
                    elapsed=time.monotonic() - start,
                    error="navigation failed (Chrome network error page)",
                )

            return FetchResult(
                url=url,
                status=200,
                text=html,
                layer=self.name,
                elapsed=time.monotonic() - start,
            )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                url=url,
                layer=self.name,
                elapsed=time.monotonic() - start,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if browser is not None:
                try:
                    browser.stop()
                except Exception:  # noqa: BLE001
                    pass

    def fetch(self, url: str, proxy: str | None = None) -> FetchResult:
        # nodriver is async-only; bridge it to our sync orchestrator. A fresh
        # event loop per call keeps layers independent (fine at our volume).
        _silence_proactor_del_noise()
        try:
            return run_coro_sync(self._run(url, proxy))
        except Exception as exc:  # noqa: BLE001 - layers report, never raise
            return FetchResult(url=url, layer=self.name,
                               error=f"{type(exc).__name__}: {exc}")
