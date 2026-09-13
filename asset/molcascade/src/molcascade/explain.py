"""Follow one molecule through a finished run and say what happened to it.

``molcascade export`` answers *what survived*.  ``molcascade trace`` answers
*what each stage removed*.  Neither answers the question a chemist actually
opens this tool with, which is about a molecule they already know: **why is this
one not in the shortlist?**

Everything needed was already on disk.  ``decision/v1`` records the reason every
stage removed or warned about an entity, ``parent/v1`` records who survived each
stage, ``parent_source_map/v1`` ties a parent back to the library rows it came
from, and ``selection_decision/v1`` records who the final budget kept.  What was
missing was the one resolution step that turns a SMILES a person can type into
the ``parent_id`` those tables are keyed on.

That step is the whole difficulty, and it has one rule: **the parent id must be
recomputed under the identity policy that run used, never under the default.**
A policy is a set of decisions about what counts as the same molecule -- which
fragment of a salt is the parent, whether tautomers collapse, whether an
unassigned stereocentre is tolerated -- and two policies give the same input two
different ids.  Recomputing under the default when the run used something else
produces an id that is absent from every table in the run, and this command
would then answer, confidently and wrongly, "that molecule was never in your
library".  That is the worst available answer, so when the policy cannot be
recovered this refuses to guess and says to pass ``--parent-id`` instead.

The policy is recoverable because the standardizer already records it: it puts
the full ``identity_policy`` and its ``identity_policy_id`` into its stage
response metadata, and the runner copies that verbatim into the artifact
manifest.  Recovery reads it back and checks the recomputed id against the
recorded one before trusting anything downstream of it.

Like :mod:`molcascade.decisions` and :mod:`molcascade.trace`, nothing here runs
inside the pipeline: it reads committed artifacts after the fact, changes no
contract, no stage configuration and no cache key, and works on a run that
failed partway -- which is one of the times the question matters most.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow.dataset as ds

from molcascade.artifacts import (
    ArtifactDatasetRef,
    ArtifactIntegrityError,
    ArtifactManifest,
    ArtifactNotFoundError,
)
from molcascade.chemistry import (
    IdentityPolicy,
    identity_policy_id,
    record_components,
    standardize_parent,
)
from molcascade.chemistry.identity import StandardizationFailure
from molcascade.contracts import (
    DECISION_V1,
    PARENT_SOURCE_MAP_V1,
    PARENT_V1,
    RAW_MOLECULE_V1,
    RAW_MOLECULE_V2,
    SELECTION_DECISION_V1,
)
from molcascade.errors import InputError, PipelineError
from molcascade.io.parquet import iter_parquet_batches
from molcascade.runtime import LocalRunner, RunState

#: The molecule was never registered: no stage in the run holds this parent.
VERDICT_ABSENT = "ABSENT"
#: A stage recorded a REJECT for it, and it appears in no stage after that.
VERDICT_REJECTED = "REJECTED"
#: It survived every stage that ran, and the selection step kept it.
VERDICT_SELECTED = "SELECTED"
#: It survived every stage that ran, and the selection step did not keep it.
VERDICT_NOT_SELECTED = "NOT_SELECTED"
#: It survived everything that ran, and no selection step ran (or the run
#: stopped first).  Distinct from SELECTED so a partial run cannot read as a win.
VERDICT_SURVIVED = "SURVIVED"
#: It *was* in the library, inside a multi-component record, and de-salting kept
#: a different fragment.  Never merged into ABSENT: "your library never had this"
#: and "it was there and registration threw it away" point at opposite fixes.
VERDICT_DESALTED = "DESALTED"

_BATCH_SIZE = 65_536

_FRAGMENTS_REMOVED = "FRAGMENTS_REMOVED"

#: Library rows this will re-split before giving up.  Replaying one record costs
#: about 6.1 ms (measured over pyrantel pamoate, hydroxyzine pamoate, amlodipine
#: besylate and diphenhydramine HCl on a warm standardizer); counting a record's
#: heavy atoms to decide whether replaying it could possibly help costs 0.043 ms,
#: 142 times less.  So the screen runs over far more records than the replay.
_DESALT_REPLAY_LIMIT = 200
_DESALT_SCREEN_RATIO = 100

#: The replay reproduced the run's own choice, so its discard list is credible.
_DESALT_AGREED = "AGREED"
#: It did not, so this record is reported without a discard list.  A replay that
#: cannot reproduce what the run kept has not earned the right to say what the
#: run threw away.
_DESALT_DISPUTED = "DISPUTED"


@dataclass(frozen=True, slots=True)
class DiscardedFragment:
    """One component of a library record that de-salting did not keep."""

    smiles: str
    heavy_atoms: int
    #: ``None`` when the fragment cannot be registered on its own -- ``[Cl-]``,
    #: ``[Na+]`` and water all fail under the ``largest_organic`` policy -- which
    #: is itself proof it is not the fragment the run kept.
    parent_id: str | None
    unregistrable: str | None
    #: True when this discarded fragment *is* the molecule that was asked about.
    is_query: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "smiles": self.smiles,
            "heavy_atoms": self.heavy_atoms,
            "parent_id": self.parent_id,
            "unregistrable": self.unregistrable,
            "is_query": self.is_query,
        }


@dataclass(frozen=True, slots=True)
class DesaltingReport:
    """What one multi-component library row was split into, and what was lost."""

    source_record_id: str
    status: str
    kept_parent_id: str | None
    discarded: tuple[DiscardedFragment, ...] = ()
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_record_id": self.source_record_id,
            "status": self.status,
            "kept_parent_id": self.kept_parent_id,
            "discarded": [fragment.as_dict() for fragment in self.discarded],
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class DesaltingScan:
    """How much of the run's de-salting was examined, so a negative can be read.

    "None of the rows this run split discarded your molecule" is only a useful
    sentence if the reader can tell it apart from "we stopped looking".
    """

    split_rows: int
    screened: int
    replayed: int
    disputed: int
    truncated: bool

    @property
    def exhaustive(self) -> bool:
        """Whether a miss is a real negative rather than a budget running out."""

        return not self.truncated and self.screened >= self.split_rows and not self.disputed

    def as_dict(self) -> dict[str, Any]:
        return {
            "split_rows": self.split_rows,
            "screened": self.screened,
            "replayed": self.replayed,
            "disputed": self.disputed,
            "truncated": self.truncated,
            "exhaustive": self.exhaustive,
        }


@dataclass(frozen=True, slots=True)
class StageEvent:
    """What one stage did about this molecule."""

    stage_id: str
    #: ``True``/``False`` when the stage published a ``parent/v1`` port and the
    #: molecule was or was not in it; ``None`` when the stage published no such
    #: port, so presence is simply not a fact this stage recorded.
    present: bool | None
    outcome: str | None
    reason_code: str | None
    rule_id: str | None
    detail: str | None
    #: Set when the stage's artifact could not be read; the event still appears.
    unavailable: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "present": self.present,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "rule_id": self.rule_id,
            "detail": self.detail,
            "unavailable": self.unavailable,
        }


@dataclass(frozen=True, slots=True)
class MoleculeVerdict:
    """The full trail of one molecule through one run."""

    run_id: str
    status: str
    query: str
    parent_id: str | None
    parent_smiles: str | None
    identity_policy_id: str | None
    verdict: str
    #: The stage that ended it, when the verdict is ``REJECTED``.
    removed_by: str | None
    reason_code: str | None
    events: tuple[StageEvent, ...] = ()
    source_record_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Multi-component library rows relevant to this molecule, and what each one
    #: discarded.  Empty unless the run split something worth reporting.
    desalting: tuple[DesaltingReport, ...] = ()
    #: How much of the run's de-salting was looked at.  ``None`` when the search
    #: was not run at all.
    desalting_scan: DesaltingScan | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "query": self.query,
            "parent_id": self.parent_id,
            "parent_smiles": self.parent_smiles,
            "identity_policy_id": self.identity_policy_id,
            "verdict": self.verdict,
            "removed_by": self.removed_by,
            "reason_code": self.reason_code,
            "source_record_ids": list(self.source_record_ids),
            "events": [event.as_dict() for event in self.events],
            "notes": list(self.notes),
            # Serialised explicitly: cli.py's _json_dump passes no ``default``,
            # so a bare dataclass here would make ``--json`` raise TypeError on
            # exactly the path this feature exists for.
            "desalting": [report.as_dict() for report in self.desalting],
            "desalting_scan": (
                None if self.desalting_scan is None else self.desalting_scan.as_dict()
            ),
        }

    def render(self) -> str:
        return "\n".join(_render_lines(self))


def explain_molecule(
    runner: LocalRunner,
    run_id: str,
    *,
    smiles: str | None = None,
    parent_id: str | None = None,
    desalt_limit: int = _DESALT_REPLAY_LIMIT,
) -> MoleculeVerdict:
    """Say what one run did about one molecule, and why.

    Exactly one of ``smiles`` and ``parent_id`` is required.  ``smiles`` is
    resolved through the run's own identity policy; ``parent_id`` is taken as
    given, which is the escape hatch for a run whose policy cannot be recovered.
    """

    if (smiles is None) == (parent_id is None):
        raise InputError(
            "explain needs exactly one of a SMILES or a parent id",
            code="EXPLAIN_QUERY_REQUIRED",
            hint="Pass --smiles to resolve a structure, or --parent-id to skip resolution.",
        )

    state = runner.load_run(run_id)
    manifests = {
        stage.stage_id: _manifest_for(runner, state, stage.stage_id) for stage in state.stages
    }
    notes: list[str] = []

    resolved_smiles: str | None = None
    policy_id: str | None = None
    policy: IdentityPolicy | None = None
    if smiles is not None:
        registered, policy_id, policy = _resolve_parent(manifests, state, smiles, notes)
        target = registered.parent_id
        resolved_smiles = registered.parent_smiles
        query = smiles
    else:
        target = str(parent_id)
        query = target

    sources = _source_records(runner, manifests, state, target)
    entities = {target, *sources}

    events: list[StageEvent] = []
    removed_by: str | None = None
    reason_code: str | None = None
    seen_anywhere = False
    for stage in state.stages:
        event = _stage_event(runner, stage.stage_id, manifests.get(stage.stage_id), entities)
        events.append(event)
        if event.present:
            seen_anywhere = True
        if event.outcome == "REJECT" and removed_by is None:
            removed_by = event.stage_id
            reason_code = event.reason_code

    selected = _selection(runner, manifests, state, target)
    verdict = _verdict(
        seen_anywhere=seen_anywhere,
        removed_by=removed_by,
        selected=selected,
        sources=sources,
    )
    # Before the note below, not after: a hit here changes the answer, and the
    # ABSENT note would otherwise contradict the verdict it is attached to.
    desalting, scan = _desalting(
        runner,
        manifests,
        state,
        policy=policy,
        parent_id=target,
        parent_smiles=resolved_smiles,
        sources=sources,
        provisional=verdict,
        limit=desalt_limit,
    )
    if verdict == VERDICT_ABSENT and any(
        fragment.is_query for report in desalting for fragment in report.discarded
    ):
        verdict = VERDICT_DESALTED
    if verdict == VERDICT_ABSENT and smiles is not None:
        notes.append(
            "No stage in this run holds that parent id. Either the molecule was not "
            "in the library, or it was written differently there — the identity "
            "policy decides what counts as the same molecule."
        )
        if scan is not None and scan.exhaustive and scan.split_rows:
            notes.append(
                f"Every one of the {scan.split_rows} library row(s) this run split "
                "was checked, and none of them discarded this structure."
            )
        elif scan is not None and scan.split_rows:
            notes.append(
                f"{scan.replayed} of the {scan.split_rows} library row(s) this run "
                "split were re-checked; raise --desalt-limit to look at more."
            )
    return MoleculeVerdict(
        run_id=run_id,
        status=str(state.status),
        query=query,
        parent_id=target,
        parent_smiles=resolved_smiles,
        identity_policy_id=policy_id,
        verdict=verdict,
        removed_by=removed_by,
        reason_code=reason_code,
        events=tuple(events),
        source_record_ids=tuple(sorted(sources)),
        notes=tuple(notes),
        desalting=desalting,
        desalting_scan=scan,
    )


def _raw_records(
    runner: LocalRunner,
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    wanted: set[str],
) -> dict[str, dict[str, Any]]:
    """The raw rows behind ``wanted`` source records, whichever contract holds them."""

    found: dict[str, dict[str, Any]] = {}
    if not wanted:
        return found
    for contract, columns in (
        (RAW_MOLECULE_V1.id, ["source_record_id", "raw_smiles", "raw_molblock"]),
        (RAW_MOLECULE_V2.id, ["source_record_id", "raw_format", "raw_structure"]),
    ):
        for stage in state.stages:
            ref = _ref_with_contract(manifests.get(stage.stage_id), contract)
            if ref is None:
                continue
            try:
                rows = _scan(
                    runner,
                    ref,
                    columns=columns,
                    column="source_record_id",
                    values=wanted,
                )
            except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
                continue
            for row in rows:
                found.setdefault(str(row["source_record_id"]), row)
    return found


def _replay_record(
    row: dict[str, Any],
    policy: IdentityPolicy,
    kept_parent_id: str | None,
    *,
    query_parent_id: str,
) -> DesaltingReport:
    """Re-split one library row and say which of its components were dropped.

    Compared by ``parent_id`` rather than by SMILES.  Registration rewrites a
    structure -- metformin's organic component canonicalises to
    ``CN(C)C(=N)NC(=N)N`` while the parent it registered as is the tautomer
    ``CN(C)C(N)=NC(=N)N`` -- so a string comparison would report the fragment the
    run *kept* as discarded.

    A replay that cannot find the run's own choice among the components it
    derived is reported as ``DISPUTED`` with no discard list.  Saying what a run
    threw away is only credible from a replay that reproduced what it kept.
    """

    source_record_id = str(row["source_record_id"])
    kwargs: dict[str, Any] = {}
    if row.get("raw_format") is not None or row.get("raw_structure") is not None:
        kwargs = {"raw_format": row.get("raw_format"), "raw_structure": row.get("raw_structure")}
    else:
        kwargs = {"raw_smiles": row.get("raw_smiles"), "raw_molblock": row.get("raw_molblock")}
    try:
        components = record_components(policy=policy, **kwargs)
    except (StandardizationFailure, ValueError) as error:
        return DesaltingReport(
            source_record_id=source_record_id,
            status=_DESALT_DISPUTED,
            kept_parent_id=kept_parent_id,
            note=f"this row could not be re-split: {type(error).__name__}",
        )

    discarded: list[DiscardedFragment] = []
    matched_kept = False
    for smiles, heavy in zip(components.smiles, components.heavy_atoms, strict=True):
        parent_id: str | None = None
        unregistrable: str | None = None
        try:
            parent_id = standardize_parent(raw_smiles=smiles, policy=policy).parent_id
        except (StandardizationFailure, ValueError) as error:
            unregistrable = getattr(error, "reason_code", type(error).__name__)
        if parent_id is not None and parent_id == kept_parent_id:
            # The component the run kept, and every component equivalent to it:
            # ``CC.CC`` registers both halves to one parent, and reporting either
            # as discarded would describe a loss that did not happen.
            matched_kept = True
            continue
        discarded.append(
            DiscardedFragment(
                smiles=smiles,
                heavy_atoms=heavy,
                parent_id=parent_id,
                unregistrable=unregistrable,
                is_query=parent_id == query_parent_id,
            )
        )
    if kept_parent_id is not None and not matched_kept:
        return DesaltingReport(
            source_record_id=source_record_id,
            status=_DESALT_DISPUTED,
            kept_parent_id=kept_parent_id,
            note=(
                "re-splitting this row did not reproduce the parent the run "
                "registered, so what it discarded cannot be stated"
            ),
        )
    return DesaltingReport(
        source_record_id=source_record_id,
        status=_DESALT_AGREED,
        kept_parent_id=kept_parent_id,
        discarded=tuple(discarded),
    )


def _split_rows(
    runner: LocalRunner,
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    *,
    limit: int,
) -> tuple[list[str], int]:
    """Source records this run recorded splitting, and how many there were.

    The run already names them: the standardizer writes ``FRAGMENTS_REMOVED``
    against the source record whenever it picks one component out of several.
    That set is the population of interest and nothing else has to be considered.
    """

    manifest = _standardize_manifest(manifests, state)
    ref = _ref_with_contract(manifest, DECISION_V1.id)
    if ref is None:
        return [], 0
    total = 0
    kept: list[str] = []
    expression = ds.field("reason_code") == _FRAGMENTS_REMOVED
    try:
        for path in _dataset_paths(runner, ref):
            if not path.exists():
                continue
            for batch in iter_parquet_batches(
                path,
                columns=["entity_id", "entity_kind", "reason_code"],
                filter_expression=expression,
                batch_size=_BATCH_SIZE,
            ):
                for row in batch.to_pylist():
                    if row["entity_kind"] != "SOURCE_RECORD":
                        continue
                    total += 1
                    if len(kept) < limit:
                        kept.append(str(row["entity_id"]))
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
        return kept, total
    return kept, total


def _mapped_parents(
    runner: LocalRunner,
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    records: set[str],
) -> dict[str, str]:
    """Which parent each of these source records registered as."""

    manifest = _standardize_manifest(manifests, state)
    ref = _ref_with_contract(manifest, PARENT_SOURCE_MAP_V1.id)
    if ref is None or not records:
        return {}
    try:
        rows = _scan(
            runner,
            ref,
            columns=["source_record_id", "parent_id"],
            column="source_record_id",
            values=records,
        )
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
        return {}
    return {str(row["source_record_id"]): str(row["parent_id"]) for row in rows}


def _heavy_atoms(row: dict[str, Any]) -> Counter[str] | None:
    """Element counts of a raw row, parsed but not sanitised.

    The cheap screen: every step of the standardisation prefix preserves heavy
    atoms, so a component's elements are a sub-multiset of its record's.  A
    record whose elements do not contain the query's cannot possibly have
    discarded it, and skipping it costs 0.043 ms instead of 6.1.
    """

    from rdkit import Chem, rdBase

    text = row.get("raw_smiles") or row.get("raw_structure") or row.get("raw_molblock")
    if not isinstance(text, str) or not text:
        return None
    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(text, sanitize=False)
    if molecule is None:
        return None
    return Counter(atom.GetSymbol() for atom in molecule.GetAtoms() if atom.GetAtomicNum() > 1)


def _desalting(
    runner: LocalRunner,
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    *,
    policy: IdentityPolicy | None,
    parent_id: str,
    parent_smiles: str | None,
    sources: set[str],
    provisional: str,
    limit: int,
) -> tuple[tuple[DesaltingReport, ...], DesaltingScan | None]:
    """Two searches over the rows this run split, sharing one replay budget.

    *Phase A* answers "what else did my molecule's own library row discard".
    Its domain is the rows that registered as this parent, which the caller
    already has.

    *Phase B* answers the question that motivates the whole feature: **my
    molecule is not here, was it thrown away as somebody's counter-ion?**  That
    one cannot start from the query's parent id, because a discarded fragment
    never reaches ``parent_source_map`` -- so it starts from the other end, at
    the rows the run recorded splitting, and asks whether any of them discarded
    this structure.  Measured: de-salting keeps pamoate (29 heavy atoms) over
    pyrantel (14), and before this the answer to "where did my pyrantel go" was
    ABSENT, which reads as "it was never in your library".
    """

    if limit <= 0 or policy is None:
        return (), None

    reports: list[DesaltingReport] = []
    budget = limit

    # Phase A: only the queried molecule's own rows, and only those the run split.
    if sources:
        split_here, _total = _split_rows(runner, manifests, state, limit=limit)
        mine = sorted(set(split_here) & sources)[:budget]
        if mine:
            raw = _raw_records(runner, manifests, state, set(mine))
            mapped = _mapped_parents(runner, manifests, state, set(mine))
            for record in mine:
                row = raw.get(record)
                if row is None:
                    continue
                budget -= 1
                reports.append(
                    _replay_record(
                        row, policy, mapped.get(record), query_parent_id=parent_id
                    )
                )

    if provisional != VERDICT_ABSENT or budget <= 0:
        return tuple(reports), None

    # Phase B: the rows this run split, screened cheaply before any replay.
    candidates, split_total = _split_rows(
        runner, manifests, state, limit=budget * _DESALT_SCREEN_RATIO
    )
    candidates = [record for record in candidates if record not in sources]
    if not candidates:
        return tuple(reports), DesaltingScan(split_total, 0, 0, 0, False)

    raw = _raw_records(runner, manifests, state, set(candidates))
    mapped = _mapped_parents(runner, manifests, state, set(candidates))
    query_atoms = _heavy_atoms({"raw_smiles": parent_smiles})
    screened = replayed = disputed = 0
    truncated = False
    for record in candidates:
        row = raw.get(record)
        if row is None:
            continue
        screened += 1
        record_atoms = _heavy_atoms(row)
        if query_atoms is not None and record_atoms is not None:
            missing = query_atoms - record_atoms
            if missing:
                continue
        if budget <= 0:
            truncated = True
            break
        budget -= 1
        replayed += 1
        report = _replay_record(row, policy, mapped.get(record), query_parent_id=parent_id)
        if report.status == _DESALT_DISPUTED:
            disputed += 1
            continue
        if any(fragment.is_query for fragment in report.discarded):
            reports.append(report)
            break
    scan = DesaltingScan(
        split_rows=split_total,
        screened=screened,
        replayed=replayed,
        disputed=disputed,
        truncated=truncated or len(candidates) < split_total,
    )
    return tuple(reports), scan


def _verdict(
    *,
    seen_anywhere: bool,
    removed_by: str | None,
    selected: bool | None,
    sources: set[str],
) -> str:
    if removed_by is not None:
        return VERDICT_REJECTED
    if not seen_anywhere and not sources:
        return VERDICT_ABSENT
    if selected is True:
        return VERDICT_SELECTED
    if selected is False:
        return VERDICT_NOT_SELECTED
    return VERDICT_SURVIVED


def _manifest_for(
    runner: LocalRunner, state: RunState, stage_id: str
) -> ArtifactManifest | None:
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


def _ref_with_contract(
    manifest: ArtifactManifest | None, contract_id: str
) -> ArtifactDatasetRef | None:
    """Locate a port by the contract it carries rather than by its name."""

    if manifest is None:
        return None
    for output in manifest.outputs:
        if output.contract_id == contract_id:
            return manifest.dataset_ref(output.port)
    return None


def _dataset_paths(runner: LocalRunner, ref: ArtifactDatasetRef) -> tuple[Path, ...]:
    root = runner.store.resolve_dataset(ref, verify=False)
    return tuple(
        root.joinpath(*PurePosixPath(relative).parts) for relative in ref.file_paths
    )


def _scan(
    runner: LocalRunner,
    ref: ArtifactDatasetRef,
    *,
    columns: list[str],
    column: str,
    values: set[str],
) -> list[dict[str, Any]]:
    """Rows of one dataset whose ``column`` is in ``values``.

    Pushed into the Arrow scanner rather than filtered in Python: these tables
    are the whole population, and this command is about one molecule in it.
    """

    rows: list[dict[str, Any]] = []
    expression = ds.field(column).isin(sorted(values))
    for path in _dataset_paths(runner, ref):
        if not path.exists():
            continue
        for batch in iter_parquet_batches(
            path, columns=columns, filter_expression=expression, batch_size=_BATCH_SIZE
        ):
            rows.extend(batch.to_pylist())
    return rows


def _standardize_manifest(
    manifests: dict[str, ArtifactManifest | None], state: RunState
) -> ArtifactManifest | None:
    """The manifest of the stage that registered parents.

    Found by what it declares rather than by plugin name: the standardizer is
    the only stage that publishes ``parent/v1`` *and* ``parent_source_map/v1``,
    because mapping source records onto parents is what registration is.
    """

    for stage in state.stages:
        manifest = manifests.get(stage.stage_id)
        if manifest is None:
            continue
        contracts = {output.contract_id for output in manifest.outputs}
        if PARENT_V1.id in contracts and PARENT_SOURCE_MAP_V1.id in contracts:
            return manifest
    return None


def _resolve_parent(
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    smiles: str,
    notes: list[str],
) -> tuple[Any, str, IdentityPolicy]:
    """Recompute the parent id under the policy this run actually applied."""

    manifest = _standardize_manifest(manifests, state)
    recorded = {}
    if manifest is not None:
        response = manifest.metadata.get("response_metadata")
        if isinstance(response, dict):
            recorded = response
    raw_policy = recorded.get("identity_policy")
    if not isinstance(raw_policy, dict):
        raise PipelineError(
            "this run does not record the identity policy it registered parents under",
            code="EXPLAIN_IDENTITY_UNAVAILABLE",
            hint=(
                "Without it a parent id cannot be recomputed, and guessing would "
                "answer the wrong question confidently. Pass --parent-id instead."
            ),
            context={"run_id": state.run_id},
        )
    try:
        policy = IdentityPolicy.model_validate(raw_policy)
    except Exception as error:  # pragma: no cover - defensive
        raise PipelineError(
            "the identity policy recorded by this run could not be read",
            code="EXPLAIN_IDENTITY_UNAVAILABLE",
            hint="Pass --parent-id to skip resolution.",
            context={"run_id": state.run_id, "error": str(error)},
        ) from error

    policy_id = identity_policy_id(policy)
    stated = recorded.get("identity_policy_id")
    if isinstance(stated, str) and stated != policy_id:
        raise PipelineError(
            "the recorded identity policy does not hash to the recorded policy id",
            code="EXPLAIN_IDENTITY_MISMATCH",
            hint=(
                "The run's own record is inconsistent, so any parent id computed "
                "from it would be a guess. Pass --parent-id instead."
            ),
            context={"run_id": state.run_id, "recorded": stated, "recomputed": policy_id},
        )
    recorded_rdkit = recorded.get("rdkit_version")
    if isinstance(recorded_rdkit, str):
        from rdkit import rdBase

        if recorded_rdkit != rdBase.rdkitVersion:
            notes.append(
                f"This run registered parents with RDKit {recorded_rdkit}; this process "
                f"has {rdBase.rdkitVersion}. A parent id is a function of the toolkit, "
                "so a mismatch can make a molecule that is present look absent."
            )
    try:
        registered = standardize_parent(raw_smiles=smiles, policy=policy)
    except Exception as error:
        raise InputError(
            f"that structure could not be standardized: {error}",
            code="EXPLAIN_SMILES_INVALID",
            hint="Check the SMILES, or pass --parent-id if you already know the id.",
            context={"smiles": smiles},
        ) from error
    return registered, policy_id, policy


def _source_records(
    runner: LocalRunner,
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    parent_id: str,
) -> set[str]:
    """Library rows that registered as this parent.

    Needed because the standardizer decides about ``SOURCE_RECORD`` entities: a
    ``FRAGMENTS_REMOVED`` or ``UNDEFINED_STEREO`` warning is keyed on the row the
    user supplied, not on the parent it became.  Without this join those two
    warnings -- the two most likely to explain a surprising structure -- would
    never reach the molecule they are about.
    """

    manifest = _standardize_manifest(manifests, state)
    ref = _ref_with_contract(manifest, PARENT_SOURCE_MAP_V1.id)
    if ref is None:
        return set()
    try:
        rows = _scan(
            runner,
            ref,
            columns=["source_record_id", "parent_id"],
            column="parent_id",
            values={parent_id},
        )
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
        return set()
    return {str(row["source_record_id"]) for row in rows}


def _selection(
    runner: LocalRunner,
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    parent_id: str,
) -> bool | None:
    """Whether the final budget kept it, or ``None`` if nothing selected."""

    for stage in reversed(state.stages):
        ref = _ref_with_contract(manifests.get(stage.stage_id), SELECTION_DECISION_V1.id)
        if ref is None:
            continue
        try:
            rows = _scan(
                runner,
                ref,
                columns=["parent_id", "selected"],
                column="parent_id",
                values={parent_id},
            )
        except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
            return None
        if rows:
            return bool(rows[0]["selected"])
        return False
    return None


def _stage_event(
    runner: LocalRunner,
    stage_id: str,
    manifest: ArtifactManifest | None,
    entities: set[str],
) -> StageEvent:
    """Presence and verdict for one stage, from whichever ports it published."""

    if manifest is None:
        return StageEvent(stage_id, None, None, None, None, None)

    present: bool | None = None
    parent_ref = _ref_with_contract(manifest, PARENT_V1.id)
    unavailable: str | None = None
    if parent_ref is not None:
        try:
            rows = _scan(
                runner,
                parent_ref,
                columns=["parent_id"],
                column="parent_id",
                values=entities,
            )
            present = bool(rows)
        except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError) as error:
            unavailable = f"{type(error).__name__}: {error}"

    outcome = reason = rule = detail = None
    decision_ref = _ref_with_contract(manifest, DECISION_V1.id)
    if decision_ref is not None and unavailable is None:
        try:
            rows = _scan(
                runner,
                decision_ref,
                columns=["entity_id", "outcome", "reason_code", "rule_id", "detail"],
                column="entity_id",
                values=entities,
            )
        except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError) as error:
            unavailable = f"{type(error).__name__}: {error}"
            rows = []
        # A REJECT is the answer whenever one exists; a stage can also emit a
        # PASS row and one or more WARNs for the same entity, and the rejection
        # is the only one that decided anything.
        chosen = next((row for row in rows if row["outcome"] == "REJECT"), None)
        if chosen is None:
            chosen = next((row for row in rows if row["outcome"] == "WARN"), None)
        if chosen is None and rows:
            chosen = rows[0]
        if chosen is not None:
            outcome = str(chosen["outcome"])
            reason = str(chosen["reason_code"])
            rule = None if chosen["rule_id"] is None else str(chosen["rule_id"])
            detail = None if chosen["detail"] is None else str(chosen["detail"])
    return StageEvent(stage_id, present, outcome, reason, rule, detail, unavailable)


_VERDICT_PROSE = {
    VERDICT_ABSENT: "not found in this run",
    VERDICT_REJECTED: "removed",
    VERDICT_SELECTED: "in the shortlist",
    VERDICT_NOT_SELECTED: "survived the funnel but the shortlist budget did not keep it",
    VERDICT_SURVIVED: "survived every stage that ran",
    VERDICT_DESALTED: (
        "was in the library, inside a multi-component record, and de-salting kept "
        "a different fragment"
    ),
}


#: Characters of a decision ``detail`` shown in the terminal.  A passing gate
#: writes its whole property panel as JSON there, which is worth keeping in
#: ``--json`` and is not worth three wrapped lines per stage on a screen.
_DETAIL_WIDTH = 96

#: Keys a JSON ``detail`` may carry that are already written for a person.
#: Ordered: the first one present wins.  ``message`` is what the property and
#: drug-likeness gates write; the alert adapters write no message and instead
#: name the catalogue and the pattern that matched.
_DETAIL_KEYS = ("message", "alert", "family", "reason")


def _detail(text: str) -> str:
    """One readable line from a decision's detail, whatever shape it is in.

    Measured across the tiers of the shipped cascade, ``detail`` comes in two
    shapes and truncation only works on one of them.  The physicochemical and
    ring gates write a sentence -- ``mw=129.167 is below 150.0`` at 25
    characters.  The drug-likeness gates write their whole metric panel as JSON:
    famotidine's ``DRUG_LIKENESS_QED_POLICY_FAIL`` detail is 758 characters and
    sertraline's is 738, so a cut at 96 lands inside a JSON object and produces a
    line that is neither valid JSON nor a sentence.

    But those JSON objects already contain the sentence -- famotidine's
    ``message`` reads ``weighted QED=0.286612438474 is below configured
    minimum=0.3`` -- so the fix is to look for it rather than to cut harder.  The
    alert adapters write no ``message`` and name the catalogue and the pattern
    instead, which is the same information in their own vocabulary.

    The full detail is never lost: ``--json`` carries it verbatim.
    """

    flat = " ".join(text.split())
    stripped = flat.lstrip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(flat)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in _DETAIL_KEYS:
                value = payload.get(key)
                if isinstance(value, str) and value:
                    catalog = payload.get("catalog")
                    if key == "alert" and isinstance(catalog, str) and catalog:
                        return f"{catalog}: {value}"
                    return value if len(value) <= _DETAIL_WIDTH else f"{value[:_DETAIL_WIDTH]}…"
    return flat if len(flat) <= _DETAIL_WIDTH else f"{flat[:_DETAIL_WIDTH]}…"


def _render_lines(verdict: MoleculeVerdict) -> list[str]:
    lines = [f"{verdict.query} in run {verdict.run_id} ({verdict.status})"]
    if verdict.parent_id:
        lines.append(f"  parent  {verdict.parent_id}")
    if verdict.parent_smiles and verdict.parent_smiles != verdict.query:
        # The single most useful line this command prints when it fires: the
        # structure the run reasoned about is not the one that was typed.
        lines.append(f"  registered as  {verdict.parent_smiles}")
    headline = _VERDICT_PROSE.get(verdict.verdict, verdict.verdict)
    if verdict.verdict == VERDICT_REJECTED:
        lines.append(f"  verdict  {headline} by {verdict.removed_by} ({verdict.reason_code})")
    else:
        lines.append(f"  verdict  {headline}")

    # A molecule that was never registered has an "absent" row for every stage,
    # and a wall of them says nothing the verdict has not already said. The
    # trail also stops at the stage that removed it: REJECT is terminal, so
    # every row below is a restatement of the row above.
    if verdict.verdict not in (VERDICT_ABSENT, VERDICT_DESALTED):
        lines.append("")
        for event in verdict.events:
            if event.unavailable is not None:
                lines.append(f"  {event.stage_id:<28}unreadable: {event.unavailable}")
                continue
            if event.present is None and event.outcome is None:
                continue
            mark = {True: "kept", False: "gone", None: "    "}[event.present]
            note = ""
            if event.outcome is not None:
                note = f"  {event.outcome} {event.reason_code}"
                if event.detail:
                    note = f"{note} — {_detail(event.detail)}"
            lines.append(f"  {event.stage_id:<28}{mark}{note}")
            if event.stage_id == verdict.removed_by:
                break
    for report in verdict.desalting:
        lines.append("")
        if report.status == _DESALT_DISPUTED:
            lines.append(f"  library row {_short(report.source_record_id)}: {report.note}")
            continue
        if not report.discarded:
            continue
        lines.append(f"  library row {_short(report.source_record_id)} was split; de-salting")
        lines.append("  kept one fragment and discarded:")
        for fragment in report.discarded:
            marker = "  <- the molecule you asked about" if fragment.is_query else ""
            reason = f" ({fragment.unregistrable})" if fragment.unregistrable else ""
            lines.append(
                f"    {fragment.smiles}  {fragment.heavy_atoms} heavy atoms{reason}{marker}"
            )
    for note in verdict.notes:
        lines.append("")
        lines.append(f"  note: {note}")
    return lines


def _short(entity_id: str) -> str:
    _, separator, digest = entity_id.rpartition(":")
    return f"{entity_id[: len(entity_id) - len(digest)]}{digest[:12]}…" if separator else entity_id


__all__ = [
    "VERDICT_ABSENT",
    "VERDICT_DESALTED",
    "VERDICT_NOT_SELECTED",
    "VERDICT_REJECTED",
    "VERDICT_SELECTED",
    "VERDICT_SURVIVED",
    "DesaltingReport",
    "DesaltingScan",
    "DiscardedFragment",
    "MoleculeVerdict",
    "StageEvent",
    "explain_molecule",
]
