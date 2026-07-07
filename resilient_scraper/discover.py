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


def discover_apis(
    url: str,
    wait: float = 6.0,
    scroll: bool = True,
    headless: bool = True,
    max_results: int = 10,
) -> list[ApiEndpoint]:
    """Load `url` in a real browser and return the data-bearing endpoints it
    called, best first. Requires Playwright (`pip install playwright` +
    `python -m playwright install chromium`)."""
    from playwright.sync_api import sync_playwright

    found: dict[str, ApiEndpoint] = {}

    def on_response(response) -> None:  # noqa: ANN001
        try:
            req = response.request
            rtype = req.resource_type
            if rtype not in ("xhr", "fetch"):
                return
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype.lower():
                return
            if not (200 <= response.status < 300):
                return
            body = response.text()
            data = json.loads(body)
            shape, score = _describe_json(data)
            # Prefer bigger payloads a touch — the real collection is rarely tiny.
            score += min(len(body), 200_000) / 100_000
            found[response.url] = ApiEndpoint(
                method=req.method,
                url=response.url,
                status=response.status,
                content_type=ctype.split(";")[0],
                size=len(body),
                resource_type=rtype,
                score=round(score, 1),
                preview=shape,
                sample=data,
            )
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

    ranked = sorted(found.values(), key=lambda e: e.score, reverse=True)
    return ranked[:max_results]
