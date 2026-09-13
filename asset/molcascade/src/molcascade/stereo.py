"""Report which molecules had a stereocentre chosen for them by 3D embedding.

``ligand_prep`` embeds with ``enforceChirality=True``, and that flag does less
than its name suggests: it constrains the centres a molecule's SMILES has
already assigned, and says nothing about the ones it has not.  An unassigned
centre is settled by distance geometry, seeded from
``sha256(seed + parent_id)`` -- deterministic, reproducible, and nobody's
chemistry.  The molecule that reaches the docking engine is one specific
stereoisomer; the ``parent_smiles`` it is filed under names a set of them.

That matters here more than it would in most pipelines, for two reasons the
codebase states itself.  The structure-quality tier's docstring says undefined
stereochemistry "in a generated library describes almost everything", so this is
the common case rather than an edge one.  And the shipped docking tier runs
Uni-Dock beside KarmaDock, which does not read the shared conformer table at all
-- it embeds its own geometry from ``parent_smiles``.  Two independent
embeddings of a molecule with an open centre can settle it in opposite
directions, and then the tier's consensus, whose whole purpose is to make
disagreement visible, is comparing two different stereoisomers without saying
so.

No row anywhere records this.  ``ligand_conformer/v1`` carries the molblock and
``docking_score/v1`` carries the pose, and the configuration is *in* those
coordinates -- it has simply never been read back out and compared against the
name the molecule is filed under.  This module reads it back out.

What it does not do is judge.  A centre assigned by embedding is not an error:
it is a fact about what was docked, and whether it matters depends on whether
the target discriminates.  So this reports and counts, and leaves the decision
where the rest of the project leaves such decisions -- with the operator.

Like :mod:`molcascade.decisions` and :mod:`molcascade.explain`, this runs after
the fact over committed artifacts: no contract, stage configuration or cache key
changes, and a run that failed partway is read as far as it got.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from molcascade.artifacts import (
    ArtifactDatasetRef,
    ArtifactIntegrityError,
    ArtifactManifest,
    ArtifactNotFoundError,
)
from molcascade.contracts import DOCKING_SCORE_V1, LIGAND_CONFORMER_V1, PARENT_V1
from molcascade.io.parquet import iter_parquet_batches
from molcascade.runtime import LocalRunner, RunState

#: The embedded structure names stereochemistry the parent does not.  This is
#: the finding the module exists for.
STEREO_ASSIGNED = "ASSIGNED_BY_EMBEDDING"
#: The two agree: every centre the geometry names, the parent named too.
STEREO_AGREES = "AGREES"
#: The geometry names a *different* configuration at a centre the parent had
#: already assigned.  ``enforceChirality=True`` should make this impossible for
#: the conformer table, so it is reported loudly rather than folded into the
#: count above -- if it ever fires, something upstream inverted a centre.
STEREO_CONTRADICTED = "CONTRADICTED"
#: The molblock or the parent SMILES would not parse.  Counted, never guessed.
STEREO_UNREADABLE = "UNREADABLE"

_BATCH_SIZE = 4_096


@dataclass(frozen=True, slots=True)
class StereoReading:
    """One structure, compared against the name it is filed under."""

    parent_id: str
    stage_id: str
    source: str
    index: int
    status: str
    parent_smiles: str | None
    assigned_smiles: str | None
    #: Stereo descriptors named by the geometry minus those named by the parent.
    added_centres: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "parent_id": self.parent_id,
            "stage_id": self.stage_id,
            "source": self.source,
            "index": self.index,
            "status": self.status,
            "parent_smiles": self.parent_smiles,
            "assigned_smiles": self.assigned_smiles,
            "added_centres": self.added_centres,
        }


@dataclass(frozen=True, slots=True)
class StereoSummary:
    """What a whole run's 3D structures say about their own stereochemistry."""

    run_id: str
    status: str
    datasets_read: int
    structures_read: int
    counts: dict[str, int]
    #: One entry per structure whose status is not ``AGREES``.
    findings: tuple[StereoReading, ...]
    notes: tuple[str, ...] = ()

    @property
    def assigned_parents(self) -> int:
        return len(
            {f.parent_id for f in self.findings if f.status == STEREO_ASSIGNED}
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "datasets_read": self.datasets_read,
            "structures_read": self.structures_read,
            "assigned_parents": self.assigned_parents,
            "counts": dict(self.counts),
            "findings": [finding.as_dict() for finding in self.findings],
            "notes": list(self.notes),
        }

    def render(self) -> str:
        return "\n".join(_render_lines(self))


def reconcile_stereo(runner: LocalRunner, run_id: str) -> StereoSummary:
    """Compare every 3D structure a run committed against its parent's name."""

    state = runner.load_run(run_id)
    manifests = {
        stage.stage_id: _manifest_for(runner, state, stage.stage_id) for stage in state.stages
    }
    sources = _structure_sources(manifests, state)
    notes: list[str] = []
    if not sources:
        return StereoSummary(
            run_id=run_id,
            status=str(state.status),
            datasets_read=0,
            structures_read=0,
            counts={},
            findings=(),
            notes=(
                "This run committed no 3D geometry, so there is nothing to reconcile.",
            ),
        )

    structures = list(_iter_structures(runner, sources, notes))
    names = _parent_smiles(runner, manifests, state, {s[0] for s in structures})

    counts: dict[str, int] = {}
    findings: list[StereoReading] = []
    for parent_id, stage_id, kind, index, molblock in structures:
        reading = _compare(parent_id, stage_id, kind, index, molblock, names.get(parent_id))
        counts[reading.status] = counts.get(reading.status, 0) + 1
        if reading.status != STEREO_AGREES:
            findings.append(reading)
    if counts.get(STEREO_CONTRADICTED):
        notes.append(
            "A structure contradicts a stereocentre its parent had already assigned. "
            "Embedding enforces specified chirality, so this points at something "
            "upstream of the geometry rather than at the geometry."
        )
    return StereoSummary(
        run_id=run_id,
        status=str(state.status),
        datasets_read=len(sources),
        structures_read=len(structures),
        counts=counts,
        findings=tuple(findings),
        notes=tuple(notes),
    )


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


def _structure_sources(
    manifests: dict[str, ArtifactManifest | None], state: RunState
) -> list[tuple[str, str, ArtifactDatasetRef]]:
    """Every committed dataset that carries 3D coordinates, in stage order."""

    found: list[tuple[str, str, ArtifactDatasetRef]] = []
    for stage in state.stages:
        manifest = manifests.get(stage.stage_id)
        if manifest is None:
            continue
        for output in manifest.outputs:
            if output.contract_id == LIGAND_CONFORMER_V1.id:
                found.append((stage.stage_id, "conformer", manifest.dataset_ref(output.port)))
            elif output.contract_id == DOCKING_SCORE_V1.id:
                found.append((stage.stage_id, "pose", manifest.dataset_ref(output.port)))
    return found


def _dataset_paths(runner: LocalRunner, ref: ArtifactDatasetRef) -> tuple[Path, ...]:
    root = runner.store.resolve_dataset(ref, verify=False)
    return tuple(
        root.joinpath(*PurePosixPath(relative).parts) for relative in ref.file_paths
    )


def _iter_structures(
    runner: LocalRunner,
    sources: list[tuple[str, str, ArtifactDatasetRef]],
    notes: list[str],
) -> Iterator[tuple[str, str, str, int, str]]:
    """``(parent_id, stage_id, kind, index, molblock)`` for every 3D structure.

    Poses are read at ``pose_rank == 0`` only, matching what the trace writes to
    SDF: an engine can return several ranks, and the top one is the geometry the
    tier's number is about.
    """

    for stage_id, kind, ref in sources:
        columns = (
            ["parent_id", "conformer_index", "molblock"]
            if kind == "conformer"
            else ["parent_id", "pose_rank", "pose_molblock"]
        )
        try:
            paths = _dataset_paths(runner, ref)
        except (ArtifactIntegrityError, ArtifactNotFoundError, OSError) as error:
            notes.append(f"{stage_id}: unreadable ({type(error).__name__})")
            continue
        for path in paths:
            if not path.exists():
                continue
            try:
                batches = iter_parquet_batches(
                    path, columns=columns, batch_size=_BATCH_SIZE
                )
                for batch in batches:
                    rows = batch.to_pylist()
                    for row in rows:
                        if kind == "conformer":
                            molblock = row.get("molblock")
                            index = int(row.get("conformer_index") or 0)
                        else:
                            index = int(row.get("pose_rank") or 0)
                            if index != 0:
                                continue
                            molblock = row.get("pose_molblock")
                        if not molblock:
                            continue
                        yield (str(row["parent_id"]), stage_id, kind, index, str(molblock))
            except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
                notes.append(f"{stage_id}: stopped early on an unreadable file")
                continue


def _parent_smiles(
    runner: LocalRunner,
    manifests: dict[str, ArtifactManifest | None],
    state: RunState,
    wanted: set[str],
) -> dict[str, str]:
    """``parent_id`` to the SMILES it is filed under, for the wanted subset."""

    names: dict[str, str] = {}
    if not wanted:
        return names
    for stage in state.stages:
        manifest = manifests.get(stage.stage_id)
        if manifest is None:
            continue
        for output in manifest.outputs:
            if output.contract_id != PARENT_V1.id:
                continue
            try:
                paths = _dataset_paths(runner, manifest.dataset_ref(output.port))
            except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
                continue
            for path in paths:
                if not path.exists():
                    continue
                try:
                    for batch in iter_parquet_batches(
                        path,
                        columns=["parent_id", "parent_smiles"],
                        batch_size=_BATCH_SIZE,
                    ):
                        for row in batch.to_pylist():
                            parent = str(row["parent_id"])
                            if parent in wanted and parent not in names:
                                names[parent] = str(row["parent_smiles"])
                except (OSError, ValueError):
                    continue
            if len(names) == len(wanted):
                return names
    return names


def _stereo_descriptors(mol: Any) -> int:
    """Assigned tetrahedral centres plus assigned double-bond configurations."""

    from rdkit import Chem

    centres = sum(
        1
        for atom in mol.GetAtoms()
        if atom.GetChiralTag() != Chem.ChiralType.CHI_UNSPECIFIED
    )
    bonds = sum(
        1 for bond in mol.GetBonds() if bond.GetStereo() != Chem.BondStereo.STEREONONE
    )
    return centres + bonds


def _compare(
    parent_id: str,
    stage_id: str,
    kind: str,
    index: int,
    molblock: str,
    parent_smiles: str | None,
) -> StereoReading:
    """Re-derive stereochemistry from coordinates and compare with the name."""

    from rdkit import Chem, rdBase

    unreadable = StereoReading(
        parent_id=parent_id,
        stage_id=stage_id,
        source=kind,
        index=index,
        status=STEREO_UNREADABLE,
        parent_smiles=parent_smiles,
        assigned_smiles=None,
        added_centres=0,
    )
    if not parent_smiles:
        return unreadable
    # Scoped rather than a global DisableLog: a molblock that will not parse is
    # expected here and its warning is noise, but silencing the toolkit for the
    # rest of the process would take every other caller's warnings with it.
    with rdBase.BlockLogs():
        parent = Chem.MolFromSmiles(parent_smiles)
        if parent is None:
            return unreadable
        geometry = Chem.MolFromMolBlock(molblock, sanitize=True, removeHs=True)
        if geometry is None or geometry.GetNumConformers() == 0:
            return unreadable

        # Coordinates are the authority here, so the perception is redone from
        # them rather than trusted from whatever the writer left in the flags.
        Chem.AssignStereochemistryFrom3D(geometry)
        Chem.AssignStereochemistry(parent, cleanIt=True, force=True)

        parent_named = _stereo_descriptors(parent)
        geometry_named = _stereo_descriptors(geometry)
        assigned = Chem.MolToSmiles(geometry)
        named = Chem.MolToSmiles(parent)

    if geometry_named > parent_named:
        status = STEREO_ASSIGNED
    elif assigned != named and geometry_named == parent_named and parent_named > 0:
        # Same number of descriptors, different structure: a centre the parent
        # had already named came back the other way round.
        status = STEREO_CONTRADICTED
    else:
        status = STEREO_AGREES
    return StereoReading(
        parent_id=parent_id,
        stage_id=stage_id,
        source=kind,
        index=index,
        status=status,
        parent_smiles=parent_smiles,
        assigned_smiles=assigned,
        added_centres=max(0, geometry_named - parent_named),
    )


def _render_lines(summary: StereoSummary) -> list[str]:
    lines = [f"Stereochemistry in run {summary.run_id} ({summary.status})"]
    if not summary.datasets_read:
        for note in summary.notes:
            lines.append(f"  {note}")
        return lines
    lines.append(
        f"  {summary.structures_read:,} structure(s) from "
        f"{summary.datasets_read} dataset(s)"
    )
    assigned = summary.counts.get(STEREO_ASSIGNED, 0)
    lines.append(
        f"  {assigned:,} structure(s) covering {summary.assigned_parents:,} molecule(s) "
        "had a configuration chosen by 3D embedding rather than named by their SMILES"
    )
    for status in (STEREO_CONTRADICTED, STEREO_UNREADABLE):
        count = summary.counts.get(status, 0)
        if count:
            lines.append(f"  {count:,} {status}")
    shown = [f for f in summary.findings if f.status == STEREO_ASSIGNED][:20]
    if shown:
        lines.append("")
        for finding in shown:
            lines.append(
                f"    {finding.parent_id[:34]:<34}"
                f"+{finding.added_centres}  {finding.assigned_smiles}"
            )
        remaining = assigned - len(shown)
        if remaining > 0:
            lines.append(f"    … and {remaining:,} more; use --json for all")
    for note in summary.notes:
        lines.append("")
        lines.append(f"  note: {note}")
    return lines


__all__ = [
    "STEREO_AGREES",
    "STEREO_ASSIGNED",
    "STEREO_CONTRADICTED",
    "STEREO_UNREADABLE",
    "StereoReading",
    "StereoSummary",
    "reconcile_stereo",
]
