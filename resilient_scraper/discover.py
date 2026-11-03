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

import json
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
        try:
            results = _discover_nodriver(url, wait, scroll, headless, max_results)
        except Exception:  # noqa: BLE001 - e.g. no Chrome binary installed
            if engine == "nodriver":
                raise
            return _discover_playwright(url, wait, scroll, headless, max_results)
        # A headless run that finds nothing on a protected site is the classic
        # "the bot check hid everything" case — a visible window clears far more
        # challenges, so retry headful once before giving up. Best effort: on a
        # machine with no display the headful launch fails, and "nothing found"
        # is still the honest answer.
        if not results and headless:
            try:
                results = _discover_nodriver(url, wait, scroll, False, max_results)
            except Exception:  # noqa: BLE001
                results = []
        return results
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
    from .layers import _silence_proactor_del_noise
    _silence_proactor_del_noise()      # no-op off Windows

    async def run() -> dict[str, ApiEndpoint]:
        import nodriver as uc
        from nodriver import cdp

        found: dict[str, ApiEndpoint] = {}
        # url -> (status, mime, rtype, request_id). Keyed by URL so repeated
        # calls dedupe.
        seen: dict[str, tuple] = {}
        methods: dict[str, str] = {}          # request_id -> HTTP method
        browser = None
        try:
            browser = await uc.start(headless=headless)
            tab = await browser.get("about:blank")
            # Generous buffers so response bodies are still readable later.
            await tab.send(cdp.network.enable(
                max_total_buffer_size=100_000_000,
                max_resource_buffer_size=20_000_000,
            ))

            async def on_request(evt: "cdp.network.RequestWillBeSent") -> None:
                methods[str(evt.request_id)] = evt.request.method

            async def on_response(evt: "cdp.network.ResponseReceived") -> None:
                r = evt.response
                t = getattr(evt, "type_", None)
                # ResourceType is a plain Enum: str() gives "ResourceType.XHR".
                rtype = str(getattr(t, "value", t) or "").lower()
                if rtype not in ("xhr", "fetch"):
                    return
                if "json" in (r.mime_type or "").lower() and 200 <= r.status < 300:
                    seen.setdefault(r.url, (r.status, r.mime_type, rtype,
                                            evt.request_id))

            tab.add_handler(cdp.network.RequestWillBeSent, on_request)
            tab.add_handler(cdp.network.ResponseReceived, on_response)
            await tab.get(url)

            # Let late XHRs fire; scrolling triggers infinite-scroll loaders.
            rounds = 3 if scroll else 1
            for _ in range(rounds):
                if scroll:
                    await tab.scroll_down(300)
                await tab.sleep(wait / rounds)

            # Read the body the page actually received (CDP getResponseBody):
            # no second request, so POST/GraphQL and header-authenticated
            # APIs keep their real method and body, and CORS never applies.
            for u, (status, mime, rtype, request_id) in list(seen.items()):
                method = methods.get(str(request_id), "GET")
                try:
                    body, b64 = await tab.send(
                        cdp.network.get_response_body(request_id))
                    if b64:
                        import base64
                        body = base64.b64decode(body).decode("utf-8", "replace")
                except Exception:  # noqa: BLE001 - body evicted
                    if method != "GET":
                        continue  # can't faithfully replay a non-GET
                    try:
                        js = (f"fetch({json.dumps(u)}).then(r => r.ok ? r.text() "
                              f": '')")
                        body = await tab.evaluate(js, await_promise=True)
                    except Exception:  # noqa: BLE001
                        continue
                if body:
                    _record(found, u, method, status, mime or "", rtype, body)
            return found
        finally:
            if browser is not None:
                try:
                    browser.stop()
                except Exception:  # noqa: BLE001
                    pass

    from .layers import run_coro_sync
    found = run_coro_sync(run())
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
