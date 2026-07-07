"""Proxy pool with health tracking.

Rotating IPs is the legitimate answer to IP-based rate limiting and blocking —
you spread requests across many addresses so no single one gets hammered. The
catch is that proxies die (especially cheap ones), and a dead proxy in
rotation silently tanks your success rate. This pool rotates over healthy
proxies, counts failures, and retires a proxy on a cooldown once it fails too
often — bringing it back automatically after the cooldown in case it recovered.

Bring your own proxies (residential/mobile recommended for tough targets).
Plug a provider's endpoints straight in:

    pool = ProxyPool(["http://user:pass@gate.provider.com:7000", ...])
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _ProxyState:
    url: str
    failures: int = 0            # consecutive failures
    retired_until: float = 0.0   # monotonic time; 0 = active
    successes: int = 0
    total_failures: int = 0


class ProxyPool:
    def __init__(self, proxies: list[str], max_failures: int = 3,
                 cooldown: float = 300.0):
        """max_failures consecutive misses retires a proxy for `cooldown`
        seconds, after which it's given another chance."""
        self._states = [_ProxyState(url=p) for p in dict.fromkeys(proxies)]
        self.max_failures = max_failures
        self.cooldown = cooldown
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._states)

    def _active(self) -> list[_ProxyState]:
        now = time.monotonic()
        return [s for s in self._states if s.retired_until <= now]

    def get(self) -> str | None:
        """Next healthy proxy (round-robin). If every proxy is in cooldown,
        return the one closest to recovering rather than nothing. Empty pool
        returns None so the caller just connects directly."""
        if not self._states:
            return None
        active = self._active()
        if active:
            self._cursor = (self._cursor + 1) % len(active)
            return active[self._cursor % len(active)].url
        # All retired — hand back the soonest-to-recover as a best effort.
        return min(self._states, key=lambda s: s.retired_until).url

    def _find(self, url: str) -> _ProxyState | None:
        return next((s for s in self._states if s.url == url), None)

    def report(self, proxy: str | None, ok: bool) -> None:
        """Feed the outcome of a request back so the pool can learn. A success
        clears the failure streak; enough consecutive failures retire it."""
        if proxy is None:
            return
        state = self._find(proxy)
        if state is None:
            return
        if ok:
            state.failures = 0
            state.retired_until = 0.0
            state.successes += 1
        else:
            state.failures += 1
            state.total_failures += 1
            if state.failures >= self.max_failures:
                state.retired_until = time.monotonic() + self.cooldown

    def check(self, test_url: str = "https://httpbin.org/ip",
              timeout: float = 10.0) -> dict:
        """Actively probe every proxy against `test_url` and retire the dead
        ones up front, so a run doesn't waste attempts discovering them. Needs
        curl_cffi. Returns {"alive": [...], "dead": [...]}."""
        from curl_cffi import requests as cffi

        alive, dead = [], []
        for state in self._states:
            try:
                r = cffi.get(test_url, proxies={"http": state.url, "https": state.url},
                             timeout=timeout, impersonate="chrome131")
                if 200 <= r.status_code < 400:
                    alive.append(state.url)
                    state.failures = 0
                    state.retired_until = 0.0
                    continue
            except Exception:  # noqa: BLE001
                pass
            dead.append(state.url)
            state.retired_until = time.monotonic() + self.cooldown
        return {"alive": alive, "dead": dead}

    def stats(self) -> dict:
        now = time.monotonic()
        return {
            "total": len(self._states),
            "active": len(self._active()),
            "retired": sum(1 for s in self._states if s.retired_until > now),
            "detail": [
                {"proxy": s.url, "successes": s.successes,
                 "total_failures": s.total_failures,
                 "retired": s.retired_until > now}
                for s in self._states
            ],
        }
