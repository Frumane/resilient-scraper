"""Hidden-API discovery.

The single most effective scraping move is to stop parsing rendered HTML and
hit the JSON endpoint the page fetches its data from. Doing that by hand means
opening DevTools, filtering XHR, and eyeballing responses. This automates it:

open the page in a real browser, watch every network response it makes, keep
the ones that look like a data API (XHR/fetch returning JSON), and rank them by
how much they look like *the* data source. You get back a short list of
endpoints you can call directly — usually faster, cleaner, and far less
defended than the page itself.

Public data only — same scope rules as the rest of the package."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ApiEndpoint:
    method: str
    url: str
    status: int
    content_type: str
    size: int
    resource_type: str                       # "xhr" / "fetch" / ...
    score: float = 0.0                        # higher = more likely the data source
    preview: str = ""                         # human-readable shape of the JSON
    sample: Optional[Any] = field(default=None, repr=False)

    def line(self) -> str:
        return f"[{self.score:5.1f}] {self.method:4} {self.url}\n         {self.preview}"


def _describe_json(data: Any) -> tuple[str, float]:
    """Return a one-line shape description and a usefulness score.

    Lists of records score highest — that's almost always the collection a
    scraper actually wants."""
    if isinstance(data, list):
        n = len(data)
        if n and isinstance(data[0], dict):
            keys = list(data[0].keys())[:6]
            return f"list of {n} objects · keys: {', '.join(keys)}", 10.0 + min(n, 50) / 10
        return f"list of {n} values", 4.0
    if isinstance(data, dict):
        keys = list(data.keys())
        # A dict that wraps a list (e.g. {"results": [...]}) is the common
        # paginated-API shape — score it nearly as high as a bare list.
        for k in keys:
            v = data[k]
            if isinstance(v, list) and v and isinstance(v[0], dict):
                inner = list(v[0].keys())[:6]
                return (f"dict -> '{k}': list of {len(v)} objects · keys: "
                        f"{', '.join(inner)}", 9.0 + min(len(v), 50) / 10)
        return f"dict · keys: {', '.join(keys[:8])}", 5.0
    return type(data).__name__, 1.0


def _rank(found: dict[str, ApiEndpoint], max_results: int) -> list[ApiEndpoint]:
    return sorted(found.values(), key=lambda e: e.score, reverse=True)[:max_results]


def _record(found: dict, url: str, method: str, status: int, ctype: str,
            rtype: str, body: str) -> None:
    """Shared scoring logic: turn one JSON response into a ranked ApiEndpoint."""
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return
    shape, score = _describe_json(data)
    score += min(len(body), 200_000) / 100_000
    found[url] = ApiEndpoint(
        method=method, url=url, status=status,
        content_type=ctype.split(";")[0], size=len(body),
        resource_type=rtype, score=round(score, 1), preview=shape, sample=data,
    )


def discover_apis(
    url: str,
    wait: float = 6.0,
    scroll: bool = True,
    headless: bool = True,
    max_results: int = 10,
    engine: str = "auto",
) -> list[ApiEndpoint]:
    """Load `url` in a real browser and return the data-bearing endpoints it
    called, best first.

    engine:
      "auto"       — use nodriver (undetected real Chrome) if installed, which
                     clears the bot checks that hide a protected site's API;
                     otherwise fall back to Playwright.
      "nodriver"   — force the undetected-Chrome engine.
      "playwright" — force Playwright (needs `playwright install chromium`)."""
    use_nodriver = engine == "nodriver" or (engine == "auto" and _nodriver_ready())
    if use_nodriver:
        return _discover_nodriver(url, wait, scroll, headless, max_results)
    return _discover_playwright(url, wait, scroll, headless, max_results)


def _nodriver_ready() -> bool:
    try:
        import nodriver  # noqa: F401
        return True
    except ImportError:
        return False


def _discover_nodriver(url, wait, scroll, headless, max_results):  # noqa: ANN001
    """Capture data endpoints via nodriver + raw CDP Network events. Because
    it drives a genuine, un-flagged Chrome, the page actually loads its data —
    so its hidden API is visible where a vanilla headless browser gets blocked."""
    if sys.platform == "win32":
        from .layers import _silence_proactor_del_noise
        _silence_proactor_del_noise()

    async def run() -> dict[str, ApiEndpoint]:
        import nodriver as uc
        from nodriver import cdp

        found: dict[str, ApiEndpoint] = {}
        # url -> (status, mime, rtype). Keyed by URL so repeated pages dedupe.
        seen: dict[str, tuple] = {}
        browser = await uc.start(headless=headless)
        try:
            tab = await browser.get("about:blank")
            await tab.send(cdp.network.enable())

            async def on_response(evt: "cdp.network.ResponseReceived") -> None:
                r = evt.response
                rtype = str(getattr(evt, "type_", "") or "")
                if "json" in (r.mime_type or "").lower() and 200 <= r.status < 300:
                    seen.setdefault(r.url, (r.status, r.mime_type, rtype))

            tab.add_handler(cdp.network.ResponseReceived, on_response)
            await tab.get(url)

            # Let late XHRs fire; scrolling triggers infinite-scroll loaders.
            rounds = 3 if scroll else 1
            for _ in range(rounds):
                if scroll:
                    await tab.scroll_down(300)
                await tab.sleep(wait / rounds)

            # Retrieve each body by re-fetching it *inside the page* — runs with
            # the browser's cookies and un-flagged fingerprint, so it succeeds
            # where a bare CDP body-read (or an outside HTTP client) would be
            # blocked. Reliable, and it dodges CDP's response-buffer eviction.
            for u, (status, mime, rtype) in list(seen.items()):
                try:
                    js = f"fetch({u!r}, {{credentials: 'include'}}).then(r => r.text())"
                    body = await tab.evaluate(js, await_promise=True)
                    if body:
                        _record(found, u, "GET", status, mime or "", rtype or "xhr", body)
                except Exception:  # noqa: BLE001 - skip anything we can't re-read
                    continue
            return found
        finally:
            try:
                browser.stop()
            except Exception:  # noqa: BLE001
                pass

    found = asyncio.run(run())
    return _rank(found, max_results)


def _discover_playwright(url, wait, scroll, headless, max_results):  # noqa: ANN001
    """Load `url` in Playwright and capture data endpoints. Simpler than the
    nodriver path but detectable — protected sites may hide their API from it."""
    from playwright.sync_api import sync_playwright

    found: dict[str, ApiEndpoint] = {}

    def on_response(response) -> None:  # noqa: ANN001
        try:
            rtype = response.request.resource_type
            ctype = (response.headers or {}).get("content-type", "")
            if rtype not in ("xhr", "fetch") or "json" not in ctype.lower():
                return
            if 200 <= response.status < 300:
                _record(found, response.url, response.request.method,
                        response.status, ctype, rtype, response.text())
        except Exception:  # noqa: BLE001 - a body we can't read just isn't a hit
            return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_context().new_page()
        page.on("response", on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=int(wait * 1000) + 15000)
        # Let late XHRs fire; scrolling triggers lazy/infinite-scroll loaders.
        if scroll:
            for _ in range(3):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(int(wait * 1000 / 3))
        else:
            page.wait_for_timeout(int(wait * 1000))
        browser.close()

    return _rank(found, max_results)
