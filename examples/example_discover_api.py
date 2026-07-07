"""Find the JSON API behind a page automatically.

quotes.toscrape.com/scroll renders nothing useful in its initial HTML — it
pulls quotes over XHR as you scroll. Instead of driving a browser to scrape
the rendered page, we let the discoverer find the endpoint, then hit it
directly with the fast Layer-1 fetcher."""

import json

from resilient_scraper import Fetcher, ScraperConfig
from resilient_scraper.discover import discover_apis


def main() -> None:
    page = "https://quotes.toscrape.com/scroll"
    print(f"Watching what {page} fetches under the hood...\n")

    endpoints = discover_apis(page, wait=5, scroll=True)
    if not endpoints:
        print("No JSON API found — the page may render server-side.")
        return

    print(f"Found {len(endpoints)} data endpoint(s), best first:\n")
    for ep in endpoints:
        print(ep.line())
        print()

    # Now hit the top one directly with the cheap HTTP layer — no browser.
    best = endpoints[0]
    print(f"Calling the top endpoint directly: {best.url}")
    data = json.loads(Fetcher(ScraperConfig(min_delay=0.2, max_delay=0.4)).fetch(best.url).text)
    quotes = data.get("quotes", []) if isinstance(data, dict) else []
    print(f"Got {len(quotes)} quotes straight from JSON. First author:",
          quotes[0]["author"]["name"] if quotes else "-")


if __name__ == "__main__":
    main()
