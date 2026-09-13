"""Byte-stable, bounded-memory framing helpers for local molecule sources."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from molcascade.config.canonical import canonical_json_bytes, canonical_sha256

SOURCE_RECORD_ID_SCHEME = "molcascade.source-record.sha256"
SOURCE_RECORD_ID_SCHEME_VERSION = 1
DELIMITED_FRAMING_VERSION = "molcascade.delimited-record/v1"
SDF_FRAMING_VERSION = "molcascade.sdf-record/v1"
_READ_CHUNK_SIZE = 64 * 1024


class SourceSnapshotError(ValueError):
    """A local source could not be snapshotted without ambiguity."""


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Immutable temporary copy and digest of one explicitly selected file."""

    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class BoundedSourceRead:
    """Stable digest plus optional bytes for one regular file.

    ``data`` is omitted after ``max_bytes`` is exceeded, while hashing and
    mutation detection continue to the end of the file.
    """

    sha256: str
    size_bytes: int
    data: bytes | None


@dataclass(frozen=True, slots=True)
class FramedRecord:
    """One exact byte-framed source occurrence.

    ``raw_bytes`` includes the record delimiter/newline.  It is ``None`` only
    when the configured record-size limit was exceeded.  ``payload_bytes``
    excludes an SDF ``$$$$`` delimiter and otherwise equals ``raw_bytes``.
    """

    index: int
    byte_start: int
    byte_length: int
    record_sha256: str
    raw_bytes: bytes | None
    payload_bytes: bytes | None
    issues: tuple[str, ...] = ()


def _same_file_state(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


def snapshot_regular_file(source: str | Path, destination: str | Path) -> SourceSnapshot:
    """Copy and hash a regular non-symlink file, detecting concurrent mutation."""

    input_path = Path(source)
    output_path = Path(destination)
    if input_path.is_symlink() or not input_path.is_file():
        raise SourceSnapshotError(f"source is not a regular non-symlink file: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    created_output = False
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(input_path, flags)
        with os.fdopen(descriptor, "rb") as reader:
            before = os.fstat(reader.fileno())
            path_before = input_path.stat()
            if (
                not stat.S_ISREG(before.st_mode)
                or input_path.is_symlink()
                or not _same_file_state(before, path_before)
            ):
                raise SourceSnapshotError(
                    f"source is not a stable regular non-symlink file: {input_path}"
                )
            with output_path.open("xb") as writer:
                created_output = True
                while chunk := reader.read(1024 * 1024):
                    writer.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            after = os.fstat(reader.fileno())
            path_after = input_path.stat()
            if (
                input_path.is_symlink()
                or not _same_file_state(before, after)
                or not _same_file_state(after, path_after)
                or copied != before.st_size
            ):
                raise SourceSnapshotError(
                    f"source changed while being snapshotted: {input_path}"
                )
    except BaseException:
        if created_output:
            output_path.unlink(missing_ok=True)
        raise
    return SourceSnapshot(output_path, digest.hexdigest(), copied)


def read_regular_file_bounded(
    source: str | Path,
    *,
    max_bytes: int,
) -> BoundedSourceRead:
    """Hash a stable regular file and retain content only within ``max_bytes``."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    input_path = Path(source)
    if input_path.is_symlink() or not input_path.is_file():
        raise SourceSnapshotError(f"source is not a regular non-symlink file: {input_path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(input_path, flags)
    digest = hashlib.sha256()
    size = 0
    buffer = bytearray()
    oversized = False
    with os.fdopen(descriptor, "rb") as reader:
        before = os.fstat(reader.fileno())
        path_before = input_path.stat()
        if (
            not stat.S_ISREG(before.st_mode)
            or input_path.is_symlink()
            or not _same_file_state(before, path_before)
        ):
            raise SourceSnapshotError(
                f"source is not a stable regular non-symlink file: {input_path}"
            )
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            if not oversized and size <= max_bytes:
                buffer.extend(chunk)
            else:
                oversized = True
                buffer.clear()
        after = os.fstat(reader.fileno())
        path_after = input_path.stat()
        if (
            input_path.is_symlink()
            or not _same_file_state(before, after)
            or not _same_file_state(after, path_after)
            or size != before.st_size
        ):
            raise SourceSnapshotError(f"source changed while being read: {input_path}")
    return BoundedSourceRead(
        sha256=digest.hexdigest(),
        size_bytes=size,
        data=None if oversized else bytes(buffer),
    )


def extraction_config_sha256(config: object) -> str:
    """Hash a JSON-compatible extraction policy independently of its file path."""

    return canonical_sha256(config)


def source_record_id(
    *,
    source_blob_sha256: str,
    source_kind: str,
    framing_version: str,
    record_index: int,
    byte_start: int,
    byte_length: int,
    record_sha256: str,
) -> str:
    """Identify a physical/logical source-record occurrence, not its extraction.

    Paths, mtimes, buffering settings, and extracted columns are intentionally
    absent.  The exact source blob, framing version, ordinal/span, and record
    digest bind the occurrence while allowing an unchanged file to be moved.
    """

    payload = {
        "scheme": SOURCE_RECORD_ID_SCHEME,
        "scheme_version": SOURCE_RECORD_ID_SCHEME_VERSION,
        "source_blob_sha256": source_blob_sha256,
        "source_kind": source_kind,
        "framing_version": framing_version,
        "record_index": record_index,
        "byte_start": byte_start,
        "byte_length": byte_length,
        "record_sha256": record_sha256,
    }
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"source-record:sha256:{digest}"


def record_provenance(
    frame: FramedRecord,
    *,
    source_blob_sha256: str,
    source_kind: str,
    framing_version: str,
    extraction_config_hash: str,
) -> dict[str, object]:
    """Return reconstructable lineage facts suitable for metadata/decisions."""

    return {
        "source_record_id_scheme": SOURCE_RECORD_ID_SCHEME,
        "source_record_id_scheme_version": SOURCE_RECORD_ID_SCHEME_VERSION,
        "source_blob_sha256": source_blob_sha256,
        "source_kind": source_kind,
        "framing_version": framing_version,
        "record_index": frame.index,
        "byte_start": frame.byte_start,
        "byte_length": frame.byte_length,
        "record_sha256": frame.record_sha256,
        "extraction_config_sha256": extraction_config_hash,
    }


def _finish_delimited_frame(
    *,
    index: int,
    start: int,
    length: int,
    digest: Any,
    buffer: bytearray,
    oversized: bool,
    issue: str | None,
) -> FramedRecord:
    issues = tuple(value for value in ("record_too_large" if oversized else None, issue) if value)
    raw = None if oversized else bytes(buffer)
    return FramedRecord(
        index=index,
        byte_start=start,
        byte_length=length,
        record_sha256=digest.hexdigest(),
        raw_bytes=raw,
        payload_bytes=raw,
        issues=issues,
    )


def _iter_physical_line_segments(stream: BinaryIO) -> Iterator[tuple[bytes, bool]]:
    """Yield bounded byte segments, marking LF, CRLF, and bare-CR line endings."""

    pending_cr = False
    while chunk := stream.read(_READ_CHUNK_SIZE):
        cursor = 0
        if pending_cr:
            if chunk.startswith(b"\n"):
                yield b"\r\n", True
                cursor = 1
            else:
                yield b"\r", True
            pending_cr = False
        start = cursor
        while cursor < len(chunk):
            value = chunk[cursor]
            if value == 13:
                if start < cursor:
                    yield chunk[start:cursor], False
                if cursor + 1 < len(chunk):
                    if chunk[cursor + 1] == 10:
                        yield chunk[cursor : cursor + 2], True
                        cursor += 2
                    else:
                        yield chunk[cursor : cursor + 1], True
                        cursor += 1
                else:
                    pending_cr = True
                    cursor += 1
                start = cursor
            elif value == 10:
                if start < cursor:
                    yield chunk[start:cursor], False
                yield chunk[cursor : cursor + 1], True
                cursor += 1
                start = cursor
            else:
                cursor += 1
        if start < len(chunk):
            yield chunk[start:], False
    if pending_cr:
        yield b"\r", True


def iter_delimited_frames(
    source: str | Path,
    *,
    delimiter: str,
    quotechar: str = '"',
    escapechar: str | None = None,
    doublequote: bool = True,
    skipinitialspace: bool = False,
    max_record_bytes: int,
) -> Iterator[FramedRecord]:
    """Frame UTF-8 CSV records while retaining exact byte spans and newlines."""

    delimiter_byte = ord(delimiter)
    quote_byte = ord(quotechar)
    escape_byte = ord(escapechar) if escapechar is not None else None
    with Path(source).open("rb") as stream:
        index = 0
        start = 0
        length = 0
        digest = hashlib.sha256()
        buffer = bytearray()
        oversized = False
        in_quotes = False
        quote_pending = False
        escape_pending = False
        field_start = True

        for chunk, physical_line_end in _iter_physical_line_segments(stream):
            digest.update(chunk)
            length += len(chunk)
            if not oversized and length <= max_record_bytes:
                buffer.extend(chunk)
            else:
                oversized = True
                buffer.clear()

            cursor = 0
            terminating_newline = False
            while cursor < len(chunk):
                value = chunk[cursor]
                reprocess = True
                while reprocess:
                    reprocess = False
                    if escape_pending:
                        escape_pending = False
                    elif in_quotes:
                        if quote_pending:
                            if doublequote and value == quote_byte:
                                quote_pending = False
                            else:
                                quote_pending = False
                                in_quotes = False
                                reprocess = True
                        elif escape_byte is not None and value == escape_byte:
                            escape_pending = True
                        elif value == quote_byte:
                            quote_pending = True
                    elif field_start and value == quote_byte:
                        in_quotes = True
                        field_start = False
                    elif escape_byte is not None and value == escape_byte:
                        escape_pending = True
                        field_start = False
                    elif field_start and skipinitialspace and value == 32:
                        pass
                    elif value == delimiter_byte:
                        field_start = True
                    elif value in {10, 13}:
                        terminating_newline = True
                    else:
                        field_start = False
                cursor += 1

            if physical_line_end and terminating_newline and not in_quotes:
                yield _finish_delimited_frame(
                    index=index,
                    start=start,
                    length=length,
                    digest=digest,
                    buffer=buffer,
                    oversized=oversized,
                    issue=None,
                )
                index += 1
                start += length
                length = 0
                digest = hashlib.sha256()
                buffer = bytearray()
                oversized = False
                quote_pending = False
                escape_pending = False
                field_start = True

        if quote_pending:
            quote_pending = False
            in_quotes = False
        if length:
            yield _finish_delimited_frame(
                index=index,
                start=start,
                length=length,
                digest=digest,
                buffer=buffer,
                oversized=oversized,
                issue="unclosed_quote" if in_quotes else None,
            )


def _append_frame_bytes(
    data: bytes,
    *,
    digest: Any,
    buffer: bytearray,
    length: int,
    max_record_bytes: int,
    oversized: bool,
) -> tuple[int, bool]:
    digest.update(data)
    length += len(data)
    if not oversized and length <= max_record_bytes:
        buffer.extend(data)
    else:
        oversized = True
        buffer.clear()
    return length, oversized


def iter_sdf_frames(
    source: str | Path,
    *,
    max_record_bytes: int,
) -> Iterator[FramedRecord]:
    """Frame SD records using CTfile's column-one ``$$$$`` terminator rule."""

    with Path(source).open("rb") as stream:
        index = 0
        start = 0
        length = 0
        payload_length = 0
        digest = hashlib.sha256()
        buffer = bytearray()
        oversized = False
        at_physical_line_start = True

        while line := stream.readline(_READ_CHUNK_SIZE):
            delimiter_line = at_physical_line_start and line.startswith(b"$$$$")
            if delimiter_line:
                delimiter_trailing_data = bool(line[4:].strip())
                while True:
                    length, oversized = _append_frame_bytes(
                        line,
                        digest=digest,
                        buffer=buffer,
                        length=length,
                        max_record_bytes=max_record_bytes,
                        oversized=oversized,
                    )
                    if line.endswith(b"\n"):
                        break
                    line = stream.readline(_READ_CHUNK_SIZE)
                    if not line:
                        break
                    delimiter_trailing_data = delimiter_trailing_data or bool(line.strip())
                issues = []
                if oversized:
                    issues.append("record_too_large")
                if delimiter_trailing_data:
                    issues.append("delimiter_trailing_data")
                raw = None if oversized else bytes(buffer)
                payload = None if oversized else raw[:payload_length]
                yield FramedRecord(
                    index=index,
                    byte_start=start,
                    byte_length=length,
                    record_sha256=digest.hexdigest(),
                    raw_bytes=raw,
                    payload_bytes=payload,
                    issues=tuple(issues),
                )
                index += 1
                start += length
                length = 0
                payload_length = 0
                digest = hashlib.sha256()
                buffer = bytearray()
                oversized = False
                at_physical_line_start = True
                continue

            length, oversized = _append_frame_bytes(
                line,
                digest=digest,
                buffer=buffer,
                length=length,
                max_record_bytes=max_record_bytes,
                oversized=oversized,
            )
            payload_length = length
            at_physical_line_start = line.endswith(b"\n")

        if length:
            issues = ["unterminated_record"]
            if oversized:
                issues.insert(0, "record_too_large")
            raw = None if oversized else bytes(buffer)
            yield FramedRecord(
                index=index,
                byte_start=start,
                byte_length=length,
                record_sha256=digest.hexdigest(),
                raw_bytes=raw,
                payload_bytes=raw,
                issues=tuple(issues),
            )


__all__ = [
    "DELIMITED_FRAMING_VERSION",
    "SDF_FRAMING_VERSION",
    "SOURCE_RECORD_ID_SCHEME",
    "SOURCE_RECORD_ID_SCHEME_VERSION",
    "BoundedSourceRead",
    "FramedRecord",
    "SourceSnapshot",
    "SourceSnapshotError",
    "extraction_config_sha256",
    "iter_delimited_frames",
    "iter_sdf_frames",
    "read_regular_file_bounded",
    "record_provenance",
    "snapshot_regular_file",
    "source_record_id",
]
