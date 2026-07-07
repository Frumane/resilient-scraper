"""Small helpers for turning fetch results into files. Kept deliberately
generic — extraction is job-specific, but saving a run summary and dumping
page bodies are needed on almost every job."""

from __future__ import annotations

import csv
import os
from typing import Iterable

from .result import FetchResult


def save_summary_csv(results: Iterable[FetchResult], path: str) -> str:
    """Write a per-URL run report: which layer won, status, size, timing.

    This is the artifact a client actually wants alongside the data — proof
    of what was fetched, how, and whether anything got blocked."""
    rows = list(results)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["url", "ok", "status", "layer", "chars", "seconds",
                         "blocked", "attempts", "error"])
        for r in rows:
            writer.writerow([
                r.url, r.ok, r.status, r.layer, len(r.text),
                f"{r.elapsed:.2f}", r.blocked, " -> ".join(r.attempts),
                r.error or "",
            ])
    return path


def save_bodies(results: Iterable[FetchResult], directory: str) -> int:
    """Dump each successful response body to its own file in `directory`.
    Returns the count written. Filenames are derived from the URL, sanitised."""
    os.makedirs(directory, exist_ok=True)
    written = 0
    for i, r in enumerate(results):
        if not r.ok or not r.text:
            continue
        safe = "".join(c if c.isalnum() else "_" for c in r.url)[-80:]
        ext = "json" if r.text.lstrip()[:1] in "{[" else "html"
        with open(os.path.join(directory, f"{i:03d}_{safe}.{ext}"), "w",
                  encoding="utf-8") as fh:
            fh.write(r.text)
        written += 1
    return written
