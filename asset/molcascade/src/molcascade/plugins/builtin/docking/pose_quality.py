"""Repair a docked pose against the receptor, then judge whether it is usable.

Every engine in this tier reports a number, and a number is not a structure.
Measured over a real KarmaDock shard against STK17B, 278 poses that all passed
the score gate broke down like this under PoseBusters' ``dock`` checks:

======================================  ==========
check                                   passing
======================================  ==========
bond lengths, bond angles                278 (100%)
aromatic ring flatness                   278 (100%)
internal steric clash                    228 (82.0%)
**minimum distance to protein**          **63 (22.7%)**
every geometry check at once             50 (18.0%)
======================================  ==========

The distribution is the argument for this module existing.  The intramolecular
geometry is fine -- ``_POSE_SUFFIX`` in :mod:`.karmadock` explains why it is
fine by construction -- and what fails is the *placement*: the ligand is packed
into the protein harder than any real complex is.  The reference ligand that
defines the site sits 2.88 A from its nearest protein heavy atom with no
clashing pair at all; the poses' median nearest contact is 2.07 A, and not one
of the 278 beat 2.87 A.  They land in the right pocket and then overlap it.

Two facts decide what to do about that.

The first is that the score cannot see it.  Across the same 278 poses the
correlation between KarmaDock's score and the closest protein contact is
+0.014, and the median score of a pose that passes every check is 44.4 against
44.5 for one that fails.  Filtering harder on the number would not remove a
single bad structure.  So this has to be a separate judgement or it is not made
at all.

The second is that most of the overlap is shallow -- 0.3 to 0.8 A -- and a few
hundred steps of constrained minimisation in the receptor's field removes it.
Rejecting on the raw pose would throw away 82% of the shortlist to pay for a
bias in the pose *writer*, not in the chemistry.  So the pose is repaired first
and judged second, which on the same 278 poses moves every-geometry-check from
50 (18.0%) to 274 (98.6%) at 43 ms a molecule, with the pose preserved rather
than reinvented: median heavy-atom displacement 0.78 A, 90th percentile 1.14 A,
worst 1.67 A.  The four that survive repair are what the judgement is for.

Why the repair is allowed to leave ``score`` alone.  A relaxed pose is not the
geometry the engine scored -- but neither was the pose it wrote.  KarmaDock's
mixture-density score is computed on the network's own internal prediction, and
the pose in the artifact is an ETKDG conformer fitted onto that prediction
afterwards; the +0.014 correlation above is the evidence that the two were
already different geometries.  Relaxing the written pose therefore does not
make ``score`` any staler than the engine already made it, and silently
*recomputing* a score with a different function would be a substitution the
cascade never asked for.  What does change is ``method_id``, which every engine
mixes this configuration into, so a run with repair on is never mistaken for a
run with it off.

This module is shared rather than per-engine because the defect is not
KarmaDock's alone: any engine that writes coordinates can write ones that
overlap the protein, and all three build the same ``docking_score/v1`` rows, so
one function applied to those rows covers the tier.  It is on by default for the
same reason the receptor digest is mandatory -- a pose nobody checked is a pose
that reaches MD as a surprise -- and switchable because an operator who wants
the engine's raw output is entitled to it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator

from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.errors import PluginError

#: Residue names treated as bulk solvent.  Waters are displaced by a ligand
#: binding, so pushing a pose away from one is repairing the wrong thing -- and
#: a crystallographic water left in a prepared receptor would otherwise fill the
#: site the ligand is supposed to occupy.  Cofactors, metals and everything else
#: stay: those are not displaceable, and a pose overlapping a haem is wrong.
_SOLVENT_RESIDUES = frozenset({"HOH", "DOD", "WAT", "TIP", "TIP3", "SOL", "H2O"})

#: Checks this module knows how to ask PoseBusters for, mapped to the module
#: specification that produces them.  Every entry is one ``dock.yml`` module
#: with its published parameters and its output renamed to the key used here:
#: naming the columns rather than inheriting PoseBusters' own means a future
#: release renaming one turns into a loud configuration error instead of a check
#: that silently stops being applied.
_MODULE_SPECS: tuple[tuple[str, tuple[str, ...], dict[str, Any]], ...] = (
    (
        "Chemistry:rdkit",
        ("sanitization",),
        {
            "function": "rdkit_sanity",
            "outputs": {"passes_rdkit_sanity_checks": "sanitization"},
        },
    ),
    (
        "Chemistry:inchi",
        ("inchi_convertible",),
        {
            "function": "inchi_convertible",
            "outputs": {"inchi_convertible": "inchi_convertible"},
        },
    ),
    (
        "Chemistry:connected",
        ("all_atoms_connected",),
        {
            "function": "atoms_connected",
            "outputs": {"all_atoms_connected": "all_atoms_connected"},
        },
    ),
    (
        "Chemistry:radicals",
        ("no_radicals",),
        {
            "function": "check_radicals",
            "outputs": {"no_radicals": "no_radicals"},
        },
    ),
    (
        "Geometry",
        ("bond_lengths", "bond_angles", "internal_steric_clash"),
        {
            "function": "distance_geometry",
            "parameters": {
                "bound_matrix_params": {
                    "set15bounds": True,
                    "scaleVDW": True,
                    "doTriangleSmoothing": True,
                    "useMacrocycle14config": False,
                },
                "threshold_bad_bond_length": 0.25,
                "threshold_bad_angle": 0.25,
                "threshold_clash": 0.3,
                "ignore_hydrogens": True,
                "sanitize": True,
            },
            "outputs": {
                "bond_lengths_within_bounds": "bond_lengths",
                "bond_angles_within_bounds": "bond_angles",
                "no_internal_clash": "internal_steric_clash",
            },
        },
    ),
    (
        "Ring flatness",
        ("aromatic_ring_flatness",),
        {
            "function": "flatness",
            "parameters": {
                "flat_systems": {
                    "aromatic_5_membered_rings_sp2": "[ar5^2]1[ar5^2][ar5^2][ar5^2][ar5^2]1",
                    "aromatic_6_membered_rings_sp2": (
                        "[ar6^2]1[ar6^2][ar6^2][ar6^2][ar6^2][ar6^2]1"
                    ),
                },
                "threshold_flatness": 0.25,
            },
            "outputs": {"flatness_passes": "aromatic_ring_flatness"},
        },
    ),
    (
        "Double bond flatness",
        ("double_bond_flatness",),
        {
            "function": "flatness",
            "parameters": {
                "flat_systems": {
                    "trigonal_planar_double_bonds": "[C;X3;^2](*)(*)=[C;X3;^2](*)(*)",
                },
                "threshold_flatness": 0.25,
            },
            "outputs": {"flatness_passes": "double_bond_flatness"},
        },
    ),
    (
        "Distance to protein",
        ("minimum_distance_to_protein", "protein_ligand_maximum_distance"),
        {
            "function": "intermolecular_distance",
            "parameters": {
                "radius_type": "vdw",
                "radius_scale": 1.0,
                "clash_cutoff": 0.75,
                "ignore_types": ["hydrogens", "organic_cofactors", "inorganic_cofactors", "waters"],
                "max_distance": 5.0,
            },
            "outputs": {
                "no_clashes": "minimum_distance_to_protein",
                "not_too_far_away": "protein_ligand_maximum_distance",
            },
        },
    ),
    # The other three quarters of the clash check.
    #
    # Upstream's ``dock.yml`` runs ``intermolecular_distance`` four times, once
    # per kind of thing a ligand can be placed inside: protein, organic
    # cofactors, inorganic cofactors and waters.  This module used to carry only
    # the protein branch -- along with that branch's own ``ignore_types``, which
    # names the other three -- so nothing here ever checked whether a pose was
    # sitting on top of a heme, a catalytic zinc or a bridging water.
    #
    # That gap lines up exactly with a decision made one module away.
    # ``cascade/receptor.py`` keeps metals by default, and says why: "a catalytic
    # zinc deleted as a 'non-standard residue' changes every score in the tier
    # and shows up in none of them".  ``--receptor-keep-waters`` and
    # ``--receptor-keep-heterogens`` keep the other two on request.  So the
    # receptor that reaches an engine can contain all three, and until now the
    # pose check ignored every one of them.
    #
    # The inorganic branch uses covalent radii rather than van der Waals, which
    # is upstream's answer to the obvious objection: a ligand coordinating a zinc
    # is *supposed* to be inside its van der Waals radius, and scored against vdW
    # every metalloenzyme complex would read as a clash.  Only ``no_clashes`` is
    # mapped: ``not_too_far_away`` is meaningful for the protein, which a ligand
    # must be near, and meaningless for a cofactor it has no obligation to touch.
    #
    # Available, and deliberately not in :data:`DEFAULT_CHECKS`: turning them on
    # by default would change what the shipped cascade rejects, on a receptor
    # whose contents depend on flags the operator chose.  Name them in ``checks``.
    (
        "Distance to organic cofactors",
        ("minimum_distance_to_organic_cofactors",),
        {
            "function": "intermolecular_distance",
            "parameters": {
                "radius_type": "vdw",
                "radius_scale": 1.0,
                "clash_cutoff": 0.75,
                "ignore_types": ["hydrogens", "protein", "inorganic_cofactors", "waters"],
                "max_distance": 5.0,
            },
            "outputs": {"no_clashes": "minimum_distance_to_organic_cofactors"},
        },
    ),
    (
        "Distance to inorganic cofactors",
        ("minimum_distance_to_inorganic_cofactors",),
        {
            "function": "intermolecular_distance",
            "parameters": {
                # Covalent, not vdw -- see the note above.
                "radius_type": "covalent",
                "radius_scale": 1.0,
                "clash_cutoff": 0.75,
                "ignore_types": ["hydrogens", "protein", "organic_cofactors", "waters"],
                "max_distance": 5.0,
            },
            "outputs": {"no_clashes": "minimum_distance_to_inorganic_cofactors"},
        },
    ),
    (
        "Distance to waters",
        ("minimum_distance_to_waters",),
        {
            "function": "intermolecular_distance",
            "parameters": {
                "radius_type": "vdw",
                "radius_scale": 1.0,
                "clash_cutoff": 0.75,
                "ignore_types": [
                    "hydrogens",
                    "protein",
                    "organic_cofactors",
                    "inorganic_cofactors",
                ],
                "max_distance": 5.0,
            },
            "outputs": {"no_clashes": "minimum_distance_to_waters"},
        },
    ),
    (
        "Volume overlap with protein",
        ("volume_overlap_with_protein",),
        {
            "function": "volume_overlap",
            "parameters": {
                "clash_cutoff": 0.075,
                "vdw_scale": 0.8,
                "ignore_types": ["hydrogens", "organic_cofactors", "inorganic_cofactors", "waters"],
            },
            "outputs": {"no_volume_clash": "volume_overlap_with_protein"},
        },
    ),
    (
        "Internal energy",
        ("internal_energy",),
        {
            "function": "energy_ratio",
            "parameters": {
                "threshold_energy_ratio": 100.0,
                "ensemble_number_conformations": 50,
            },
            "outputs": {"energy_ratio_passes": "internal_energy"},
        },
    ),
)

#: Every check name :data:`_MODULE_SPECS` can produce.
AVAILABLE_CHECKS: tuple[str, ...] = tuple(
    sorted(name for _module, names, _spec in _MODULE_SPECS for name in names)
)

#: Required by default: the checks whose cost and whose yield on this tier have
#: both been measured.  Together they run in roughly 1 ms a pose on top of the
#: repair, which is noise beside any docking engine.
DEFAULT_CHECKS: tuple[str, ...] = (
    "sanitization",
    "all_atoms_connected",
    "no_radicals",
    "bond_lengths",
    "bond_angles",
    "internal_steric_clash",
    "aromatic_ring_flatness",
    "double_bond_flatness",
    "minimum_distance_to_protein",
)

#: Available and deliberately not required by default, with the measurement that
#: settled it.  ``internal_energy`` embeds a 50-conformer ETKDG ensemble per
#: molecule: 351 ms a pose for the full ``dock`` set against ~1 ms without it,
#: which is 4.4 hours rather than minutes over a 45 000-molecule shortlist, and
#: on one fused polycyclic scaffold in the measured shard the ensemble failed to
#: generate at all -- so it is fragile as well as slow.  ``inchi_convertible``
#: and ``volume_overlap_with_protein`` are cheap but redundant here: the first
#: never fired across 278 poses, and the second is strictly more permissive than
#: ``minimum_distance_to_protein`` (87.8% against 22.7%) because a shallow
#: overlap displaces little volume.  Turn any of them on by naming it in
#: ``checks``; they are computed only when required.
OPTIONAL_CHECKS: tuple[str, ...] = tuple(
    name for name in AVAILABLE_CHECKS if name not in DEFAULT_CHECKS
)

_POSEBUSTERS_HINT = (
    "Pose checking is on by default because a pose nobody checked reaches MD as "
    "a surprise. Install it with the 'docking' extra (pip install "
    "'molcascade[docking]'), or set 'pose_quality.enabled' to false on the "
    "docking stage to take the engine's raw coordinates."
)


class PoseQualityConfig(StrictFrozenModel):
    """Whether a written pose is repaired and judged, and how hard.

    Lowering never writes these -- they are the operator's, not the target's.
    Every field is mixed into each engine's ``method_id`` through
    :meth:`identity`, so a shortlist produced with repair on is distinguishable
    from one produced without it.
    """

    #: Repair and judge every pose an engine writes.  On by default.  Turning it
    #: off restores the engine's raw coordinates and the raw pass rates above.
    enabled: bool = True

    #: Relax the pose in the receptor's field before judging it.  Off means
    #: judge what the engine wrote, which on the measured shard keeps 18% of a
    #: score-gated shortlist rather than 98.6%.
    relax: bool = True

    #: Drop the score row of a pose that still fails after repair.  A molecule
    #: whose every pose is dropped has no score left, so the evidence gate
    #: downstream rejects it as missing -- the funnel shows it, and
    #: ``pose_check_*`` in this stage's metadata says which check did it.  Off
    #: keeps the rows and only counts them, for measuring a threshold before
    #: enforcing it.
    discard_failing: bool = True

    #: Checks a pose has to pass.  Only the modules needed for these are
    #: computed.  See :data:`DEFAULT_CHECKS` and :data:`OPTIONAL_CHECKS`.
    checks: tuple[str, ...] = DEFAULT_CHECKS

    #: How far a heavy atom may drift from where the engine put it, in A.  This
    #: is the flat bottom of a position restraint, not a hard cap: the pose is
    #: being repaired, and one free to travel is one being re-docked by a force
    #: field that cannot dock.  At 0.5 A the measured median displacement was
    #: 0.78 A and the worst 1.67 A, hydrogens included.
    max_displacement: float = Field(default=0.5, ge=0.0, le=5.0)

    #: Restraint stiffness outside that flat bottom, kcal/mol/A^2.
    position_force_constant: float = Field(default=10.0, gt=0.0, le=10_000.0)

    #: A ligand-protein heavy-atom pair closer than this multiple of the sum of
    #: their van der Waals radii gets a repulsive term.  Above 1.0 so that a
    #: pair just outside contact is included before minimisation pushes
    #: something into it.
    detect_scale: float = Field(default=1.05, ge=0.5, le=2.0)

    #: Where those pairs are pushed to, as the same multiple.  0.80 clears
    #: PoseBusters' 0.75 clash cutoff with a margin rather than landing on it.
    target_scale: float = Field(default=0.80, ge=0.3, le=1.5)

    #: Stiffness of the repulsion, kcal/mol/A^2.  Stiff on purpose: it has to
    #: win against whatever the pose writer did, and it is flat-bottomed, so
    #: once the pair is clear it contributes nothing.
    repulsion_force_constant: float = Field(default=200.0, gt=0.0, le=100_000.0)

    #: Minimisation steps per round.
    max_iterations: int = Field(default=400, ge=1, le=100_000)

    #: Detect-then-minimise passes.  More than one because an atom pushed clear
    #: of one neighbour can arrive next to another that was not in the first
    #: pair list.
    rounds: int = Field(default=2, ge=1, le=10)

    #: Receptor atoms are pre-filtered to the pose's bounding box grown by this
    #: many A.  Purely a cost control: at 8 A nothing that could clash is
    #: excluded, and a 2 000-atom receptor drops to the few hundred that matter.
    neighbour_padding: float = Field(default=8.0, ge=1.0, le=50.0)

    #: Treat crystallographic waters in the receptor as displaceable and ignore
    #: them during repair.  See :data:`_SOLVENT_RESIDUES`.
    ignore_waters: bool = True

    @field_validator("checks", mode="before")
    @classmethod
    def _checks_from_sequence(cls, value: Any) -> Any:
        """Accept the list a JSON round-trip leaves behind.

        The model is strict, so a ``list`` will not coerce to the declared
        ``tuple`` on its own -- and this config makes that round-trip twice: once
        out of the cascade file, and once more into the worker process, where
        every shard revalidates ``task.config`` from a plain dict.  The tuple is
        kept as the internal type because the value is hashed into ``method_id``
        and has to be immutable to be trusted there.
        """

        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("checks")
    @classmethod
    def _checks_are_known(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Refuse a check this module cannot produce.

        Load-bearing rather than tidy.  PoseBusters fills a column it was not
        asked for with ``NA``, and ``NA`` is skipped by the reduction that
        decides whether a pose passed -- so a misspelled check name would not
        fail, it would silently pass every pose while appearing to be enforced.
        """

        unknown = sorted(set(value) - set(AVAILABLE_CHECKS))
        if unknown:
            raise ValueError(
                f"unknown pose checks: {', '.join(unknown)}; "
                f"available: {', '.join(AVAILABLE_CHECKS)}"
            )
        return tuple(dict.fromkeys(value))

    @model_validator(mode="after")
    def _target_below_detect(self) -> PoseQualityConfig:
        if self.relax and self.target_scale > self.detect_scale:
            raise ValueError(
                "target_scale must not exceed detect_scale, or repair would push "
                "pairs it never detected"
            )
        return self

    def identity(self) -> dict[str, Any]:
        """What of this belongs in a score row's ``method_id``.

        ``discard_failing`` is in it: it decides which rows exist, and two
        artifacts differing in which poses they contain are not the same
        computation.  Nothing here is a machine setting, so nothing is excluded
        for the reason ``allow_cpu`` is excluded from GNINA's.
        """

        if not self.enabled:
            return {"enabled": False}
        payload: dict[str, Any] = {
            "enabled": True,
            "relax": self.relax,
            "discard_failing": self.discard_failing,
            "checks": sorted(self.checks),
        }
        if self.relax:
            payload.update(
                {
                    "max_displacement": self.max_displacement,
                    "position_force_constant": self.position_force_constant,
                    "detect_scale": self.detect_scale,
                    "target_scale": self.target_scale,
                    "repulsion_force_constant": self.repulsion_force_constant,
                    "max_iterations": self.max_iterations,
                    "rounds": self.rounds,
                    "neighbour_padding": self.neighbour_padding,
                    "ignore_waters": self.ignore_waters,
                }
            )
        return payload

    def digest(self) -> str:
        return canonical_sha256(self.identity())


@dataclass
class PoseQualityReport:
    """What repair and judgement did to one shard, in counters.

    Flat integers rather than a nested structure because
    :meth:`ShardedResult.total` sums one key across shards, and a per-check
    breakdown is the difference between a stage that filtered visibly and one
    that quietly lost four fifths of the shortlist.
    """

    #: Rows carrying a pose that could be looked at.
    considered: int = 0
    #: Rows whose ``pose_molblock`` was null -- nothing to check.
    skipped: int = 0
    #: Rows whose molblock would not parse into a molecule at all.
    unparsable: int = 0
    #: Poses the repair moved.
    relaxed: int = 0
    #: Poses the repair could not be set up for, usually MMFF typing.  These are
    #: still judged, on the coordinates the engine wrote.
    relax_failed: int = 0
    #: Poses passing every required check after repair.
    passed: int = 0
    #: Rows removed because their pose failed and ``discard_failing`` was set.
    discarded: int = 0
    #: Sum of heavy-atom displacement, for a mean.  Not a distribution: this has
    #: to survive being summed across shards.
    displacement_total: float = 0.0
    #: Required check -> poses failing it.  A pose failing three checks counts
    #: in three of these, so they do not sum to :attr:`passed`'s complement.
    failures: dict[str, int] = field(default_factory=dict)

    def metadata(self) -> dict[str, Any]:
        """Counters as a flat mapping for ``ShardOutcome.metadata``."""

        payload: dict[str, Any] = {
            "pose_checked_count": self.considered,
            "pose_unchecked_count": self.skipped,
            "pose_unparsable_count": self.unparsable,
            "pose_relaxed_count": self.relaxed,
            "pose_relax_failed_count": self.relax_failed,
            "pose_passed_count": self.passed,
            "pose_discarded_count": self.discarded,
            # Milliangstrom, and an integer, because the counters are summed
            # across shards by `ShardedResult.total`, which coerces to int --
            # a float here would be truncated once per shard.
            "pose_displacement_total_mA": round(self.displacement_total * 1000.0),
        }
        for name, count in self.failures.items():
            payload[f"pose_check_failed__{name}"] = count
        return payload


@dataclass(frozen=True)
class ReceptorField:
    """The receptor as the repair needs it: coordinates and radii, nothing else.

    Built once per shard.  Reading and typing a 2 000-atom protein for every
    ligand would cost more than the minimisation it feeds.
    """

    coordinates: Any
    radii: Any

    def neighbours(self, lower: Any, upper: Any) -> Any:
        """Indices of receptor atoms inside a bounding box."""

        import numpy as np

        inside = np.all((self.coordinates >= lower) & (self.coordinates <= upper), axis=1)
        return np.flatnonzero(inside)


def load_receptor_field(receptor_path: str | Path, *, ignore_waters: bool = True) -> ReceptorField:
    """Read the receptor's heavy atoms and their van der Waals radii.

    ``sanitize=False`` and ``proximityBonding=False`` for the same reason
    PoseBusters uses them: a prepared receptor routinely has valences RDKit
    would refuse, and this only ever needs coordinates and elements.  Inferring
    bonds by proximity across a whole protein is expensive and buys nothing
    here.
    """

    import numpy as np
    from rdkit import Chem

    molecule = Chem.MolFromPDBFile(
        str(receptor_path),
        removeHs=True,
        sanitize=False,
        proximityBonding=False,
    )
    if molecule is None or molecule.GetNumConformers() == 0:
        raise PluginError(
            "the receptor structure could not be read for pose checking",
            code="DOCKING_POSE_RECEPTOR_UNREADABLE",
            hint=(
                "Pose repair needs the receptor's atoms. This is the same file "
                "the engine docked against, so if the engine ran, the likely "
                "cause is a PDB with no ATOM records RDKit recognises."
            ),
            context={"receptor_path": str(receptor_path)},
        )

    table = Chem.GetPeriodicTable()
    positions = molecule.GetConformer().GetPositions()
    kept: list[int] = []
    radii: list[float] = []
    for atom in molecule.GetAtoms():
        number = atom.GetAtomicNum()
        if number <= 1:
            continue
        if ignore_waters:
            info = atom.GetPDBResidueInfo()
            if info is not None and info.GetResidueName().strip().upper() in _SOLVENT_RESIDUES:
                continue
        kept.append(atom.GetIdx())
        radii.append(float(table.GetRvdw(number)))

    if not kept:
        raise PluginError(
            "the receptor structure contains no heavy atoms to check poses against",
            code="DOCKING_POSE_RECEPTOR_EMPTY",
            hint=(
                "Every atom was a hydrogen or a water. A receptor of only "
                "solvent would let every pose pass the clash check."
            ),
            context={"receptor_path": str(receptor_path)},
        )

    return ReceptorField(
        coordinates=np.asarray(positions, dtype=float)[kept],
        radii=np.asarray(radii, dtype=float),
    )


def relax_pose(
    molecule: Any,
    *,
    receptor: ReceptorField,
    config: PoseQualityConfig,
) -> float | None:
    """Push one pose out of the receptor in place, and say how far it moved.

    Returns the median heavy-atom displacement, or ``None`` when the pose could
    not be set up -- a molecule MMFF has no parameters for, most often.

    The force field is built on the *ligand alone* and the receptor enters as
    fixed extra points.  That is the whole trick: MMFF typing a pocket carved
    out of a PDB fails on the broken valences at the cut, while a ligand-only
    field typed cleanly on all 278 poses measured.  The receptor contributes
    geometry without ever being typed.
    """

    import numpy as np
    from rdkit import Chem
    from rdkit.Chem import rdForceFieldHelpers

    added_hydrogens = not any(atom.GetAtomicNum() == 1 for atom in molecule.GetAtoms())
    working = Chem.AddHs(molecule, addCoords=True) if added_hydrogens else molecule

    try:
        properties = rdForceFieldHelpers.MMFFGetMoleculeProperties(working, mmffVariant="MMFF94s")
    except (RuntimeError, ValueError):
        properties = None
    if properties is None:
        return None

    heavy = [atom.GetIdx() for atom in working.GetAtoms() if atom.GetAtomicNum() > 1]
    if not heavy:
        return None
    table = Chem.GetPeriodicTable()
    ligand_radii = np.asarray(
        [float(table.GetRvdw(working.GetAtomWithIdx(index).GetAtomicNum())) for index in heavy],
        dtype=float,
    )
    conformer = working.GetConformer()
    before = np.asarray(conformer.GetPositions(), dtype=float)[heavy]

    for _round in range(config.rounds):
        positions = np.asarray(working.GetConformer().GetPositions(), dtype=float)
        ligand_points = positions[heavy]
        window = config.neighbour_padding
        candidates = receptor.neighbours(
            ligand_points.min(axis=0) - window,
            ligand_points.max(axis=0) + window,
        )
        if candidates.size == 0:
            break

        receptor_points = receptor.coordinates[candidates]
        receptor_radii = receptor.radii[candidates]
        separation = np.linalg.norm(ligand_points[:, None, :] - receptor_points[None, :, :], axis=2)
        contact = ligand_radii[:, None] + receptor_radii[None, :]
        ligand_hit, receptor_hit = np.nonzero(separation < contact * config.detect_scale)
        if ligand_hit.size == 0:
            break

        try:
            force_field = rdForceFieldHelpers.MMFFGetMoleculeForceField(
                working, properties, ignoreInterfragInteractions=False
            )
        except (RuntimeError, ValueError):
            return None
        if force_field is None:
            return None

        for index in heavy:
            force_field.MMFFAddPositionConstraint(
                index, config.max_displacement, config.position_force_constant
            )

        # One extra point per receptor atom involved, not per clashing pair: the
        # same atom is commonly too close to several ligand atoms at once, and
        # adding it twice would double the wall it presents.
        extra: dict[int, int] = {}
        for ligand_index, receptor_index in zip(ligand_hit, receptor_hit, strict=True):
            neighbour = int(receptor_index)
            point = extra.get(neighbour)
            if point is None:
                x, y, z = receptor_points[neighbour]
                # AddExtraPoint returns the count of points, one-based, so the
                # index of the one just added is that minus one.  Passing the
                # count straight through addresses the next point, which does
                # not exist yet, and the constraint silently anchors nothing.
                point = int(force_field.AddExtraPoint(float(x), float(y), float(z), True)) - 1
                extra[neighbour] = point
            atom = heavy[int(ligand_index)]
            minimum = float(
                (ligand_radii[int(ligand_index)] + receptor_radii[neighbour]) * config.target_scale
            )
            force_field.AddDistanceConstraint(
                atom, point, minimum, 100.0, config.repulsion_force_constant
            )

        force_field.Initialize()
        force_field.Minimize(maxIts=config.max_iterations)

    after = np.asarray(working.GetConformer().GetPositions(), dtype=float)[heavy]
    displacement = float(np.median(np.linalg.norm(after - before, axis=1)))

    if added_hydrogens:
        stripped = Chem.RemoveHs(working)
        molecule.RemoveAllConformers()
        molecule.AddConformer(stripped.GetConformer(), assignId=True)
    return displacement


def posebusters_config(checks: Sequence[str]) -> dict[str, Any]:
    """Build the smallest PoseBusters configuration that answers ``checks``.

    Modules are included only when something in ``checks`` needs them, which is
    what keeps ``internal_energy``'s 50-conformer ensemble out of a run that did
    not ask for it.  Outputs are renamed to the names used here so the caller
    reads columns it chose rather than columns PoseBusters chose.
    """

    wanted = set(checks)
    modules: list[dict[str, Any]] = []
    for name, produced, spec in _MODULE_SPECS:
        if not wanted & set(produced):
            continue
        outputs = {source: target for source, target in spec["outputs"].items() if target in wanted}
        module: dict[str, Any] = {
            "name": name,
            "function": spec["function"],
            "chosen_binary_test_output": sorted(outputs),
            "rename_outputs": outputs,
        }
        if "parameters" in spec:
            module["parameters"] = spec["parameters"]
        modules.append(module)

    return {
        "modules": modules,
        "top_n": None,
        # PoseBusters would otherwise start its own pool inside a shard that is
        # already one process of MolCascade's, and oversubscribe every core.
        #
        # Zero, not one: the branch that runs in-process is
        # ``max_workers <= 0`` (posebusters.py:182), so ``1`` still builds a
        # ``ProcessPoolExecutor`` -- one that forks a child out of a shard
        # already holding RDKit and torch threads.  Python 3.12 warns about that
        # and 3.14 makes it an error, and a single worker buys nothing anyway
        # when the parallelism belongs to the shard above.
        "max_workers": 0,
        "loading": {
            "mol_pred": {
                "cleanup": False,
                "sanitize": False,
                "add_hs": False,
                "assign_stereo": False,
                "load_all": True,
            },
            "mol_cond": {
                "cleanup": False,
                "sanitize": False,
                "add_hs": False,
                "assign_stereo": False,
                "proximityBonding": False,
            },
        },
    }


def _require_posebusters() -> Any:
    try:
        from posebusters import PoseBusters
    except ImportError as error:  # pragma: no cover - exercised by the extra
        raise PluginError(
            "pose checking is enabled but PoseBusters is not installed",
            code="DOCKING_POSE_CHECK_BACKEND_MISSING",
            hint=_POSEBUSTERS_HINT,
            context={"package": "posebusters"},
        ) from error
    return PoseBusters


def judge_poses(
    molecules: Sequence[Any],
    *,
    receptor: Any,
    checks: Sequence[str],
) -> list[dict[str, bool]]:
    """Ask PoseBusters about a batch of poses against one receptor.

    One call for the batch rather than one per pose: PoseBusters re-derives
    nothing between molecules, but the call itself has a fixed cost worth paying
    once.  ``receptor`` is an already-loaded molecule, so a 2 000-atom protein
    is parsed once per shard instead of once per ligand.

    A check PoseBusters returns as null is reported as a failure.  Null means it
    could not decide -- the ensemble that would not generate, most often -- and a
    pose nothing could decide about is not a pose to hand onward.
    """

    if not molecules:
        return []
    pose_busters = _require_posebusters()
    buster = pose_busters(config=posebusters_config(checks))
    frame = buster.bust(mol_pred=list(molecules), mol_true=None, mol_cond=receptor)

    missing: list[Any] = sorted(set(checks) - set(frame.columns))
    if missing:
        reported: list[Any] = sorted(str(column) for column in frame.columns)
        raise PluginError(
            "PoseBusters did not report checks that were required",
            code="DOCKING_POSE_CHECK_UNAVAILABLE",
            hint=(
                "This is a PoseBusters version whose output columns differ from "
                "the ones MolCascade asks for. Pin the version named in the "
                "'docking' extra, or drop the check from 'pose_quality.checks'."
            ),
            context={"missing_checks": missing, "reported": reported},
        )

    verdicts: list[dict[str, bool]] = []
    for _index, row in frame.iterrows():
        verdicts.append(
            {name: bool(row[name]) if row[name] is not None else False for name in checks}
        )
    if len(verdicts) != len(molecules):
        raise PluginError(
            "PoseBusters reported a different number of poses than it was given",
            code="DOCKING_POSE_CHECK_UNAVAILABLE",
            hint="This is a PoseBusters behaviour change; pin the version in the 'docking' extra.",
            context={"poses_given": len(molecules), "rows_returned": len(verdicts)},
        )
    return verdicts


class PoseQualitySession:
    """One shard's worth of pose repair and judgement.

    A session rather than a free function because two of the three engines
    write their score rows one batch at a time inside a loop: the receptor has
    to be read once for the shard rather than once per batch, and the counters
    have to accumulate across batches to arrive as a single ``ShardOutcome``.

    Both the receptor field (coordinates and radii, for the repair) and the
    receptor molecule (for PoseBusters' intermolecular checks) are loaded on
    first use, so a shard that turns out to have no poses at all -- every ligand
    unscored, or checking switched off -- never opens the file.
    """

    __slots__ = ("_config", "_field", "_receptor", "_receptor_loaded", "_receptor_path", "report")

    def __init__(self, config: PoseQualityConfig, *, receptor_path: str) -> None:
        self._config = config
        self._receptor_path = receptor_path
        self._field: ReceptorField | None = None
        self._receptor: Any = None
        self._receptor_loaded = False
        self.report = PoseQualityReport()

    @property
    def config(self) -> PoseQualityConfig:
        return self._config

    def _receptor_field(self) -> ReceptorField:
        if self._field is None:
            self._field = load_receptor_field(
                self._receptor_path, ignore_waters=self._config.ignore_waters
            )
        return self._field

    def _receptor_molecule(self) -> Any:
        if not self._receptor_loaded:
            from rdkit import Chem

            self._receptor = Chem.MolFromPDBFile(
                self._receptor_path, removeHs=False, sanitize=False, proximityBonding=False
            )
            self._receptor_loaded = True
            if self._receptor is None:
                raise PluginError(
                    "the receptor structure could not be read for pose checking",
                    code="DOCKING_POSE_RECEPTOR_UNREADABLE",
                    hint="Pose checking reads the same receptor the engine docked against.",
                    context={"receptor_path": self._receptor_path},
                )
        return self._receptor

    def apply(
        self,
        rows: list[dict[str, Any]],
        *,
        keep_poses: bool,
    ) -> list[dict[str, Any]]:
        """Repair and judge one batch of ``docking_score/v1`` rows.

        ``keep_poses`` is separate from ``config.enabled`` on purpose.  Checking
        a pose and *storing* one are different requests: an operator who only
        wants scores still wants the scores to be about structures that exist,
        so the geometry is read and judged either way and the column is nulled
        afterwards if they did not ask to keep it.
        """

        config = self._config
        report = self.report
        if not config.enabled:
            if not keep_poses:
                for row in rows:
                    row["pose_molblock"] = None
            return rows

        from rdkit import Chem

        pending: list[tuple[int, Any]] = []
        unparsable: set[int] = set()
        for position, row in enumerate(rows):
            molblock = row.get("pose_molblock")
            if not isinstance(molblock, str) or not molblock:
                report.skipped += 1
                continue
            try:
                molecule = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=True)
            except (RuntimeError, ValueError):
                molecule = None
            if molecule is None or molecule.GetNumConformers() == 0:
                report.unparsable += 1
                unparsable.add(position)
                continue
            pending.append((position, molecule))

        if not pending and not unparsable:
            # Nothing to judge.  Poses may simply not have been requested, and
            # every engine already counts that; failing here would refuse a
            # scores-only run.  The counters say which case this was.
            if not keep_poses:
                for row in rows:
                    row["pose_molblock"] = None
            return rows

        report.considered += len(pending)

        if config.relax and pending:
            field_ = self._receptor_field()
            for _position, molecule in pending:
                moved = relax_pose(molecule, receptor=field_, config=config)
                if moved is None:
                    report.relax_failed += 1
                else:
                    report.relaxed += 1
                    report.displacement_total += moved

        rejected: set[int] = set()
        if config.checks and pending:
            verdicts = judge_poses(
                [molecule for _position, molecule in pending],
                receptor=self._receptor_molecule(),
                checks=config.checks,
            )
            for (position, _molecule), verdict in zip(pending, verdicts, strict=True):
                failed = [name for name, ok in verdict.items() if not ok]
                if failed:
                    for name in failed:
                        report.failures[name] = report.failures.get(name, 0) + 1
                    rejected.add(position)
                else:
                    report.passed += 1
        else:
            report.passed += len(pending)

        # The repaired geometry replaces what the engine wrote, so a kept pose is
        # the one that was judged.  Handing MD the unrepaired coordinates while
        # claiming the repaired ones passed would be the worst of both.
        if keep_poses:
            for position, molecule in pending:
                try:
                    rows[position]["pose_molblock"] = str(Chem.MolToMolBlock(molecule))
                except (RuntimeError, ValueError):
                    rows[position]["pose_molblock"] = None
        else:
            for row in rows:
                row["pose_molblock"] = None

        if not config.discard_failing:
            return rows

        # A pose that would not parse is discarded alongside one that failed a
        # check.  Both are structures nothing could vouch for, and the
        # alternative is passing a molblock downstream that already failed to
        # become a molecule here.  Rows that never carried a pose are untouched:
        # those are the engine's own misses, counted separately and already
        # visible.
        removed = rejected | unparsable
        kept = [row for position, row in enumerate(rows) if position not in removed]
        report.discarded += len(rows) - len(kept)
        if len(kept) != len(rows):
            _renumber_poses(kept)
        return kept


def apply_pose_quality(
    rows: list[dict[str, Any]],
    *,
    config: PoseQualityConfig,
    receptor_path: str,
    keep_poses: bool,
) -> tuple[list[dict[str, Any]], PoseQualityReport]:
    """Repair and judge a whole shard's score rows in one call.

    The single-batch form, for the engine that docks its entire shard at once
    and for tests.  Engines that emit rows batch by batch build a
    :class:`PoseQualitySession` instead and call ``apply`` per batch, so the
    receptor is read once and the counters accumulate.
    """

    session = PoseQualitySession(config, receptor_path=receptor_path)
    kept = session.apply(rows, keep_poses=keep_poses)
    return kept, session.report


def _renumber_poses(rows: list[dict[str, Any]]) -> None:
    """Close the gaps discarding leaves in ``pose_rank``.

    Two consumers read the best pose as *rank zero* rather than as the first
    surviving row -- ``handoff.py`` filters ``pose_rank == 0`` to build the
    best-pose view, and ``trace.py`` does the same to choose which pose enters
    the shortlist SDF.  Discarding a rank-zero pose while a lower-ranked one
    survives would therefore hide the molecule from both, which is the opposite
    of what a quality check is for: the pose that passed would be the one nobody
    could see.  Renumbering also restores the ``pose_rank_ordered`` invariant
    and keeps the contract's ``(parent_id, engine_id, receptor_id, pose_rank)``
    primary key contiguous.

    Rows arrive best-first within a ligand -- every engine ranks before
    emitting -- so renumbering in place preserves the ordering it repairs.
    """

    counters: dict[tuple[Any, Any, Any], int] = {}
    for row in rows:
        key = (row.get("parent_id"), row.get("engine_id"), row.get("receptor_id"))
        rank = counters.get(key, 0)
        row["pose_rank"] = rank
        counters[key] = rank + 1


#: The counters every shard reports, whatever the configuration.
_COUNTER_KEYS = (
    "pose_checked_count",
    "pose_unchecked_count",
    "pose_unparsable_count",
    "pose_relaxed_count",
    "pose_relax_failed_count",
    "pose_passed_count",
    "pose_discarded_count",
    "pose_displacement_total_mA",
)


def pose_quality_metadata(
    total: Callable[[str], int],
    *,
    config: PoseQualityConfig,
) -> dict[str, Any]:
    """Summarise the shard counters for a stage's response metadata.

    ``total`` is ``ShardedResult.total``: one integer counter summed across
    every shard, reused ones included.  The per-check failure keys are flat
    (``pose_check_failed__<name>``) for exactly that reason -- a nested mapping
    could not be summed by a function that adds integers.  Only a requested
    check can fail, so ``config.checks`` enumerates them completely.
    """

    counters: dict[str, Any] = {key: total(key) for key in _COUNTER_KEYS}
    for name in config.checks:
        key = f"pose_check_failed__{name}"
        count = total(key)
        if count:
            counters[key] = count

    payload: dict[str, Any] = {
        "pose_quality_enabled": config.enabled,
        "pose_quality_relax": config.relax and config.enabled,
        "pose_quality_discard_failing": config.discard_failing and config.enabled,
        "pose_quality_checks": list(config.checks) if config.enabled else [],
        "pose_quality_id": config.digest(),
        **counters,
    }
    relaxed = counters["pose_relaxed_count"]
    if relaxed:
        payload["pose_mean_displacement"] = round(
            counters["pose_displacement_total_mA"] / (1000.0 * relaxed), 4
        )
    checked = counters["pose_checked_count"]
    if checked:
        payload["pose_pass_fraction"] = round(counters["pose_passed_count"] / checked, 4)
    return payload


__all__ = [
    "AVAILABLE_CHECKS",
    "DEFAULT_CHECKS",
    "OPTIONAL_CHECKS",
    "PoseQualityConfig",
    "PoseQualityReport",
    "PoseQualitySession",
    "ReceptorField",
    "apply_pose_quality",
    "judge_poses",
    "load_receptor_field",
    "pose_quality_metadata",
    "posebusters_config",
    "relax_pose",
]
