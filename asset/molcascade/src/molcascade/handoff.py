"""Materialize a verified shortlist artifact for downstream local software."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pyarrow as pa

from molcascade.artifacts import ArtifactDatasetRef
from molcascade.contracts import (
    DOCKING_SCORE_V1,
    PARENT_SOURCE_MAP_V1,
    RAW_MOLECULE_V1,
    RAW_MOLECULE_V2,
    SHORTLIST_EXPORT_V1,
)
from molcascade.errors import PipelineError
from molcascade.io.atomic import (
    DestinationExistsError,
    atomic_commit_directory,
    atomic_publish_file,
)
from molcascade.io.parquet import iter_parquet_batches, write_parquet_batches
from molcascade.runtime import LocalRunner, RunState, RunStatus

#: Contracts a source reader may emit.  Both carry ``source_candidate_id``.
_SOURCE_CONTRACTS = frozenset({RAW_MOLECULE_V1.id, RAW_MOLECULE_V2.id})

#: Batch size for the two projected scans that build the identifier index.
#: Larger than the export batch because only two narrow string columns are read.
_INDEX_BATCH_SIZE = 65_536

#: Batch size for the docking sidecar.  Far smaller than the index scans: these
#: rows carry ``pose_molblock``, a large_string holding a full 3D structure, so a
#: 64k batch would be hundreds of megabytes resident for no gain.
_DOCKING_BATCH_SIZE = 4_096

#: Suffix appended to the shortlist's stem to name its evidence directory.
_DOCKING_SIDECAR_SUFFIX = "-docking"

#: The one derived table in the sidecar; everything else is named for a stage.
_BEST_POSE_FILE = "best-pose.parquet"

#: The merged best-pose view.
#:
#: Deliberately *not* a contract, and deliberately not a pivot.  One row per
#: (molecule, docking stage) in long form, so adding an engine adds rows rather
#: than columns and nothing downstream has to be rewritten.  The pose geometry is
#: left in the per-stage file -- join back on
#: ``(parent_id, engine_id, receptor_id)`` at ``pose_rank = 0`` -- because
#: duplicating molblocks would double the size of the bundle to say nothing new.
_BEST_POSE_SCHEMA = pa.schema(
    [
        pa.field("parent_id", pa.string(), nullable=False),
        # Which stage produced the row, not merely which engine: one cascade can
        # run the same engine twice with different settings, and the stage id is
        # the name that appears in the run report and in the cascade file.
        pa.field("stage_id", pa.string(), nullable=False),
        pa.field("engine_id", pa.string(), nullable=False),
        pa.field("receptor_id", pa.string(), nullable=False),
        pa.field("score", pa.float64(), nullable=False),
        pa.field("score_kind", pa.string(), nullable=False),
        # Carried per row rather than assumed: Vina-like scores are stronger when
        # lower and KarmaDock's MDN score is stronger when higher, so a consumer
        # that sorts without reading this column will rank one engine backwards.
        pa.field("direction", pa.string(), nullable=False),
        pa.field("secondary_score", pa.float64(), nullable=True),
        pa.field("method_id", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True, slots=True)
class DockingSidecar:
    """The docking evidence written beside a shortlist, if the run produced any."""

    path: Path
    #: One file per docking stage, holding its ``docking_score/v1`` rows verbatim.
    engine_files: tuple[Path, ...]
    best_pose_path: Path
    #: Pose rows carried over -- every rank, not only the best.
    pose_count: int
    #: Rows in the merged table: one per molecule per docking stage.
    best_pose_count: int
    #: Distinct ``engine_id`` values observed, sorted.
    engines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MaterializedShortlist:
    path: Path
    record_format: str
    row_count: int
    sha256: str
    source_artifact_id: str
    export_spec_id: str
    #: Rows carrying an identifier from the user's library.  Zero when the
    #: library had no identifier column, which is also when no column is added.
    identified_count: int = 0
    #: ``None`` when the cascade had no docking tier, which is most of them.
    docking: DockingSidecar | None = None


def _property_value(value: str) -> str:
    """Keep an SDF property single-line without changing molecular structure."""

    return value.replace("\r", " ").replace("\n", " ")


def _field_value(value: str) -> str:
    """Keep a library identifier inside one tab-separated field.

    Identifiers come from a spreadsheet cell, so they can hold anything a user
    typed -- including a tab.  Passing one through unchanged would silently add
    a column to that row and misalign every parser downstream.
    """

    return value.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _dataset_files(runner: LocalRunner, ref: ArtifactDatasetRef) -> tuple[Path, ...]:
    """The verified files behind one port-qualified dataset view."""

    root = runner.store.resolve_dataset(ref, verify=True)
    return tuple(
        root.joinpath(*PurePosixPath(relative).parts) for relative in ref.file_paths
    )


def _ports_with_contract(
    runner: LocalRunner,
    state: RunState,
    contract_ids: Collection[str],
) -> tuple[ArtifactDatasetRef, ...]:
    """Every dataset in the run whose manifest declares one of ``contract_ids``.

    Stages are matched by the contract they emit rather than by plugin key, so
    this keeps working when the cascade selects a different reader or a
    different standardizer -- which is the entire point of letting the user
    assemble the flow.  Manifests are read without re-hashing; only the datasets
    actually opened are verified, by :func:`_dataset_files`.
    """

    refs: list[ArtifactDatasetRef] = []
    seen: set[str] = set()
    for stage in state.stages:
        if stage.output_ref is None or stage.output_ref.artifact_id in seen:
            continue
        seen.add(stage.output_ref.artifact_id)
        manifest = runner.store.get_manifest(stage.output_ref.artifact_id)
        for output in manifest.outputs:
            if output.contract_id in contract_ids:
                refs.append(manifest.dataset_ref(output.port))
    return tuple(refs)


def _identifier_index(
    runner: LocalRunner,
    state: RunState,
    wanted: Collection[str],
) -> dict[str, str]:
    """Map each shortlisted parent back to the identifier its library row carried.

    A shortlist keyed only by content hash cannot be joined to the spreadsheet it
    came from, which is what anyone screening a named library actually needs.
    The identifier is already recorded -- ``source_candidate_id`` on the raw
    records, and ``parent_source_map/v1`` links those records to parents -- so
    this is a join over data the run already holds, not new information.  Doing
    it here rather than threading a column through the cascade leaves every
    contract and every plugin untouched.

    Both scans are bounded by ``wanted``: one PRIMARY mapping row exists per
    parent, so the intermediate index never exceeds the shortlist, whatever the
    size of the library behind it.

    A parent that several library rows collapsed onto is named by exactly one of
    them -- the standardizer's PRIMARY, chosen deterministically but by record
    digest rather than by position in the file.  The occurrences it stands in for
    are in the run's decision log as ``EXACT_DUPLICATE``; they are left out of a
    handoff file, which is a handoff and not an audit trail.
    """

    if not wanted:
        return {}
    mapping_refs = _ports_with_contract(runner, state, {PARENT_SOURCE_MAP_V1.id})
    source_refs = _ports_with_contract(runner, state, _SOURCE_CONTRACTS)
    if not mapping_refs or not source_refs:
        # A pipeline may legitimately have neither -- one assembled by hand, or
        # one resumed from parents.  No identifier is not an error; the file is
        # simply written the way it was before identifiers existed.
        return {}

    origin: dict[str, str] = {}
    for ref in mapping_refs:
        for path in _dataset_files(runner, ref):
            for batch in iter_parquet_batches(
                path,
                columns=["source_record_id", "parent_id", "relation"],
                batch_size=_INDEX_BATCH_SIZE,
            ):
                columns = batch.to_pydict()
                for source_id, parent_id, relation in zip(
                    columns["source_record_id"],
                    columns["parent_id"],
                    columns["relation"],
                    strict=True,
                ):
                    if relation == "PRIMARY" and parent_id in wanted:
                        origin[source_id] = parent_id
    if not origin:
        return {}

    identifiers: dict[str, str] = {}
    for ref in source_refs:
        for path in _dataset_files(runner, ref):
            for batch in iter_parquet_batches(
                path,
                columns=["source_record_id", "source_candidate_id"],
                batch_size=_INDEX_BATCH_SIZE,
            ):
                columns = batch.to_pydict()
                for source_id, candidate_id in zip(
                    columns["source_record_id"],
                    columns["source_candidate_id"],
                    strict=True,
                ):
                    # An empty cell reads as "" from a CSV and as None from a
                    # worksheet; neither identifies anything.
                    if not candidate_id:
                        continue
                    parent_id = origin.get(source_id)
                    if parent_id is not None:
                        identifiers[parent_id] = candidate_id
    return identifiers


def _docking_ports(
    runner: LocalRunner,
    state: RunState,
) -> tuple[tuple[str, ArtifactDatasetRef], ...]:
    """Every docking-score dataset in the run, tagged with the stage that made it.

    Like :func:`_ports_with_contract`, but it keeps the stage id, because that is
    what the sidecar names its files after.  Two stages that lowered to the same
    cache key share one artifact and are therefore listed once, under whichever
    of them ran first -- they hold identical bytes, so a second copy would be a
    second name for the same file rather than a second piece of evidence.
    """

    found: list[tuple[str, ArtifactDatasetRef]] = []
    seen: set[str] = set()
    for stage in state.stages:
        if stage.output_ref is None or stage.output_ref.artifact_id in seen:
            continue
        seen.add(stage.output_ref.artifact_id)
        manifest = runner.store.get_manifest(stage.output_ref.artifact_id)
        for output in manifest.outputs:
            if output.contract_id == DOCKING_SCORE_V1.id:
                found.append((stage.stage_id, manifest.dataset_ref(output.port)))
    return tuple(found)


def _sidecar_file(staging: Path, name: str) -> Path:
    """Resolve one file inside the staging directory, refusing to leave it.

    A stage id is validated at config load and cannot hold a separator today, so
    this is defence against a future loosening rather than against anything
    reachable now -- but the id travels through a run-state file on disk, and a
    handoff is the last place that should be willing to write outside the
    directory it was asked to fill.
    """

    destination = (staging / name).resolve()
    if destination.parent != staging.resolve():
        raise PipelineError(
            "docking sidecar file name escapes its directory",
            code="HANDOFF_DOCKING_NAME_INVALID",
            context={"name": name, "directory": str(staging)},
        )
    return destination


def _aligned_docking_batch(batch: pa.RecordBatch) -> pa.RecordBatch:
    """Re-project one batch into the contract's exact column order and types.

    The source file has already passed ``validate_schema``, so a column missing
    here is necessarily a nullable one -- an engine that never records a
    secondary score, say -- and filling it with nulls is what the contract says
    it means.  A required column cannot reach this point absent.
    """

    arrays = []
    for field in DOCKING_SCORE_V1.schema:
        index = batch.schema.get_field_index(field.name)
        arrays.append(
            batch.column(index)
            if index >= 0
            else pa.nulls(batch.num_rows, type=field.type)
        )
    return pa.RecordBatch.from_arrays(arrays, schema=DOCKING_SCORE_V1.schema)


def _best_pose_batch(batch: pa.RecordBatch, stage_id: str) -> pa.RecordBatch:
    """Project the rank-zero rows of one aligned batch into the merged schema."""

    return pa.RecordBatch.from_arrays(
        [
            batch.column("parent_id"),
            pa.array([stage_id] * batch.num_rows, type=pa.string()),
            batch.column("engine_id"),
            batch.column("receptor_id"),
            batch.column("score"),
            batch.column("score_kind"),
            batch.column("direction"),
            batch.column("secondary_score"),
            batch.column("method_id"),
        ],
        schema=_BEST_POSE_SCHEMA,
    )


def _docking_batches(
    files: Sequence[Path],
    *,
    stage_id: str,
    wanted: pa.Array,
    best_poses: list[pa.RecordBatch],
    engines: set[str],
) -> Iterator[pa.RecordBatch]:
    """Stream one stage's docking rows, keeping only shortlisted molecules.

    The schema is checked once per file rather than once per batch.  These bytes
    were resolved with ``verify=True``, so their checksums already hold; what is
    still worth asserting is the shape, because that is the assumption
    :func:`_aligned_docking_batch` makes when it re-projects.  Re-running full
    contract validation per batch would walk every molblock a second time for a
    guarantee the artifact store has already given.

    The rank-zero rows are collected on the way past, in ``best_poses``, so the
    merged table costs no second pass over the pose geometry.
    """

    import pyarrow.compute as pc

    for path in files:
        checked = False
        for batch in iter_parquet_batches(path, batch_size=_DOCKING_BATCH_SIZE):
            if not checked:
                DOCKING_SCORE_V1.validate_schema(batch.schema)
                checked = True
            kept = batch.filter(pc.is_in(batch.column("parent_id"), value_set=wanted))
            if kept.num_rows == 0:
                continue
            aligned = _aligned_docking_batch(kept)
            engines.update(pc.unique(aligned.column("engine_id")).to_pylist())
            best = aligned.filter(pc.equal(aligned.column("pose_rank"), 0))
            if best.num_rows:
                best_poses.append(_best_pose_batch(best, stage_id))
            yield aligned


def _prepare_sidecar_destination(directory: Path, *, overwrite: bool) -> None:
    """Make room for the sidecar, or refuse, before any work is done.

    ``overwrite`` gives permission to replace a bundle MolCascade wrote.  It does
    not give permission to delete a directory that merely happens to sit at the
    derived path, so anything other than the parquet files this function itself
    would have produced stops the export instead of being removed.

    Called for every export, including one with nothing to put here: clearing the
    old directory is how an undocked shortlist stops inheriting a docked run's
    scores when it is written over the top of one.
    """

    if not directory.exists() and not directory.is_symlink():
        return
    if not overwrite:
        raise PipelineError(
            f"docking sidecar already exists: {directory}",
            code="HANDOFF_DOCKING_SIDECAR_EXISTS",
            hint="Choose another output path or explicitly enable overwrite.",
            context={"path": str(directory)},
        )
    if directory.is_symlink() or not directory.is_dir():
        raise PipelineError(
            f"docking sidecar path is not a directory: {directory}",
            code="HANDOFF_DOCKING_SIDECAR_FOREIGN",
            hint="Remove or rename that path by hand; overwrite will not.",
            context={"path": str(directory)},
        )
    entries = sorted(directory.iterdir())
    for entry in entries:
        if entry.is_symlink() or not entry.is_file() or entry.suffix != ".parquet":
            raise PipelineError(
                "docking sidecar directory holds files MolCascade did not write",
                code="HANDOFF_DOCKING_SIDECAR_FOREIGN",
                hint="Remove or rename that directory by hand; overwrite will not.",
                context={"path": str(directory), "entry": entry.name},
            )
    for entry in entries:
        entry.unlink()
    directory.rmdir()


def _fill_docking_sidecar(
    runner: LocalRunner,
    staging: Path,
    ports: Sequence[tuple[str, ArtifactDatasetRef]],
    shortlisted: Collection[str],
    run_id: str,
) -> tuple[tuple[str, ...], int, int, tuple[str, ...]]:
    """Write the whole sidecar into ``staging`` and describe what went in.

    One parquet per docking *stage*, not per engine as the plan first put it,
    because a cascade may legitimately run one engine twice -- two boxes, two
    scoring functions -- and one file per engine would then have to either merge
    them or overwrite one with the other.  The ``engine_id`` column inside every
    file still answers "which engine", and the merged table carries both names.

    Returns the file names, the pose-row count, the merged-row count and the
    engines seen.  Names rather than paths: the caller commits this directory by
    rename, so no path built here survives the move.
    """

    wanted = pa.array(sorted(shortlisted), type=pa.string())
    best_poses: list[pa.RecordBatch] = []
    engines: set[str] = set()
    written: list[str] = []
    pose_count = 0
    for stage_id, ref in ports:
        if f"{stage_id}.parquet" == _BEST_POSE_FILE:
            raise PipelineError(
                f"a docking stage is named after the merged table: {stage_id}",
                code="HANDOFF_DOCKING_NAME_RESERVED",
                hint=f"Rename that stage; {_BEST_POSE_FILE} is written by the export.",
                context={"stage_id": stage_id},
            )
        destination = _sidecar_file(staging, f"{stage_id}.parquet")
        summary = write_parquet_batches(
            _docking_batches(
                _dataset_files(runner, ref),
                stage_id=stage_id,
                wanted=wanted,
                best_poses=best_poses,
                engines=engines,
            ),
            destination,
            schema=DOCKING_SCORE_V1.schema,
        )
        written.append(destination.name)
        pose_count += summary.row_count
    merged = write_parquet_batches(
        best_poses,
        staging / _BEST_POSE_FILE,
        schema=_BEST_POSE_SCHEMA,
        metadata={
            # Enough to tell, from the file alone, that this is a derived view of
            # one run rather than a contract dataset somebody may build on.
            "molcascade.derived_from": DOCKING_SCORE_V1.id,
            "molcascade.run_id": run_id,
            "molcascade.table": "docking best pose per molecule per stage",
        },
    )
    return tuple(written), pose_count, merged.row_count, tuple(sorted(engines))


def materialize_shortlist(
    runner: LocalRunner,
    run_id: str,
    output: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> MaterializedShortlist:
    """Stream the final export-record port into one portable SMILES or SDF file.

    A run that docked also gets a ``<stem>-docking/`` directory beside that file:
    one parquet per docking stage, plus ``best-pose.parquet`` joining every
    shortlisted molecule to each stage's top-ranked score.  It sits beside the
    shortlist rather than inside it because the formats a shortlist travels in
    have nowhere to put a table -- an SDF tag per engine would be a schema
    invented in string form -- and because a docking score is evidence a reader
    may want to weigh, not a field every consumer of a ``.smi`` must now parse.
    ``shortlist_export/v1`` is untouched.

    Runs without a docking tier are unaffected: no directory is created, and the
    returned ``docking`` is ``None``.
    """

    state = runner.load_run(run_id)
    if state.status is not RunStatus.SUCCEEDED or state.output_ref is None:
        raise PipelineError(
            "only a successful run with a final output can be materialized",
            code="HANDOFF_RUN_NOT_SUCCEEDED",
            context={"run_id": run_id, "status": state.status.value},
        )
    manifest = runner.store.verify(state.output_ref.artifact_id)
    candidates = [
        item for item in manifest.outputs if item.contract_id == SHORTLIST_EXPORT_V1.id
    ]
    if len(candidates) != 1:
        raise PipelineError(
            "final stage does not expose exactly one shortlist export-record port",
            code="HANDOFF_EXPORT_PORT_MISSING",
            hint="End the pipeline with a MolCascade exporter plugin.",
            context={
                "run_id": run_id,
                "artifact_id": state.output_ref.artifact_id,
                "matching_ports": len(candidates),
            },
        )
    declared = candidates[0]
    suggested = declared.metadata.get("suggested_filename")
    if not isinstance(suggested, str) or not suggested:
        suggested = f"{run_id}-shortlist.smi"
    target = Path(suggested if output is None else output).expanduser()
    expected_format = declared.metadata.get("record_format")
    suffix = target.suffix.casefold()
    if expected_format == "SMILES_TSV" and suffix not in {".smi", ".smiles", ".tsv"}:
        raise PipelineError(
            "SMILES shortlist output must use .smi, .smiles, or .tsv",
            code="HANDOFF_OUTPUT_SUFFIX_INVALID",
            context={"record_format": expected_format, "path": str(target)},
        )
    if expected_format == "SDF_MOLBLOCK" and suffix not in {".sdf", ".sd"}:
        raise PipelineError(
            "SDF shortlist output must use .sdf or .sd",
            code="HANDOFF_OUTPUT_SUFFIX_INVALID",
            context={"record_format": expected_format, "path": str(target)},
        )
    dataset_ref = manifest.dataset_ref(declared.port)
    # ``_dataset_files`` resolves with ``verify=True``, which has already
    # validated manifest checksums, containment, regular-file status, symlink
    # components and the exact port-qualified file inventory.
    files = _dataset_files(runner, dataset_ref)
    # One projected pass to learn which parents were shortlisted, so the
    # identifier join can be bounded by the shortlist rather than the library.
    # Reading ``parent_id`` alone keeps this cheap even for SDF records.
    shortlisted: set[str] = set()
    for source in files:
        for batch in iter_parquet_batches(
            source, columns=["parent_id"], batch_size=_INDEX_BATCH_SIZE
        ):
            shortlisted.update(batch.column("parent_id").to_pylist())
    identifiers = _identifier_index(runner, state, shortlisted)
    # The column appears only when the library actually carried identifiers.
    # Otherwise every row would end in an empty field, changing the output of
    # every existing configuration for no gain.
    include_identifier = bool(identifiers)
    identified_count = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    # Settled now rather than after the shortlist has been streamed: the sidecar
    # is the expensive half of this function, and a run that cannot publish it
    # should not spend that time first.
    #
    # Checked even when this run has no docking evidence, because the two
    # destinations describe one export.  Overwriting a docked shortlist with an
    # undocked one and leaving the old directory behind would leave scores that
    # silently belong to a different run sitting beside the new file.
    docking_ports = _docking_ports(runner, state)
    sidecar_directory = target.with_name(target.stem + _DOCKING_SIDECAR_SUFFIX)
    _prepare_sidecar_destination(sidecar_directory, overwrite=overwrite)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    row_count = 0
    observed_format: str | None = None
    export_spec_id: str | None = None
    plugin_config = manifest.metadata.get("plugin_config", {})
    include_header = (
        isinstance(plugin_config, dict)
        and plugin_config.get("include_header", True) is True
    )
    sidecar_staging: Path | None = None
    docking: DockingSidecar | None = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            if expected_format == "SMILES_TSV" and include_header:
                names = ["SMILES", "parent_id"]
                if include_identifier:
                    names.append("source_id")
                header = ("\t".join(names) + "\n").encode("utf-8")
                stream.write(header)
                digest.update(header)
            for source in files:
                for batch in iter_parquet_batches(source, batch_size=16_384):
                    SHORTLIST_EXPORT_V1.validate(batch)
                    for row in batch.to_pylist():
                        current_format = row["record_format"]
                        current_spec = row["export_spec_id"]
                        if observed_format is None:
                            observed_format = current_format
                            export_spec_id = current_spec
                        if current_format != observed_format or current_spec != export_spec_id:
                            raise PipelineError(
                                "shortlist export dataset mixes formats or specifications",
                                code="HANDOFF_EXPORT_DATASET_MIXED",
                            )
                        source_id = identifiers.get(row["parent_id"])
                        if source_id is not None:
                            identified_count += 1
                        if current_format == "SMILES_TSV":
                            content = row["structure_record"].rstrip("\r\n")
                            if include_identifier:
                                # An empty field rather than a short row: a
                                # ragged TSV misaligns every parser that reads
                                # it, and some parents legitimately come from a
                                # library row whose identifier cell was blank.
                                content += "\t" + _field_value(source_id or "")
                            content += "\n"
                        elif current_format == "SDF_MOLBLOCK":
                            properties = (
                                "\n>  <MOLCASCADE_PARENT_ID>\n"
                                + _property_value(row["parent_id"])
                                + "\n\n>  <MOLCASCADE_PARENT_SMILES>\n"
                                + _property_value(row["parent_smiles"])
                                + "\n\n"
                            )
                            if source_id is not None:
                                # SD properties are per-record and optional, so
                                # a record with no identifier simply omits this
                                # tag instead of carrying an empty one.
                                properties += (
                                    ">  <MOLCASCADE_SOURCE_ID>\n"
                                    + _property_value(source_id)
                                    + "\n\n"
                                )
                            content = (
                                row["structure_record"].rstrip("\r\n")
                                + properties
                                + "$$$$\n"
                            )
                        else:  # Contract validation should make this unreachable.
                            raise PipelineError(
                                "shortlist record format is unsupported",
                                code="HANDOFF_RECORD_FORMAT_UNSUPPORTED",
                                context={"record_format": str(current_format)},
                            )
                        encoded = content.encode("utf-8")
                        stream.write(encoded)
                        digest.update(encoded)
                        row_count += 1
            if row_count == 0 or observed_format is None or export_spec_id is None:
                raise PipelineError(
                    "shortlist export dataset is empty",
                    code="HANDOFF_EXPORT_EMPTY",
                )
            if observed_format != expected_format:
                raise PipelineError(
                    "shortlist manifest format differs from its records",
                    code="HANDOFF_EXPORT_FORMAT_MISMATCH",
                    context={
                        "manifest_format": str(expected_format),
                        "record_format": observed_format,
                    },
                )
            stream.flush()
            os.fsync(stream.fileno())
        if docking_ports:
            # Built before either destination is touched, so a sidecar that
            # cannot be written leaves the previous shortlist where it was
            # instead of replacing it with half a bundle.
            sidecar_staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{sidecar_directory.name}.",
                    suffix=".tmp",
                    dir=target.parent,
                )
            )
            names, poses, best, engines = _fill_docking_sidecar(
                runner, sidecar_staging, docking_ports, shortlisted, run_id
            )
        try:
            published = atomic_publish_file(temporary, target, overwrite=overwrite)
        except DestinationExistsError as error:
            raise PipelineError(
                f"shortlist output already exists: {target}",
                code="HANDOFF_OUTPUT_EXISTS",
                hint="Choose another path or explicitly enable overwrite.",
                context={"path": str(target)},
            ) from error
        if sidecar_staging is not None:
            committed = atomic_commit_directory(sidecar_staging, sidecar_directory)
            sidecar_staging = None
            docking = DockingSidecar(
                path=committed,
                engine_files=tuple(committed / name for name in names),
                best_pose_path=committed / _BEST_POSE_FILE,
                pose_count=poses,
                best_pose_count=best,
                engines=engines,
            )
    except BaseException:
        temporary.unlink(missing_ok=True)
        if sidecar_staging is not None:
            shutil.rmtree(sidecar_staging, ignore_errors=True)
        raise
    return MaterializedShortlist(
        path=published,
        record_format=observed_format,
        row_count=row_count,
        sha256=digest.hexdigest(),
        source_artifact_id=state.output_ref.artifact_id,
        export_spec_id=export_spec_id,
        identified_count=identified_count,
        docking=docking,
    )


__all__ = ["DockingSidecar", "MaterializedShortlist", "materialize_shortlist"]
