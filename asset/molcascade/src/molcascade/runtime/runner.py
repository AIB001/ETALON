"""Crash-consistent, resumable local execution for compiled linear pipelines."""

from __future__ import annotations

import shutil
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import JsonValue, ValidationError

from molcascade import __version__
from molcascade.artifacts import (
    ArtifactDatasetRef,
    ArtifactIntegrityError,
    ArtifactProducer,
    LocalArtifactStore,
    canonical_json_bytes,
    sha256_bytes,
)
from molcascade.config.models import PipelineConfig
from molcascade.contracts import DEFAULT_CONTRACT_REGISTRY, ContractRegistry
from molcascade.environment.models import HostEnvironment
from molcascade.errors import (
    ExecutionError,
    MolCascadeError,
    PipelineError,
    PluginError,
)
from molcascade.io.atomic import DestinationExistsError, atomic_write_bytes
from molcascade.parallel.models import StageResources
from molcascade.parallel.shards import count_completed_shards
from molcascade.pipeline import CompiledPipeline, CompiledStage, PipelineCompiler
from molcascade.pipeline.models import PipelineRevision
from molcascade.plugins import (
    Determinism,
    PluginKind,
    PluginRegistry,
    StageContext,
    StageInput,
    StageRequest,
)
from molcascade.runtime.audit import AuditLog
from molcascade.runtime.cache import (
    EXECUTION_SEMANTICS_VERSION,
    LocalStageCache,
    stage_cache_key,
)
from molcascade.runtime.locking import acquire_run_lock
from molcascade.runtime.models import (
    AuditEvent,
    CacheEntry,
    RunResult,
    RunState,
    RunStatus,
    StageRunState,
    StageRunStatus,
    validate_run_id,
)
from molcascade.runtime.validation import validate_stage_response

_STATE_SIZE_LIMIT = 16 * 1024 * 1024
_REVISION_SIZE_LIMIT = 16 * 1024 * 1024
_COMPLETED_STAGE_STATUSES = {
    StageRunStatus.SUCCEEDED,
    StageRunStatus.CACHED,
}
_ERROR_INFO_SIZE_LIMIT = 64 * 1024
_ERROR_TEXT_CHARACTER_LIMIT = 2_048


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _bounded_error_text(value: str) -> str:
    if len(value) <= _ERROR_TEXT_CHARACTER_LIMIT:
        return value
    digest = sha256_bytes(value.encode("utf-8", errors="replace"))
    return (
        "[error text truncated; "
        f"original_chars={len(value)}; sha256={digest}]"
    )


def _exception_type_name(error: BaseException) -> str:
    """Return a defensive type label without invoking exception rendering."""

    try:
        name = type(error).__name__
    except BaseException:
        return "Exception"
    return name if isinstance(name, str) and name else "Exception"


def _safe_exception_text(error: BaseException) -> str:
    """Render hostile third-party exceptions without breaking failure persistence."""

    error_type = _exception_type_name(error)
    try:
        return _bounded_error_text(str(error))
    except BaseException:
        return f"<unprintable {error_type}>"


def _ensure_workspace_child(workspace: Path, name: str) -> Path:
    """Create one direct control-plane directory without following symlinks."""

    child = workspace / name
    if child.is_symlink():
        raise ValueError(f"workspace directory may not be a symlink: {child}")
    child.mkdir(exist_ok=True)
    if child.is_symlink() or not child.is_dir():
        raise ValueError(f"workspace path is not a real directory: {child}")
    resolved = child.resolve(strict=True)
    if resolved.parent != workspace:
        raise ValueError(f"workspace directory escapes its root: {child}")
    return child


class LocalRunner:
    """Execute exact plugins against immutable local artifact checkpoints.

    Version 1 deliberately supports a single ordered main chain.  Side outputs
    are committed and retained in each stage artifact, but only the named
    ``primary`` dataset advances to the next compiled stage.
    """

    def __init__(
        self,
        workspace: str | Path,
        *,
        plugins: PluginRegistry,
        contracts: ContractRegistry = DEFAULT_CONTRACT_REGISTRY,
        clock: Callable[[], datetime] = _utcnow,
        run_id_factory: Callable[[], str] | None = None,
        resources: StageResources | None = None,
        permitted_licenses: Mapping[str, str] | None = None,
        environment: HostEnvironment | None = None,
    ) -> None:
        if not isinstance(plugins, PluginRegistry):
            raise TypeError("plugins must be a PluginRegistry")
        if not isinstance(contracts, ContractRegistry):
            raise TypeError("contracts must be a ContractRegistry")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if run_id_factory is not None and not callable(run_id_factory):
            raise TypeError("run_id_factory must be callable")
        if resources is not None and not isinstance(resources, StageResources):
            raise TypeError("resources must be a StageResources")
        if permitted_licenses is not None and not isinstance(permitted_licenses, Mapping):
            raise TypeError("permitted_licenses must be a mapping of backend id to SPDX id")
        if environment is not None and not isinstance(environment, HostEnvironment):
            raise TypeError("environment must be a HostEnvironment")

        requested = Path(workspace).expanduser()
        if requested.is_symlink():
            raise ValueError("workspace may not be a symlink")
        requested.mkdir(parents=True, exist_ok=True)
        self.workspace = requested.resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {self.workspace}")

        self.plugins = plugins
        self.contracts = contracts
        self.compiler = PipelineCompiler(plugins)
        # The artifact store owns workspace/artifacts, .staging, and .locks;
        # run control files live alongside those directories.
        self.store = LocalArtifactStore(self.workspace)
        self.revisions_root = _ensure_workspace_child(self.workspace, "revisions")
        self.runs_root = _ensure_workspace_child(self.workspace, "runs")
        self.events_root = _ensure_workspace_child(self.workspace, "events")
        self.cache_root = _ensure_workspace_child(self.workspace, "cache")
        self.run_locks_root = _ensure_workspace_child(self.workspace, ".run-locks")
        # Keyed by invocation cache key, not by run id: an interrupted run and
        # the run that resumes it are two run ids computing the same stage, and
        # sharing shards between them is the entire point.
        self.checkpoints_root = _ensure_workspace_child(self.workspace, "checkpoints")
        self.cache = LocalStageCache(self.cache_root)
        self.resources = resources or StageResources()
        # Which copyleft backends this run was deliberately allowed to link, by
        # SPDX identifier.  Recorded rather than merely enforced: a screening
        # campaign that used GNINA has to be able to say so in its methods
        # section, and "the operator passed a flag" is not the same statement as
        # "this run executed GNINA under GPL-2.0-or-later".
        self.permitted_licenses = dict(permitted_licenses or {})
        # The machine this run happened on, recorded rather than re-measured.
        #
        # A run already measures it: the caller's lane planning calls
        # ``detect_environment``, which forks ``nvidia-smi``, and then keeps the
        # worker count and the batch size. Everything else used to be dropped, so
        # a finished run recorded which molecules it kept and nothing about the
        # toolkit that decided that -- and the stage cache key does not hash any
        # third-party library version, so the same configuration under a
        # different RDKit is the same cache entry and a different answer.
        #
        # Optional, and absent rather than measured here when it is not supplied:
        # a runner constructed inside a test should not fork a subprocess to
        # inventory a machine nobody asked about.
        self.environment = environment
        self._clock = clock
        self._run_id_factory = run_id_factory or (lambda: f"run-{uuid.uuid4().hex}")
        self._lock = threading.RLock()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _stage_resources(self, cache_key: str, *, force: bool) -> StageResources:
        """Grant one stage its share of this machine, plus somewhere to resume from.

        The directory is named by the invocation cache key rather than by the
        run, so a stage that is recomputing exactly the same thing finds its own
        earlier shards no matter which run wrote them -- and a stage whose
        inputs or config changed gets a different key and therefore an empty
        directory, without anyone having to invalidate anything.

        ``--force`` already discards the stage cache; it discards checkpoints
        for the same reason.  A user re-running to escape a result they do not
        trust must not be handed pieces of it back.
        """

        return self.resources.replace(
            checkpoint_dir=self.checkpoints_root / cache_key,
            reuse_checkpoints=not force,
        )

    @staticmethod
    def _discard_checkpoints(checkpoint_dir: Path | None) -> None:
        """Drop a committed stage's shards; never let cleanup fail a good run."""

        if checkpoint_dir is None:
            return
        shutil.rmtree(checkpoint_dir, ignore_errors=True)

    def _audit_shard_plan(
        self,
        audit: AuditLog,
        stage_id: str,
        metadata: Mapping[str, JsonValue],
    ) -> None:
        """Record how a stage was actually divided, if it was divided at all.

        Read back out of the plugin's own response rather than predicted, so
        the log says what happened.  Above all it names the lanes: a run that
        had GPUs and used CPU is exactly the outcome that must never be
        reconstructable only by watching how long it took.
        """

        if "shard_count" not in metadata:
            return
        details: dict[str, JsonValue] = {"shard_count": metadata["shard_count"]}
        for field in ("shards_reused", "execution_lanes", "execution_notes"):
            if field in metadata:
                details[field] = metadata[field]
        audit.append(
            "STAGE_SHARDS_PLANNED",
            timestamp=self._now(),
            stage_id=stage_id,
            details=details,
        )

    @staticmethod
    def _checked_run_id(run_id: str) -> str:
        try:
            return validate_run_id(run_id)
        except ValueError as error:
            raise PipelineError(
                f"invalid run ID: {error}",
                code="RUN_ID_INVALID",
                context={"run_id": str(run_id)},
            ) from error

    def _state_path(self, run_id: str) -> Path:
        return self.runs_root / f"{self._checked_run_id(run_id)}.json"

    def _event_path(self, run_id: str) -> Path:
        return self.events_root / f"{self._checked_run_id(run_id)}.jsonl"

    def _revision_path(self, revision_id: str) -> Path:
        if (
            len(revision_id) != 64
            or any(character not in "0123456789abcdef" for character in revision_id)
        ):
            raise PipelineError(
                "invalid pipeline revision ID",
                code="PIPELINE_REVISION_ID_INVALID",
            )
        return self.revisions_root / f"{revision_id}.json"

    def compile(
        self,
        pipeline: PipelineConfig | PipelineRevision | CompiledPipeline | Mapping[str, Any],
    ) -> CompiledPipeline:
        """Compile or independently verify an already compiled plan."""

        if isinstance(pipeline, CompiledPipeline):
            verified = self.compiler.compile(pipeline.revision)
            if verified != pipeline:
                raise PipelineError(
                    "compiled pipeline does not match its immutable revision",
                    code="PIPELINE_COMPILED_PLAN_MISMATCH",
                    context={"revision_id": pipeline.revision_id},
                )
            return verified
        return self.compiler.compile(pipeline)

    def _persist_revision(self, revision: PipelineRevision) -> None:
        path = self._revision_path(revision.revision_id)
        content = canonical_json_bytes(revision)
        if len(content) > _REVISION_SIZE_LIMIT:
            raise PipelineError(
                "pipeline revision exceeds the local persistence limit",
                code="PIPELINE_REVISION_TOO_LARGE",
                context={
                    "revision_id": revision.revision_id,
                    "size_bytes": len(content),
                    "limit_bytes": _REVISION_SIZE_LIMIT,
                },
            )
        try:
            atomic_write_bytes(path, content, overwrite=False)
        except DestinationExistsError:
            stored = self._load_revision(revision.revision_id)
            if stored != revision:
                # ``from None`` for the same reason as the cache: the file
                # already existing is how the collision is detected, not the
                # problem.  The problem is that one revision id now names two
                # different revisions.
                raise ArtifactIntegrityError(
                    "stored revision differs from its content identity",
                    code="RUNTIME_REVISION_INVALID",
                    context={"revision_id": revision.revision_id},
                ) from None

    def _load_revision(self, revision_id: str) -> PipelineRevision:
        path = self._revision_path(revision_id)
        if path.is_symlink() or not path.is_file():
            raise ArtifactIntegrityError(
                f"stored pipeline revision is missing: {revision_id}",
                code="RUNTIME_REVISION_INVALID",
                context={"revision_id": revision_id},
            )
        try:
            if path.stat().st_size > _REVISION_SIZE_LIMIT:
                raise ValueError("revision file is unreasonably large")
            content = path.read_bytes()
            revision = PipelineRevision.model_validate_json(content)
        except (OSError, ValueError, ValidationError) as error:
            raise ArtifactIntegrityError(
                f"stored pipeline revision is invalid: {revision_id}: {error}",
                code="RUNTIME_REVISION_INVALID",
                context={"revision_id": revision_id},
            ) from error
        if revision.revision_id != revision_id or content != canonical_json_bytes(revision):
            raise ArtifactIntegrityError(
                f"stored pipeline revision is not canonical: {revision_id}",
                code="RUNTIME_REVISION_INVALID",
                context={"revision_id": revision_id},
            )
        return revision

    def _write_state(self, state: RunState, *, overwrite: bool = True) -> None:
        content = canonical_json_bytes(state)
        if len(content) > _STATE_SIZE_LIMIT:
            raise ExecutionError(
                "run state exceeds the local persistence limit",
                code="RUNTIME_STATE_TOO_LARGE",
                context={
                    "run_id": state.run_id,
                    "size_bytes": len(content),
                    "limit_bytes": _STATE_SIZE_LIMIT,
                },
            )
        try:
            atomic_write_bytes(
                self._state_path(state.run_id),
                content,
                overwrite=overwrite,
            )
        except DestinationExistsError as error:
            raise PipelineError(
                f"run already exists: {state.run_id}",
                code="RUN_ALREADY_EXISTS",
                hint="Choose a different run ID or explicitly resume the existing run.",
                context={"run_id": state.run_id},
            ) from error

    def load_run(self, run_id: str) -> RunState:
        """Load and validate a durable run state file."""

        checked = self._checked_run_id(run_id)
        path = self._state_path(checked)
        if path.is_symlink() or not path.is_file():
            raise PipelineError(
                f"run does not exist: {checked}",
                code="RUN_NOT_FOUND",
                context={"run_id": checked},
            )
        try:
            if path.stat().st_size > _STATE_SIZE_LIMIT:
                raise ValueError("run state is unreasonably large")
            content = path.read_bytes()
            state = RunState.model_validate_json(content)
        except (OSError, ValueError, ValidationError) as error:
            raise ArtifactIntegrityError(
                f"run state is invalid for {checked}: {error}",
                code="RUNTIME_STATE_INVALID",
                context={"run_id": checked},
            ) from error
        if state.run_id != checked or content != canonical_json_bytes(state):
            raise ArtifactIntegrityError(
                f"run state is not canonical for {checked}",
                code="RUNTIME_STATE_INVALID",
                context={"run_id": checked},
            )
        return state

    def read_events(self, run_id: str) -> tuple[AuditEvent, ...]:
        """Return a validated, monotonically sequenced audit trail."""

        state = self.load_run(run_id)
        return AuditLog(
            self._event_path(state.run_id),
            run_id=state.run_id,
            revision_id=state.revision_id,
        ).read()

    @staticmethod
    def _replace_stage(
        state: RunState,
        index: int,
        replacement: StageRunState,
        *,
        now: datetime,
    ) -> RunState:
        stages = list(state.stages)
        stages[index] = replacement
        return state.model_copy(update={"stages": tuple(stages), "updated_at": now})

    @staticmethod
    def _artifact_kind(stage: CompiledStage) -> str:
        return f"stage-output/{stage.slot}"

    @staticmethod
    def _binding_metadata(stage: CompiledStage) -> dict[str, dict[str, str]]:
        return {
            binding.request_port: {
                "stage_id": binding.source_stage_id,
                "port": binding.source_port,
            }
            for binding in stage.input_bindings
        }

    @staticmethod
    def _resolve_stage_inputs(
        stage: CompiledStage,
        retained: Mapping[tuple[str, str], ArtifactDatasetRef],
    ) -> dict[str, ArtifactDatasetRef]:
        inputs: dict[str, ArtifactDatasetRef] = {}
        for binding in stage.input_bindings:
            try:
                ref = retained[binding.source_key]
            except KeyError as error:
                raise PipelineError(
                    "compiled input binding is unavailable in retained runtime context",
                    code="PIPELINE_RUNTIME_BINDING_MISSING",
                    context={
                        "stage_id": stage.stage_id,
                        "request_port": binding.request_port,
                        "source": {
                            "stage_id": binding.source_stage_id,
                            "port": binding.source_port,
                        },
                    },
                ) from error
            if ref.port != binding.source_port or ref.contract_id != binding.contract_id:
                raise ArtifactIntegrityError(
                    "retained dataset differs from its compiled input binding",
                    code="RUNTIME_CHECKPOINT_INVALID",
                    context={
                        "stage_id": stage.stage_id,
                        "request_port": binding.request_port,
                        "source": {
                            "stage_id": binding.source_stage_id,
                            "port": binding.source_port,
                        },
                        "expected_contract": binding.contract_id,
                        "actual_contract": ref.contract_id,
                    },
                )
            inputs[binding.request_port] = ref
        return inputs

    @staticmethod
    def _cacheable(stage: CompiledStage) -> bool:
        # Local source plugins commonly dereference mutable files or
        # directories from config.  Until the plugin API exposes a pre-run
        # content fingerprint, config-only source cache keys are unsafe across
        # runs.  Their immutable output artifact still keys every downstream
        # stage, and the same run may resume its verified source checkpoint.
        if stage.descriptor.kind is PluginKind.SOURCE:
            return False
        determinism = stage.descriptor.determinism
        if determinism is Determinism.DETERMINISTIC:
            return True
        if determinism is Determinism.SEEDED:
            seed = stage.config.get("seed")
            return (
                isinstance(seed, int)
                and not isinstance(seed, bool)
            ) or (isinstance(seed, str) and bool(seed))
        return False

    def _verify_checkpoint(
        self,
        stage: CompiledStage,
        cache_key: str,
        ref: ArtifactDatasetRef,
        inputs: Mapping[str, ArtifactDatasetRef],
    ) -> dict[tuple[str, str], ArtifactDatasetRef]:
        """Verify both bytes and the invocation lineage behind a checkpoint."""

        if ref.port != stage.output_port or ref.contract_id != stage.output_contract:
            raise ArtifactIntegrityError(
                "checkpoint dataset does not match the compiled primary output",
                code="RUNTIME_CHECKPOINT_INVALID",
                context={
                    "stage_id": stage.stage_id,
                    "expected_port": stage.output_port,
                    "actual_port": ref.port,
                    "expected_contract": stage.output_contract,
                    "actual_contract": ref.contract_id,
                },
            )
        manifest = self.store.verify(ref.artifact_id)
        try:
            manifest.validate_dataset_ref(ref)
        except (TypeError, ValueError) as error:
            raise ArtifactIntegrityError(
                "checkpoint dataset reference differs from its artifact manifest",
                code="RUNTIME_CHECKPOINT_INVALID",
                context={"stage_id": stage.stage_id, "artifact_id": ref.artifact_id},
            ) from error

        descriptor = stage.descriptor
        expected_inputs = tuple(ref for _, ref in sorted(inputs.items()))
        identity_errors: list[str] = []
        if manifest.cache_key != cache_key:
            identity_errors.append("cache_key")
        if manifest.kind != self._artifact_kind(stage) or ref.artifact_kind != manifest.kind:
            identity_errors.append("artifact_kind")
        if manifest.producer.plugin_id != descriptor.id:
            identity_errors.append("producer.plugin_id")
        if manifest.producer.plugin_version != descriptor.version:
            identity_errors.append("producer.plugin_version")
        if manifest.producer.api_version != descriptor.api_version:
            identity_errors.append("producer.api_version")
        if manifest.inputs != expected_inputs:
            identity_errors.append("inputs")
        if manifest.metadata.get("stage_id") != stage.stage_id:
            identity_errors.append("metadata.stage_id")
        if manifest.metadata.get("slot") != stage.slot:
            identity_errors.append("metadata.slot")
        if manifest.metadata.get("plugin_key") != stage.plugin_key:
            identity_errors.append("metadata.plugin_key")
        if manifest.metadata.get("plugin_config") != dict(stage.config):
            identity_errors.append("metadata.plugin_config")
        if manifest.metadata.get("execution_semantics") != EXECUTION_SEMANTICS_VERSION:
            identity_errors.append("metadata.execution_semantics")
        if manifest.metadata.get("input_bindings") != self._binding_metadata(stage):
            identity_errors.append("metadata.input_bindings")
        # ``ArtifactManifest.cache_key`` is intentionally observational and is
        # excluded from the artifact ID.  Bind the invocation key into metadata
        # as well so changing only that observation (and a cache-index entry)
        # can never make an artifact impersonate another invocation.
        if manifest.metadata.get("invocation_cache_key") != cache_key:
            identity_errors.append("metadata.invocation_cache_key")

        actual_output_ports = {
            output.port: output.contract_id for output in manifest.outputs
        }
        if actual_output_ports != dict(stage.descriptor.output_ports):
            identity_errors.append("outputs")
        if identity_errors:
            raise ArtifactIntegrityError(
                "checkpoint artifact does not match the stage invocation",
                code="RUNTIME_CHECKPOINT_INVALID",
                context={
                    "stage_id": stage.stage_id,
                    "artifact_id": ref.artifact_id,
                    "mismatched_fields": identity_errors,
                },
            )
        retained: dict[tuple[str, str], ArtifactDatasetRef] = {}
        for output in manifest.outputs:
            output_ref = manifest.dataset_ref(output.port)
            self.store.resolve_dataset(output_ref, verify=False)
            retained[(stage.stage_id, output.port)] = output_ref
        return retained

    def _prepare_resume(
        self,
        compiled: CompiledPipeline,
        state: RunState,
        audit: AuditLog,
    ) -> tuple[RunState, int, dict[tuple[str, str], ArtifactDatasetRef]]:
        if state.revision_id != compiled.revision_id:
            raise PipelineError(
                "cannot resume a run with a different pipeline revision",
                code="RUN_REVISION_MISMATCH",
                context={
                    "run_id": state.run_id,
                    "stored_revision_id": state.revision_id,
                    "requested_revision_id": compiled.revision_id,
                },
            )
        stored_revision = self._load_revision(state.revision_id)
        if stored_revision != compiled.revision:
            raise ArtifactIntegrityError(
                "stored pipeline revision does not match the compiled run",
                code="RUNTIME_REVISION_INVALID",
                context={"revision_id": state.revision_id},
            )
        if len(state.stages) != len(compiled.stages):
            raise ArtifactIntegrityError(
                "run state stage count differs from its pipeline revision",
                code="RUNTIME_STATE_INVALID",
                context={"run_id": state.run_id},
            )
        for saved, stage in zip(state.stages, compiled.stages, strict=True):
            if saved.stage_id != stage.stage_id or saved.plugin_key != stage.plugin_key:
                raise ArtifactIntegrityError(
                    "run state stages differ from the compiled pipeline",
                    code="RUNTIME_STATE_INVALID",
                    context={"run_id": state.run_id},
                )

        # A checkpoint is reusable only when every earlier main-chain
        # checkpoint is valid.  Completed stages after a gap indicate tampering
        # or a non-atomic writer and are rejected.
        retained: dict[tuple[str, str], ArtifactDatasetRef] = {}
        first_incomplete = len(compiled.stages)
        saw_incomplete = False
        resumed_indices: list[int] = []
        for index, (saved, stage) in enumerate(
            zip(state.stages, compiled.stages, strict=True)
        ):
            completed = saved.status in _COMPLETED_STAGE_STATUSES
            if saw_incomplete and completed:
                raise ArtifactIntegrityError(
                    "run state contains a completed stage after an incomplete stage",
                    code="RUNTIME_STATE_INVALID",
                    context={"run_id": state.run_id, "stage_id": saved.stage_id},
                )
            if not completed:
                saw_incomplete = True
                first_incomplete = min(first_incomplete, index)
                continue
            if saved.cache_key is None or saved.output_ref is None:
                raise ArtifactIntegrityError(
                    "completed stage is missing its checkpoint identity",
                    code="RUNTIME_CHECKPOINT_INVALID",
                    context={"run_id": state.run_id, "stage_id": saved.stage_id},
                )
            inputs = self._resolve_stage_inputs(stage, retained)
            expected_key = stage_cache_key(stage, inputs)
            if saved.cache_key != expected_key:
                raise ArtifactIntegrityError(
                    "checkpoint cache key differs from the compiled invocation",
                    code="RUNTIME_CHECKPOINT_INVALID",
                    context={"run_id": state.run_id, "stage_id": saved.stage_id},
                )
            restored = self._verify_checkpoint(
                stage,
                expected_key,
                saved.output_ref,
                inputs,
            )
            retained.update(restored)
            resumed_indices.append(index)

        now = self._now()
        reset_stages = list(state.stages)
        for index in range(first_incomplete, len(reset_stages)):
            saved = reset_stages[index]
            reset_stages[index] = saved.model_copy(
                update={
                    "status": StageRunStatus.PENDING,
                    "cache_key": None,
                    "output_ref": None,
                    "started_at": None,
                    "finished_at": None,
                    "error": None,
                }
            )
        resumed = state.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "stages": tuple(reset_stages),
                "output_ref": None,
                "updated_at": now,
                "finished_at": None,
                "error": None,
            }
        )
        self._write_state(resumed)
        audit.append(
            "RUN_RESUMED",
            timestamp=now,
            details={"checkpoint_count": len(resumed_indices)},
        )
        for index in resumed_indices:
            saved = resumed.stages[index]
            assert saved.output_ref is not None
            audit.append(
                "STAGE_RESUMED",
                timestamp=self._now(),
                stage_id=saved.stage_id,
                details={
                    "artifact_id": saved.output_ref.artifact_id,
                    "cache_key": saved.cache_key,
                },
            )
        return resumed, first_incomplete, retained

    def _new_state(self, compiled: CompiledPipeline, run_id: str) -> RunState:
        now = self._now()
        return RunState(
            run_id=run_id,
            revision_id=compiled.revision_id,
            status=RunStatus.RUNNING,
            stages=tuple(
                StageRunState(stage_id=stage.stage_id, plugin_key=stage.plugin_key)
                for stage in compiled.stages
            ),
            started_at=now,
            updated_at=now,
        )

    @staticmethod
    def _bounded_public_error(error: MolCascadeError) -> MolCascadeError:
        try:
            encoded = canonical_json_bytes(error.to_info())
            if len(encoded) <= _ERROR_INFO_SIZE_LIMIT:
                return error
            context = {
                "error_type": _exception_type_name(error),
                "original_code": error.code,
                "original_error_size_bytes": len(encoded),
                "original_error_sha256": sha256_bytes(encoded),
                "truncated": True,
            }
            return type(error)(
                _bounded_error_text(error.message),
                code=error.code,
                hint=(None if error.hint is None else _bounded_error_text(error.hint)),
                retryable=error.retryable,
                context=context,
            )
        except BaseException:
            return ExecutionError(
                "an exception could not be serialized safely",
                code="RUNTIME_ERROR_SERIALIZATION_FAILED",
                context={"error_type": _exception_type_name(error)},
            )

    @classmethod
    def _control_plane_error(
        cls,
        error: Exception,
        *,
        operation: str,
    ) -> MolCascadeError:
        if isinstance(error, MolCascadeError):
            return cls._bounded_public_error(error)
        error_text = _safe_exception_text(error)
        return ExecutionError(
            f"runtime control-plane operation {operation!r} failed: {error_text}",
            code="RUNTIME_CONTROL_PLANE_FAILED",
            retryable=True,
            context={
                "operation": operation,
                "error_type": _exception_type_name(error),
                "error": error_text,
            },
        )

    def _mark_failed(
        self,
        state: RunState,
        *,
        stage_index: int | None,
        error: MolCascadeError,
        audit: AuditLog,
    ) -> RunState:
        error = self._bounded_public_error(error)
        try:
            # Bypass a potentially hostile subclass override after the
            # normalizer has validated the public fields.
            error_info = MolCascadeError.to_info(error)
        except BaseException:
            error = ExecutionError(
                "an exception could not be serialized safely",
                code="RUNTIME_ERROR_SERIALIZATION_FAILED",
                context={"error_type": _exception_type_name(error)},
            )
            error_info = MolCascadeError.to_info(error)
        now = self._now()
        current = None if stage_index is None else state.stages[stage_index]
        if current is None:
            normalised_stages = tuple(
                stage.model_copy(
                    update={
                        "status": StageRunStatus.FAILED,
                        "output_ref": None,
                        "finished_at": now,
                        "error": error_info,
                    }
                )
                if stage.status is StageRunStatus.RUNNING
                else stage
                for stage in state.stages
            )
            failed = state.model_copy(update={"stages": normalised_stages})
        else:
            failed_stage = current.model_copy(
                update={
                    "status": StageRunStatus.FAILED,
                    "output_ref": None,
                    "finished_at": now,
                    "error": error_info,
                }
            )
            failed = self._replace_stage(
                state,
                stage_index,
                failed_stage,
                now=now,
            )
        failed = failed.model_copy(
            update={
                "status": RunStatus.FAILED,
                "output_ref": None,
                "updated_at": now,
                "finished_at": now,
                "error": error_info,
            }
        )
        self._write_state(failed)
        details = {"error": error_info.model_dump(mode="json")}
        # Audit failure must never shadow the original failure or leave the
        # durable run state marked RUNNING.  When the audit sink itself is the
        # problem, the failed state is the surviving recovery evidence.
        try:
            if current is not None:
                audit.append(
                    "STAGE_FAILED",
                    timestamp=now,
                    stage_id=current.stage_id,
                    details=details,
                )
            audit.append("RUN_FAILED", timestamp=self._now(), details=details)
        except Exception:
            return failed
        return failed

    def run(
        self,
        pipeline: PipelineConfig | PipelineRevision | CompiledPipeline | Mapping[str, Any],
        *,
        run_id: str | None = None,
        resume: bool = False,
        force: bool = False,
    ) -> RunResult:
        """Execute one run while holding its crash-releasing workspace lock."""

        if force and resume:
            raise PipelineError(
                "force recomputation and resume cannot be used together",
                code="RUN_OPTIONS_CONFLICT",
                context={"force": True, "resume": True},
            )
        selected_run_id = self._checked_run_id(
            self._run_id_factory() if run_id is None else run_id
        )
        with acquire_run_lock(self.run_locks_root, selected_run_id):
            return self._run_locked(
                pipeline,
                run_id=selected_run_id,
                resume=resume,
                force=force,
            )

    def _run_locked(
        self,
        pipeline: PipelineConfig | PipelineRevision | CompiledPipeline | Mapping[str, Any],
        *,
        run_id: str | None = None,
        resume: bool = False,
        force: bool = False,
    ) -> RunResult:
        """Execute after :meth:`run` has acquired the cross-instance run lock."""

        if force and resume:
            raise PipelineError(
                "force recomputation and resume cannot be used together",
                code="RUN_OPTIONS_CONFLICT",
                context={"force": True, "resume": True},
            )
        with self._lock:
            compiled = self.compile(pipeline)
            selected_run_id = self._checked_run_id(
                self._run_id_factory() if run_id is None else run_id
            )
            self._persist_revision(compiled.revision)
            state_path = self._state_path(selected_run_id)
            event_path = self._event_path(selected_run_id)

            if resume:
                state = self.load_run(selected_run_id)
                audit = AuditLog(
                    event_path,
                    run_id=selected_run_id,
                    revision_id=state.revision_id,
                )
                try:
                    # Validate the old log before adding evidence to it.  A run
                    # state without its initial audit record is not resumable:
                    # it may be the residue of a crash during run creation.
                    previous_events = audit.read()
                    if (
                        not previous_events
                        or previous_events[0].event_type != "RUN_STARTED"
                    ):
                        raise ArtifactIntegrityError(
                            "run audit trail is missing its initial RUN_STARTED record",
                            code="RUNTIME_AUDIT_INVALID",
                            context={"run_id": selected_run_id},
                        )
                    state, start_index, retained = self._prepare_resume(
                        compiled,
                        state,
                        audit,
                    )
                except Exception as raw_error:
                    public_error = self._control_plane_error(
                        raw_error,
                        operation="resume validation",
                    )
                    try:
                        current_state = self.load_run(selected_run_id)
                    except MolCascadeError:
                        current_state = state
                    # A caller supplying the wrong revision is a rejected
                    # request, not a mutation of the stored run.  Persist a
                    # failure when validation proves that a RUNNING or
                    # SUCCEEDED run is corrupt, or when a runtime control-plane
                    # failure happened after resume had already transitioned
                    # the state back to RUNNING.
                    should_record_failure = (
                        current_state.status is RunStatus.RUNNING
                        and not isinstance(public_error, PipelineError)
                    ) or (
                        current_state.status is RunStatus.SUCCEEDED
                        and isinstance(public_error, ArtifactIntegrityError)
                    )
                    if should_record_failure:
                        self._mark_failed(
                            current_state,
                            stage_index=None,
                            error=public_error,
                            audit=audit,
                        )
                    if public_error is raw_error:
                        raise
                    raise public_error from raw_error
            else:
                if state_path.exists() or state_path.is_symlink():
                    raise PipelineError(
                        f"run already exists: {selected_run_id}",
                        code="RUN_ALREADY_EXISTS",
                        hint="Choose a different run ID or pass resume=True.",
                        context={"run_id": selected_run_id},
                    )
                if event_path.exists() or event_path.is_symlink():
                    raise ArtifactIntegrityError(
                        f"orphaned audit log exists for run {selected_run_id}",
                        code="RUNTIME_AUDIT_INVALID",
                        context={"run_id": selected_run_id},
                    )
                state = self._new_state(compiled, selected_run_id)
                self._write_state(state, overwrite=False)
                audit = AuditLog(
                    event_path,
                    run_id=selected_run_id,
                    revision_id=compiled.revision_id,
                )
                try:
                    audit.append(
                        "RUN_STARTED",
                        timestamp=state.started_at,
                        details={
                            "force": force,
                            "permitted_licenses": dict(self.permitted_licenses),
                            "molcascade_version": __version__,
                            "environment": (
                                None
                                if self.environment is None
                                else self.environment.provenance()
                            ),
                        },
                    )
                except Exception as raw_error:
                    public_error = self._control_plane_error(
                        raw_error,
                        operation="initial audit",
                    )
                    self._mark_failed(
                        state,
                        stage_index=None,
                        error=public_error,
                        audit=audit,
                    )
                    if public_error is raw_error:
                        raise
                    raise public_error from raw_error
                start_index = 0
                retained: dict[tuple[str, str], ArtifactDatasetRef] = {}

            # A completed run may be explicitly resumed to re-verify all of
            # its checkpoints.  No plugin code is executed in that case.
            if start_index == len(compiled.stages):
                final_key = (compiled.stages[-1].stage_id, "primary")
                try:
                    final_ref = retained[final_key]
                except KeyError as error:
                    raise ArtifactIntegrityError(
                        "completed run is missing its final primary checkpoint",
                        code="RUNTIME_CHECKPOINT_INVALID",
                        context={"run_id": selected_run_id},
                    ) from error
                now = self._now()
                state = state.model_copy(
                    update={
                        "status": RunStatus.SUCCEEDED,
                        "output_ref": final_ref,
                        "updated_at": now,
                        "finished_at": now,
                        "error": None,
                    }
                )
                self._write_state(state)
                try:
                    audit.append(
                        "RUN_SUCCEEDED",
                        timestamp=now,
                        details={"artifact_id": final_ref.artifact_id},
                    )
                except Exception as raw_error:
                    public_error = self._control_plane_error(
                        raw_error,
                        operation="completed-resume audit",
                    )
                    self._mark_failed(
                        state,
                        stage_index=None,
                        error=public_error,
                        audit=audit,
                    )
                    if public_error is raw_error:
                        raise
                    raise public_error from raw_error
                return state

            current_index = start_index
            try:
                for current_index in range(start_index, len(compiled.stages)):
                    stage = compiled.stages[current_index]
                    bound_inputs = self._resolve_stage_inputs(stage, retained)
                    if current_index == 0 and bound_inputs:
                        raise PipelineError(
                            "source stage unexpectedly has input bindings",
                            code="PIPELINE_RUNTIME_EDGE_MISMATCH",
                            context={"stage_id": stage.stage_id},
                        )

                    key = stage_cache_key(stage, bound_inputs)
                    now = self._now()
                    pending = state.stages[current_index].model_copy(
                        update={
                            "status": StageRunStatus.RUNNING,
                            "cache_key": key,
                            "output_ref": None,
                            "started_at": None,
                            "finished_at": None,
                            "error": None,
                        }
                    )
                    state = self._replace_stage(
                        state,
                        current_index,
                        pending,
                        now=now,
                    )
                    self._write_state(state)

                    cacheable = self._cacheable(stage)
                    if cacheable and not force:
                        entry = self.cache.get(key)
                        if entry is not None:
                            cached_outputs = self._verify_checkpoint(
                                stage,
                                key,
                                entry.output_ref,
                                bound_inputs,
                            )
                            cached_ref = cached_outputs[
                                (stage.stage_id, stage.output_port)
                            ]
                            finished_at = self._now()
                            cached_stage = state.stages[current_index].model_copy(
                                update={
                                    "status": StageRunStatus.CACHED,
                                    "output_ref": cached_ref,
                                    "finished_at": finished_at,
                                }
                            )
                            state = self._replace_stage(
                                state,
                                current_index,
                                cached_stage,
                                now=finished_at,
                            )
                            self._write_state(state)
                            audit.append(
                                "STAGE_CACHE_HIT",
                                timestamp=finished_at,
                                stage_id=stage.stage_id,
                                details={
                                    "cache_key": key,
                                    "artifact_id": cached_ref.artifact_id,
                                },
                            )
                            retained.update(cached_outputs)
                            continue
                        audit.append(
                            "STAGE_CACHE_MISS",
                            timestamp=self._now(),
                            stage_id=stage.stage_id,
                            details={"cache_key": key},
                        )
                    elif force:
                        audit.append(
                            "STAGE_CACHE_BYPASSED",
                            timestamp=self._now(),
                            stage_id=stage.stage_id,
                            details={"reason": "force", "cache_key": key},
                        )
                    else:
                        bypass_reason = (
                            "external_source"
                            if stage.descriptor.kind is PluginKind.SOURCE
                            else "determinism"
                        )
                        audit.append(
                            "STAGE_CACHE_BYPASSED",
                            timestamp=self._now(),
                            stage_id=stage.stage_id,
                            details={"reason": bypass_reason, "cache_key": key},
                        )

                    started_at = self._now()
                    running_stage = state.stages[current_index].model_copy(
                        update={
                            "attempts": state.stages[current_index].attempts + 1,
                            "started_at": started_at,
                        }
                    )
                    state = self._replace_stage(
                        state,
                        current_index,
                        running_stage,
                        now=started_at,
                    )
                    self._write_state(state)
                    audit.append(
                        "STAGE_STARTED",
                        timestamp=started_at,
                        stage_id=stage.stage_id,
                        details={"attempt": running_stage.attempts, "cache_key": key},
                    )

                    plugin = self.plugins.get(stage.plugin_key)
                    if plugin.descriptor != stage.descriptor:
                        raise PluginError(
                            "plugin descriptor changed after pipeline compilation",
                            code="PLUGIN_DESCRIPTOR_CHANGED",
                            context={
                                "stage_id": stage.stage_id,
                                "plugin": stage.plugin_key,
                            },
                        )

                    stage_resources = self._stage_resources(key, force=force)
                    checkpoint_dir = stage_resources.checkpoint_dir
                    if force:
                        # --force already throws away the stage cache.  Leaving
                        # half a dataset behind under the same key would let a
                        # later, non-forced run pick pieces of it back up.
                        self._discard_checkpoints(checkpoint_dir)
                    elif checkpoint_dir is not None:
                        resumed, _ = count_completed_shards(checkpoint_dir)
                        if resumed:
                            # One event per stage, not per shard: AuditLog.append
                            # rewrites the whole JSONL, so forty shards across
                            # twenty tiers would be eight hundred full rewrites.
                            audit.append(
                                "STAGE_SHARDS_RESUMED",
                                timestamp=self._now(),
                                stage_id=stage.stage_id,
                                details={"shards_reused": resumed, "cache_key": key},
                            )

                    with self.store.staging_area() as staging:
                        request_inputs = {
                            request_port: StageInput(
                                ref=input_dataset,
                                root=self.store.resolve_dataset(
                                    input_dataset,
                                    verify=True,
                                ),
                            )
                            for request_port, input_dataset in sorted(bound_inputs.items())
                        }
                        request = StageRequest(
                            stage_id=stage.stage_id,
                            inputs=request_inputs,
                            config=stage.config,
                        )
                        response = plugin.execute(
                            request,
                            StageContext(staging, stage_resources),
                        )
                        self._audit_shard_plan(audit, stage.stage_id, response.metadata)
                        validated = validate_stage_response(
                            stage,
                            response,
                            staging,
                            self.contracts,
                        )
                        manifest = self.store.commit(
                            staging,
                            kind=self._artifact_kind(stage),
                            producer=ArtifactProducer(
                                plugin_id=stage.descriptor.id,
                                plugin_version=stage.descriptor.version,
                                api_version=stage.descriptor.api_version,
                            ),
                            inputs=tuple(
                                input_dataset
                                for _, input_dataset in sorted(bound_inputs.items())
                            ),
                            outputs=validated.outputs,
                            metadata={
                                "execution_semantics": EXECUTION_SEMANTICS_VERSION,
                                "invocation_cache_key": key,
                                "stage_id": stage.stage_id,
                                "slot": stage.slot,
                                "plugin_key": stage.plugin_key,
                                "plugin_config": dict(stage.config),
                                "input_bindings": self._binding_metadata(stage),
                                "response_metadata": dict(response.metadata),
                                "row_counts": dict(validated.row_counts),
                            },
                            cache_key=key,
                        )
                    # The artifact is committed and immutable, so the shards it
                    # was built from are now redundant copies of it.  Kept on
                    # failure, discarded on success -- that asymmetry is the
                    # whole contract of the checkpoint directory.
                    self._discard_checkpoints(checkpoint_dir)
                    output_ref = manifest.dataset_ref(stage.output_port)
                    produced_outputs = self._verify_checkpoint(
                        stage,
                        key,
                        output_ref,
                        bound_inputs,
                    )
                    if cacheable:
                        self.cache.put(CacheEntry(cache_key=key, output_ref=output_ref))

                    finished_at = self._now()
                    succeeded_stage = state.stages[current_index].model_copy(
                        update={
                            "status": StageRunStatus.SUCCEEDED,
                            "output_ref": output_ref,
                            "finished_at": finished_at,
                            "error": None,
                        }
                    )
                    state = self._replace_stage(
                        state,
                        current_index,
                        succeeded_stage,
                        now=finished_at,
                    )
                    self._write_state(state)
                    audit.append(
                        "STAGE_SUCCEEDED",
                        timestamp=finished_at,
                        stage_id=stage.stage_id,
                        details={
                            "cache_key": key,
                            "artifact_id": output_ref.artifact_id,
                            "row_counts": dict(validated.row_counts),
                        },
                    )
                    retained.update(produced_outputs)

                final_ref = retained[(compiled.stages[-1].stage_id, "primary")]
                finished_at = self._now()
                state = state.model_copy(
                    update={
                        "status": RunStatus.SUCCEEDED,
                        "output_ref": final_ref,
                        "updated_at": finished_at,
                        "finished_at": finished_at,
                        "error": None,
                    }
                )
                self._write_state(state)
                audit.append(
                    "RUN_SUCCEEDED",
                    timestamp=finished_at,
                    details={"artifact_id": final_ref.artifact_id},
                )
                return state
            except Exception as raw_error:
                if isinstance(raw_error, MolCascadeError):
                    public_error = self._bounded_public_error(raw_error)
                else:
                    stage = compiled.stages[current_index]
                    error_text = _safe_exception_text(raw_error)
                    public_error = ExecutionError(
                        f"plugin stage {stage.stage_id!r} failed: {error_text}",
                        code="PLUGIN_EXECUTION_FAILED",
                        context={
                            "stage_id": stage.stage_id,
                            "plugin": stage.plugin_key,
                            "error_type": _exception_type_name(raw_error),
                            "error": error_text,
                        },
                    )
                self._mark_failed(
                    state,
                    stage_index=current_index,
                    error=public_error,
                    audit=audit,
                )
                if public_error is raw_error:
                    raise
                raise public_error from raw_error


__all__ = ["LocalRunner"]
