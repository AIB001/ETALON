"""Use the pinned MolQuarry SDK with a shared, bounded HTTP allowance per data run."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from etalon.boundary.infra import load
from etalon.data.source_archive import SourceArchive, public_url


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

    def __init__(self, home: Path, budget: DataBudget, *, transport: Any = None,
                 archive_root: Path | None = None, source: str | None = None) -> None:
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
        self.archive = SourceArchive(archive_root, source=source) if archive_root else None
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
            def __init__(self, stream: Any, writer: Any = None) -> None:
                self.stream = stream
                self.writer = writer

            def __iter__(self):
                try:
                    for chunk in self.stream:
                        # Count and retain received bytes even when this chunk crosses the limit.
                        if self.writer:
                            self.writer.write(chunk)
                        charge(size=len(chunk))
                        yield chunk
                except BaseException:
                    if self.writer:
                        self.writer.finish(reason="stream_aborted")
                    raise
                else:
                    if self.writer:
                        self.writer.finish(complete=True)

            def close(self) -> None:
                try:
                    # Downloads may reject status/redirect headers before reading the body.
                    # Preserve that error response too, within the same byte/time allowance.
                    if self.writer and not self.writer.closed:
                        try:
                            for chunk in self.stream:
                                self.writer.write(chunk)
                                charge(size=len(chunk))
                        except Exception:
                            self.writer.finish(reason="close_drain_aborted")
                            raise
                        self.writer.finish(complete=True)
                finally:
                    self.stream.close()
                    if self.writer:
                        self.writer.finish(reason="stream_not_consumed")

        def request_hook(_request: Any) -> None:
            attempt = owner.archive.begin_attempt(_request) if owner.archive else None
            try:
                charge(request=True)
            except Exception as error:
                if attempt is not None:
                    attempt["error"] = {"type": type(error).__name__, "code": "request_refused"}
                raise
            if attempt is not None:
                attempt["admitted"] = True

        def response_hook(response: Any) -> None:
            writer = owner.archive.response(response) if owner.archive else None
            if response.is_stream_consumed:
                # Injected transports may return a response whose content is already buffered.
                if writer:
                    writer.write(response.content)
                try:
                    charge(size=len(response.content))
                except Exception:
                    if writer:
                        writer.finish(reason="budget_exhausted")
                    raise
                if writer:
                    writer.finish(complete=True)
            else:
                response.stream = MeteredStream(response.stream, writer)

        class ArchivedHttpClient(HttpClient):
            def _request(self, spec: Any, method: str, url: str, **kwargs: Any) -> Any:
                if owner.archive is None:
                    return super()._request(spec, method, url, **kwargs)
                operation = owner.archive.begin_operation(spec.id, method, url, kwargs)
                try:
                    result = super()._request(spec, method, url, **kwargs)
                except Exception as error:
                    owner.archive.finish_operation(operation, error=error)
                    raise
                owner.archive.finish_operation(operation, provenance=result.provenance.model_dump())
                # Workflows may persist this provenance independently of service._query_pages.
                # Keep scientific data and the SDK's original digests; only publicize locators.
                result.provenance.url = public_url(result.provenance.url)
                result.headers = {key: public_url(value) if value.startswith(("https://", "http://"))
                                  else value for key, value in result.headers.items()}
                return result

        http = ArchivedHttpClient(cache_dir=None, transport=transport,
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
        try:
            self.client.close()
        finally:
            if self.archive:
                self.archive.finalize()


def describe(source: str | None = None) -> dict[str, Any]:
    """Catalog and exact operation schemas; discovery performs no HTTP requests."""
    with Quarry(Path(".molquarry"), DataBudget(max_requests=0)) as quarry:
        return {"infrastructure": quarry.infra.provenance(),
                "source": quarry.client.describe(source)} if source else {
                    "infrastructure": quarry.infra.provenance(), "sources": quarry.client.sources()}
