"""Local lead-reference similarity, novelty, and applicability evidence.

The implementation targets a relatively small, curated lead set.  It keeps only
reference fingerprints in memory, streams candidate parents, and refuses to exceed
an explicit comparison budget.  Large reference collections should use an indexed
adapter such as FPSim2 instead of this exact O(candidates x references) backend.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import stat
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any, ClassVar, TextIO

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import discover_contract_files, require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import APPLICABILITY_V1, DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_APPLICABILITY_PATH = Path("datasets/reference_similarity/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: One nested key the worker pops before validating the config model, which is
#: strict and would reject an unknown field.  It carries what the parent already
#: paid for and a shard must not redo -- above all the *parsed* reference set,
#: so no worker re-reads a file that the pinning rules allow to be unpinned and
#: that could therefore change underneath a running stage.
_RUNTIME_KEY = "molcascade.runtime"


class ReferenceFileFormat(StrEnum):
    AUTO = "auto"
    CSV = "csv"
    TSV = "tsv"
    SMI = "smi"


class SimilarityObjective(StrEnum):
    ANNOTATE = "annotate"
    ANALOGUE = "analogue"
    NOVEL = "novel"


class SimilarityFailureAction(StrEnum):
    WARN = "warn"
    REJECT = "reject"


class ReferenceRecord(StrictFrozenModel):
    reference_id: str = Field(min_length=1, max_length=512)
    smiles: str = Field(min_length=1, max_length=1_000_000)


class ReferenceSimilarityConfig(StrictFrozenModel):
    """Configuration for exact similarity to a frozen local lead set."""

    #: What to tell someone whose reference set is too big for this backend.
    #: A class attribute rather than a literal at the raise site because the
    #: indexed backend inherits this configuration and would otherwise advise
    #: itself as the way out of its own limit.
    over_limit_hint: ClassVar[str] = (
        "Reduce the lead set, or switch to the indexed FPSim2 backend, which is "
        "built for reference sets this size."
    )

    schema_version: int = Field(default=1, ge=1, le=1)
    #: The lead set, as a path this machine can open.  There is deliberately no
    #: default: ``"leads.csv"`` used to be one, and a relative name that almost
    #: never exists is worse than nothing, because it passes ``molcascade
    #: validate`` -- which reports ``Valid cascade`` and ``Backends ready`` --
    #: and then fails at the first row of a run that has already been paid for.
    #: Unset means the objective validator below refuses the stage up front.
    reference_path: str | None = None
    reference_sha256: str | None = None
    references: tuple[ReferenceRecord, ...] = ()
    file_format: ReferenceFileFormat = ReferenceFileFormat.AUTO
    has_header: bool = True
    smiles_column: str = Field(default="smiles", min_length=1, max_length=256)
    id_column: str | None = Field(default="id", min_length=1, max_length=256)
    batch_size: int = Field(default=8_192, ge=1, le=250_000)
    max_reference_count: int = Field(default=10_000, ge=1, le=1_000_000)
    max_reference_file_bytes: int = Field(
        default=64 * 1024 * 1024,
        ge=1,
        le=4 * 1024 * 1024 * 1024,
    )
    max_total_comparisons: int = Field(
        default=100_000_000,
        ge=1,
        le=100_000_000_000,
    )
    fingerprint_bits: int = Field(default=2_048, ge=128, le=65_536)
    fingerprint_radius: int = Field(default=2, ge=1, le=6)
    include_chirality: bool = True
    domain_similarity_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    objective: SimilarityObjective = SimilarityObjective.ANNOTATE
    analogue_min_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    novelty_max_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    failure_action: SimilarityFailureAction = SimilarityFailureAction.WARN

    @field_validator("references", mode="before")
    @classmethod
    def _parse_references(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("file_format", "objective", "failure_action", mode="before")
    @classmethod
    def _parse_enums(cls, value: Any, info: Any) -> Any:
        enum_types = {
            "file_format": ReferenceFileFormat,
            "objective": SimilarityObjective,
            "failure_action": SimilarityFailureAction,
        }
        enum_type = enum_types[info.field_name]
        return enum_type(value) if isinstance(value, str) else value

    @field_validator("reference_path", mode="before")
    @classmethod
    def _blank_reference_path_is_unset(cls, value: Any) -> Any:
        """Treat an empty box in the builder as "not configured".

        Otherwise a config exported with the field left blank carries
        ``reference_path: ""``, which is a path to nothing and would be reported
        as a missing file rather than as a stage nobody finished filling in.
        """

        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("reference_sha256")
    @classmethod
    def _validate_sha256(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("reference_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def _validate_objective(self) -> ReferenceSimilarityConfig:
        if self.references and self.reference_path is not None:
            raise ValueError("use either embedded references or reference_path, not both")
        if not self.references and self.reference_path is None:
            raise ValueError(
                "this criterion compares against a reference set and none was "
                "given: set reference_path to a local CSV/TSV/SMI file of known "
                "actives, or remove the criterion"
            )
        if len(self.references) > self.max_reference_count:
            raise ValueError("embedded references exceed max_reference_count")
        if self.objective is SimilarityObjective.ANALOGUE:
            if self.analogue_min_similarity is None:
                raise ValueError(
                    "analogue_min_similarity is required for the analogue objective"
                )
            if self.novelty_max_similarity is not None:
                raise ValueError(
                    "novelty_max_similarity is only valid for the novel objective"
                )
        elif self.objective is SimilarityObjective.NOVEL:
            if self.novelty_max_similarity is None:
                raise ValueError(
                    "novelty_max_similarity is required for the novel objective"
                )
            if self.analogue_min_similarity is not None:
                raise ValueError(
                    "analogue_min_similarity is only valid for the analogue objective"
                )
        elif self.analogue_min_similarity is not None or self.novelty_max_similarity is not None:
            raise ValueError("objective thresholds are not valid for annotate mode")
        return self


def _snapshot_reference_file(
    path: Path,
    snapshot: Path,
    *,
    maximum_bytes: int,
) -> tuple[str, int]:
    """Copy and hash one already-open source descriptor into private staging."""

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    digest = hashlib.sha256()
    copied = 0
    try:
        descriptor = os.open(path, flags)
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise PluginError(
                "reference set path must identify a regular file",
                code="REFERENCE_SET_PATH_INVALID",
                context={"path": str(path)},
            )
        if source_stat.st_size > maximum_bytes:
            raise PluginError(
                "reference set exceeds the configured file-size limit",
                code="REFERENCE_SET_TOO_LARGE",
                context={
                    "path": str(path),
                    "size_bytes": source_stat.st_size,
                    "limit_bytes": maximum_bytes,
                },
            )
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = None
            with snapshot.open("xb") as destination:
                while chunk := source.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > maximum_bytes:
                        raise PluginError(
                            "reference set grew beyond the configured file-size limit",
                            code="REFERENCE_SET_TOO_LARGE",
                            context={
                                "path": str(path),
                                "size_bytes": copied,
                                "limit_bytes": maximum_bytes,
                            },
                        )
                    digest.update(chunk)
                    destination.write(chunk)
        if copied != source_stat.st_size:
            raise PluginError(
                "reference set changed size while it was being snapshotted",
                code="REFERENCE_SET_CHANGED_DURING_READ",
                context={
                    "path": str(path),
                    "initial_size_bytes": source_stat.st_size,
                    "copied_size_bytes": copied,
                },
            )
    except FileNotFoundError as error:
        raise PluginError(
            f"reference set was not found: {path}",
            code="REFERENCE_SET_NOT_FOUND",
            context={"path": str(path)},
        ) from error
    except OSError as error:
        raise PluginError(
            "could not snapshot the reference set",
            code="REFERENCE_SET_READ_FAILED",
            context={"path": str(path), "error_type": type(error).__name__},
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return digest.hexdigest(), copied


def _resolved_format(config: ReferenceSimilarityConfig, path: Path) -> ReferenceFileFormat:
    if config.file_format is not ReferenceFileFormat.AUTO:
        return config.file_format
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        return ReferenceFileFormat.CSV
    if suffix in {".tsv", ".tab"}:
        return ReferenceFileFormat.TSV
    if suffix in {".smi", ".smiles", ".txt"}:
        return ReferenceFileFormat.SMI
    raise PluginError(
        "could not infer the reference-set format from its filename",
        code="REFERENCE_SET_FORMAT_UNKNOWN",
        hint="Set file_format to csv, tsv, or smi.",
        context={"path": str(path)},
    )


def _file_records(
    stream: TextIO,
    *,
    config: ReferenceSimilarityConfig,
    file_format: ReferenceFileFormat,
) -> list[ReferenceRecord]:
    records: list[ReferenceRecord] = []

    def append(record: ReferenceRecord) -> None:
        if len(records) >= config.max_reference_count:
            raise PluginError(
                "reference set exceeds the exact backend's configured count limit",
                code="REFERENCE_SET_TOO_MANY_RECORDS",
                hint=config.over_limit_hint,
                context={"limit": config.max_reference_count},
            )
        records.append(record)

    try:
        if file_format is ReferenceFileFormat.SMI:
            for line_number, line in enumerate(stream, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                fields = stripped.split()
                if not fields:
                    continue
                reference_id = fields[1] if len(fields) > 1 else f"reference-{line_number}"
                append(ReferenceRecord(reference_id=reference_id, smiles=fields[0]))
        else:
            delimiter = "," if file_format is ReferenceFileFormat.CSV else "\t"
            if config.has_header:
                reader = csv.DictReader(stream, delimiter=delimiter)
                fields = reader.fieldnames or []
                if config.smiles_column not in fields:
                    raise PluginError(
                        "reference-set SMILES column was not found",
                        code="REFERENCE_SET_COLUMN_MISSING",
                        context={
                            "column": config.smiles_column,
                            "available_columns": fields,
                        },
                    )
                if config.id_column is not None and config.id_column not in fields:
                    raise PluginError(
                        "reference-set ID column was not found",
                        code="REFERENCE_SET_COLUMN_MISSING",
                        context={"column": config.id_column, "available_columns": fields},
                    )
                for row_number, row in enumerate(reader, start=2):
                    smiles = row.get(config.smiles_column)
                    reference_id = (
                        row.get(config.id_column) if config.id_column is not None else None
                    )
                    append(
                        ReferenceRecord(
                            reference_id=reference_id or f"reference-{row_number}",
                            smiles=smiles or "",
                        )
                    )
            else:
                reader = csv.reader(stream, delimiter=delimiter)
                for row_number, row in enumerate(reader, start=1):
                    if not row:
                        continue
                    append(
                        ReferenceRecord(
                            reference_id=(
                                row[1]
                                if len(row) > 1 and row[1]
                                else f"reference-{row_number}"
                            ),
                            smiles=row[0],
                        )
                    )
    except UnicodeError as error:
        raise PluginError(
            "reference set is not valid UTF-8 text",
            code="REFERENCE_SET_ENCODING_INVALID",
        ) from error
    except csv.Error as error:
        raise PluginError(
            f"reference set is not valid delimited text: {error}",
            code="REFERENCE_SET_PARSE_FAILED",
        ) from error
    except ValidationError as error:
        raise PluginError(
            "reference set contains a blank or invalid record",
            code="REFERENCE_SET_RECORD_INVALID",
            context={"error_count": error.error_count()},
        ) from error
    return records


def _load_reference_records(
    config: ReferenceSimilarityConfig,
    *,
    staging_root: Path,
) -> tuple[list[ReferenceRecord], dict[str, Any]]:
    if config.references:
        records = list(config.references)
        content_identity = canonical_sha256(
            [record.model_dump(mode="json") for record in records]
        )
        return records, {
            "source": "embedded",
            "content_sha256": content_identity,
            "path": None,
            "pinned": True,
        }
    assert config.reference_path is not None
    path = Path(config.reference_path).expanduser()
    file_format = _resolved_format(config, path)
    snapshot = staging_root / ".reference-set.snapshot"
    if snapshot.exists() or snapshot.is_symlink():
        raise PluginError(
            "reference-set snapshot already exists in staging",
            code="PLUGIN_STAGING_NOT_EMPTY",
            context={"path": str(snapshot)},
        )
    try:
        digest, size = _snapshot_reference_file(
            path,
            snapshot,
            maximum_bytes=config.max_reference_file_bytes,
        )
        if config.reference_sha256 is not None and digest != config.reference_sha256:
            raise PluginError(
                "reference-set SHA-256 does not match the configured value",
                code="REFERENCE_SET_HASH_MISMATCH",
                context={
                    "path": str(path),
                    "expected_sha256": config.reference_sha256,
                    "actual_sha256": digest,
                },
            )
        with snapshot.open("r", encoding="utf-8", newline="") as stream:
            records = _file_records(stream, config=config, file_format=file_format)
    except OSError as error:
        raise PluginError(
            "could not open the reference set",
            code="REFERENCE_SET_READ_FAILED",
            context={"path": str(path)},
        ) from error
    finally:
        snapshot.unlink(missing_ok=True)
    return records, {
        "source": "file",
        "content_sha256": digest,
        "path": str(path),
        "size_bytes": size,
        "file_format": file_format.value,
        "pinned": config.reference_sha256 is not None,
    }


def objective_outcome(
    config: ReferenceSimilarityConfig,
    maximum: float,
) -> tuple[bool, str, float | None]:
    """Turn a nearest-neighbour similarity into a verdict, reason and threshold.

    Shared by every similarity backend on purpose.  Which engine found the
    nearest reference is an implementation detail -- exact bulk comparison,
    popcount-bounded index, something added later -- but what counts as an
    analogue and what counts as novel is a scientific policy, and two backends
    answering the same question have to answer it the same way or the choice of
    engine silently becomes a choice of filter.
    """

    if config.objective is SimilarityObjective.ANNOTATE:
        return True, "REFERENCE_SIMILARITY_ANNOTATED", None
    if config.objective is SimilarityObjective.ANALOGUE:
        assert config.analogue_min_similarity is not None
        threshold = config.analogue_min_similarity
        passes = maximum >= threshold
        reason = "REFERENCE_ANALOGUE_PASS" if passes else "REFERENCE_ANALOGUE_BELOW_MINIMUM"
        return passes, reason, threshold
    assert config.novelty_max_similarity is not None
    threshold = config.novelty_max_similarity
    passes = maximum <= threshold
    reason = "REFERENCE_NOVELTY_PASS" if passes else "REFERENCE_NOVELTY_ABOVE_MAXIMUM"
    return passes, reason, threshold


def similarity_detail(
    config: ReferenceSimilarityConfig,
    *,
    maximum: float,
    nearest_id: str | None,
    in_domain: bool,
    threshold: float | None,
) -> str:
    """The JSON detail carried by every similarity decision."""

    return json.dumps(
        {
            "objective": config.objective.value,
            "maximum_similarity": maximum,
            "nearest_reference_id": nearest_id,
            "objective_threshold": threshold,
            "domain_threshold": config.domain_similarity_threshold,
            "in_domain": in_domain,
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def similarity_decision_rows(
    *,
    parent_id: Any,
    stage_id: str,
    policy_id: str,
    detail: str,
    reason: str,
    passes: bool,
    rejected: bool,
) -> list[dict[str, Any]]:
    """Decision rows for one molecule, including the WARN-plus-PASS pair.

    A warning does not remove the parent, so a warned molecule emits both the
    WARN and an explicit terminal PASS: a downstream join has to be able to tell
    "this stage looked and was uneasy" from "this stage never reported", and
    with only the WARN row those two are the same absence of a PASS.
    """

    if not passes and not rejected:
        return [
            {
                "entity_id": parent_id,
                "entity_kind": "PARENT",
                "stage_id": stage_id,
                "outcome": "WARN",
                "reason_code": reason,
                "rule_id": policy_id,
                "detail": detail,
            },
            {
                "entity_id": parent_id,
                "entity_kind": "PARENT",
                "stage_id": stage_id,
                "outcome": "PASS",
                "reason_code": "REFERENCE_POLICY_RETAINED_AFTER_WARNING",
                "rule_id": policy_id,
                "detail": detail,
            },
        ]
    return [
        {
            "entity_id": parent_id,
            "entity_kind": "PARENT",
            "stage_id": stage_id,
            "outcome": "REJECT" if rejected else "PASS",
            "reason_code": reason,
            "rule_id": policy_id,
            "detail": detail,
        }
    ]


@cache
def _reference_fingerprints(
    records: tuple[tuple[str, str], ...],
    bits: int,
    radius: int,
    include_chirality: bool,
) -> tuple[Any, ...]:
    """Fingerprint the canonical reference SMILES once per worker process.

    The records arrive already parsed and canonicalised by the parent, so this
    cannot disagree with the identity the parent hashed, and the memo is keyed on
    them: two stages with different lead sets in the same worker never share.
    """

    from rdkit import Chem
    from rdkit.Chem import rdFingerprintGenerator

    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=radius, fpSize=bits, includeChirality=include_chirality
    )
    fingerprints = []
    for reference_id, smiles in records:
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:  # pragma: no cover - the parent already parsed these
            raise PluginError(
                "reference set contains an invalid SMILES",
                code="REFERENCE_SET_SMILES_INVALID",
                context={"reference_id": reference_id},
            )
        fingerprints.append(generator.GetFingerprint(molecule))
    return tuple(fingerprints)


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Compare one contiguous range of parents against the whole lead set."""

    from rdkit import Chem, DataStructs
    from rdkit.Chem import rdFingerprintGenerator

    settings = dict(task.config)
    runtime = dict(settings.pop(_RUNTIME_KEY))
    config = ReferenceSimilarityConfig.model_validate(settings)
    records = tuple((str(entry[0]), str(entry[1])) for entry in runtime["references"])
    reference_fingerprints = _reference_fingerprints(
        records,
        config.fingerprint_bits,
        config.fingerprint_radius,
        config.include_chirality,
    )
    reference_set_id = str(runtime["reference_set_id"])
    method_id = str(runtime["method_id"])
    policy_id = str(runtime["policy_id"])
    generator = rdFingerprintGenerator.GetMorganGenerator(
        radius=config.fingerprint_radius,
        fpSize=config.fingerprint_bits,
        includeChirality=config.include_chirality,
    )

    input_count = 0
    output_count = 0
    reject_count = 0
    warning_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["applicability"], APPLICABILITY_V1.schema, compression="zstd"
        ) as applicability_writer,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decision_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            retained: list[dict[str, Any]] = []
            applicability_rows: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                input_count += 1
                molecule = Chem.MolFromSmiles(row["parent_smiles"])
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed for similarity",
                        code="REFERENCE_SIMILARITY_PARENT_INVALID",
                        context={"parent_id": str(row["parent_id"])},
                    )
                fingerprint = generator.GetFingerprint(molecule)
                similarities = DataStructs.BulkTanimotoSimilarity(
                    fingerprint, list(reference_fingerprints)
                )
                nearest_index = max(
                    range(len(similarities)), key=lambda index: similarities[index]
                )
                maximum = float(similarities[nearest_index])
                nearest_id = records[nearest_index][0]
                in_domain = maximum >= config.domain_similarity_threshold
                applicability_rows.append(
                    {
                        "parent_id": row["parent_id"],
                        "model_id": reference_set_id,
                        "method_id": method_id,
                        "in_domain": in_domain,
                        "raw_metric": maximum,
                        "threshold": config.domain_similarity_threshold,
                        "nearest_reference_id": nearest_id,
                        "nearest_similarity": maximum,
                    }
                )

                passes, reason, threshold = objective_outcome(config, maximum)
                rejected = (
                    not passes and config.failure_action is SimilarityFailureAction.REJECT
                )
                if not rejected:
                    retained.append(row)
                    output_count += 1
                else:
                    reject_count += 1
                if not passes and not rejected:
                    warning_count += 1
                decision_rows.extend(
                    similarity_decision_rows(
                        parent_id=row["parent_id"],
                        stage_id=task.stage_id,
                        policy_id=policy_id,
                        detail=similarity_detail(
                            config,
                            maximum=maximum,
                            nearest_id=nearest_id,
                            in_domain=in_domain,
                            threshold=threshold,
                        ),
                        reason=reason,
                        passes=passes,
                        rejected=rejected,
                    )
                )
            if retained:
                parent_writer.write_table(
                    pa.Table.from_pylist(retained, schema=PARENT_V1.schema)
                )
            applicability_writer.write_table(
                pa.Table.from_pylist(applicability_rows, schema=APPLICABILITY_V1.schema)
            )
            decision_writer.write_table(
                pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
            )
            decision_count += len(decision_rows)
    return ShardOutcome(
        rows_in=input_count,
        rows_out={
            "primary": output_count,
            "applicability": input_count,
            "decisions": decision_count,
        },
        metadata={"reject_count": reject_count, "warning_count": warning_count},
    )


class RDKitReferenceSimilarityPlugin:
    """Calculate exact maximum Tanimoto similarity to a local lead set."""

    descriptor = PluginDescriptor(
        id="applicability.rdkit_reference_similarity",
        version="0.1.0",
        kind=PluginKind.APPLICABILITY,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, APPLICABILITY_V1.id, DECISION_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "applicability": APPLICABILITY_V1.id,
            "decisions": DECISION_V1.id,
        },
        cardinality=Cardinality.FILTER,
        # A file-backed reference set may be intentionally unpinned.  The actual
        # bytes are always hashed into output provenance, but unsafe cache reuse is
        # disabled by this declaration.  Embedded references are revision-pinned.
        determinism=Determinism.BEST_EFFORT,
        display_name="RDKit maximum lead similarity",
        description=(
            "Exact maximum Morgan/Tanimoto similarity to a small frozen lead set, with "
            "separate applicability, analogue, or novelty semantics."
        ),
    )
    config_model = ReferenceSimilarityConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import Chem, rdBase

        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid reference-similarity configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        context.staging_root.mkdir(parents=True, exist_ok=True)
        records, source_provenance = _load_reference_records(
            config,
            staging_root=context.staging_root,
        )
        if not records:
            raise PluginError(
                "reference set contains no molecules",
                code="REFERENCE_SET_EMPTY",
            )
        if len(records) > config.max_reference_count:
            raise PluginError(
                "reference set exceeds the exact backend's configured count limit",
                code="REFERENCE_SET_TOO_MANY_RECORDS",
                hint=config.over_limit_hint,
                context={
                    "reference_count": len(records),
                    "limit": config.max_reference_count,
                },
            )

        # Parsed and canonicalised here rather than in the workers: the reference
        # set is allowed to be unpinned, and a file re-read per shard could hand
        # two shards different chemistry within one stage.  The shards receive
        # these canonical strings and fingerprint them once per process.
        reference_rows: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for record in records:
            if record.reference_id in seen_ids:
                raise PluginError(
                    "reference identifiers must be unique",
                    code="REFERENCE_SET_DUPLICATE_ID",
                    context={"reference_id": record.reference_id},
                )
            seen_ids.add(record.reference_id)
            molecule = Chem.MolFromSmiles(record.smiles)
            if molecule is None:
                raise PluginError(
                    "reference set contains an invalid SMILES",
                    code="REFERENCE_SET_SMILES_INVALID",
                    context={"reference_id": record.reference_id},
                )
            canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
            reference_rows.append((record.reference_id, canonical))
        reference_rows.sort(key=lambda row: row[0])
        # Scientific identity is derived from the parsed, canonical reference
        # records rather than their filesystem location (or whether the same
        # bytes happened to be supplied inline).  The byte hash and source path
        # remain available in output provenance for forensic reproducibility.
        # This keeps a relocated frozen lead set scientifically identical while
        # still making any parsed ID/structure change produce a new identity.
        reference_set_id = "reference-set:sha256:" + canonical_sha256(
            {"records": [(row[0], row[1]) for row in reference_rows]}
        )
        method_id = "reference-similarity:sha256:" + canonical_sha256(
            {
                "backend": "rdkit",
                "backend_version": rdBase.rdkitVersion,
                "fingerprint": "morgan",
                "radius": config.fingerprint_radius,
                "bits": config.fingerprint_bits,
                "include_chirality": config.include_chirality,
                "reference_set_id": reference_set_id,
                "domain_similarity_threshold": config.domain_similarity_threshold,
            }
        )
        policy_id = "reference-similarity-policy:sha256:" + canonical_sha256(
            {
                "objective": config.objective.value,
                "analogue_min_similarity": config.analogue_min_similarity,
                "novelty_max_similarity": config.novelty_max_similarity,
                "failure_action": config.failure_action.value,
                "method_id": method_id,
            }
        )

        # The comparison budget is now settled before a single molecule is read:
        # the row count comes out of the Parquet footers, and candidates x
        # references is exactly what the old in-loop counter accumulated.  A
        # budget that only fires once the work is half done is not a budget.
        total_rows = sum(
            pq.ParquetFile(path).metadata.num_rows
            for path in discover_contract_files(stage_input, PARENT_V1)
        )
        comparisons = total_rows * len(reference_rows)
        if comparisons > config.max_total_comparisons:
            raise PluginError(
                "reference-similarity comparison budget exceeded",
                code="REFERENCE_SIMILARITY_BUDGET_EXCEEDED",
                hint="Reduce candidates/references or use an indexed FPSim2 adapter.",
                context={
                    "comparison_count": comparisons,
                    "limit": config.max_total_comparisons,
                },
            )

        result = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "applicability": _APPLICABILITY_PATH.as_posix(),
                "decisions": _DECISION_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config={
                **config.model_dump(mode="json"),
                _RUNTIME_KEY: {
                    "references": [[row[0], row[1]] for row in reference_rows],
                    "reference_set_id": reference_set_id,
                    "method_id": method_id,
                    "policy_id": policy_id,
                },
            },
        )
        input_count = result.rows_in
        if input_count == 0:
            raise PluginError(
                "reference-similarity input contains no parents",
                code="REFERENCE_SIMILARITY_EMPTY_INPUT",
            )
        output_count = result.rows_out.get("primary", 0)
        reject_count = result.total("reject_count")
        warning_count = result.total("warning_count")

        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": output_count},
                ),
                "applicability": PendingOutput(
                    APPLICABILITY_V1.id,
                    result.file_paths["applicability"],
                    {
                        "row_count": input_count,
                        "method_id": method_id,
                        "reference_set_id": reference_set_id,
                    },
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": output_count,
                "reject_count": reject_count,
                "warning_count": warning_count,
                "reference_count": len(reference_rows),
                "comparison_count": comparisons,
                "reference_set_id": reference_set_id,
                "reference_source": source_provenance,
                "method_id": method_id,
                "policy_id": policy_id,
                "objective": config.objective.value,
                "cacheable": False,
                **result.response_metadata(),
            },
        )


__all__ = [
    "RDKitReferenceSimilarityPlugin",
    "ReferenceFileFormat",
    "ReferenceRecord",
    "ReferenceSimilarityConfig",
    "SimilarityFailureAction",
    "SimilarityObjective",
    "objective_outcome",
    "similarity_decision_rows",
    "similarity_detail",
]
