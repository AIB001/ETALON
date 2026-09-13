"""A content-addressed, atomic local artifact store."""

from __future__ import annotations

import errno
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import JsonValue, ValidationError

from molcascade.artifacts.hashing import sha256_file_checksum
from molcascade.artifacts.models import (
    ArtifactDatasetRef,
    ArtifactFile,
    ArtifactManifest,
    ArtifactOutput,
    ArtifactProducer,
    ArtifactRef,
    artifact_digest,
    normalize_artifact_outputs,
    validate_relative_artifact_path,
)
from molcascade.errors import ArtifactIntegrityError, MolCascadeError
from molcascade.io.atomic import (
    DestinationExistsError,
    atomic_commit_directory,
    atomic_write_bytes,
)

_MANIFEST_NAME = "artifact.json"
_MANIFEST_SIZE_LIMIT = 16 * 1024 * 1024
_PUBLICATION_LOCK_TIMEOUT_SECONDS = 5.0
_MEDIA_TYPES = {
    ".arrow": "application/vnd.apache.arrow.file",
    ".csv": "text/csv",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".parquet": "application/vnd.apache.parquet",
    ".sdf": "chemical/x-mdl-sdfile",
    ".smi": "chemical/x-daylight-smiles",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


def _ensure_real_directory(
    directory: Path,
    *,
    owner_root: Path,
    create: bool,
) -> Path:
    """Return an owned directory without following a child symlink.

    ``owner_root`` is already resolved.  Walking every component catches an
    intermediate symlink such as ``root/artifacts -> /outside`` as well as a
    symlink at the final path.
    """

    try:
        relative = directory.relative_to(owner_root)
    except ValueError as error:
        raise ValueError(f"directory escapes its owner root: {directory}") from error
    current = owner_root
    if current.is_symlink() or not current.is_dir():
        raise ValueError(f"owner root is not a real directory: {owner_root}")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"store directory may not be a symlink: {current}")
        if create:
            current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise ValueError(f"store path is not a real directory: {current}")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(owner_root)
    except ValueError as error:
        raise ValueError(f"store directory escapes its root: {current}") from error
    return current


class ArtifactStoreError(MolCascadeError):
    """Base class for local artifact-store errors."""

    default_code = "ARTIFACT_STORE_ERROR"


class ArtifactNotFoundError(ArtifactStoreError):
    """Raised when no complete artifact exists for an ID."""

    default_code = "ARTIFACT_NOT_FOUND"


class ArtifactConflictError(ArtifactStoreError):
    """Raised rather than overwriting an existing or concurrently published artifact."""

    default_code = "ARTIFACT_CONFLICT"


class InvalidStagingError(ArtifactStoreError):
    """Raised when a staging directory violates the artifact boundary."""

    default_code = "ARTIFACT_STAGING_INVALID"


class LocalArtifactStore:
    """Store immutable artifacts below one local filesystem root.

    Layout::

        root/
          .staging/stage-*/
          .locks/<digest>.lock
          artifacts/sha256/<digest>/artifact.json

    Only the last location is discoverable.  Staging directories can therefore survive
    a process crash without becoming partially visible artifacts.
    """

    def __init__(self, root: str | Path) -> None:
        requested_root = Path(root).expanduser()
        if requested_root.is_symlink():
            raise ValueError("artifact-store root may not be a symlink")
        requested_root.mkdir(parents=True, exist_ok=True)
        self.root = requested_root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"artifact-store root is not a directory: {self.root}")
        self.staging_root = self.root / ".staging"
        self.lock_root = self.root / ".locks"
        self.artifacts_root = self.root / "artifacts" / "sha256"
        for directory in (self.staging_root, self.lock_root, self.artifacts_root):
            _ensure_real_directory(
                directory,
                owner_root=self.root,
                create=True,
            )

    def _owned_directory(
        self,
        directory: Path,
        *,
        error_type: type[MolCascadeError],
    ) -> Path:
        """Revalidate a store-owned directory before a filesystem operation."""

        try:
            return _ensure_real_directory(
                directory,
                owner_root=self.root,
                create=False,
            )
        except (OSError, ValueError) as error:
            raise error_type(f"artifact-store directory is invalid: {directory}") from error

    def begin_staging(self) -> Path:
        """Create an isolated, invisible staging directory on the store filesystem."""

        root = self._owned_directory(
            self.staging_root,
            error_type=InvalidStagingError,
        )
        return Path(tempfile.mkdtemp(prefix="stage-", dir=root))

    @contextmanager
    def staging_area(self) -> Iterator[Path]:
        """Yield a staging directory and clean it unless a commit moved it away."""

        staging = self.begin_staging()
        try:
            yield staging
        finally:
            if staging.exists():
                self.discard_staging(staging)

    def discard_staging(self, staging: str | Path) -> None:
        """Remove one store-owned staging directory."""

        stage = self._validate_staging_directory(staging)
        shutil.rmtree(stage)

    def commit(
        self,
        staging: str | Path,
        *,
        kind: str,
        producer: ArtifactProducer | dict[str, Any],
        inputs: Sequence[ArtifactDatasetRef] = (),
        outputs: Mapping[str, Any] | Sequence[ArtifactOutput] | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
        cache_key: str | None = None,
        file_paths: Iterable[str] | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactManifest:
        """Inventory, checksum, validate, and atomically publish staged output."""

        stage = self._validate_staging_directory(staging)
        paths = self._inventory(stage)
        normalized_outputs: tuple[ArtifactOutput, ...] | None = None
        if outputs is not None:
            try:
                normalized_outputs = normalize_artifact_outputs(outputs, file_paths=paths)
            except (TypeError, ValueError, ValidationError) as error:
                raise InvalidStagingError(f"invalid artifact output ports: {error}") from error
            self._validate_output_inventory(normalized_outputs, paths)
        if file_paths is not None:
            declared = [validate_relative_artifact_path(path) for path in file_paths]
            if len(declared) != len(set(declared)):
                raise InvalidStagingError("declared file_paths contains duplicates")
            if set(declared) != set(paths):
                missing = sorted(set(declared) - set(paths))
                unexpected = sorted(set(paths) - set(declared))
                raise InvalidStagingError(
                    f"declared files do not match staging; missing={missing}, "
                    f"unexpected={unexpected}"
                )

        files = tuple(self._describe_file(stage, relative_path) for relative_path in paths)
        manifest = ArtifactManifest.create(
            kind=kind,
            producer=producer,
            files=files,
            outputs=normalized_outputs,
            inputs=list(inputs),
            metadata={} if metadata is None else dict(metadata),
            cache_key=cache_key,
            created_at=created_at,
        )
        return self.commit_manifest(stage, manifest)

    def commit_manifest(
        self,
        staging: str | Path,
        manifest: ArtifactManifest,
    ) -> ArtifactManifest:
        """Validate a supplied manifest and publish its staging directory.

        The manifest is never trusted merely because it is a Pydantic object: the exact
        file set, path containment, sizes, and hashes are checked again here.
        """

        stage = self._validate_staging_directory(staging)
        manifest_bytes = manifest.to_json_bytes()
        if len(manifest_bytes) > _MANIFEST_SIZE_LIMIT:
            raise InvalidStagingError(
                "artifact manifest exceeds the publication size limit: "
                f"{len(manifest_bytes)} > {_MANIFEST_SIZE_LIMIT} bytes"
            )
        actual_paths = self._inventory(stage)
        declared_paths = [entry.path for entry in manifest.files]
        if actual_paths != declared_paths:
            missing = sorted(set(declared_paths) - set(actual_paths))
            unexpected = sorted(set(actual_paths) - set(declared_paths))
            raise InvalidStagingError(
                f"manifest files do not match staging; missing={missing}, "
                f"unexpected={unexpected}"
            )
        for entry in manifest.files:
            self._verify_file(stage, entry, staging=True)

        manifest_path = stage / _MANIFEST_NAME
        if manifest_path.exists() or manifest_path.is_symlink():
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise InvalidStagingError("staged artifact.json is not a regular file")
            try:
                manifest_stat = manifest_path.stat()
                if manifest_stat.st_nlink != 1:
                    raise InvalidStagingError(
                        "staged artifact.json must not be hard-linked"
                    )
                if manifest_stat.st_size > _MANIFEST_SIZE_LIMIT:
                    raise InvalidStagingError(
                        "staged artifact.json exceeds the publication size limit"
                    )
                staged_bytes = manifest_path.read_bytes()
            except OSError as error:
                raise InvalidStagingError(
                    "cannot safely read staged artifact.json"
                ) from error
            try:
                staged_manifest = ArtifactManifest.from_json_bytes(staged_bytes)
            except (ValidationError, ValueError) as error:
                raise InvalidStagingError(f"invalid staged artifact.json: {error}") from error
            if staged_bytes != staged_manifest.to_json_bytes():
                raise InvalidStagingError("staged artifact.json is not in canonical form")
            if staged_manifest.identity_payload() != manifest.identity_payload():
                raise InvalidStagingError(
                    "staged retry manifest does not match the requested artifact identity"
                )
            if staged_manifest.cache_key != manifest.cache_key:
                raise InvalidStagingError(
                    "staged retry manifest cache_key does not match the request"
                )
            # A failed publication can be retried without changing its observation time.
            manifest = staged_manifest
        else:
            atomic_write_bytes(manifest_path, manifest_bytes, overwrite=False)

        destination = self._artifact_path(manifest.artifact_id)
        digest = artifact_digest(manifest.artifact_id)
        reuse_existing = False
        with self._publication_lock(digest, destination):
            if destination.exists() or destination.is_symlink():
                reuse_existing = True
            else:
                try:
                    atomic_commit_directory(stage, destination)
                except DestinationExistsError:
                    reuse_existing = True
        if reuse_existing:
            # The committed destination is immutable.  Hashing it and deleting this
            # attempt's private staging tree need not extend the publication lock.
            return self._reuse_existing(stage, manifest)
        return manifest

    def get_manifest(self, artifact_id: str) -> ArtifactManifest:
        """Load and validate the manifest for an artifact without hashing data files."""

        artifact_path = self._artifact_path(artifact_id)
        if artifact_path.is_symlink():
            raise ArtifactIntegrityError(
                f"artifact directory may not be a symlink: {artifact_id}"
            )
        manifest_path = artifact_path / _MANIFEST_NAME
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ArtifactNotFoundError(f"complete artifact not found: {artifact_id}")
        try:
            manifest_stat = manifest_path.stat()
            if manifest_stat.st_nlink != 1:
                raise ArtifactIntegrityError(
                    f"manifest must not be hard-linked: {artifact_id}"
                )
            if manifest_stat.st_size > _MANIFEST_SIZE_LIMIT:
                raise ArtifactIntegrityError(f"manifest is unreasonably large: {artifact_id}")
            content = manifest_path.read_bytes()
            manifest = ArtifactManifest.from_json_bytes(content)
        except (OSError, ValidationError, ValueError) as error:
            raise ArtifactIntegrityError(f"invalid manifest for {artifact_id}: {error}") from error
        if manifest.artifact_id != artifact_id:
            raise ArtifactIntegrityError(
                f"manifest ID {manifest.artifact_id} does not match directory ID {artifact_id}"
            )
        if content != manifest.to_json_bytes():
            raise ArtifactIntegrityError(f"manifest is not in canonical form: {artifact_id}")
        return manifest

    def verify(self, artifact_id: str) -> ArtifactManifest:
        """Re-hash every declared file and reject missing or undeclared content."""

        manifest = self.get_manifest(artifact_id)
        artifact_path = self._artifact_path(artifact_id)
        try:
            actual_paths = self._inventory(artifact_path)
        except (InvalidStagingError, ValueError) as error:
            raise ArtifactIntegrityError(
                f"invalid file tree for artifact {artifact_id}: {error}"
            ) from error
        declared_paths = [entry.path for entry in manifest.files]
        if actual_paths != declared_paths:
            missing = sorted(set(declared_paths) - set(actual_paths))
            unexpected = sorted(set(actual_paths) - set(declared_paths))
            raise ArtifactIntegrityError(
                f"artifact file set changed; missing={missing}, unexpected={unexpected}"
            )
        for entry in manifest.files:
            self._verify_file(artifact_path, entry, staging=False)
        return manifest

    def list_refs(self, *, verify: bool = False) -> tuple[ArtifactRef, ...]:
        """List complete artifacts; staging and incomplete directories are ignored."""

        refs: list[ArtifactRef] = []
        artifacts_root = self._owned_directory(
            self.artifacts_root,
            error_type=ArtifactIntegrityError,
        )
        for manifest_path in sorted(artifacts_root.glob(f"*/{_MANIFEST_NAME}")):
            artifact_id = f"artifact:sha256:{manifest_path.parent.name}"
            manifest = self.verify(artifact_id) if verify else self.get_manifest(artifact_id)
            refs.append(manifest.as_ref())
        return tuple(refs)

    def artifact_directory(self, artifact_id: str, *, verify: bool = False) -> Path:
        """Return a committed artifact directory after validating its manifest."""

        self.verify(artifact_id) if verify else self.get_manifest(artifact_id)
        return self._artifact_path(artifact_id)

    def resolve_file(
        self,
        artifact_id: str,
        relative_path: str,
        *,
        verify: bool = False,
    ) -> Path:
        """Resolve one declared artifact file without allowing path traversal."""

        path_text = validate_relative_artifact_path(relative_path)
        manifest = self.verify(artifact_id) if verify else self.get_manifest(artifact_id)
        if path_text not in {entry.path for entry in manifest.files}:
            raise ArtifactNotFoundError(
                f"file {path_text!r} is not declared by artifact {artifact_id}"
            )
        return self._contained_file(self._artifact_path(artifact_id), path_text)

    def resolve_dataset(
        self,
        ref: ArtifactDatasetRef,
        *,
        verify: bool = False,
    ) -> Path:
        """Resolve and validate an exact port-qualified dataset view.

        The returned path is the containing immutable artifact directory.  Consumers
        must read only ``ref.file_paths`` beneath it; :class:`StageInput` preserves that
        exact list at the plugin boundary.
        """

        if not isinstance(ref, ArtifactDatasetRef):
            raise TypeError("ref must be an ArtifactDatasetRef")
        manifest = self.verify(ref.artifact_id) if verify else self.get_manifest(ref.artifact_id)
        try:
            manifest.validate_dataset_ref(ref)
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError(
                f"dataset reference does not match artifact manifest: {error}"
            ) from error
        root = self._artifact_path(ref.artifact_id)
        for relative_path in ref.file_paths:
            self._contained_file(root, relative_path)
        return root

    def _artifact_path(self, artifact_id: str) -> Path:
        artifacts_root = self._owned_directory(
            self.artifacts_root,
            error_type=ArtifactIntegrityError,
        )
        return artifacts_root / artifact_digest(artifact_id)

    def _validate_staging_directory(self, staging: str | Path) -> Path:
        supplied = Path(staging)
        if supplied.is_symlink():
            raise InvalidStagingError(f"staging directory may not be a symlink: {supplied}")
        try:
            stage = supplied.resolve(strict=True)
        except FileNotFoundError as error:
            raise InvalidStagingError(f"staging directory does not exist: {supplied}") from error
        staging_root = self._owned_directory(
            self.staging_root,
            error_type=InvalidStagingError,
        )
        if not stage.is_dir() or stage.parent != staging_root:
            raise InvalidStagingError(
                f"staging directory is not owned by this store: {supplied}"
            )
        if not stage.name.startswith("stage-"):
            raise InvalidStagingError(f"invalid staging directory name: {stage.name}")
        return stage

    @staticmethod
    def _inventory(root: Path) -> list[str]:
        paths: list[str] = []
        for candidate in root.rglob("*"):
            relative = candidate.relative_to(root).as_posix()
            if relative == _MANIFEST_NAME:
                continue
            validate_relative_artifact_path(relative)
            try:
                candidate_stat = candidate.stat(follow_symlinks=False)
            except OSError as error:
                raise InvalidStagingError(
                    f"cannot safely inspect artifact path: {relative}"
                ) from error
            if stat.S_ISLNK(candidate_stat.st_mode):
                raise InvalidStagingError(f"artifact may not contain symlinks: {relative}")
            if stat.S_ISDIR(candidate_stat.st_mode):
                continue
            if not stat.S_ISREG(candidate_stat.st_mode):
                raise InvalidStagingError(
                    f"artifact may contain only directories and regular files: {relative}"
                )
            if candidate_stat.st_nlink != 1:
                raise InvalidStagingError(
                    f"artifact may not contain hard-linked files: {relative}"
                )
            paths.append(relative)
        paths.sort()
        if len(paths) != len(set(paths)):
            raise InvalidStagingError("artifact contains duplicate portable paths")
        if len(paths) != len({path.casefold() for path in paths}):
            raise InvalidStagingError(
                "artifact paths collide on a case-insensitive filesystem"
            )
        return paths

    @staticmethod
    def _validate_output_inventory(
        outputs: Sequence[ArtifactOutput],
        inventory: Sequence[str],
    ) -> None:
        claimed_by: dict[str, str] = {}
        overlap: list[str] = []
        for output in outputs:
            for path in output.file_paths:
                if path in claimed_by:
                    overlap.append(path)
                else:
                    claimed_by[path] = output.port
        if overlap:
            raise InvalidStagingError(
                "staged files are claimed by multiple output ports: "
                + ", ".join(sorted(set(overlap)))
            )
        actual = set(inventory)
        claimed = set(claimed_by)
        if actual != claimed:
            missing = sorted(claimed - actual)
            unexpected = sorted(actual - claimed)
            raise InvalidStagingError(
                "port files do not match staging; "
                f"missing={missing}, unexpected={unexpected}"
            )

    @staticmethod
    def _contained_file(root: Path, relative_path: str) -> Path:
        path_text = validate_relative_artifact_path(relative_path)
        candidate = root.joinpath(*PurePosixPath(path_text).parts)
        try:
            current = root
            for part in PurePosixPath(path_text).parts:
                current /= part
                if current.is_symlink():
                    raise ArtifactIntegrityError(
                        f"artifact path contains a symlink: {path_text}"
                    )
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root.resolve(strict=True))
        except (FileNotFoundError, ValueError) as error:
            raise ArtifactIntegrityError(
                f"artifact path is missing or escaped its root: {path_text}"
            ) from error
        if candidate.is_symlink() or not resolved.is_file():
            raise ArtifactIntegrityError(f"artifact path is not a regular file: {path_text}")
        try:
            if resolved.stat().st_nlink != 1:
                raise ArtifactIntegrityError(
                    f"artifact path must not be hard-linked: {path_text}"
                )
        except OSError as error:
            raise ArtifactIntegrityError(
                f"cannot safely inspect artifact path: {path_text}"
            ) from error
        return resolved

    def _describe_file(self, root: Path, relative_path: str) -> ArtifactFile:
        path = self._contained_file(root, relative_path)
        stat_before = path.stat()
        if stat_before.st_nlink != 1:
            raise InvalidStagingError(
                f"staged file must not be hard-linked: {relative_path}"
            )
        checksum = sha256_file_checksum(path)
        row_count: int | None = None
        schema_checksum: str | None = None
        if path.suffix.lower() == ".parquet":
            from molcascade.io.parquet import ParquetValidationError, validate_parquet_file

            try:
                summary = validate_parquet_file(path)
            except ParquetValidationError as error:
                raise InvalidStagingError(
                    f"invalid staged Parquet file {relative_path}: {error}"
                ) from error
            row_count = summary.row_count
            schema_checksum = summary.schema_checksum
        stat_after = path.stat()
        if stat_after.st_nlink != 1:
            raise InvalidStagingError(
                f"staged file became hard-linked while hashing: {relative_path}"
            )
        if self._file_identity(stat_before) != self._file_identity(stat_after):
            raise InvalidStagingError(f"file changed while hashing: {relative_path}")
        return ArtifactFile(
            path=relative_path,
            size_bytes=stat_after.st_size,
            checksum=checksum,
            media_type=_MEDIA_TYPES.get(path.suffix.lower()),
            row_count=row_count,
            schema_checksum=schema_checksum,
        )

    def _verify_file(self, root: Path, entry: ArtifactFile, *, staging: bool) -> None:
        error_type = InvalidStagingError if staging else ArtifactIntegrityError
        try:
            path = self._contained_file(root, entry.path)
            stat_before = path.stat()
            if stat_before.st_nlink != 1:
                raise error_type(f"file must not be hard-linked: {entry.path}")
            if stat_before.st_size != entry.size_bytes:
                raise error_type(
                    f"size mismatch for {entry.path}: "
                    f"expected {entry.size_bytes}, got {stat_before.st_size}"
                )
            checksum = sha256_file_checksum(path)
            parquet_summary: Any | None = None
            if path.suffix.lower() == ".parquet" and (
                entry.row_count is not None or entry.schema_checksum is not None
            ):
                from molcascade.io.parquet import (
                    ParquetValidationError,
                    validate_parquet_file,
                )

                try:
                    parquet_summary = validate_parquet_file(path)
                except ParquetValidationError as error:
                    raise error_type(f"invalid Parquet file {entry.path}: {error}") from error
            stat_after = path.stat()
            if stat_after.st_nlink != 1:
                raise error_type(
                    f"file became hard-linked while verifying: {entry.path}"
                )
            if self._file_identity(stat_before) != self._file_identity(stat_after):
                raise error_type(f"file changed while verifying: {entry.path}")
            if checksum != entry.checksum:
                raise error_type(
                    f"checksum mismatch for {entry.path}: "
                    f"expected {entry.checksum}, got {checksum}"
                )
            if parquet_summary is not None:
                if (
                    entry.row_count is not None
                    and parquet_summary.row_count != entry.row_count
                ):
                    raise error_type(
                        f"row-count mismatch for {entry.path}: "
                        f"expected {entry.row_count}, got {parquet_summary.row_count}"
                    )
                if (
                    entry.schema_checksum is not None
                    and parquet_summary.schema_checksum != entry.schema_checksum
                ):
                    raise error_type(
                        f"schema checksum mismatch for {entry.path}: "
                        f"expected {entry.schema_checksum}, "
                        f"got {parquet_summary.schema_checksum}"
                    )
        except ArtifactIntegrityError as error:
            if isinstance(error, error_type):
                raise
            raise error_type(str(error)) from error

    @staticmethod
    def _file_identity(stat: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            stat.st_dev,
            stat.st_ino,
            stat.st_nlink,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    def _reuse_existing(
        self,
        stage: Path,
        requested: ArtifactManifest,
    ) -> ArtifactManifest:
        try:
            existing = self.verify(requested.artifact_id)
        except (ArtifactNotFoundError, ArtifactIntegrityError) as error:
            raise ArtifactConflictError(
                f"refusing to overwrite existing destination for {requested.artifact_id}: {error}"
            ) from error
        if existing.identity_payload() != requested.identity_payload():
            raise ArtifactConflictError(
                f"artifact ID collision for {requested.artifact_id}; identities differ"
            )
        shutil.rmtree(stage)
        return existing

    @contextmanager
    def _publication_lock(self, digest: str, destination: Path) -> Iterator[None]:
        del destination  # The digest names the lock; visibility never bypasses it.
        lock_root = self._owned_directory(
            self.lock_root,
            error_type=ArtifactConflictError,
        )
        lock_path = lock_root / f"{digest}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(lock_path, flags, 0o644)
        except OSError as error:
            raise ArtifactConflictError(
                f"cannot open publication lock for artifact digest {digest}: {error}"
            ) from error

        acquired = False
        try:
            try:
                opened = os.fstat(descriptor)
                linked = os.stat(lock_path, follow_symlinks=False)
            except OSError as error:
                raise ArtifactConflictError(
                    f"cannot inspect publication lock for artifact digest {digest}"
                ) from error
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(linked.st_mode)
                or opened.st_nlink != 1
                or linked.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                raise ArtifactConflictError(
                    f"publication lock is not a stable regular file for digest {digest}"
                )

            # Windows byte-range locking requires the byte to exist.  Keeping
            # the inode on disk is intentional: only the OS lock represents
            # ownership, so a process crash releases it without stale-file
            # recovery races.
            if os.name == "nt" and opened.st_size == 0:
                try:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)
                except OSError as error:
                    raise ArtifactConflictError(
                        "cannot initialize publication lock for artifact digest "
                        f"{digest}: {error}"
                    ) from error

            deadline = time.monotonic() + _PUBLICATION_LOCK_TIMEOUT_SECONDS
            while not acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError as error:
                    busy_errnos = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                    if error.errno not in busy_errnos:
                        raise ArtifactConflictError(
                            "cannot acquire publication lock for artifact digest "
                            f"{digest}: {error}"
                        ) from error
                    if time.monotonic() >= deadline:
                        raise ArtifactConflictError(
                            f"publication lock timed out for artifact digest {digest}"
                        ) from error
                    time.sleep(0.01)
            yield
        finally:
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    # Closing the descriptor is the authoritative crash-safe
                    # release path on both supported implementations.
                    acquired = False
            os.close(descriptor)


__all__ = [
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactNotFoundError",
    "ArtifactStoreError",
    "InvalidStagingError",
    "LocalArtifactStore",
]
