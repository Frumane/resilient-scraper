"""The #1 move: don't scrape the rendered HTML — find the JSON API behind it.

Most "hard to scrape" pages are React/Vue apps that fetch their data from a
JSON endpoint. That endpoint is usually far less defended than the HTML page,
returns clean structured data (no parsing!), and supports paging.

How to find it on a real site:
    1. Open the page in Chrome, hit F12 -> Network -> filter "Fetch/XHR"
    2. Reload; watch the requests that fire
    3. Find the one whose response holds the data you see on screen
    4. Copy its URL (and any query params) and hit it directly, like below

Here we hit a public sandbox JSON API to show the Fetcher returns JSON just
as happily as HTML — the layer logic is identical."""

import json

from resilient_scraper import Fetcher, ScraperConfig


def main() -> None:
    fetcher = Fetcher(ScraperConfig(min_delay=0.3, max_delay=0.6), verbose=True)

    # This is the "API behind the page" — structured data, no HTML parsing.
    api_url = "https://dummyjson.com/products?limit=5&select=title,price,brand"
    result = fetcher.fetch(api_url)
    print("\n" + result.short())

    if result.ok:
        data = json.loads(result.text)
        print(f"\nPulled {len(data['products'])} products straight from JSON:")
        for p in data["products"]:
            print(f"  - {p['title']:<28} ${p['price']}")


if __name__ == "__main__":
    main()
