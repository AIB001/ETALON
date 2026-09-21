"""Use the pinned MolQuarry SDK with a shared, bounded HTTP allowance per data run."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from etalon.boundary.infra import load


@dataclass(frozen=True)
class DataBudget:
    max_requests: int = 100
    max_bytes: int = 50_000_000
    max_seconds: float = 300.0

    def __post_init__(self) -> None:
        if type(self.max_requests) is not int or not 0 <= self.max_requests <= 10_000:
            raise ValueError("max_requests must be an integer between 0 (offline) and 10000")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 1_000_000_000:
            raise ValueError("max_bytes must be an integer between 1 and 1000000000")
        if (isinstance(self.max_seconds, bool) or not isinstance(self.max_seconds, (int, float))
                or not math.isfinite(self.max_seconds) or not 0 < self.max_seconds <= 3600):
            raise ValueError("max_seconds must be finite, positive and at most 3600")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Quarry:
    """One operation's client; retries, redirects and concurrent sources share its allowance.

    The clock is a cooperative network deadline, not a process kill or CPU quota. Bytes
    count the raw HTTP response stream, including failed responses. MolQuarry separately
    bounds decoded response size. No HTTP response cache is used for new evidence runs.
    A byte limit stops further consumption; the received chunk that crosses it remains
    counted as an actual overrun, rather than being hidden from the usage journal.
    """

    def __init__(self, home: Path, budget: DataBudget, *, transport: Any = None) -> None:
        self.infra = load("molquarry")
        import httpx
        from molquarry import MolQuarry
        from molquarry.errors import MolQuarryError
        from molquarry.http import HttpClient

        self.budget = budget
        self.requests = self.bytes = 0
        self.exhausted: str | None = None
        self.started = time.monotonic()
        self.lock = threading.Lock()
        owner = self

        def charge(*, request: bool = False, size: int = 0) -> None:
            with owner.lock:
                owner.bytes += size
                # Refusing the next request must not discard already permitted in-flight replies.
                reason = owner.exhausted if request or owner.exhausted != "max_requests" else None
                if time.monotonic() - owner.started >= budget.max_seconds:
                    reason = reason or "max_seconds"
                if request and owner.requests >= budget.max_requests:
                    reason = reason or "max_requests"
                if owner.bytes > budget.max_bytes:
                    reason = reason or "max_bytes"
                if reason:
                    owner.exhausted = reason
                    raise MolQuarryError("data_budget_exhausted", f"ETALON data allowance exhausted: {reason}")
                owner.requests += int(request)

        class MeteredStream(httpx.SyncByteStream):
            def __init__(self, stream: Any) -> None:
                self.stream = stream

            def __iter__(self):
                for chunk in self.stream:
                    charge(size=len(chunk))
                    yield chunk

            def close(self) -> None:
                self.stream.close()

        def request_hook(_request: Any) -> None:
            charge(request=True)

        def response_hook(response: Any) -> None:
            if response.is_stream_consumed:
                # Injected transports may return a response whose content is already buffered.
                charge(size=len(response.content))
            else:
                response.stream = MeteredStream(response.stream)

        http = HttpClient(cache_dir=None, transport=transport,
                          timeout=min(30.0, budget.max_seconds),
                          max_retry_wait=min(30.0, budget.max_seconds),
                          max_response_bytes=min(20 * 1024 * 1024, budget.max_bytes))
        http.client.event_hooks = {"request": [request_hook], "response": [response_hook]}
        self.client = MolQuarry(home=home, cache=False, http=http)

    def usage(self) -> dict[str, Any]:
        with self.lock:
            return {"requests": self.requests, "response_bytes": self.bytes,
                    "elapsed_seconds": time.monotonic() - self.started,
                    "exhausted": self.exhausted, "cost_unit": "HTTP requests and response bytes"}

    def __enter__(self) -> Quarry:
        return self

    def __exit__(self, *_: Any) -> None:
        self.client.close()


def describe(source: str | None = None) -> dict[str, Any]:
    """Catalog and exact operation schemas; discovery performs no HTTP requests."""
    with Quarry(Path(".molquarry"), DataBudget(max_requests=0)) as quarry:
        return {"infrastructure": quarry.infra.provenance(),
                "source": quarry.client.describe(source)} if source else {
                    "infrastructure": quarry.infra.provenance(), "sources": quarry.client.sources()}
