"""Read-only HTTP queries, bounded retries, per-source throttling and a TTL cache."""

import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from ._version import __version__
from .errors import MolQuarryError
from .models import Provenance, utcnow
from .transport import StdlibTransport


@dataclass
class Response:
    data: Any
    provenance: Provenance
    headers: dict[str, str]


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


def http_error(response: httpx.Response, source: str) -> MolQuarryError:
    status = response.status_code
    code = {
        400: "invalid_request",
        401: "authentication_required",
        403: "access_denied",
        404: "not_found",
        429: "rate_limited",
    }.get(status, "upstream_error")
    return MolQuarryError(
        code,
        f"{source} returned HTTP {status}",
        source=source,
        retryable=status == 429 or status >= 500,
        details={"status": status, "retry_after": response.headers.get("retry-after")},
    )


class HttpClient:
    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        timeout: float = 30,
        max_attempts: int = 3,
        transport: httpx.BaseTransport | None = None,
        throttle: bool = True,
        sleep=time.sleep,
        max_retry_wait: float = 30,
        max_response_bytes: int = 20 * 1024 * 1024,
    ):
        if timeout <= 0 or max_attempts < 1 or max_retry_wait < 0 or max_response_bytes < 1:
            raise ValueError("timeout/max_attempts must be positive; max_retry_wait nonnegative")
        self.client = httpx.Client(
            timeout=timeout,
            transport=transport,
            mounts={"https://clinicaltrials.gov": StdlibTransport()} if transport is None else None,
            follow_redirects=False,
            headers={
                "User-Agent": f"MolQuarry/{__version__} (+CADD data access)",
                "Accept": "application/json",
            },
        )
        self.cache_dir = cache_dir
        self.max_response_bytes = max_response_bytes
        self.max_attempts = max_attempts
        self.max_retry_wait = max_retry_wait
        self.throttle = throttle
        self.sleep = sleep
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()
        self._source_locks: dict[str, threading.Lock] = {}

    def close(self):
        self.client.close()

    def pace(self, spec):
        if not self.throttle:
            return
        with self._lock:
            source_lock = self._source_locks.setdefault(spec.id, threading.Lock())
        with source_lock:
            wait = spec.min_interval_seconds - (
                time.monotonic() - self._last_request.get(spec.id, -1e20)
            )
            if wait > 0:
                self.sleep(wait)
            self._last_request[spec.id] = time.monotonic()

    def _cached(self, key: str, ttl: float) -> Response | None:
        if self.cache_dir is None or ttl <= 0:
            return None
        try:
            value = json.loads((self.cache_dir / f"{key}.json").read_text())
            age = time.time() - value["stored_at"]
            if not 0 <= age < ttl:
                return None
            provenance = Provenance.model_validate(value["provenance"])
            provenance.cached = True
            return Response(value["data"], provenance, value["headers"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _store(self, key: str, result: Response):
        if self.cache_dir is None:
            return
        # Cache write failures must not invalidate a successfully retrieved record.
        tmp: str | None = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", dir=self.cache_dir, delete=False) as f:
                tmp = f.name
                json.dump(
                    {
                        "stored_at": time.time(),
                        "data": result.data,
                        "provenance": result.provenance.model_dump(),
                        "headers": result.headers,
                    },
                    f,
                )
            os.replace(tmp, self.cache_dir / f"{key}.json")
        except OSError:
            pass
        finally:
            if tmp and Path(tmp).exists():
                Path(tmp).unlink(missing_ok=True)

    def json(self, spec, method: str, url: str, **kwargs) -> Response:
        return self._request(spec, method, url, response_format="json", **kwargs)

    def text(self, spec, method: str, url: str, **kwargs) -> Response:
        return self._request(spec, method, url, response_format="text", **kwargs)

    def _request(
        self,
        spec,
        method: str,
        url: str,
        *,
        params=None,
        body=None,
        form=None,
        cache: bool = True,
        empty_ok: bool = False,
        headers: dict[str, str] | None = None,
        private: bool = False,
        response_format: str = "json",
        redirect_hosts: frozenset[str] = frozenset(),
    ) -> Response:
        # Credentials use headers or private OAuth forms, neither of which is hashed.
        # Scientific JSON bodies distinguish requests; private replies are not cached.
        if private:
            cache = False
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = fingerprint(
            {
                "source": spec.id,
                "method": method,
                "url": url,
                "params": params,
                "body": body,
                "form": form if not private else None,
                "response_format": response_format,
                "url_query_policy": "merge-v1",
            }
        )
        hit = self._cached(key, spec.cache_ttl_seconds) if cache else None
        if hit is not None:
            return hit
        for attempt in range(self.max_attempts):
            self.pace(spec)
            try:
                # httpx's params argument replaces an existing query string, even for {}.
                # Preserve fixed URL parameters and override only explicitly supplied keys.
                target = str(httpx.URL(url).copy_merge_params(params))
                for redirects in range(6):
                    with self.client.stream(
                        method,
                        target,
                        json=body,
                        data=form,
                        headers=headers,
                    ) as response:
                        if response.is_redirect and redirect_hosts and method == "GET":
                            target = urljoin(target, response.headers.get("location", ""))
                            parsed = urlparse(target)
                            if (
                                redirects == 5
                                or parsed.scheme != "https"
                                or parsed.hostname not in redirect_hosts
                                or parsed.username
                                or parsed.password
                                or parsed.port not in (None, 443)
                            ):
                                raise MolQuarryError(
                                    "invalid_redirect", "Unapproved response redirect"
                                )
                            self.pace(spec)
                            continue
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.max_response_bytes:
                                raise MolQuarryError(
                                    "response_too_large",
                                    "Query response exceeds byte budget; use bulk",
                                    source=spec.id,
                                )
                            chunks.append(chunk)
                        decoded_headers = dict(response.headers)
                        decoded_headers.pop("content-encoding", None)
                        decoded_headers.pop("content-length", None)
                        response = httpx.Response(
                            response.status_code,
                            headers=decoded_headers,
                            content=b"".join(chunks),
                            request=response.request,
                        )
                        break
            except httpx.TransportError as exc:
                if attempt + 1 < self.max_attempts:
                    self.sleep(min(2**attempt, self.max_retry_wait))
                    continue
                raise MolQuarryError(
                    "network_error",
                    f"{spec.id} request failed ({type(exc).__name__})",
                    source=spec.id,
                    retryable=True,
                ) from exc
            if response.status_code == 429 or response.status_code >= 500:
                delay = retry_after_seconds(response.headers.get("retry-after"))
                delay = delay if delay is not None else 2**attempt
                if attempt + 1 < self.max_attempts and delay <= self.max_retry_wait:
                    self.sleep(delay)
                    continue
            if not response.is_success:
                raise http_error(response, spec.id)
            try:
                data = (
                    None
                    if not response.content and empty_ok
                    else response.json()
                    if response_format == "json"
                    else response.text
                )
            except ValueError as exc:
                raise MolQuarryError(
                    "invalid_response",
                    f"{spec.id} returned non-JSON content",
                    source=spec.id,
                ) from exc
            if isinstance(data, dict) and data.get("errors"):
                raise MolQuarryError(
                    "graphql_error",
                    f"{spec.id} returned GraphQL errors",
                    source=spec.id,
                    details={} if private else {"errors": data["errors"]},
                )
            result_headers = {
                k: v
                for k, v in response.headers.items()
                if k
                in {
                    "content-type",
                    "etag",
                    "last-modified",
                    "link",
                    "x-uniprot-release",
                    "x-total-results",
                    "retry-after",
                }
            }
            result = Response(
                data,
                Provenance(
                    source=spec.id,
                    url=str(response.url),
                    method=method,
                    retrieved_at=utcnow(),
                    request_sha256=key,
                    response_sha256=hashlib.sha256(response.content).hexdigest(),
                    source_version=result_headers.get("x-uniprot-release"),
                    license_url=spec.license_url,
                    etag=result_headers.get("etag"),
                    last_modified=result_headers.get("last-modified"),
                ),
                result_headers,
            )
            if cache:
                self._store(key, result)
            return result
        raise AssertionError("unreachable")
