"""Transport observations inside an existing, sealed data snapshot.

Only body bytes observed before parsing are eligible. In particular, a parsed
MolQuarry cache entry is not an original HTTP response.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from etalon.data.artifacts import digest, file_hash, write_json

SCHEMA = "etalon-source-archive/1"
INDEX = "source-archive/index.json"
_RESPONSE_HEADERS = frozenset({
    "content-type", "content-encoding", "content-length", "etag", "last-modified",
    "x-uniprot-release", "x-total-results", "retry-after", "date", "link", "location",
})
_REQUEST_HEADERS = frozenset({"accept", "accept-encoding", "content-type", "range"})
# Existing ClinicalTrials/PLINDER inputs use page_token; their public HTTP APIs
# use pageToken. These opaque page positions are needed to reproduce a query,
# and are distinct from authentication tokens. Keep this exception explicit.
_PUBLIC_PAGINATION_KEYS = frozenset({"page_token", "pagetoken"})
_SECRET_KEYS = frozenset({
    "authorization", "proxyauthorization", "cookie", "setcookie", "apikey", "accesskey",
    "accesskeyid", "awsaccesskeyid", "secret", "secretkey", "password", "passwd", "token",
    "accesstoken", "refreshtoken", "idtoken", "clientsecret", "session", "sessionid",
    "signature", "sig", "credential", "credentials", "xamzcredential", "xamzsignature",
    "xamzsecuritytoken",
})


def _secret(key: str) -> bool:
    if key.casefold() in _PUBLIC_PAGINATION_KEYS:
        return False
    clean = re.sub("[^a-z0-9]", "", key.casefold())
    return clean in _SECRET_KEYS or clean.endswith(("password", "token", "secret"))


def public_url(value: str) -> str:
    """Keep scientific URL parameters; remove common authentication material."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return value
    # Split at the final @ rather than retaining a username or password.
    host = parsed.netloc.rsplit("@", 1)[-1]
    query = urlencode([(key, "REDACTED" if _secret(key) else item)
                       for key, item in parse_qsl(parsed.query, keep_blank_values=True)])
    return urlunsplit((parsed.scheme, host, parsed.path, query, ""))


def public_descriptor(value: Any) -> Any:
    """Sanitize descriptors/provenance, never an entire scientific response body."""
    if isinstance(value, dict):
        return {key: "REDACTED" if _secret(str(key)) else public_descriptor(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_descriptor(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"https?://[^\s<>\"']+", lambda match: public_url(match.group()), value)
    return value


def public_page(page: dict[str, Any]) -> dict[str, Any]:
    """Scientific records are unchanged; their request/provenance locators are public."""
    return {**page, "parameters": public_descriptor(page.get("parameters", {})),
            "provenance": public_descriptor(page.get("provenance", []))}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _Body:
    def __init__(self, archive: SourceArchive, attempt: dict[str, Any], *, decoded: bool) -> None:
        self.archive, self.attempt = archive, attempt
        self.decoded = decoded
        self.path = archive.directory / f".{attempt['capture_id']}.part"
        self.handle = self.path.open("xb")
        self.sha = hashlib.sha256()
        self.size = 0
        self.closed = False

    def write(self, chunk: bytes) -> None:
        self.handle.write(chunk)
        self.sha.update(chunk)
        self.size += len(chunk)

    def finish(self, *, complete: bool = False, reason: str | None = None) -> None:
        if self.closed:
            return
        self.closed = True
        self.handle.flush()
        os.fsync(self.handle.fileno())
        self.handle.close()
        sha = self.sha.hexdigest()
        relative = f"source-archive/bodies/{sha}.body"
        destination = self.archive.root / relative
        try:
            os.link(self.path, destination)
        except FileExistsError:
            if destination.stat().st_size != self.size or file_hash(destination) != sha:
                raise ValueError("source archive body digest collision or changed body") from None
        finally:
            self.path.unlink(missing_ok=True)
        length = self.attempt["response_headers"].get("content-length", "")
        if complete and not self.decoded and length.isdigit() and int(length) != self.size:
            complete, reason = False, "content_length_mismatch"
        self.attempt["body"] = {
            "path": relative, "sha256": sha, "bytes": self.size,
            "storage_encoding": "binary",
            "digest_scope": "http_content_decoded_body" if self.decoded else "http_content_encoded_body",
            "content_encoding": self.attempt["response_headers"].get("content-encoding", "identity"),
            "complete": complete, "incomplete_reason": None if complete else reason or "stream_not_consumed",
            "encoded_original_available": not self.decoded and complete,
        }
        self.attempt["body_finished_at"] = _now()


class SourceArchive:
    """One data run's observations; all publication uses its existing snapshot seal."""

    def __init__(self, root: Path, *, source: str | None = None) -> None:
        self.root = Path(root)
        self.directory = self.root / "source-archive"
        (self.directory / "bodies").mkdir(parents=True)
        self.source = source
        self.local = threading.local()
        self.lock = threading.Lock()
        self.attempts: list[dict[str, Any]] = []
        self.operations: list[dict[str, Any]] = []
        self.pages: list[dict[str, Any]] = []
        self.writers: list[_Body] = []
        self.metadata: dict[str, Any] | None = None

    def begin_operation(self, source: str, method: str, url: str,
                        kwargs: dict[str, Any]) -> dict[str, Any]:
        private = bool(kwargs.get("private"))
        descriptor = public_descriptor({"method": method, "url": url,
            "parameters": kwargs.get("params"),
            "body": "OMITTED_PRIVATE" if private else kwargs.get("body"),
            "form": "OMITTED_PRIVATE" if private else kwargs.get("form"),
            "response_format": kwargs.get("response_format", "json")})
        with self.lock:
            operation = {"operation_id": f"operation-{len(self.operations) + 1:06d}",
                         "source": source, "request": descriptor,
                         "request_descriptor_sha256": digest(descriptor),
                         "private": private, "capture_ids": [], "started_at": _now()}
            self.operations.append(operation)
        self.local.operation = operation
        return operation

    def begin_attempt(self, request: Any) -> dict[str, Any]:
        operation = getattr(self.local, "operation", None)
        descriptor = {"method": request.method, "url": public_url(str(request.url)),
            "headers": {key: public_descriptor(value) for key, value in request.headers.items()
                        if key in _REQUEST_HEADERS}}
        with self.lock:
            attempt = {"capture_id": f"http-{len(self.attempts) + 1:06d}",
                "operation_id": operation["operation_id"] if operation else None,
                "source": operation["source"] if operation else self.source,
                "request": descriptor, "request_descriptor_sha256": digest(descriptor),
                "requested_at": _now(), "retrieved_at": None,
                "admitted": False, "status_code": None,
                "response_headers": {}, "source_version": None, "body": None,
                "origin": "network", "private": bool(operation and operation["private"])}
            self.attempts.append(attempt)
            if operation:
                operation["capture_ids"].append(attempt["capture_id"])
        request.extensions["etalon_source_capture"] = attempt
        return attempt

    def response(self, response: Any) -> _Body | None:
        attempt = response.request.extensions["etalon_source_capture"]
        attempt.update(status_code=response.status_code, retrieved_at=_now(),
            response_headers={key: public_descriptor(value) for key, value in response.headers.items()
                              if key in _RESPONSE_HEADERS})
        attempt["source_version"] = response.headers.get("x-uniprot-release")
        if attempt["private"]:
            attempt["body_availability"] = "omitted_private_response"
            return None
        decoded = (response.is_stream_consumed and
                   response.headers.get("content-encoding", "identity").casefold() != "identity")
        writer = _Body(self, attempt, decoded=decoded)
        with self.lock:
            self.writers.append(writer)
        return writer

    def finish_operation(self, operation: dict[str, Any], *, provenance: dict[str, Any] | None = None,
                         error: Exception | None = None) -> None:
        operation["finished_at"] = _now()
        operation["outcome"] = "error" if error else "parsed_response"
        if error:
            operation["error"] = {"type": type(error).__name__, "code": getattr(error, "code", None)}
        if provenance:
            operation["provenance"] = public_descriptor(provenance)
            operation["provenance_digest_scope"] = "http_content_decoded_body"
            if provenance.get("cached"):
                # Only actual bodies already observed in this snapshot can satisfy a hit.
                matching = [item for item in self.attempts if item.get("provenance") and
                            item["provenance"].get("request_sha256") == provenance["request_sha256"] and
                            item["provenance"].get("response_sha256") == provenance["response_sha256"] and
                            (item.get("body") or {}).get("complete")]
                operation["origin"] = "cache"
                operation["original_availability"] = (
                    "original_available_in_snapshot" if matching else "original_unavailable")
                operation["original_capture_ids"] = [item["capture_id"] for item in matching]
            elif operation["capture_ids"]:
                attempt = next(item for item in self.attempts
                               if item["capture_id"] == operation["capture_ids"][-1])
                attempt["provenance"] = public_descriptor(provenance)
                attempt["provenance_digest_scope"] = "http_content_decoded_body"
        self.local.operation = None

    def link_page(self, path: Path, page: dict[str, Any]) -> None:
        links = []
        for provenance in page.get("provenance", []):
            matched = [operation for operation in self.operations
                if operation.get("provenance", {}).get("request_sha256") == provenance["request_sha256"]
                and operation.get("provenance", {}).get("response_sha256") == provenance["response_sha256"]]
            links.append({"request_sha256": provenance["request_sha256"],
                "response_sha256": provenance["response_sha256"],
                "response_digest_scope": "http_content_decoded_body",
                "operation_ids": [operation["operation_id"] for operation in matched],
                "capture_ids": [capture for operation in matched for capture in
                    operation.get("original_capture_ids", operation["capture_ids"])]})
        self.pages.append({"path": path.relative_to(self.root).as_posix(), "response_links": links})

    def finalize(self) -> dict[str, Any]:
        if self.metadata is not None:
            return self.metadata
        for writer in self.writers:
            writer.finish(reason="capture_interrupted")
        outputs = {path.relative_to(self.root).as_posix(): {"sha256": file_hash(path),
                   "bytes": path.stat().st_size} for path in sorted((self.root / "payload").rglob("*"))
                   if path.is_file()}
        bodies = [attempt["body"] for attempt in self.attempts if attempt["body"] is not None]
        coverage = {"request_attempts": len(self.attempts),
            "network_attempts": sum(attempt["admitted"] for attempt in self.attempts),
            "admitted_requests": sum(attempt["admitted"] for attempt in self.attempts),
            "responses": sum(attempt["status_code"] is not None for attempt in self.attempts),
            "complete_bodies": sum(body["complete"] for body in bodies),
            "incomplete_bodies": sum(not body["complete"] for body in bodies),
            "private_bodies_omitted": sum(attempt.get("body_availability") == "omitted_private_response"
                                          for attempt in self.attempts),
            "admitted_without_response": sum(attempt["admitted"] and attempt["status_code"] is None
                                              for attempt in self.attempts),
            "requests_refused": sum(not attempt["admitted"] for attempt in self.attempts),
            "cache_hits": sum(operation.get("origin") == "cache" for operation in self.operations),
            "cache_original_unavailable": sum(operation.get("original_availability") == "original_unavailable"
                                               for operation in self.operations)}
        value = {"schema": SCHEMA, "created_at": _now(), "http_cache_policy": "disabled_for_evidence_runs",
            "retention": "snapshot_evidence_no_ttl", "coverage": coverage,
            "attempts": self.attempts, "operations": self.operations,
            "normalized_pages": self.pages, "normalized_outputs": outputs}
        write_json(self.root / INDEX, value)
        gaps = (coverage["incomplete_bodies"] or coverage["cache_original_unavailable"]
                or coverage["private_bodies_omitted"] or coverage["admitted_without_response"])
        self.metadata = {"schema": SCHEMA, "index": INDEX, "coverage": coverage,
                         "status": ("captured_with_gaps" if gaps else "captured") if bodies
                                   else "no_response_bodies"}
        return self.metadata


def source_archive_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Derived compatibility view; never add provenance to a historical seal."""
    return snapshot.get("source_archive") or {"schema": None, "status": "legacy_normalized_only",
        "index": None, "original_availability": "original_unavailable",
        "note": "No source archive was sealed; normalized outputs are not original response bytes."}
