"""Source-scoped streaming downloads with atomic publication and a provenance manifest."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

from ._version import __version__
from .errors import MolQuarryError
from .http import http_error, retry_after_seconds
from .models import DownloadResult, utcnow


def validate_url(url: str, hosts: frozenset[str]):
    parsed = urlparse(url)
    try:
        valid_port = parsed.port in {None, 443}
    except ValueError:
        valid_port = False
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or not valid_port
        or parsed.fragment
    ):
        raise MolQuarryError(
            "invalid_download_url", "Download URL is outside this source's HTTPS hosts"
        )


def public_url(url: str) -> str:
    """Do not persist temporary object-store credentials in download manifests."""
    parsed = urlparse(url)
    secret_keys = {
        "x-amz-signature",
        "x-amz-security-token",
        "x-amz-credential",
        "awsaccesskeyid",
        "signature",
        "token",
        "api_key",
        "apikey",
    }
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key.casefold() in secret_keys for key, _ in pairs):
        return url
    return urlunparse(
        parsed._replace(
            query=urlencode(
                [
                    (key, "REDACTED" if key.casefold() in secret_keys else value)
                    for key, value in pairs
                ]
            )
        )
    )


def public_metadata(value):
    """Redact signed URLs throughout nested provenance/plan metadata, not scientific fields."""
    if isinstance(value, dict):
        return {key: public_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_metadata(item) for item in value]
    if isinstance(value, str) and value.startswith("https://"):
        return public_url(value)
    return value


def validate_signature(path: Path, format_: str):
    """Catch error bodies masquerading as data; this is not a scientific file parser."""
    with path.open("rb") as file:
        prefix = file.read(16384)
    clean = prefix.lstrip()
    if format_.endswith(".gz") or format_ in {"gz", "tgz"}:
        valid = prefix.startswith(b"\x1f\x8b\x08")
    elif format_ in {"zip", "xlsx"}:
        valid = prefix.startswith((b"PK\x03\x04", b"PK\x05\x06"))
    elif format_.endswith("bz2"):
        valid = prefix.startswith(b"BZh")
    elif format_ == "xz":
        valid = prefix.startswith(b"\xfd7zXZ\x00")
    elif format_ == "parquet":
        valid = prefix.startswith(b"PAR1")
        with path.open("rb") as f:
            if path.stat().st_size < 8:
                valid = False
            else:
                f.seek(-4, 2)
                valid = valid and f.read() == b"PAR1"
    elif format_ in {"h5", "hdf5"}:
        valid = prefix.startswith(b"\x89HDF\r\n\x1a\n")
    elif format_ == "xls":
        valid = prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    elif format_ in {"sdf", "mol"}:
        valid = b"V2000" in prefix or b"V3000" in prefix
    elif format_ in {"cif", "mmcif"}:
        valid = clean.startswith(b"data_")
    elif format_ in {"fasta", "fa"}:
        valid = clean.startswith(b">")
    elif format_ == "pdb":
        valid = clean.startswith((b"HEADER", b"TITLE", b"ATOM", b"HETATM", b"MODEL"))
    elif format_ == "json":
        valid = clean.startswith((b"[", b"{"))
    elif format_ in {"csv", "tsv", "smi", "smiles", "txt", "sql", "obo", "ttl", "xml", "owl"}:
        # Validate only the transport/container, not the scientific schema.
        valid = (
            bool(clean)
            and b"\x00" not in prefix
            and not clean.lower().startswith(
                (b"<!doctype html", b"<html", b'{"error"', b'{"message"')
            )
        )
    else:
        raise MolQuarryError("unsupported_format", f"No file signature check for {format_}")
    if not valid:
        raise MolQuarryError("unexpected_format", f"Downloaded body does not resemble {format_}")


def download(http, provider, plan, output_dir: Path, max_bytes: int) -> DownloadResult:
    if max_bytes <= 0:
        raise MolQuarryError("invalid_parameters", "max_bytes must be positive")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", plan.filename):
        raise MolQuarryError("invalid_filename", "Artifact filename must be a plain, safe basename")
    validate_url(plan.url, provider.download_hosts)
    if plan.estimated_bytes and plan.estimated_bytes > max_bytes:
        raise MolQuarryError(
            "download_too_large",
            "Catalog size estimate exceeds max_bytes",
            details={"estimated_bytes": plan.estimated_bytes, "max_bytes": max_bytes},
        )
    if plan.expected_sha256 and not re.fullmatch(r"[a-fA-F0-9]{64}", plan.expected_sha256):
        raise MolQuarryError("invalid_checksum", "Expected SHA256 must be 64 hex characters")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.resolve()
    destination = output_dir / plan.filename
    manifest_path = output_dir / (plan.filename + ".manifest.json")
    if (
        destination.exists()
        or destination.is_symlink()
        or manifest_path.exists()
        or manifest_path.is_symlink()
    ):
        raise MolQuarryError(
            "file_exists", "Artifact or manifest already exists; choose another output directory"
        )
    try:
        with tempfile.TemporaryDirectory(prefix=".molquarry-", dir=output_dir) as staging:
            temporary = Path(staging) / "artifact"
            url = plan.url
            redirects = 0
            attempts = 0
            while True:
                validate_url(url, provider.download_hosts)
                http.pace(provider.spec)
                with http.client.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": "*/*",
                        "Accept-Encoding": "identity",
                        **provider.download_headers(url),
                    },
                ) as response:
                    if response.is_redirect:
                        redirects += 1
                        if redirects > 5 or not response.headers.get("location"):
                            raise MolQuarryError(
                                "invalid_redirect", "Too many or invalid download redirects"
                            )
                        url = urljoin(url, response.headers["location"])
                        if url in provider.spec.download_login_urls:
                            raise MolQuarryError(
                                "authentication_required",
                                "Official file link redirects to a login page; anonymous "
                                "file discovery does not imply anonymous download access",
                                source=provider.id,
                                details={"login_url": url},
                            )
                        continue
                    if response.status_code == 429 or response.status_code >= 500:
                        attempts += 1
                        delay = retry_after_seconds(response.headers.get("retry-after"))
                        delay = delay if delay is not None else 2 ** (attempts - 1)
                        if attempts < http.max_attempts and delay <= http.max_retry_wait:
                            http.sleep(delay)
                            continue
                    if not response.is_success:
                        raise http_error(response, provider.id)
                    if response.status_code != 200:
                        raise MolQuarryError(
                            "invalid_response", "Expected a complete HTTP 200 artifact"
                        )
                    if "text/html" in response.headers.get("content-type", "").lower():
                        raise MolQuarryError(
                            "invalid_response", "Source returned HTML instead of an artifact"
                        )
                    length_header = response.headers.get("content-length")
                    length = (
                        int(length_header) if length_header and length_header.isdigit() else None
                    )
                    if length is not None and length > max_bytes:
                        raise MolQuarryError(
                            "download_too_large", "Content-Length exceeds max_bytes"
                        )
                    if response.headers.get("content-encoding", "identity") != "identity":
                        raise MolQuarryError(
                            "invalid_response",
                            "Server ignored identity transfer encoding; "
                            "cannot preserve artifact bytes and upstream checksum",
                        )
                    digest = hashlib.sha256()
                    size = 0
                    with temporary.open("wb") as f:
                        for chunk in response.iter_raw(chunk_size=64 * 1024):
                            size += len(chunk)
                            if size > max_bytes:
                                raise MolQuarryError(
                                    "download_too_large", "Stream exceeds max_bytes"
                                )
                            digest.update(chunk)
                            f.write(chunk)
                        f.flush()
                        os.fsync(f.fileno())
                    if size == 0 or (length is not None and size != length):
                        raise MolQuarryError(
                            "incomplete_download", "Empty or truncated artifact", retryable=True
                        )
                    sha256 = digest.hexdigest()
                    if plan.expected_sha256 and sha256 != plan.expected_sha256.lower():
                        raise MolQuarryError(
                            "checksum_mismatch",
                            "Downloaded SHA256 differs from source checksum",
                            details={"expected": plan.expected_sha256, "actual": sha256},
                        )
                    validate_signature(temporary, plan.format)
                    headers = {
                        k: response.headers[k]
                        for k in ("etag", "last-modified", "content-type", "x-uniprot-release")
                        if k in response.headers
                    }
                    break
            manifest = {
                "schema_version": "1",
                "molquarry_version": __version__,
                "source": plan.source,
                "source_version": plan.source_version or headers.get("x-uniprot-release"),
                "license_url": plan.license_url,
                "retrieved_at": utcnow(),
                "source_url": public_url(plan.url),
                "final_url": public_url(url),
                "files": [{"name": plan.filename, "bytes": size, "sha256": sha256}],
                "upstream_checksum_verified": bool(plan.expected_sha256),
                "http_headers": headers,
                "plan": public_metadata(plan.model_dump()),
                "processing": {"transformation": "none; original bytes preserved"},
            }
            staged_manifest = Path(staging) / "manifest.json"
            staged_manifest.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            # Hard links atomically publish files without overwriting a concurrent writer.
            os.link(temporary, destination)
            try:
                os.link(staged_manifest, manifest_path)
            except OSError:
                destination.unlink(missing_ok=True)
                raise
            return DownloadResult(
                path=str(destination),
                manifest_path=str(manifest_path),
                sha256=sha256,
                bytes=size,
                manifest=manifest,
            )
    except FileExistsError as exc:
        raise MolQuarryError(
            "file_exists", "A concurrent download already created the output"
        ) from exc
    except httpx.TransportError as exc:
        raise MolQuarryError(
            "download_interrupted",
            f"Download interrupted ({type(exc).__name__})",
            source=provider.id,
            retryable=True,
        ) from exc
    except OSError as exc:
        raise MolQuarryError("filesystem_error", str(exc)) from exc
