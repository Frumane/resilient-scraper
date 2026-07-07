"""Proof that the fingerprint copy actually works.

We hit a TLS-mirror API twice — once with Python's stdlib (what a naive
scraper looks like), once through our curl_cffi layer — and compare the
JA3/JA4 fingerprints the server sees. If the copy works, ours should read
as a real Chrome and the stdlib one should stand out."""

import json
import urllib.request

from resilient_scraper import Fetcher, ScraperConfig

MIRROR = "https://tls.peet.ws/api/all"


def with_stdlib() -> dict:
    req = urllib.request.Request(MIRROR, headers={"User-Agent": "Python-urllib/3.11"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def with_our_tool() -> dict:
    fetcher = Fetcher(ScraperConfig(min_delay=0.2, max_delay=0.4))
    result = fetcher.fetch(MIRROR)
    return json.loads(result.text)


def summarize(label: str, data: dict) -> None:
    tls = data.get("tls", {})
    http2 = data.get("http2", {})
    print(f"\n=== {label} ===")
    print("  Reported UA :", data.get("user_agent", "-")[:70])
    print("  JA3 hash    :", tls.get("ja3_hash", "-"))
    print("  JA4         :", tls.get("ja4", "-"))
    print("  Akamai H2   :", http2.get("akamai_fingerprint_hash", "-"))
    if "peetprint_hash" in tls:
        print("  PeetPrint   :", tls.get("peetprint_hash"))


def main() -> None:
    print("Asking the mirror what our TLS handshake looks like...")
    summarize("Naive stdlib (urllib)", with_stdlib())
    summarize("Our curl_cffi layer", with_our_tool())
    print("\nIf the JA3/JA4 differ, the server can tell the two clients apart —")
    print("ours should match a real Chrome, the stdlib one should not.")


if __name__ == "__main__":
    main()
