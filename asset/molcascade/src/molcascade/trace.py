"""Flatten one run into a directory of per-stage CSVs and docked-pose SDFs.

``molcascade export`` delivers the one file a finished run is *for*: the
shortlist.  That is the right product and the wrong diagnostic.  While tuning a
cascade the question is not "what survived" but "what did each block remove, and
what did it look like" -- and answering it today means resolving
``runs/<run-id>.json`` by hand to find content-addressed parquet directories
under ``artifacts/sha256/``, because the store is keyed by content and therefore
has no readable filenames.

This module writes that answer out once, as one CSV per executed stage plus an
``index.csv`` that says how many molecules each stage kept.  For a run that
docked and retained its poses it also writes SDFs carrying the real
receptor-frame geometry, which nothing else in the tree can produce: the export
sidecar is parquet, and its merged best-pose table deliberately omits
``pose_molblock`` (see :mod:`molcascade.handoff`).

Two deliberate differences from the export:

* A trace does **not** require a successful run.  ``materialize_shortlist``
  refuses anything but ``SUCCEEDED`` because it publishes a product; a run that
  died in its docking tier is precisely when the layers above it are worth
  reading, so stages are skipped individually when they have no output rather
  than the whole run being refused.
* Nothing here is a contract dataset.  These files are a view for a person and a
  spreadsheet, named for legibility, and no other part of MolCascade reads them
  back.
"""

from __future__ import annotations

import csv
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from molcascade.artifacts import (
    ArtifactDatasetRef,
    ArtifactIntegrityError,
    ArtifactManifest,
    ArtifactNotFoundError,
)
from molcascade.config.models import StageConfig
from molcascade.contracts import (
    DECISION_V1,
    DOCKING_SCORE_V1,
    PARENT_SOURCE_MAP_V1,
    PARENT_V1,
    RAW_MOLECULE_V1,
    RAW_MOLECULE_V2,
    SHORTLIST_EXPORT_V1,
    get_contract,
)
from molcascade.errors import ContractError, PipelineError

# Imported across a module boundary on purpose: these are the resolution,
# identifier-join and SDF-escaping rules the export already uses, and a trace
# that re-implemented any of them would be a second answer to a question that
# already has one.  Internal first-party boundary, as in ``molcascade.ui.report``.
from molcascade.handoff import (
    _dataset_files,
    _identifier_index,
    _property_value,
)
from molcascade.io.atomic import DestinationExistsError, atomic_commit_directory
from molcascade.io.parquet import iter_parquet_batches
from molcascade.runtime import LocalRunner, RunState

#: Request ports that carry the surviving population forward, as opposed to the
#: evidence a stage was handed to decide with.  Same list the funnel uses.
_PARENT_REQUEST_PORTS = frozenset({"primary", "parents"})

#: Contracts a source reader may emit.  Traced instead of ``parent/v1`` for the
#: ingest stage, which runs before parents exist.
_SOURCE_CONTRACTS = frozenset({RAW_MOLECULE_V1.id, RAW_MOLECULE_V2.id})

#: Datasets that are not per-molecule evidence and must not be joined onto a
#: stage's rows.  ``decision/v1`` is an audit log keyed by ``entity_id`` with one
#: row per rule, so joining it would multiply rows; ``parent_source_map/v1`` is
#: keyed by ``source_record_id`` and is already consumed by the identifier index;
#: ``shortlist_export/v1`` holds whole structure records, which is what the SDF
#: is for.
_NOT_EVIDENCE = frozenset(
    {
        DECISION_V1.id,
        PARENT_SOURCE_MAP_V1.id,
        PARENT_V1.id,
        SHORTLIST_EXPORT_V1.id,
        *_SOURCE_CONTRACTS,
    }
)

#: Columns never written into a CSV cell.  A molblock is a multi-line 3D
#: structure: quoting would keep the file technically valid and make it
#: unreadable, and the geometry belongs in the SDF beside it.  Listed by name
#: because the contracts do not mark which columns hold geometry; the test
#: beside this one walks every contract so a new one cannot be missed the way
#: ``ligand_conformer/v1``'s plain ``molblock`` was.
_STRUCTURE_COLUMNS = frozenset({"molblock", "pose_molblock", "raw_molblock", "structure_record"})

#: Suffixes this command writes, and therefore the only ones ``--overwrite`` will
#: remove from an existing directory.
_OWNED_SUFFIXES = frozenset({".csv", ".sdf"})

_INDEX_FILE = "index.csv"
_SHORTLIST_SDF = "shortlist.sdf"

#: Batch size for the CSV scans.  Narrow string columns, so the default 64k is
#: fine; poses are read separately at a much smaller size.
_CSV_BATCH_SIZE = 65_536

#: Batch size for the pose scans, which carry ``pose_molblock`` -- a large_string
#: holding a full 3D structure, so a 64k batch would be hundreds of megabytes.
_POSE_BATCH_SIZE = 4_096

#: Stage-id conventions produced by cascade lowering; see
#: ``CriterionConfig.gate_stage_id`` in ``molcascade/cascade/models.py``.
_GATE_SUFFIX = "__gate"

_INDEX_COLUMNS = (
    "order",
    "stage_id",
    "slot",
    "plugin",
    "status",
    "tier_id",
    "tier_title",
    "entering",
    "rows",
    "kept_pct",
    "csv_file",
    "sdf_file",
)


@dataclass(frozen=True, slots=True)
class TracedStage:
    """One executed stage, written out as the molecules that left it."""

    stage_id: str
    order: int
    slot: str
    plugin: str
    status: str
    tier_id: str | None
    tier_title: str | None
    #: ``None`` only when the stage exposed no traceable population port.
    csv_name: str | None
    sdf_name: str | None
    row_count: int | None
    #: Rows the stage was handed on its population port, read from the stage that
    #: produced them.  Taken from the input binding rather than from the previous
    #: row of the report: siblings in a parallel tier all consume the tier's
    #: input, so chaining row counts down the list yields ratios above 100%.
    entering: int | None
    evidence_columns: tuple[str, ...] = ()

    @property
    def kept_pct(self) -> float | None:
        if self.row_count is None or not self.entering:
            return None
        return self.row_count / self.entering * 100.0


@dataclass(frozen=True, slots=True)
class TracedRun:
    """Everything one traced run put on disk."""

    path: Path
    run_id: str
    status: str
    index_path: Path
    stages: tuple[TracedStage, ...]
    #: Written only when the run docked *and* retained poses.
    shortlist_sdf: Path | None
    #: SDF records written across every SDF, poses included once per file.
    pose_count: int
    #: Stages with no output to trace, and why -- an unrun tail, or a port
    #: topology this command does not recognize.
    skipped: tuple[tuple[str, str], ...]
    #: Human-readable observations for the caller to print.  A trace that writes
    #: no SDF says so here rather than leaving the absence to be guessed at.
    notes: tuple[str, ...]

    @property
    def csv_count(self) -> int:
        return sum(1 for stage in self.stages if stage.csv_name is not None)

    @property
    def sdf_paths(self) -> tuple[Path, ...]:
        paths = [self.path / stage.sdf_name for stage in self.stages if stage.sdf_name is not None]
        if self.shortlist_sdf is not None:
            paths.append(self.shortlist_sdf)
        return tuple(paths)


def _tier_index(
    metadata: Mapping[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Recover which tier each lowered stage came from, for the index.

    The revision holds the lowered pipeline, which has no tiers in it -- the
    cascade builder records them in ``metadata`` instead.  A pipeline assembled
    by hand has none, and then every stage is simply untiered.
    """

    tiers = metadata.get("tiers")
    if not isinstance(tiers, Sequence) or isinstance(tiers, (str, bytes)):
        return {}, {}
    tier_of: dict[str, str] = {}
    title_of: dict[str, str] = {}
    for tier in tiers:
        if not isinstance(tier, Mapping):
            continue
        tier_id = tier.get("id")
        if not isinstance(tier_id, str) or not tier_id:
            continue
        title = tier.get("title")
        title_of[tier_id] = title if isinstance(title, str) and title else tier_id
        criteria = tier.get("criteria")
        if isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes)):
            for criterion in criteria:
                if not isinstance(criterion, str) or not criterion:
                    continue
                tier_of[criterion] = tier_id
                tier_of[criterion + _GATE_SUFFIX] = tier_id
        tier_of[f"{tier_id}__policy"] = tier_id
    return tier_of, title_of


def _staged_file(staging: Path, name: str) -> Path:
    """Resolve one file inside the staging directory, refusing to leave it.

    Stage ids are validated at config load and cannot hold a separator today, so
    this guards a future loosening rather than anything reachable now -- but the
    id reaches here from a state file on disk, and this is the last place that
    should be willing to write outside the directory it was given.
    """

    destination = (staging / name).resolve()
    if destination.parent != staging.resolve():
        raise PipelineError(
            "trace file name escapes its directory",
            code="TRACE_NAME_INVALID",
            context={"name": name, "directory": str(staging)},
        )
    return destination


def _prepare_destination(directory: Path, *, overwrite: bool) -> None:
    """Make room for the trace, or refuse, before any work is done.

    ``overwrite`` gives permission to replace a directory this command wrote.  It
    does not give permission to delete a directory that merely happens to sit at
    the requested path, so anything other than the CSVs and SDFs written here
    stops the trace instead of being removed.
    """

    if not directory.exists() and not directory.is_symlink():
        return
    if not overwrite:
        raise PipelineError(
            f"trace output already exists: {directory}",
            code="TRACE_OUTPUT_EXISTS",
            hint="Choose another output path or explicitly enable overwrite.",
            context={"path": str(directory)},
        )
    if directory.is_symlink() or not directory.is_dir():
        raise PipelineError(
            f"trace output path is not a directory: {directory}",
            code="TRACE_OUTPUT_FOREIGN",
            hint="Remove or rename that path by hand; overwrite will not.",
            context={"path": str(directory)},
        )
    entries = sorted(directory.iterdir())
    for entry in entries:
        if entry.is_symlink() or not entry.is_file() or entry.suffix not in _OWNED_SUFFIXES:
            raise PipelineError(
                "trace directory holds files MolCascade did not write",
                code="TRACE_OUTPUT_FOREIGN",
                hint="Remove or rename that directory by hand; overwrite will not.",
                context={"path": str(directory), "entry": entry.name},
            )
    for entry in entries:
        entry.unlink()
    directory.rmdir()


def _manifest_for(runner: LocalRunner, state: RunState, stage_id: str) -> ArtifactManifest | None:
    """The manifest a stage published, or ``None`` if it published nothing.

    Integrity and lookup failures are folded into ``None`` on purpose: a trace of
    a broken run should show the stages that are readable rather than refuse the
    whole run because one artifact was pruned out from under it.
    """

    for stage in state.stages:
        if stage.stage_id != stage_id:
            continue
        if stage.output_ref is None:
            return None
        try:
            return runner.store.get_manifest(stage.output_ref.artifact_id)
        except (ArtifactIntegrityError, ArtifactNotFoundError, OSError):
            return None
    return None


def _declared_rows(manifest: ArtifactManifest, ref: ArtifactDatasetRef) -> int | None:
    """Row count for one dataset view, from manifest metadata rather than a scan."""

    wanted = set(ref.file_paths)
    counts = [entry.row_count for entry in manifest.files if entry.path in wanted]
    if not counts or any(count is None for count in counts):
        return None
    return sum(count for count in counts if count is not None)


def _population_ref(
    manifest: ArtifactManifest,
) -> tuple[ArtifactDatasetRef, str] | None:
    """The port holding what left this stage, and the contract it carries.

    Matched by contract rather than by port name or plugin key, so this keeps
    working when the cascade selects a different reader or standardizer -- which
    is the entire point of letting the user assemble the flow.
    """

    for output in manifest.outputs:
        if output.contract_id == PARENT_V1.id:
            return manifest.dataset_ref(output.port), output.contract_id
    for output in manifest.outputs:
        if output.contract_id in _SOURCE_CONTRACTS:
            return manifest.dataset_ref(output.port), output.contract_id
    return None


def _evidence_refs(
    runner: LocalRunner,
    state: RunState,
    stage_config: StageConfig,
    manifest: ArtifactManifest,
) -> tuple[tuple[str, ArtifactDatasetRef], ...]:
    """Every per-molecule table worth showing beside this stage's molecules.

    Two sources, because a criterion and its threshold are two stages: a scoring
    stage owns its evidence port, while the gate that filters on it is *handed*
    that port as an input.  Reading both is what puts the docking score next to
    the molecules that passed the docking gate, rather than only next to the ones
    that were scored.
    """

    found: list[tuple[str, ArtifactDatasetRef]] = []
    seen: set[tuple[str, str]] = set()

    def consider(label: str, candidate: ArtifactDatasetRef) -> None:
        if candidate.contract_id in _NOT_EVIDENCE:
            return
        key = (candidate.artifact_id, candidate.port)
        if key in seen:
            return
        seen.add(key)
        found.append((label, candidate))

    for output in manifest.outputs:
        consider(output.port, manifest.dataset_ref(output.port))
    for binding in stage_config.inputs:
        if binding.request_port in _PARENT_REQUEST_PORTS:
            continue
        producer = _manifest_for(runner, state, binding.stage)
        if producer is None:
            continue
        try:
            consider(binding.request_port, producer.dataset_ref(binding.port))
        except ValueError:
            # The producer no longer declares that port.  Nothing to show, and
            # nothing worth failing a diagnostic over.
            continue
    return tuple(found)


def _evidence_columns(ref: ArtifactDatasetRef) -> tuple[str, ...]:
    """Readable, per-molecule columns of one evidence dataset, in contract order."""

    try:
        contract = get_contract(ref.contract_id)
    except ContractError:
        return ()
    return tuple(
        name
        for name in contract.schema.names
        if name != "parent_id" and name not in _STRUCTURE_COLUMNS
    )


def _key_columns(ref: ArtifactDatasetRef, names: Sequence[str]) -> tuple[str, ...]:
    """The primary-key columns, other than ``parent_id``, that this table fans out on.

    A contract keyed on ``parent_id`` alone is one row per molecule.  Every other
    evidence contract here is keyed on ``parent_id`` plus something -- an
    endpoint, a calculator, a fingerprint spec, a conformer index -- and whether
    that something takes one value or twenty-four is a property of the run, not
    of the schema.  Returned in contract order, so a caller can keep the
    catalogue's own column order when it turns out there is nothing to fan out.
    """

    try:
        contract = get_contract(ref.contract_id)
    except ContractError:
        return ()
    keys = frozenset(contract.primary_key) - {"parent_id"}
    return tuple(name for name in names if name in keys)


def _evidence_index(
    runner: LocalRunner,
    refs: Sequence[tuple[str, ArtifactDatasetRef]],
) -> tuple[tuple[str, ...], dict[str, dict[str, Any]]]:
    """Join every evidence dataset for one stage into one row per molecule.

    Almost every evidence contract is keyed on ``parent_id`` plus something else,
    and almost always that something takes a single value in a run: one
    calculator, one fingerprint spec, one conformer.  Then the join is trivial
    and the columns are the contract's own.  But ``prediction/v1`` is keyed on
    ``endpoint_id`` and an ADMET model answers twenty-four endpoints at once, and
    a reader that assumed one row per molecule kept whichever endpoint the scan
    reached first -- silently, since a CSV with a plausible number in it looks
    like a CSV that worked.  The endpoint it kept was not even the one the gate
    below it filtered on.  ``prediction/v1`` says so itself: *different endpoints
    are never collapsed into one score column*.

    So the key columns are measured rather than assumed.  A key that took one
    value is an ordinary column and the output is exactly what it always was; a
    key that took several becomes the column axis, its values naming one column
    each.  A value column that is null in every row is dropped in that case
    only -- an empty column costs one column when there is nothing to fan out
    and twenty-four when there is.  Where that leaves a single value column, the
    key value alone names it (``herg_blocking``); where it leaves more, the
    column name carries both (``herg_blocking__prediction_std``).  Key tuples are
    sorted, so the header does not depend on parquet row order or shard order.

    Docking is the one contract that fans out and must *not* become columns: its
    extra key is ``pose_rank`` and poses belong in the SDF beside this file, so
    only the top-ranked one is read and the rest never reach the join.

    Column names collide only when a stage carries two evidence ports that share
    a field, and the second is then prefixed with its port.

    Held in memory, as the join always was.  Fanning out multiplies the cells per
    molecule by the number of key values -- twenty-five rather than seven for a
    24-endpoint ADMET table -- which is the cost of the file saying what the run
    computed.
    """

    header: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    #: Scan-time cell name -> the column it ends up as.  Names are assigned while
    #: reading, before the fan-out is known, and resolved once at the end;
    #: anything absent here is a key column that became the axis and must not
    #: also appear as a column of its own.
    rename: dict[str, str] = {}

    for ordinal, (label, ref) in enumerate(refs):
        names = _evidence_columns(ref)
        if not names:
            continue
        keys = _key_columns(ref, names)
        values = tuple(name for name in names if name not in keys)
        is_docking = ref.contract_id == DOCKING_SCORE_V1.id

        # Cell names are interned rather than built per row: one per (key tuple,
        # value column) for the whole dataset, and one per key column.  The
        # ordinal keeps them distinct across the ports of one stage.
        slots: dict[tuple[Any, ...], dict[str, str]] = {}
        key_slots = {name: f"\x00{ordinal}\x00k\x00{name}" for name in keys}
        filled: set[str] = set()

        wanted = ["parent_id", *names]
        if is_docking and "pose_rank" not in names:
            wanted.append("pose_rank")
        for path in _dataset_files(runner, ref):
            for batch in iter_parquet_batches(path, columns=wanted, batch_size=_CSV_BATCH_SIZE):
                columns = batch.to_pydict()
                ranks = columns.get("pose_rank")
                key_columns = [columns[name] for name in keys]
                value_columns = [columns[name] for name in values]
                for index, parent_id in enumerate(columns["parent_id"]):
                    if is_docking and ranks is not None and ranks[index] != 0:
                        continue
                    row = rows.setdefault(parent_id, {})
                    key_tuple = tuple(column[index] for column in key_columns)
                    row_slots = slots.get(key_tuple)
                    if row_slots is None:
                        row_slots = {
                            name: f"\x00{ordinal}\x00{len(slots)}\x00{name}" for name in values
                        }
                        slots[key_tuple] = row_slots
                    for name, column in zip(keys, key_columns, strict=True):
                        row.setdefault(key_slots[name], column[index])
                    for name, column in zip(values, value_columns, strict=True):
                        cell = column[index]
                        if cell is None:
                            # Left unwritten rather than written empty.  It reads
                            # the same in the CSV either way, and it is how an
                            # all-null column is recognised below.
                            continue
                        # First write wins.  Every key is in the key tuple, so
                        # this only fires on a dataset that repeats its own
                        # primary key.
                        row.setdefault(row_slots[name], cell)
                        filled.add(row_slots[name])
        if not slots:
            continue

        def emit(source: str, name: str, *, port: str = label) -> None:
            """Give one scan-time cell its column, de-duplicating against the header."""

            rename[source] = name if name not in header else f"{port}_{name}"
            header.append(rename[source])

        varying = tuple(
            name
            for position, name in enumerate(keys)
            if len({key_tuple[position] for key_tuple in slots}) > 1
        )
        if not varying:
            # One key tuple, therefore one row per molecule: the contract's own
            # columns, in the contract's own order.
            (only,) = slots
            for name in names:
                emit(key_slots[name] if name in keys else slots[only][name], name)
            continue

        surviving = tuple(
            name for name in values if any(row[name] in filled for row in slots.values())
        )
        positions = tuple(keys.index(name) for name in varying)
        for key_tuple in sorted(slots, key=lambda t: tuple(str(t[at]) for at in positions)):
            prefix = "__".join(str(key_tuple[at]) for at in positions)
            row_slots = slots[key_tuple]
            if len(surviving) == 1:
                emit(row_slots[surviving[0]], prefix)
            else:
                for name in surviving:
                    emit(row_slots[name], f"{prefix}__{name}")
        for name in keys:
            if name not in varying:
                emit(key_slots[name], name)

    for parent_id, row in rows.items():
        rows[parent_id] = {
            rename[source]: value for source, value in row.items() if source in rename
        }
    return tuple(header), rows


def _write_population_csv(
    runner: LocalRunner,
    destination: Path,
    ref: ArtifactDatasetRef,
    contract_id: str,
    *,
    identifiers: Mapping[str, str],
    evidence_header: Sequence[str],
    evidence_rows: Mapping[str, Mapping[str, Any]],
) -> int:
    """Stream one stage's surviving molecules into a CSV."""

    is_source = contract_id in _SOURCE_CONTRACTS
    if is_source:
        key_column = "source_record_id"
        columns = ["source_record_id", "smiles", "raw_format", "source_index"]
        read = ["source_record_id", "raw_format", "raw_structure", "source_index"]
        if contract_id == RAW_MOLECULE_V1.id:
            read = ["source_record_id", "raw_smiles", "source_index"]
    else:
        key_column = "parent_id"
        columns = ["parent_id", "smiles"]
        read = ["parent_id", "parent_smiles", "formula", "duplicate_count"]
        columns.extend(("formula", "duplicate_count"))
    if identifiers and not is_source:
        columns.insert(2, "source_id")
    columns.extend(evidence_header)

    written = 0
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        for path in _dataset_files(runner, ref):
            for batch in iter_parquet_batches(path, columns=read, batch_size=_CSV_BATCH_SIZE):
                values = batch.to_pydict()
                keys = values[key_column]
                for index, key in enumerate(keys):
                    if is_source:
                        if contract_id == RAW_MOLECULE_V1.id:
                            smiles = values["raw_smiles"][index] or ""
                            row = [key, smiles, "SMILES", values["source_index"][index]]
                        else:
                            raw_format = values["raw_format"][index] or ""
                            structure = values["raw_structure"][index] or ""
                            # Only a single-line record belongs in a cell.  A
                            # molblock library leaves this blank rather than
                            # wrapping a 3D structure in quotes; the parent
                            # SMILES is on the very next row of the trace.
                            smiles = structure if "\n" not in structure else ""
                            row = [
                                key,
                                smiles,
                                raw_format,
                                values["source_index"][index],
                            ]
                    else:
                        row = [key, values["parent_smiles"][index]]
                        if identifiers:
                            row.append(identifiers.get(key, ""))
                        row.extend((values["formula"][index], values["duplicate_count"][index]))
                    evidence = evidence_rows.get(key)
                    for name in evidence_header:
                        row.append("" if evidence is None else evidence.get(name, ""))
                    writer.writerow(row)
                    written += 1
        stream.flush()
    return written


def _pose_refs(
    runner: LocalRunner,
    state: RunState,
) -> tuple[tuple[str, ArtifactDatasetRef], ...]:
    """Every docking-score dataset in the run, tagged with its producing stage."""

    found: list[tuple[str, ArtifactDatasetRef]] = []
    seen: set[str] = set()
    for stage in state.stages:
        if stage.output_ref is None or stage.output_ref.artifact_id in seen:
            continue
        seen.add(stage.output_ref.artifact_id)
        manifest = _manifest_for(runner, state, stage.stage_id)
        if manifest is None:
            continue
        for output in manifest.outputs:
            if output.contract_id == DOCKING_SCORE_V1.id:
                found.append((stage.stage_id, manifest.dataset_ref(output.port)))
    return tuple(found)


def _write_pose_sdf(
    runner: LocalRunner,
    destination: Path,
    refs: Sequence[tuple[str, ArtifactDatasetRef]],
    survivors: Mapping[str, str],
    identifiers: Mapping[str, str],
) -> int:
    """Write the top-ranked pose of every surviving molecule, geometry intact.

    The molblock is written through unchanged.  Its coordinates are the ones the
    engine produced in the receptor's frame, which is the entire value of the
    file: re-embedding a 3D conformer from SMILES would produce something that
    looks like a pose, is not one, and cannot be told apart afterwards.

    ``direction`` is carried on every record because it is not a constant across
    engines -- Vina-like scores are stronger when lower and KarmaDock's MDN score
    is stronger when higher, so a consumer that sorts without reading it ranks
    one engine backwards.
    """

    written = 0
    emitted: set[str] = set()
    with destination.open("w", encoding="utf-8", newline="") as stream:
        for stage_id, ref in refs:
            columns = [
                "parent_id",
                "pose_rank",
                "pose_molblock",
                "score",
                "score_kind",
                "direction",
                "engine_id",
                "receptor_id",
                "secondary_score",
                "method_id",
            ]
            for path in _dataset_files(runner, ref):
                for batch in iter_parquet_batches(
                    path, columns=columns, batch_size=_POSE_BATCH_SIZE
                ):
                    values = batch.to_pydict()
                    for index, parent_id in enumerate(values["parent_id"]):
                        if values["pose_rank"][index] != 0:
                            continue
                        if parent_id not in survivors or parent_id in emitted:
                            continue
                        molblock = values["pose_molblock"][index]
                        if not molblock:
                            continue
                        emitted.add(parent_id)
                        properties = [
                            ("MOLCASCADE_PARENT_ID", parent_id),
                            ("MOLCASCADE_PARENT_SMILES", survivors[parent_id]),
                            ("MOLCASCADE_DOCKING_STAGE", stage_id),
                            ("MOLCASCADE_DOCKING_ENGINE", values["engine_id"][index]),
                            ("MOLCASCADE_DOCKING_SCORE", values["score"][index]),
                            (
                                "MOLCASCADE_DOCKING_SCORE_KIND",
                                values["score_kind"][index],
                            ),
                            (
                                "MOLCASCADE_DOCKING_DIRECTION",
                                values["direction"][index],
                            ),
                            ("MOLCASCADE_RECEPTOR_ID", values["receptor_id"][index]),
                            ("MOLCASCADE_DOCKING_METHOD_ID", values["method_id"][index]),
                        ]
                        secondary = values["secondary_score"][index]
                        if secondary is not None:
                            properties.append(("MOLCASCADE_DOCKING_SECONDARY_SCORE", secondary))
                        source_id = identifiers.get(parent_id)
                        if source_id is not None:
                            # SD properties are per-record and optional, so a
                            # record with no identifier omits the tag instead of
                            # carrying an empty one.
                            properties.append(("MOLCASCADE_SOURCE_ID", source_id))
                        record = [molblock.rstrip("\r\n")]
                        for name, value in properties:
                            record.append(f"\n>  <{name}>\n{_property_value(str(value))}\n")
                        record.append("\n$$$$\n")
                        stream.write("".join(record))
                        written += 1
        stream.flush()
    return written


def _gate_of(stage_id: str, known: Mapping[str, StageConfig]) -> str:
    """The stage that filtered on a scoring stage's evidence, if there is one."""

    gate = stage_id + _GATE_SUFFIX
    return gate if gate in known else stage_id


def _traced_parents(
    runner: LocalRunner,
    population: Mapping[str, tuple[ArtifactDatasetRef, str]],
) -> set[str]:
    """Every parent this trace will write a row for, in any layer.

    The export bounds its identifier join by the shortlist, because the shortlist
    is all it writes.  A trace writes every layer, and a molecule removed halfway
    down needs its library identifier at least as much as one that survived --
    that is the row someone came here to find.  So the bound is the union of the
    populations, which is dominated by the widest of them since the layers below
    it are subsets.  The scan is one narrow column over datasets the CSV pass
    reads anyway; the mapping join itself is a full pass either way.
    """

    parents: set[str] = set()
    for ref, contract_id in population.values():
        if contract_id != PARENT_V1.id:
            continue
        for path in _dataset_files(runner, ref):
            for batch in iter_parquet_batches(
                path, columns=["parent_id"], batch_size=_CSV_BATCH_SIZE
            ):
                parents.update(batch.column("parent_id").to_pylist())
    return parents


def _survivor_map(
    runner: LocalRunner,
    ref: ArtifactDatasetRef,
) -> dict[str, str]:
    """``parent_id`` to SMILES for one population port, for the SDF pass."""

    survivors: dict[str, str] = {}
    for path in _dataset_files(runner, ref):
        for batch in iter_parquet_batches(
            path, columns=["parent_id", "parent_smiles"], batch_size=_CSV_BATCH_SIZE
        ):
            values = batch.to_pydict()
            for parent_id, smiles in zip(values["parent_id"], values["parent_smiles"], strict=True):
                survivors[parent_id] = smiles
    return survivors


def _write_index(destination: Path, stages: Sequence[TracedStage]) -> None:
    """One row per executed stage: what it was, and what it kept."""

    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(_INDEX_COLUMNS)
        for stage in stages:
            kept = stage.kept_pct
            writer.writerow(
                [
                    stage.order,
                    stage.stage_id,
                    stage.slot,
                    stage.plugin,
                    stage.status,
                    stage.tier_id or "",
                    stage.tier_title or "",
                    "" if stage.entering is None else stage.entering,
                    "" if stage.row_count is None else stage.row_count,
                    "" if kept is None else f"{kept:.2f}",
                    stage.csv_name or "",
                    stage.sdf_name or "",
                ]
            )
        stream.flush()


def trace_run(
    runner: LocalRunner,
    run_id: str,
    output: str | Path | None = None,
    *,
    overwrite: bool = False,
) -> TracedRun:
    """Write one directory holding every stage's survivors, plus docked poses.

    Unlike :func:`molcascade.handoff.materialize_shortlist` this accepts a run in
    any state and traces the stages that finished.  Stages with no output are
    reported in ``skipped`` rather than treated as an error.
    """

    state = runner.load_run(run_id)
    revision = runner._load_revision(state.revision_id)  # Internal first-party boundary.
    configured = {stage.id: stage for stage in revision.config.stages}
    tier_of, title_of = _tier_index(revision.config.metadata)

    # Resolved rather than kept as typed, unlike the export's single file: this
    # is a directory a person opens afterwards, so the path reported has to mean
    # something from outside the process -- and the staging directory below is
    # created beside the target, which a relative path would tie to the cwd.
    target = Path(f"{run_id}-trace" if output is None else output).expanduser().resolve()
    _prepare_destination(target, overwrite=overwrite)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Resolved before anything is written: the SDF pass needs to know which
    # stages carry poses, and a run with no docking tier must not create one.
    pose_refs = _pose_refs(runner, state)
    pose_stages = {stage_id for stage_id, _ in pose_refs}
    sdf_targets = {_gate_of(stage_id, configured) for stage_id in pose_stages}

    # Declared row counts for every stage, so ``entering`` can be read from the
    # input binding rather than inferred from the previous row of the report.
    declared: dict[str, int | None] = {}
    population: dict[str, tuple[ArtifactDatasetRef, str]] = {}
    manifests: dict[str, ArtifactManifest] = {}
    for stage in state.stages:
        manifest = _manifest_for(runner, state, stage.stage_id)
        if manifest is None:
            continue
        manifests[stage.stage_id] = manifest
        found = _population_ref(manifest)
        if found is None:
            continue
        ref, contract_id = found
        population[stage.stage_id] = (ref, contract_id)
        declared[stage.stage_id] = _declared_rows(manifest, ref)

    def entering_for(stage_id: str) -> int | None:
        stage_config = configured.get(stage_id)
        if stage_config is None:
            return None
        for binding in stage_config.inputs:
            if binding.request_port in _PARENT_REQUEST_PORTS:
                return declared.get(str(binding.stage))
        return None

    # The last stage still holding molecules, which is what the shortlist SDF
    # describes.  Source records are not molecules with a parent identity, so a
    # run that got no further than its reader has no final population here.
    final_ref: ArtifactDatasetRef | None = None
    for stage in reversed(state.stages):
        found = population.get(stage.stage_id)
        if found is not None and found[1] == PARENT_V1.id:
            final_ref = found[0]
            break
    identifiers = _identifier_index(runner, state, _traced_parents(runner, population))

    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent))
    traced: list[TracedStage] = []
    skipped: list[tuple[str, str]] = []
    notes: list[str] = []
    pose_count = 0
    shortlist_sdf_name: str | None = None
    try:
        for order, stage in enumerate(state.stages):
            stage_config = configured.get(stage.stage_id)
            slot = stage_config.slot if stage_config is not None else ""
            plugin = stage.plugin_key
            tier_id = tier_of.get(stage.stage_id)
            base = TracedStage(
                stage_id=stage.stage_id,
                order=order,
                slot=slot,
                plugin=plugin,
                status=stage.status.value,
                tier_id=tier_id,
                tier_title=None if tier_id is None else title_of.get(tier_id),
                csv_name=None,
                sdf_name=None,
                row_count=None,
                entering=entering_for(stage.stage_id),
            )
            if stage.stage_id not in population:
                reason = (
                    "stage produced no output"
                    if stage.stage_id not in manifests
                    else "stage exposes no molecule population port"
                )
                skipped.append((stage.stage_id, reason))
                traced.append(base)
                continue

            ref, contract_id = population[stage.stage_id]
            csv_name = f"{order:02d}-{stage.stage_id}.csv"
            header, evidence = (
                ((), {})
                if stage_config is None
                else _evidence_index(
                    runner,
                    _evidence_refs(runner, state, stage_config, manifests[stage.stage_id]),
                )
            )
            rows = _write_population_csv(
                runner,
                _staged_file(staging, csv_name),
                ref,
                contract_id,
                identifiers=identifiers,
                evidence_header=header,
                evidence_rows=evidence,
            )

            sdf_name: str | None = None
            if stage.stage_id in sdf_targets and contract_id == PARENT_V1.id:
                sdf_name = f"{order:02d}-{stage.stage_id}.sdf"
                records = _write_pose_sdf(
                    runner,
                    _staged_file(staging, sdf_name),
                    pose_refs,
                    _survivor_map(runner, ref),
                    identifiers,
                )
                if records:
                    pose_count += records
                else:
                    # Poses were declared but none survived with geometry, which
                    # is what a run with ``keep_poses`` off looks like.  Leave no
                    # empty file behind to be mistaken for a result.
                    _staged_file(staging, sdf_name).unlink()
                    sdf_name = None

            traced.append(
                TracedStage(
                    stage_id=base.stage_id,
                    order=order,
                    slot=slot,
                    plugin=plugin,
                    status=base.status,
                    tier_id=tier_id,
                    tier_title=base.tier_title,
                    csv_name=csv_name,
                    sdf_name=sdf_name,
                    row_count=rows,
                    entering=base.entering,
                    evidence_columns=tuple(header),
                )
            )

        if pose_refs and final_ref is not None:
            records = _write_pose_sdf(
                runner,
                _staged_file(staging, _SHORTLIST_SDF),
                pose_refs,
                _survivor_map(runner, final_ref),
                identifiers,
            )
            if records:
                shortlist_sdf_name = _SHORTLIST_SDF
                pose_count += records
            else:
                _staged_file(staging, _SHORTLIST_SDF).unlink()

        if pose_refs and shortlist_sdf_name is None:
            notes.append(
                "Docking ran but kept no pose geometry, so no SDF was written. "
                "Set keep_poses on the docking stage to retain poses."
            )
        elif not pose_refs:
            notes.append("No docking stage in this run, so no SDF was written.")
        if skipped:
            notes.append(f"{len(skipped)} stage(s) had nothing to trace; see index.csv.")

        _write_index(_staged_file(staging, _INDEX_FILE), traced)
        try:
            committed = atomic_commit_directory(staging, target)
        except DestinationExistsError as error:
            raise PipelineError(
                f"trace output already exists: {target}",
                code="TRACE_OUTPUT_EXISTS",
                hint="Choose another path or explicitly enable overwrite.",
                context={"path": str(target)},
            ) from error
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return TracedRun(
        path=committed,
        run_id=state.run_id,
        status=state.status.value,
        index_path=committed / _INDEX_FILE,
        stages=tuple(traced),
        shortlist_sdf=(None if shortlist_sdf_name is None else committed / shortlist_sdf_name),
        pose_count=pose_count,
        skipped=tuple(skipped),
        notes=tuple(notes),
    )


__all__ = ["TracedRun", "TracedStage", "trace_run"]
