"""Command-line entry point:

    python -m resilient_scraper https://example.com https://books.toscrape.com/
    python -m resilient_scraper --file urls.txt --out run --save-bodies

Fetches each URL through the escalation ladder, prints a summary, and writes
a CSV report (and optionally the page bodies) to the output directory."""

from __future__ import annotations

import argparse
import sys

from .config import ScraperConfig
from .export import save_bodies, save_summary_csv
from .fetcher import Fetcher


def _read_urls(args: argparse.Namespace) -> list[str]:
    urls = list(args.urls)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            urls += [line.strip() for line in fh if line.strip()
                     and not line.startswith("#")]
    return urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="resilient_scraper",
        description="Layered, polite fetcher for public web data.",
    )
    parser.add_argument("urls", nargs="*", help="one or more URLs to fetch")
    parser.add_argument("--file", help="text file with one URL per line")
    parser.add_argument("--out", default="output", help="output directory")
    parser.add_argument("--save-bodies", action="store_true",
                        help="also write each page body to the output dir")
    parser.add_argument("--min-delay", type=float, default=1.0)
    parser.add_argument("--max-delay", type=float, default=3.0)
    parser.add_argument("--no-browser", action="store_true",
                        help="disable the Playwright/nodriver fallback layers")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    urls = _read_urls(args)
    if not urls:
        parser.error("give at least one URL, or --file with URLs")

    config = ScraperConfig(
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        use_playwright_fallback=not args.no_browser,
        use_nodriver_fallback=not args.no_browser,
    )
    fetcher = Fetcher(config, verbose=not args.quiet)

    results = fetcher.fetch_all(urls)

    print("\n=== summary ===")
    ok = 0
    for r in results:
        mark = "OK " if r.ok else ("BLK" if r.blocked else "ERR")
        ok += r.ok
        print(f"[{mark}] {r.status or '-':>4} via {r.layer or '-':<10} {r.url}")
    print(f"\n{ok}/{len(results)} succeeded")

    csv_path = save_summary_csv(results, f"{args.out}/summary.csv")
    print(f"report: {csv_path}")
    if args.save_bodies:
        n = save_bodies(results, args.out)
        print(f"bodies: {n} written to {args.out}/")

    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
