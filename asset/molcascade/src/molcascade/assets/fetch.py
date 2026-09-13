"""The one place in MolCascade that is allowed to reach the network.

Nothing imports this module during a screening run.  It is reached only from
``molcascade assets fetch``, which a person types, having read what is about to
be downloaded and from where.  Keeping downloading in a separate command, in a
separate module, with no caller inside the pipeline, is what makes the promise
"a run never touches the network" checkable rather than aspirational.

The download itself is paranoid in the ordinary ways: a declared size is a hard
ceiling rather than a hint, bytes land in a temporary file and are verified
before anything is moved into place, and a file whose digest disagrees is
deleted rather than kept for inspection.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from pathlib import Path

from molcascade import __version__
from molcascade.assets.models import AssetFile, AssetSpec, PayloadTrust
from molcascade.assets.store import asset_directory, asset_status, write_stamp
from molcascade.errors import AssetError

_CHUNK = 1024 * 1024
_TIMEOUT_SECONDS = 120.0
_USER_AGENT = f"molcascade/{__version__} (+https://github.com/)"
# Slack above the declared size before the transfer is abandoned.  A correct
# file is exactly the declared length; this exists only so the failure message
# can say "too large" instead of "digest mismatch" when a host serves an error
# page in place of the payload.
_SIZE_SLACK_BYTES = 4096

ProgressCallback = Callable[[str, int, int], None]


def _download(entry: AssetFile, destination: Path, progress: ProgressCallback | None) -> str:
    """Stream one file to ``destination`` and return its SHA-256."""

    request = urllib.request.Request(
        entry.url,
        headers={
            "User-Agent": _USER_AGENT,
            # GitHub's Git-data API returns base64 JSON by default; this asks
            # for the bytes themselves and is ignored by ordinary hosts.
            "Accept": "application/vnd.github.raw, */*",
        },
    )
    hasher = hashlib.sha256()
    written = 0
    ceiling = entry.size_bytes + _SIZE_SLACK_BYTES
    try:
        with (
            urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response,
            open(destination, "wb") as handle,
        ):
            while chunk := response.read(_CHUNK):
                written += len(chunk)
                if written > ceiling:
                    raise AssetError(
                        f"{entry.name} is larger than its declared "
                        f"{entry.size_bytes} bytes; transfer abandoned",
                        code="ASSET_DOWNLOAD_TOO_LARGE",
                    )
                hasher.update(chunk)
                handle.write(chunk)
                if progress is not None:
                    progress(entry.name, written, entry.size_bytes)
    except urllib.error.HTTPError as error:
        raise AssetError(
            f"{entry.name}: server returned HTTP {error.code}",
            code="ASSET_DOWNLOAD_FAILED",
            retryable=True,
            context={"url": entry.url, "status": error.code},
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AssetError(
            f"{entry.name}: download failed: {error}",
            code="ASSET_DOWNLOAD_FAILED",
            retryable=True,
            context={"url": entry.url},
        ) from error
    if written != entry.size_bytes:
        raise AssetError(
            f"{entry.name}: expected {entry.size_bytes} bytes, received {written}",
            code="ASSET_DOWNLOAD_SIZE_MISMATCH",
            retryable=True,
        )
    return hasher.hexdigest()


def _git_blob_sha1(path: Path) -> str:
    """Recompute the Git object id, which the host published independently.

    The digest in our catalogue and the object id in the remote's tree were
    produced by different parties with different algorithms.  Checking both
    means a single compromised or corrupted source cannot pass silently.
    """

    size = path.stat().st_size
    hasher = hashlib.sha1(b"blob %d\0" % size)
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


def _bibtex(spec: AssetSpec) -> str:
    lines: list[str] = []
    for index, citation in enumerate(spec.citations, start=1):
        key = f"{spec.id.replace('-', '')}{citation.year}" + ("" if index == 1 else str(index))
        fields = [
            f"  title   = {{{citation.title}}}",
            f"  author  = {{{citation.authors}}}",
            f"  journal = {{{citation.venue}}}",
            f"  year    = {{{citation.year}}}",
        ]
        if citation.doi:
            fields.append(f"  doi     = {{{citation.doi}}}")
        lines.append("@article{" + key + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(lines)


def render_citation_document(spec: AssetSpec) -> str:
    """Compose the ``CITATION.md`` that travels with the downloaded bytes.

    Nothing here depends on where the asset landed.  An earlier version wrote
    the absolute install directory into the document, which made every
    machine's copy differ from every other's and put ``/home/<whoever>`` into a
    file that is meant to be the shareable provenance record.  The document
    already sits inside the directory it describes, so the path was telling the
    reader where they already were.
    """

    lines = [
        f"# {spec.display_name}",
        "",
        spec.summary,
        "",
        "## Provenance",
        "",
        f"- **Asset id**: `{spec.id}`",
        f"- **Kind**: {spec.kind.value}",
        f"- **Upstream version**: {spec.version}",
        f"- **Homepage**: {spec.homepage}",
        f"- **Licence**: {spec.license_spdx}",
        f"- **Payload trust**: {spec.trust.value}",
        f"- **Directory**: `{spec.id}/`, under the asset root",
        "",
    ]
    if spec.trust is PayloadTrust.EXECUTABLE:
        lines += [
            "> **This payload is executable.** Loading it runs code chosen by whoever",
            "> produced the file. The digest below proves these are the bytes that were",
            "> published; it does not make deserialising them safe. MolCascade requires",
            "> a separate, explicit trust acknowledgement before any backend loads it.",
            "",
        ]
    if spec.citations:
        lines += ["## How to cite", ""]
        for citation in spec.citations:
            lines.append(f"- {citation.reference}")
            if citation.url:
                lines.append(f"  - {citation.url}")
            if citation.note:
                lines.append(f"  - {citation.note}")
        lines += ["", "```bibtex", _bibtex(spec), "```", ""]
    if spec.used_by:
        lines += ["## Used by", ""]
        lines += [f"- `{plugin}`" for plugin in spec.used_by]
        lines.append("")
    lines += [
        "## Files",
        "",
        "| File | Size | SHA-256 |",
        "| --- | ---: | --- |",
    ]
    for entry in spec.files:
        lines.append(f"| `{entry.name}` | {entry.size_bytes:,} | `{entry.sha256}` |")
    lines.append("")
    lines += ["## Referencing these files from a cascade", ""]
    lines.append(
        "Use an `asset:` reference so the configuration means the same thing on "
        "every machine:"
    )
    lines.append("")
    lines.append("```")
    for entry in spec.files:
        lines.append(f"asset:{spec.id}/{entry.name}")
    lines.append("```")
    lines.append("")
    if spec.notes:
        lines += ["## Notes", "", spec.notes, ""]
    lines += [
        "---",
        "",
        "This file is generated by `molcascade assets fetch`. MolCascade downloads "
        "nothing during a screening run; if a file listed above is absent or its "
        "digest disagrees, the backend that needs it stops rather than proceeding.",
        "",
    ]
    return "\n".join(lines)


def fetch_asset(
    spec: AssetSpec,
    *,
    root: Path | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> tuple[str, ...]:
    """Download whatever is missing or wrong, and return what changed.

    Files already present and verified are left alone, so re-running the
    command after an interrupted transfer costs only the remainder.
    """

    directory = asset_directory(spec, root=root)
    directory.mkdir(parents=True, exist_ok=True)
    before = asset_status(spec, root=root, deep=True)
    verified = {status.name for status in before.files if status.verified}
    installed: list[str] = []
    for entry in spec.files:
        if entry.name in verified and not force:
            continue
        target = directory / entry.name
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".part"
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            digest = _download(entry, temporary, progress)
            if digest != entry.sha256:
                raise AssetError(
                    f"{entry.name}: digest mismatch; expected {entry.sha256}, "
                    f"received {digest}",
                    code="ASSET_DIGEST_MISMATCH",
                    context={"url": entry.url},
                )
            if entry.git_blob_sha1 is not None:
                observed = _git_blob_sha1(temporary)
                if observed != entry.git_blob_sha1:
                    raise AssetError(
                        f"{entry.name}: Git object id mismatch; expected "
                        f"{entry.git_blob_sha1}, computed {observed}",
                        code="ASSET_GIT_SHA_MISMATCH",
                        context={"url": entry.url},
                    )
            os.replace(temporary, target)
            installed.append(entry.name)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    after = asset_status(spec, root=root, deep=True)
    if not after.ready:
        broken = [status.name for status in after.files if not status.verified]
        raise AssetError(
            f"asset {spec.id!r} is still not usable after fetching: {', '.join(broken)}",
            code="ASSET_NOT_READY",
        )
    write_stamp(
        directory,
        {
            entry.name: {
                "sha256": entry.sha256,
                "size_bytes": entry.size_bytes,
                "mtime_ns": (directory / entry.name).stat().st_mtime_ns,
            }
            for entry in spec.files
        },
    )
    (directory / "CITATION.md").write_text(
        render_citation_document(spec), encoding="utf-8"
    )
    return tuple(installed)


def render_manifest(specs: Iterable[AssetSpec]) -> str:
    """Compose the top-level index of everything the vendor directory holds.

    Machine-independent for the same reason as the per-asset citation: this
    file is checked in, and a manifest that names one contributor's home
    directory as *the* asset root is both wrong for every reader and a diff on
    every machine that regenerates it.
    """

    entries = list(specs)
    lines = [
        "# Vendored assets",
        "",
        "Model weights, rule tables and reference data that MolCascade uses but does "
        "not author. Each subdirectory carries a `CITATION.md` naming the paper "
        "behind it, the licence it is distributed under, and the SHA-256 of every "
        "file.",
        "",
        "The asset root is the directory this file sits in -- `vendor/` beside the "
        "source tree by default. Override it with the `MOLCASCADE_ASSET_ROOT` "
        "environment variable, and run `molcascade assets status` to print the one "
        "in effect.",
        "",
        "| Asset | Kind | Licence | Size | Cite |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for spec in entries:
        citation = spec.citations[0].doi if spec.citations and spec.citations[0].doi else "—"
        megabytes = spec.total_bytes / (1024 * 1024)
        lines.append(
            f"| [`{spec.id}`]({spec.id}/CITATION.md) | {spec.kind.value} | "
            f"{spec.license_spdx} | {megabytes:,.1f} MiB | {citation} |"
        )
    lines += [
        "",
        "## Getting them",
        "",
        "```bash",
        "molcascade assets status          # what is here and whether it verifies",
        "molcascade assets fetch --all     # download everything that is missing",
        "molcascade assets fetch scscore   # or just one",
        "```",
        "",
        "## Using them",
        "",
        "Refer to a file by `asset:<asset-id>/<path>` in a cascade configuration. That "
        "reference resolves to the same content on every machine, and resolution "
        "verifies the digest before the path is handed to a backend.",
        "",
        "## What a screening run will not do",
        "",
        "Download. Ever. A run that needs an absent asset stops and prints the "
        "`molcascade assets fetch` command that would provide it. Results therefore "
        "depend on files whose digests are recorded, not on what a remote host served "
        "that afternoon.",
        "",
        "## Software, not just bytes",
        "",
        "This file covers assets that ship as files. The backends that read them are "
        "pip-installed and have no directory here to hold a citation, so their "
        "references live in `docs/citations.md` alongside these -- one place to look "
        "when writing up a screen. Regenerate it with `molcascade cite`.",
        "",
    ]
    return "\n".join(lines)


__all__ = ["fetch_asset", "render_citation_document", "render_manifest"]
