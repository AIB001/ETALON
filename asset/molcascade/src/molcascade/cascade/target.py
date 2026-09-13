"""Turn one receptor and one site definition into what each engine reads.

Three engines want three different things about the same protein.  Uni-Dock
reads a PDBQT and a box; GNINA reads the PDB and the same box; KarmaDock reads
the PDB and a ligand whose atoms locate the site.  Asking the operator for all
of that would be asking them to keep three descriptions of one pocket in
agreement, and the first time they drifted apart the tier would quietly stop
being a consensus.

So the operator supplies a receptor and *one* site definition, and this module
expands it: a reference ligand or a pocket PDB becomes an explicit box, and the
box is what reaches the stage config -- so a re-run needs the numbers, not the
ligand file.  The receptor's PDBQT is derived with meeko once, cached by the
receptor's digest, and can be replaced wholesale when meeko cannot cope.

The receptor itself is repaired first, before anything is derived from it, and
that is a change of position worth stating plainly.  This module used to edit
nothing, on the grounds that a silently truncated protein is exactly the kind of
change that never shows up in a score.  The grounds were right and the policy
did not achieve them: a deposition that stops a lysine at CB makes ``meeko``
refuse to build a receptor at all, and the only documented way past that refusal
is ``meeko``'s ``--allow_bad_res``, which does not relax the check -- it deletes
the residue, backbone included.  On a 278-residue kinase that quietly removed
twelve.  Refusing to edit the file did not prevent the truncation; it only moved
it out of this module and out of the notes.

So the receptor is now repaired by :mod:`molcascade.cascade.receptor` and the
line is drawn somewhere it can actually be held: **every change is named in the
notes, and nothing absent from the model is invented.**  Waters and
co-crystallised matter are removed and side chains the crystallographer could
not resolve are rebuilt -- the residue's identity is known and its rotamer is
constrained by its neighbours -- while an unresolved loop is reported and left
unresolved, because building nine residues from a template produces coordinates
that look exactly as authoritative as measured ones and are not.  Metals stay.
Protonation and tautomers are still not guessed here.  ``--allow_bad_res`` is
still not passed, and now does not need to be.

Repair happens here rather than inside the PDBQT derivation because the receptor
has four consumers and not one: Uni-Dock reads the PDBQT, GNINA is handed the
PDB directly, KarmaDock copies it, and PoseBusters computes its receptor field
from it.  Fixing only the PDBQT would leave three of the four docking against a
protein with holes in it -- one pocket, two descriptions, and a consensus
between them that means nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from molcascade.backends.preflight import TARGET_SETTING_FLAGS
from molcascade.cascade.models import (
    BoxConfig,
    CascadeConfig,
    ReceptorPreparation,
    TargetConfig,
)
from molcascade.cascade.receptor import prepare_receptor_pdb
from molcascade.errors import ConfigError
from molcascade.plugins.registry import PluginRegistry

#: Added to each side of the reference ligand's extent, the convention AutoDock
#: Vina's ``--autobox_ligand`` uses and the one every downstream engine's
#: documentation is written against.  Enough room for a ligand a little larger
#: than the reference without turning a pocket study into blind docking.
BOX_PADDING_ANGSTROM = 4.0

#: The same ceiling :class:`BoxConfig` enforces, checked here so an oversized
#: pocket file produces a sentence about the pocket file rather than a Pydantic
#: validation report about a field the operator never typed.
_MAX_BOX_EDGE_ANGSTROM = 200.0

#: A structure file, not a trajectory.  Matches the docking adapters' own limit.
_MAX_STRUCTURE_BYTES = 256 * 1024 * 1024

#: Receptor preparation is a one-off on a single structure.  Ten minutes is
#: generous for meeko and short enough that a hang is reported rather than slept
#: through.
_PREPARE_TIMEOUT_SECONDS = 600.0

#: How much of meeko's account of a failure travels in the error.  Enough for
#: its summary and a readable length of the trace behind it, bounded so a
#: pathological structure cannot put a megabyte of debug output in an audit log.
_MEEKO_DETAIL_BUDGET = 4000

_LIGAND_SUFFIXES = frozenset({".sdf", ".mol", ".mol2", ".pdb"})


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    """A target with every derivation already performed and recorded.

    ``notes`` is the part that matters for reproducibility: it says which
    numbers were computed rather than typed, and from what.  A run whose box
    came from a ligand file and one whose box was typed are the same experiment
    only if the numbers match, and this is where a reader can check.
    """

    target: TargetConfig
    notes: tuple[str, ...] = ()


def parse_box(text: str) -> BoxConfig:
    """Read ``--box cx,cy,cz,sx,sy,sz``.

    Six numbers, no defaults.  Accepting three and inventing an edge length
    would be guessing at the size of someone else's binding site.
    """

    parts = [piece.strip() for piece in text.split(",")]
    if len(parts) != 6 or any(not piece for piece in parts):
        raise ConfigError(
            "--box takes exactly six comma-separated numbers",
            code="DOCKING_BOX_MALFORMED",
            hint="--box cx,cy,cz,sx,sy,sz -- centre first, then the edge lengths in angstrom.",
            context={"value": text},
        )
    try:
        numbers = [float(piece) for piece in parts]
    except ValueError as error:
        raise ConfigError(
            "--box contains something that is not a number",
            code="DOCKING_BOX_MALFORMED",
            hint="--box cx,cy,cz,sx,sy,sz -- centre first, then the edge lengths in angstrom.",
            context={"value": text},
        ) from error
    return _box(numbers, source="--box")


def _box(numbers: Sequence[float], *, source: str) -> BoxConfig:
    try:
        return BoxConfig(
            center_x=numbers[0],
            center_y=numbers[1],
            center_z=numbers[2],
            size_x=numbers[3],
            size_y=numbers[4],
            size_z=numbers[5],
        )
    except ValueError as error:
        raise ConfigError(
            f"the search box from {source} is not usable",
            code="DOCKING_BOX_INVALID",
            hint=(
                "Edge lengths must be positive and no larger than "
                f"{_MAX_BOX_EDGE_ANGSTROM:g} A. A box that big is a blind-docking "
                "run rather than a pocket."
            ),
            context={"source": source, "detail": str(error)},
        ) from error


def merge_target(
    base: TargetConfig | None,
    *,
    receptor: Path | None = None,
    reference_ligand: Path | None = None,
    pocket: Path | None = None,
    box: str | None = None,
    receptor_pdbqt: Path | None = None,
    prepare_receptor: bool | None = None,
    keep_waters: bool | None = None,
    keep_heterogens: bool | None = None,
) -> TargetConfig | None:
    """Lay the run-time flags over whatever target the cascade already carried.

    A site flag *replaces* the cascade's site rather than joining it.  Passing
    ``--box`` to a cascade written against a reference ligand is an operator
    saying "this pocket, these numbers"; treating it as a second definition and
    refusing would make the override useless exactly when it is wanted.
    """

    site_flags = [
        ("--reference-ligand", reference_ligand),
        ("--pocket", pocket),
        ("--box", box),
    ]
    given = [name for name, value in site_flags if value is not None]
    if len(given) > 1:
        raise ConfigError(
            f"the binding site was named more than once ({', '.join(given)})",
            code="DOCKING_TARGET_SITE_AMBIGUOUS",
            hint=(
                "Two site definitions that disagree have no correct resolution, so "
                "exactly one is accepted. Pick the one you trust."
            ),
            context={"flags": given},
        )
    preparation = _merge_preparation(
        base.preparation if base is not None else ReceptorPreparation(),
        enabled=prepare_receptor,
        keep_waters=keep_waters,
        keep_heterogens=keep_heterogens,
    )
    nothing_given = (
        receptor is None
        and receptor_pdbqt is None
        and not given
        and preparation == ReceptorPreparation()
    )
    if base is None and nothing_given:
        return None

    receptor_path = str(receptor.expanduser().resolve()) if receptor is not None else None
    if receptor_path is None and base is not None:
        receptor_path = base.receptor_path
    if receptor_path is None:
        raise ConfigError(
            "a binding site was given but no receptor to find it in",
            code="DOCKING_TARGET_RECEPTOR_REQUIRED",
            hint="Pass '--receptor RECEPTOR.pdb' as well.",
            context={"flags": given},
        )

    fields: dict[str, object] = {
        "name": base.name if base is not None else Path(receptor_path).stem,
        "receptor_path": receptor_path,
        "receptor_sha256": base.receptor_sha256 if base is not None else None,
        "receptor_pdbqt_path": (
            str(receptor_pdbqt.expanduser().resolve())
            if receptor_pdbqt is not None
            else (base.receptor_pdbqt_path if base is not None else None)
        ),
        "pocket_pdb_path": base.pocket_pdb_path if base is not None else None,
        "preparation": preparation,
    }
    if receptor is not None and base is not None and receptor_path != base.receptor_path:
        # A different structure is a different target, and a digest pinned
        # against the old bytes would stop the run with a mismatch the operator
        # already resolved by naming a new file.
        fields["receptor_sha256"] = None

    if given:
        fields["box"] = parse_box(box) if box is not None else None
        fields["reference_ligand_path"] = (
            str(reference_ligand.expanduser().resolve()) if reference_ligand is not None else None
        )
        fields["pocket_path"] = str(pocket.expanduser().resolve()) if pocket is not None else None
    elif base is not None:
        fields["box"] = base.box
        fields["reference_ligand_path"] = base.reference_ligand_path
        fields["pocket_path"] = base.pocket_path
    else:
        raise ConfigError(
            "a receptor was given but no binding site in it",
            code="DOCKING_TARGET_SITE_REQUIRED",
            hint=(
                "Name the site exactly once: '--reference-ligand LIGAND.sdf', "
                "'--pocket POCKET.pdb', or '--box cx,cy,cz,sx,sy,sz'."
            ),
            context={"receptor_path": receptor_path},
        )
    return TargetConfig.model_validate(fields)


#: Absolute, because every engine refuses a relative path, and unmistakable,
#: because the point of these is to be reported rather than believed.  The
#: counterpart of the library placeholders in :mod:`molcascade.cascade.library`,
#: and for the same reason: a cascade is authored, validated and exported long
#: before anyone chooses a protein, and the shape of the funnel is checkable
#: either way.
_PLACEHOLDER_PATHS: Mapping[str, str] = MappingProxyType(
    {
        "receptor_path": "/target-not-yet-chosen/receptor.pdb",
        "receptor_pdbqt_path": "/target-not-yet-chosen/receptor.pdbqt",
        "reference_ligand_path": "/target-not-yet-chosen/reference-ligand.sdf",
        "pocket_pdb_path": "/target-not-yet-chosen/pocket.pdb",
    }
)

#: A cube at the origin.  Any six numbers would do -- nothing reads them -- and
#: these are the ones most obviously not measured from a protein.
_PLACEHOLDER_BOX = BoxConfig(
    center_x=0.0, center_y=0.0, center_z=0.0, size_x=20.0, size_y=20.0, size_z=20.0
)


def placeholder_target(
    cascade: CascadeConfig,
    *,
    registry: PluginRegistry,
) -> TargetConfig | None:
    """A target that makes a receptor-less cascade compilable, or ``None``.

    ``None`` when the cascade docks against nothing, so a cascade with no
    docking tier is never handed a protein it will not open.

    Fills exactly the fields the cascade's own engines declare as required, and
    the box the model insists a site be named with.  Not the optional ones: a
    placeholder ``pocket_pdb_path`` would move KarmaDock's method identity for a
    file that does not exist, and the fewer fabricated values reach a stage the
    closer the checked shape is to the one that will run.

    Only ``validate`` asks for this, via ``allow_missing_target``.  A run
    reaches :func:`~molcascade.backends.preflight.preflight_docking_target`
    instead and is refused there, by name and with the flag that fixes it.
    """

    needed = docking_requirements(cascade, registry=registry)
    if not needed:
        return None
    fields: dict[str, Any] = {
        "name": "target-not-yet-chosen",
        "box": _PLACEHOLDER_BOX,
        "receptor_path": _PLACEHOLDER_PATHS["receptor_path"],
    }
    for name, value in _PLACEHOLDER_PATHS.items():
        if name in needed:
            fields[name] = value
    return TargetConfig.model_validate(fields)


def _merge_preparation(
    base: ReceptorPreparation,
    *,
    enabled: bool | None,
    keep_waters: bool | None,
    keep_heterogens: bool | None,
) -> ReceptorPreparation:
    """Lay whichever preparation flags were passed over the cascade's own.

    ``None`` for "not passed" rather than a default, so a cascade that turned
    preparation off keeps it off through a run that says nothing about it.  The
    flags are one-way -- each turns something on or off but cannot be spelled to
    mean "whatever the cascade said" -- which is the ordinary shape for a CLI
    override and the reason absence has to be a third value.
    """

    return ReceptorPreparation(
        enabled=base.enabled if enabled is None else enabled,
        keep_waters=base.keep_waters if keep_waters is None else keep_waters,
        keep_heterogens=base.keep_heterogens if keep_heterogens is None else keep_heterogens,
    )


def docking_requirements(
    cascade: CascadeConfig,
    *,
    registry: PluginRegistry,
) -> frozenset[str]:
    """Which target settings this cascade's engines actually require.

    Derivation is not free -- preparing a receptor with meeko takes seconds and
    needs meeko installed -- so a cascade with only KarmaDock in it must not be
    made to produce a PDBQT nothing will read, and a cascade with no docking at
    all must not be made to produce anything.
    """

    needed: set[str] = set()
    for backend in _cascade_backends(cascade):
        try:
            plugin = registry.entry(backend).plugin
        except Exception:
            # An unknown plugin is the compiler's error to raise, with a better
            # message than anything this function could invent.
            continue
        fields = getattr(getattr(plugin, "config_model", None), "model_fields", None)
        if not fields or "receptor_path" not in fields:
            continue
        needed.update(
            name
            for name, field_info in fields.items()
            if name in TARGET_SETTING_FLAGS and field_info.is_required()
        )
    return frozenset(needed)


def _cascade_backends(cascade: CascadeConfig) -> Iterator[str]:
    yield cascade.ingest.backend
    if cascade.standardize is not None and cascade.standardize.enabled:
        yield cascade.standardize.backend
    for tier in cascade.tiers:
        if not tier.enabled:
            continue
        for criterion in tier.active_criteria:
            yield criterion.backend
            if criterion.gate is not None:
                yield criterion.gate.backend
    for step in cascade.finalize.steps:
        if step.enabled:
            yield step.backend


def resolve_target(
    target: TargetConfig | None,
    *,
    requirements: frozenset[str],
    workspace: Path,
) -> ResolvedTarget | None:
    """Perform every derivation the cascade's engines need, once, up front.

    Once, because deriving per shard would mean discovering a receptor
    preparation failure one worker at a time; up front, because a run that
    reaches the docking tier before finding out that meeko cannot read the
    structure has already spent the whole funnel.
    """

    if target is None:
        return None
    notes: list[str] = []
    fields: dict[str, object] = {
        "name": target.name,
        "receptor_path": target.receptor_path,
        "receptor_sha256": target.receptor_sha256,
        "box": target.box,
        "reference_ligand_path": target.reference_ligand_path,
        "pocket_path": target.pocket_path,
        "receptor_pdbqt_path": target.receptor_pdbqt_path,
        "pocket_pdb_path": target.pocket_pdb_path,
        "preparation": target.preparation,
    }

    # First, before the box is derived from anything and long before meeko is
    # asked to read the structure.  Every later step and every engine reads
    # whatever this leaves in ``receptor_path``, which is the point: repairing
    # it downstream would repair it for one consumer out of four.
    if "receptor_path" in requirements and target.preparation.enabled:
        _prepare_receptor(target, fields=fields, notes=notes, workspace=workspace)

    site_file = target.reference_ligand_path or target.pocket_path
    if "center_x" in requirements and target.box is None:
        assert site_file is not None  # TargetConfig guarantees a site in some form
        derived = box_from_structure(Path(site_file))
        fields["box"] = derived
        notes.append(
            f"box derived from {Path(site_file).name}: centre "
            f"({derived.center_x:.3f}, {derived.center_y:.3f}, {derived.center_z:.3f}) "
            f"size ({derived.size_x:.3f}, {derived.size_y:.3f}, {derived.size_z:.3f}) "
            f"with {BOX_PADDING_ANGSTROM:g} A padding"
        )
    elif target.box is not None and site_file is not None:
        # Both were written by hand.  The engines that read six numbers and the
        # one that reads a ligand would otherwise search different pockets and
        # the tier would report their disagreement as a consensus.
        _require_site_agreement(target.box, Path(site_file))
        notes.append(f"box checked against {Path(site_file).name}: the site is inside it")

    if "receptor_pdbqt_path" in requirements and target.receptor_pdbqt_path is None:
        # ``fields`` rather than ``target``: preparation above rewrote the former
        # and cannot rewrite the latter, a frozen model.  Reading the original
        # here would hand meeko the unrepaired structure while the three engines
        # that read ``receptor_path`` got the repaired one -- one pocket, two
        # descriptions, and the repair silently doing nothing for Uni-Dock.
        source = Path(str(fields["receptor_path"]))
        prepared, note = prepare_receptor_pdbqt(source, workspace=workspace)
        fields["receptor_pdbqt_path"] = str(prepared)
        notes.append(note)

    return ResolvedTarget(target=TargetConfig.model_validate(fields), notes=tuple(notes))


def _prepare_receptor(
    target: TargetConfig,
    *,
    fields: dict[str, object],
    notes: list[str],
    workspace: Path,
) -> None:
    """Repair the receptor and re-point the target at the repaired file.

    The digest needs care, because it means two different things at the two ends
    of this function.  A ``receptor_sha256`` the operator wrote is a pin on *the
    file they named*, so it is checked against that file and a mismatch stops
    the run before anything is repaired -- verifying it against the prepared
    structure instead would fail every pin the moment preparation was switched
    on, for the one reason that is not an error.  What the workers need is the
    opposite: the digest of the bytes they will actually dock, or
    ``verified_receptor`` would reject the prepared file as an impostor.  So the
    field is re-stated afterwards, and both digests go into the notes, which is
    what keeps the chain from the operator's file to the docked one legible.
    """

    from molcascade.plugins.builtin.docking.common import structure_digest

    source = Path(target.receptor_path)
    _, before = structure_digest(
        str(source),
        code="DOCKING_RECEPTOR_UNREADABLE",
        hint="The receptor is read here so a run cannot find it unreadable three tiers deep.",
        limit_bytes=_MAX_STRUCTURE_BYTES,
    )
    if target.receptor_sha256 is not None and target.receptor_sha256 != before:
        raise ConfigError(
            f"{source.name} is not the receptor this cascade pinned",
            code="DOCKING_RECEPTOR_DIGEST_MISMATCH",
            hint=(
                "receptor_sha256 pins a docking run to bytes rather than to a path, "
                "and these bytes are not those. Point the target at the structure "
                "the pin was taken from, or clear the pin if the structure has "
                "deliberately changed -- and re-run, because scores taken against "
                "two receptors are not comparable."
            ),
            context={
                "receptor_path": str(source),
                "expected_sha256": target.receptor_sha256,
                "actual_sha256": before,
            },
        )

    prepared, report = prepare_receptor_pdb(source, options=target.preparation, workspace=workspace)
    _, after = structure_digest(
        str(prepared),
        code="DOCKING_RECEPTOR_UNREADABLE",
        hint="The prepared receptor is hashed so the workers verify the bytes they dock.",
        limit_bytes=_MAX_STRUCTURE_BYTES,
    )
    fields["receptor_path"] = str(prepared)
    fields["receptor_sha256"] = after
    notes.append(f"receptor read from {source.name} (sha256 {before[:12]})")
    notes.extend(report.notes())
    notes.append(f"docking uses {prepared.name} (sha256 {after[:12]})")


def _require_site_agreement(box: BoxConfig, site: Path) -> None:
    """Refuse a hand-written box that does not contain the hand-written site.

    Containment rather than equality on purpose: shrinking the box around a
    reference ligand to speed a search up, or widening it to allow a larger
    scaffold, are both ordinary and both leave the engines looking at one
    pocket.  A centre outside the box is the case that does not -- KarmaDock
    would dock around the ligand while Uni-Dock searched somewhere else, and
    the tier would call the resulting disagreement a consensus.
    """

    derived = box_from_structure(site)
    centres = (
        ("x", derived.center_x, box.center_x, box.size_x),
        ("y", derived.center_y, box.center_y, box.size_y),
        ("z", derived.center_z, box.center_z, box.size_z),
    )
    outside = [axis for axis, site_c, box_c, size in centres if abs(site_c - box_c) > size / 2.0]
    if outside:
        raise ConfigError(
            f"the search box does not contain the site in {site.name}",
            code="DOCKING_SITE_DISAGREEMENT",
            hint=(
                "The box and the site file are both used -- the box by Uni-Dock and "
                "GNINA, the file by KarmaDock -- so a box the file's atoms sit "
                "outside of means the engines are docking into different places. "
                "Drop the box and let it be derived, or point the file at the same "
                "pocket."
            ),
            context={
                "site_path": str(site),
                "axes_outside": outside,
                "site_center": [derived.center_x, derived.center_y, derived.center_z],
                "box_center": [box.center_x, box.center_y, box.center_z],
                "box_size": [box.size_x, box.size_y, box.size_z],
            },
        )


def box_from_structure(path: Path) -> BoxConfig:
    """The smallest box containing a structure's atoms, plus padding on each side.

    Deliberately geometric and nothing more.  A reference ligand is being used
    here only for where its atoms are; nothing is read about its chemistry, so a
    file this project could not parse as a molecule still works as a ruler.
    """

    coordinates = list(_coordinates(path))
    if not coordinates:
        raise ConfigError(
            f"no atom coordinates were found in {path.name}",
            code="DOCKING_SITE_STRUCTURE_EMPTY",
            hint=(
                "The binding site is located from this file's atoms. Accepted "
                "formats are .sdf, .mol, .mol2 and .pdb; an empty or unrecognised "
                "one gives no site rather than a wrong one."
            ),
            context={"path": str(path)},
        )
    lows = [min(axis) for axis in zip(*coordinates, strict=True)]
    highs = [max(axis) for axis in zip(*coordinates, strict=True)]
    centre = [(low + high) / 2.0 for low, high in zip(lows, highs, strict=True)]
    sizes = [
        (high - low) + 2.0 * BOX_PADDING_ANGSTROM
        for low, high in zip(lows, highs, strict=True)
    ]
    if any(size > _MAX_BOX_EDGE_ANGSTROM for size in sizes):
        raise ConfigError(
            f"the atoms in {path.name} span more than a binding site",
            code="DOCKING_SITE_TOO_LARGE",
            hint=(
                "This looks like a whole structure rather than a pocket or a bound "
                "ligand. Pass the ligand actually in the site, or give the six "
                "numbers with '--box'."
            ),
            context={
                "path": str(path),
                "size_x": sizes[0],
                "size_y": sizes[1],
                "size_z": sizes[2],
            },
        )
    return _box([*centre, *sizes], source=path.name)


def _coordinates(path: Path) -> Iterable[tuple[float, float, float]]:
    suffix = path.suffix.lower()
    if suffix not in _LIGAND_SUFFIXES:
        raise ConfigError(
            f"{path.name} is not a structure format the site can be read from",
            code="DOCKING_SITE_FORMAT_UNSUPPORTED",
            hint="Accepted: .sdf, .mol, .mol2 and .pdb.",
            context={"path": str(path), "suffix": suffix},
        )
    text = _read_text(path)
    if suffix == ".pdb":
        return _pdb_coordinates(text)
    if suffix == ".mol2":
        return _mol2_coordinates(text, path=path)
    return _molfile_coordinates(text, path=path)


def _read_text(path: Path) -> str:
    try:
        info = path.stat()
    except OSError as error:
        raise ConfigError(
            f"the binding-site file could not be read: {path}",
            code="DOCKING_SITE_UNREADABLE",
            hint="The path is read at start-up so a run cannot discover it three tiers deep.",
            context={"path": str(path), "error_type": type(error).__name__},
        ) from error
    if info.st_size > _MAX_STRUCTURE_BYTES:
        raise ConfigError(
            f"the binding-site file is larger than a structure file: {path}",
            code="DOCKING_SITE_UNREADABLE",
            hint="A reference ligand or a pocket, not a trajectory.",
            context={"path": str(path), "size_bytes": info.st_size},
        )
    return path.read_text(encoding="utf-8", errors="replace")


def _pdb_coordinates(text: str) -> Iterable[tuple[float, float, float]]:
    """Read the fixed columns, not whitespace-separated fields.

    PDB is a column format and its coordinate fields run together as soon as one
    of them needs four digits before the decimal point, which happens in any
    structure placed far from the origin.  Splitting on whitespace reads those
    as one number and silently loses atoms.
    """

    for line in text.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")) or len(line) < 54:
            continue
        try:
            yield (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError:
            continue


def _mol2_coordinates(text: str, *, path: Path) -> Iterable[tuple[float, float, float]]:
    lines = text.splitlines()
    try:
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip().upper() == "@<TRIPOS>ATOM"
        )
    except StopIteration:
        raise ConfigError(
            f"{path.name} has no @<TRIPOS>ATOM record",
            code="DOCKING_SITE_FORMAT_UNSUPPORTED",
            hint="A mol2 file without an atom block carries no coordinates to locate a site with.",
            context={"path": str(path)},
        ) from None
    found: list[tuple[float, float, float]] = []
    for line in lines[start + 1 :]:
        if line.startswith("@<TRIPOS>"):
            break
        fields = line.split()
        if len(fields) < 5:
            continue
        try:
            found.append((float(fields[2]), float(fields[3]), float(fields[4])))
        except ValueError:
            continue
    return found


def _molfile_coordinates(text: str, *, path: Path) -> Iterable[tuple[float, float, float]]:
    """Read a V2000 connection table's atom block.

    V3000 is refused rather than half-read.  Its atom block is a different
    format entirely, and a parser that skipped the lines it did not recognise
    would report an empty structure as a site of size zero.
    """

    lines = text.splitlines()
    if len(lines) < 4:
        raise ConfigError(
            f"{path.name} is too short to be a molfile",
            code="DOCKING_SITE_FORMAT_UNSUPPORTED",
            hint="Accepted: .sdf, .mol, .mol2 and .pdb.",
            context={"path": str(path)},
        )
    counts = lines[3]
    if "V3000" in counts.upper():
        raise ConfigError(
            f"{path.name} is a V3000 molfile, which this reader does not parse",
            code="DOCKING_SITE_FORMAT_UNSUPPORTED",
            hint=(
                "Convert it to V2000 or mol2, or give the six numbers with '--box'. "
                "Half-reading it would report a site of size zero."
            ),
            context={"path": str(path)},
        )
    try:
        atom_count = int(counts[0:3])
    except ValueError:
        raise ConfigError(
            f"{path.name} has no readable counts line",
            code="DOCKING_SITE_FORMAT_UNSUPPORTED",
            hint="Accepted: .sdf, .mol, .mol2 and .pdb.",
            context={"path": str(path)},
        ) from None
    found: list[tuple[float, float, float]] = []
    for line in lines[4 : 4 + max(atom_count, 0)]:
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            found.append((float(fields[0]), float(fields[1]), float(fields[2])))
        except ValueError:
            continue
    return found


def prepare_receptor_pdbqt(receptor: Path, *, workspace: Path) -> tuple[Path, str]:
    """Derive Uni-Dock's PDBQT from the operator's PDB, cached by its digest.

    Cached by digest rather than by path so that re-running the same cascade
    does not re-prepare, and so that editing the receptor cannot leave a stale
    PDBQT behind wearing the same name.

    This is the fragile step in the whole tier -- protonation, non-standard
    residues and missing atoms are all places meeko stops -- so its failure is
    reported with meeko's own words and the documented way past it.  Meeko's
    ``--allow_bad_res``, which deletes incomplete residues, is deliberately not
    passed: quietly docking against a protein with holes in it is worse than
    stopping.
    """

    from molcascade.plugins.builtin.docking.common import structure_digest

    _, digest = structure_digest(
        str(receptor),
        code="DOCKING_RECEPTOR_UNREADABLE",
        hint=(
            "This is the receptor for the docking tier, read once here so a run "
            "cannot discover it is unreadable three tiers deep."
        ),
    )
    destination = workspace / "targets" / digest / "receptor.pdbqt"
    if destination.exists():
        return destination, f"receptor PDBQT reused from the workspace cache for sha256:{digest}"

    command = _meeko_command()
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [*command, "--read_pdb", str(receptor), "-p", str(destination)],
        capture_output=True,
        timeout=_PREPARE_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0 or not destination.exists():
        destination.unlink(missing_ok=True)
        detail = _meeko_failure_detail(completed)
        raise ConfigError(
            f"meeko could not prepare {receptor.name} as a PDBQT receptor",
            code="DOCKING_RECEPTOR_PREPARATION_FAILED",
            hint=(
                "Uni-Dock reads PDBQT and this is where a PDB becomes one. "
                "Protonation, non-standard residues and missing atoms are where it "
                "stops. Prepare the receptor yourself and pass "
                "'--receptor-pdbqt PREPARED.pdbqt', or fix the structure -- "
                "MolCascade will not delete residues to make it parse."
            ),
            context={
                "receptor_path": str(receptor),
                "returncode": completed.returncode,
                "meeko_output": detail,
            },
        )
    return destination, f"receptor PDBQT derived with meeko from sha256:{digest}"


def _meeko_failure_detail(completed: subprocess.CompletedProcess[bytes]) -> str:
    """Both of meeko's streams, with the half an operator can act on first.

    meeko splits a receptor failure across the two.  stderr gets the
    residue-by-residue template-matching trace, thousands of lines of it on a
    real protein; stdout gets the few hundred characters that name which
    residues failed, which carry an alternate location, and the flags that get
    past each.  Keeping one stream therefore drops half the failure, and this
    kept the wrong half: ``stderr or stdout`` never reaches stdout, because a
    meeko that failed always wrote a trace.  Keeping the *tail* of the pair
    drops the same half again, since the summary is what a program prints last.

    So stdout leads and is budgeted first, and stderr takes what is left of it
    from its end -- the end of a trace being the part nearest the failure.
    """

    summary = completed.stdout.decode("utf-8", "replace").strip()[:_MEEKO_DETAIL_BUDGET]
    remaining = _MEEKO_DETAIL_BUDGET - len(summary)
    trace = completed.stderr.decode("utf-8", "replace").strip()
    trace = trace[-remaining:] if remaining > 0 else ""
    if summary and trace:
        return f"{summary}\n\n--- meeko trace (tail) ---\n{trace}"
    return summary or trace


def _meeko_command() -> list[str]:
    """How to invoke meeko's receptor preparation on this installation.

    The console script when it is on ``PATH``, and the module otherwise -- a
    conda environment activated only by ``PYTHONPATH`` has the package without
    the script, and failing there would look like meeko being absent.
    """

    script = shutil.which("mk_prepare_receptor.py")
    if script is not None:
        return [script]
    if _meeko_importable():
        return [sys.executable, "-m", "meeko.cli.mk_prepare_receptor"]
    raise ConfigError(
        "meeko is not installed, so a receptor PDBQT cannot be derived",
        code="DOCKING_RECEPTOR_PREPARATION_UNAVAILABLE",
        hint=(
            'pip install "molcascade[docking]" -- meeko is what turns a PDB into '
            "the PDBQT Uni-Dock reads. Alternatively prepare it yourself and pass "
            "'--receptor-pdbqt PREPARED.pdbqt'."
        ),
    )


def _meeko_importable() -> bool:
    from importlib.util import find_spec

    try:
        return find_spec("meeko") is not None
    except (ImportError, ValueError):
        return False


__all__ = [
    "BOX_PADDING_ANGSTROM",
    "ResolvedTarget",
    "box_from_structure",
    "docking_requirements",
    "merge_target",
    "parse_box",
    "prepare_receptor_pdbqt",
    "resolve_target",
]
