# resilient-scraper

A small, layered fetcher for **public** web data that survives real bot
defence — JS-rendered pages, TLS/fingerprint checks, rate limits, and managed
Cloudflare/DataDome challenges — without pretending to be magic.

The idea isn't "defeat every protection." It's **spend the least effort that
works.** Each request walks an escalation ladder, cheapest strategy first, and
only climbs when a layer actually fails or hits a challenge page.

```
fetch(url)
  │
  ├─ Layer 1  curl_cffi    real Chrome/Safari TLS + HTTP2 fingerprint, no browser
  │             │          fast (~0.5s); clears the first line of many CDNs
  │             ▼  blocked / errored?
  ├─ Layer 2  Playwright   real headless browser, executes JS, light stealth
  │             │          renders SPAs; clears simple JS gates
  │             ▼  still blocked?
  ├─ Layer 3  nodriver     undetected real Chrome over raw CDP — no automation
  │             │          tells; clears managed Cloudflare/DataDome challenges
  │             ▼  still blocked?
  └─ give up, return the last response flagged `blocked` (never a false success)
```

Between every attempt: **per-host rate limiting**, **exponential backoff with
jitter**, optional **proxy rotation**, and **block detection** that reads the
body for Cloudflare / DataDome / PerimeterX / Incapsula signatures instead of
trusting the HTTP status (plenty of challenge pages return `200`).

## Does it actually work?

Against `scrapingcourse.com/antibot-challenge` (a live Cloudflare-style
managed challenge), the ladder does exactly what it's designed to:

```
curl_cffi#1   → blocked (403)      network fingerprint is right, but the JS
                                   challenge needs a browser
playwright#1  → blocked (200)      real browser, but Cloudflare still flags
                                   vanilla automation
nodriver#1    → OK (200, 27 KB)    undetected real Chrome clears it
```

Two levers do most of the work, and the design leans on both:

1. **Go around, don't go through.** The biggest single win is finding the JSON
   API a page fetches its data from and hitting that directly — usually far
   less defended and returns clean structured data. This is automated: see
   **[Find the hidden API](#find-the-hidden-api)** below.
2. **Be a real browser, not a copy of one.** `curl_cffi` copies a current
   browser's TLS/JA3 + HTTP2 fingerprint at the network layer; when JS is
   required, we drive an actual Chrome rather than hand-faking the hundreds of
   JS-level signals (which must all stay mutually consistent — a losing game).
   [`examples/example_fingerprint_check.py`](examples/example_fingerprint_check.py)
   proves the network-layer copy: our JA3/JA4 read as Chrome, a stdlib request
   reads as Python.

## Find the hidden API

Most "hard to scrape" pages are SPAs that fetch their data from a JSON endpoint.
That endpoint is usually far less defended than the page and hands you clean,
structured, paginated data — no HTML parsing. Finding it by hand means opening
DevTools and reading the Network tab; this does it for you:

```bash
python -m resilient_scraper "https://quotes.toscrape.com/scroll" --discover
```

```
# https://quotes.toscrape.com/scroll
  [ 10.0] GET  https://quotes.toscrape.com/api/quotes?page=1
           dict -> 'quotes': list of 10 objects · keys: author, tags, text
  [ 10.0] GET  https://quotes.toscrape.com/api/quotes?page=2
  ...
```

It opens the page in a real browser, watches every network response, keeps the
ones returning JSON, and ranks them by how much they look like *the* data
source (a list of records scores highest). It even surfaces the pagination
pattern. Then you fetch the endpoint directly with the fast Layer 1 — see
[`examples/example_discover_api.py`](examples/example_discover_api.py).

```python
from resilient_scraper import discover_apis, Fetcher
endpoints = discover_apis("https://quotes.toscrape.com/scroll")
best = endpoints[0]                       # ranked, best first
data = Fetcher().fetch(best.url).text     # hit the JSON directly, no browser
```

## Install

```bash
pip install -r requirements.txt

# Layer 2 (Playwright) needs its browser downloaded once:
python -m playwright install chromium

# Layer 3 (nodriver) uses your installed Google Chrome — nothing to download.
```

Layer 1 alone handles a large share of targets. Add the browser layers when you
meet JS-rendered content or managed challenges.

## Use it — library

```python
from resilient_scraper import Fetcher, ScraperConfig

fetcher = Fetcher(ScraperConfig(min_delay=1.0, max_delay=3.0), verbose=True)

result = fetcher.fetch("https://books.toscrape.com/")
if result:                       # FetchResult is truthy when ok
    print(result.status, len(result.text), "via", result.layer)
    print("tried:", result.attempts)
```

### Proxies

Rotating IPs is the legitimate answer to IP-based rate limiting. Pass a list
and the fetcher rotates over them, tracks each one's failures, and retires a
proxy on a cooldown once it fails too often (bringing it back after) — so a
dead proxy doesn't silently tank your success rate. Residential/mobile are
recommended for tough targets; datacenter IPs are often pre-flagged. Bring your
own, or plug a provider's gateway straight in.

```python
cfg = ScraperConfig(proxies=[
    "http://user:pass@gate.provider.com:7000",
    "http://user:pass@gate.provider.com:7001",
])
fetcher = Fetcher(cfg)
```

Probe them up front and drop the dead ones before a run:

```python
from resilient_scraper import ProxyPool
pool = ProxyPool(cfg.proxies)
print(pool.check())      # {"alive": [...], "dead": [...]}
```

## Use it — command line

```bash
# fetch a few URLs, write output/summary.csv
python -m resilient_scraper https://books.toscrape.com/ https://quotes.toscrape.com/

# from a file, and dump each page body too
python -m resilient_scraper --file urls.txt --out run --save-bodies

# network-only (no browser layers), quieter
python -m resilient_scraper --file urls.txt --no-browser -q
```

The CSV report records, per URL: which layer won, status, size, timing, whether
it was blocked, and the full attempt chain — the audit trail a client wants
next to the data.

## Run the examples

```bash
python examples/example_basic.py             # HTML page, Layer 1
python examples/example_api_discovery.py     # the JSON-API-behind-the-page move
python examples/example_fingerprint_check.py # proof the TLS copy reads as Chrome
```

## Honest limits

- This clears early, mid, and many managed challenges. **Enterprise**
  Cloudflare/DataDome tuned against your IP, and CAPTCHAs, still need
  residential proxies and/or a CAPTCHA-solving service — no home-grown client
  beats those for free. When you hit that wall, say so up front.
- "Zero ban risk" doesn't exist. Scrape politely: real rate limits, minimal
  requests, cache what you can.

## Scope & ethics

Built for collecting **public** data — catalogues, listings, prices, public
directories. **Not** for getting behind login walls, harvesting personal data,
or anything a site's terms or the law forbid. Check `robots.txt` and the
target's terms; when a job asks you to cross that line, decline it.

## Layout

```
resilient_scraper/
  fetcher.py   orchestrator — the escalation ladder
  layers.py    the strategies (curl_cffi, Playwright, nodriver)
  discover.py  hidden-API discovery (the "go around" tool)
  proxies.py   rotating proxy pool with health tracking + retirement
  utils.py     rate limiting, block detection, backoff
  export.py    CSV run report + body dumps
  config.py    every tunable, with defaults
  result.py    FetchResult — the common return type
  __main__.py  the `python -m resilient_scraper` CLI
examples/
  example_basic.py
  example_discover_api.py
  example_api_discovery.py
  example_fingerprint_check.py
```
