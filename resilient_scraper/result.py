"""The single object every layer returns, so the orchestrator can reason
about success/failure the same way regardless of which strategy ran."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FetchResult:
    """Outcome of one fetch attempt."""

    url: str
    status: Optional[int] = None      # HTTP status, or None if the layer errored out
    text: str = ""                    # response body (HTML or JSON text)
    layer: str = ""                   # which strategy produced this ("curl_cffi", "playwright")
    elapsed: float = 0.0              # seconds spent
    ok: bool = False                  # did we get usable content?
    error: Optional[str] = None       # exception text if the layer failed hard
    blocked: bool = False             # did this look like a bot-challenge/block page?

    # populated only when the orchestrator gives up, listing what it tried
    attempts: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    def short(self) -> str:
        body = f"{len(self.text)} chars" if self.text else "empty"
        flag = "OK" if self.ok else ("BLOCKED" if self.blocked else "FAIL")
        return f"[{flag}] {self.status} via {self.layer or '-'} · {body} · {self.elapsed:.2f}s"
