"""Basic fetch: point the Fetcher at a page and let it pick the layer.

Target is books.toscrape.com — a sandbox site built for practicing scraping.
Always practice on sandboxes or public data you're allowed to collect."""

from resilient_scraper import Fetcher, ScraperConfig


def main() -> None:
    fetcher = Fetcher(ScraperConfig(min_delay=0.5, max_delay=1.0), verbose=True)

    result = fetcher.fetch("https://books.toscrape.com/")
    print("\n" + result.short())
    print("layers tried:", " -> ".join(result.attempts))

    if result.ok:
        # Tiny parse just to prove we got real content back.
        import re
        titles = re.findall(r'<article class="product_pod">.*?title="([^"]+)"',
                            result.text, re.S)
        print(f"\nFound {len(titles)} book titles on the page. First 5:")
        for t in titles[:5]:
            print("  -", t)


if __name__ == "__main__":
    main()
