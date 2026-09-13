"""Atomic JSONL audit logging for local runs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue, ValidationError

from molcascade.artifacts import ArtifactIntegrityError, canonical_json_bytes
from molcascade.errors import ExecutionError
from molcascade.io.atomic import atomic_write_bytes
from molcascade.runtime.models import AuditEvent

_AUDIT_SIZE_LIMIT = 128 * 1024 * 1024


class AuditLog:
    """Append records by atomically replacing one complete JSONL file.

    Local v1 optimises for crash consistency and inspectability.  Event logs
    are intentionally bounded control-plane files, so rewriting them avoids a
    half-written final line after power loss.
    """

    def __init__(self, path: str | Path, *, run_id: str, revision_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.revision_id = revision_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> tuple[AuditEvent, ...]:
        if not self.path.exists() and not self.path.is_symlink():
            return ()
        if self.path.is_symlink() or not self.path.is_file():
            raise ArtifactIntegrityError(
                f"audit log is not a regular file: {self.path.name}",
                code="RUNTIME_AUDIT_INVALID",
                context={"run_id": self.run_id},
            )
        try:
            if self.path.stat().st_size > _AUDIT_SIZE_LIMIT:
                raise ValueError("audit log is unreasonably large")
            content = self.path.read_bytes()
            if content and not content.endswith(b"\n"):
                raise ValueError("audit log has an incomplete final line")
            events: list[AuditEvent] = []
            for line_number, line in enumerate(content.splitlines(), start=1):
                if not line:
                    raise ValueError(f"blank audit line at {line_number}")
                event = AuditEvent.model_validate_json(line)
                if line != canonical_json_bytes(event):
                    raise ValueError(f"non-canonical audit line at {line_number}")
                events.append(event)
        except (OSError, ValueError, ValidationError) as error:
            raise ArtifactIntegrityError(
                f"audit log is invalid for run {self.run_id}: {error}",
                code="RUNTIME_AUDIT_INVALID",
                context={"run_id": self.run_id},
            ) from error

        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence:
                raise ArtifactIntegrityError(
                    f"audit sequence is discontinuous for run {self.run_id}",
                    code="RUNTIME_AUDIT_INVALID",
                    context={
                        "run_id": self.run_id,
                        "expected_sequence": expected_sequence,
                        "actual_sequence": event.sequence,
                    },
                )
            if event.run_id != self.run_id or event.revision_id != self.revision_id:
                raise ArtifactIntegrityError(
                    f"audit identity does not match run {self.run_id}",
                    code="RUNTIME_AUDIT_INVALID",
                    context={"run_id": self.run_id, "sequence": event.sequence},
                )
        return tuple(events)

    def append(
        self,
        event_type: str,
        *,
        timestamp: datetime,
        stage_id: str | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> AuditEvent:
        events = self.read()
        event = AuditEvent(
            sequence=len(events) + 1,
            timestamp=timestamp,
            event_type=event_type,
            run_id=self.run_id,
            revision_id=self.revision_id,
            stage_id=stage_id,
            details={} if details is None else dict(details),
        )
        content = b"".join(
            canonical_json_bytes(item) + b"\n" for item in (*events, event)
        )
        if len(content) > _AUDIT_SIZE_LIMIT:
            raise ExecutionError(
                f"audit log exceeds the local persistence limit for run {self.run_id}",
                code="RUNTIME_AUDIT_TOO_LARGE",
                context={
                    "run_id": self.run_id,
                    "size_bytes": len(content),
                    "limit_bytes": _AUDIT_SIZE_LIMIT,
                },
            )
        try:
            atomic_write_bytes(self.path, content, overwrite=True)
        except OSError as error:
            raise ExecutionError(
                f"cannot atomically persist audit event for run {self.run_id}: {error}",
                code="RUNTIME_AUDIT_WRITE_FAILED",
                retryable=True,
                context={
                    "run_id": self.run_id,
                    "event_type": event_type,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
            ) from error
        return event


__all__ = ["AuditLog"]
