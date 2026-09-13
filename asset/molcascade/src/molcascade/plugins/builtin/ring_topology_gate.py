"""Ring-system shape and rigidity, as a window a project can move.

The property window upstream asks how *heavy* a molecule is.  This one asks what
*shape* it is, and it exists because the two turn out to be almost independent
on a generated library.

Three-dimensional generative models emit a characteristic failure that no window
in this catalogue catches.  Rings are cheap for them to draw and each new ring
adds contact surface without adding much weight, so they produce molecules that
are seven or eight rings fused into one continuous polycyclic sheet, carrying no
rotatable bond at all.  Those molecules sit comfortably inside Lipinski, score
well on QED, raise no PAINS match and pass every ADMET endpoint -- and then dock
*better* than the flexible molecules around them, because a rigid planar surface
makes many contacts and pays no conformational entropy penalty for doing it.
Measured over one 2,000-molecule run of this pipeline, molecules whose largest
fused system held four or more rings were 71.5% of the input, 48.7% after the
synthesis tier, and 83.3% of the final shortlist.  The funnel does not select
against this shape; docking selects *for* it.

The reason no existing rule catches it is a counting convention, and it is worth
naming precisely because it is easy to reimplement the same mistake here.
``medchem``'s ``rule_of_generative_design`` looks like it caps fusion --
``N_FUSED_AROMATIC_RINGS_TOGETHER <= 2`` -- and Mordred offers ``nFRing``, which
reads like a fused-ring count.  Both count fused *systems*, not the rings inside
one.  Pentacene returns 1 from each.  A fourteen-ring cage returns 1 from Mordred
and 0 from medchem, the latter because it additionally requires every ring in the
system to be aromatic and one saturated lactam anywhere in the cage zeroes the
count.  Neither library exposes "how many rings are fused into the largest
system", which is the one number that describes the problem, so this gate
computes it.

What the older rules do provide is a *size* cap on the same system, in atoms:
FAF-Drugs4's drug-like filter and ``medchem``'s generative-design rule both stop
at 18.  That cap is real and is kept here as its own field, but it is a proxy and
peri-fusion defeats it -- pyrene packs four rings into sixteen atoms.  On the run
described above the atom cap caught 74% of the offending input molecules and only
54% of the ones that reached the shortlist, because what survives docking is
precisely the compact, densely fused kind.

Defaults, and why each one is the number it is:

``max_rings = 6`` and ``max_ring_system_size = 18``
    Both are the FAF-Drugs4 "Drug-Like" filter's own values, chosen by its
    authors so that up to 90% of 916 FDA-approved oral drugs pass.  Taking them
    unchanged means this gate's default strictness is a published, calibrated
    quantity rather than a preference.

``max_fused_rings = 4``
    The number this module exists to provide, so it has no filter to inherit
    from.  Toxtree's SA_18 treats three or more fused aromatic rings as a
    mutagenicity alert, and ring systems in drugs are predominantly mono- and
    bicyclic.  Four is deliberately one step looser than the alert, which is
    what buys the steroids: estradiol and dexamethasone are four fused rings
    each.  On a panel of thirty-three approved drugs it costs exactly morphine,
    camptothecin and artemisinin -- three pentacyclic natural products, none of
    them the kind of thing a generative model is producing.  Thirty of the
    thirty-three clear all three default bounds together, which is the same 91%
    that FAF-Drugs4 calibrated its own numbers against.
    ``tests/plugins/test_ring_topology_gate.py`` pins that panel, so if a
    default moves, the drugs it costs are named in a failing assertion.

``max_fused_ring_total`` is a second, independent way to ask the same question,
and it ships off.  ``max_fused_rings`` bounds the largest system; a molecule
carrying three separate naphthalenes satisfies it at 2 and is still six fused
rings of flat, unrotatable surface.  Summing the rings over every fused system
catches that, and against the seventy-seven-drug oral panel in
``tests/fixtures`` a total of four costs nothing ``max_fused_rings = 4`` has not
already taken -- which is exactly why it cannot be defended as a default.  It
earns its keep only when the per-system cap is tightened: at
``max_fused_rings = 2`` the pair is a real rule, "at most two bicyclics", and
there the total is what stops a molecule from stringing naphthalenes together.
That pair costs ten of the seventy-seven, and ten of the thirty-three panelled
below, so it is a deliberate choice and not a default.

Everything else defaults to ``None``, meaning off, and that is a finding rather
than an omission.  A rotatable-bond *floor* is the most obvious way to express
"this molecule cannot move", and the literature will not support one as a
default: conformational restriction is a standard optimisation tactic, Veber's
rule gives a ceiling and no floor, and on the same panel a floor of three rotors
rejects twelve of the thirty-three, including caffeine, estradiol, morphine and
olanzapine, which have none at all.  A ring-atom-fraction ceiling of 0.75
rejects eleven, olanzapine at 0.91 and imatinib at 0.81, and
``min_fraction_csp3`` at 0.25 rejects seven, imatinib again at 0.24 --
Lovering's 0.36-to-0.47 figures are cohort *means*, a description of what
succeeded rather than a cutoff.  These fields are offered because on a
generated library they are exactly the right knobs to reach for, and they are
left off because a default has to be defensible against approved drugs.

Citations:
Lagorce D, Bouslama L, Becot J, Miteva MA, Villoutreix BO. FAF-Drugs4: free
ADME-tox filtering computations for chemical biology and early stages drug
discovery. Bioinformatics. 2017;33(22):3658-3660.
doi:10.1093/bioinformatics/btx491 (rings <= 6, ring system <= 18 atoms)
Benigni R, Bossa C, Jeliazkova N, Netzeva T, Worth A. The Benigni/Bossa rulebase
for mutagenicity and carcinogenicity. JRC Scientific and Technical Report
EUR 23241 EN. 2008. doi:10.2788/60246 (SA_18, fused polycyclic aromatics)
Shearer J, Castro JL, Lawson ADG, MacCoss M, Taylor RD. Rings in clinical trials
and drugs: present and future. J Med Chem. 2022;65(13):8699-8712.
doi:10.1021/acs.jmedchem.2c00473 (ring-system size distribution in drugs)
Veber DF, Johnson SR, Cheng H-Y, Smith BR, Ward KW, Kopple KD. Molecular
properties that influence the oral bioavailability of drug candidates. J Med
Chem. 2002;45(12):2615-2623. doi:10.1021/jm020017n (rotatable bonds)
Lovering F, Bikker J, Humblet C. Escape from flatland: increasing saturation as
an approach to improving clinical success. J Med Chem. 2009;52(21):6752-6756.
doi:10.1021/jm901241e (fraction of sp3 carbon)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, model_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

if TYPE_CHECKING:  # pragma: no cover - import cost is paid only by type checkers
    from rdkit.Chem import Mol

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")


class RingTopologyGateConfig(StrictFrozenModel):
    """Bounds on ring topology and rigidity. ``None`` means the check is off."""

    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)

    #: FAF-Drugs4 drug-like: <=6 rings, calibrated to pass 90% of 916 approved
    #: oral drugs.
    max_rings: int | None = Field(default=6, ge=0, le=1_000)
    #: Rings fused into one system -- the number nothing else in the catalogue
    #: reports.  See the module docstring for why 4 rather than 3 or 5.
    max_fused_rings: int | None = Field(default=4, ge=1, le=1_000)
    #: Rings summed across *every* fused system, ignoring rings that stand
    #: alone.  ``max_fused_rings`` bounds one system; this bounds how much
    #: fused-ring content a molecule may carry in total, so three separate
    #: bicyclics cannot pass a rule written to allow two.  Off by default: no
    #: published filter states this threshold, and on the approved oral panel
    #: it costs nothing that ``max_fused_rings`` has not already taken.
    max_fused_ring_total: int | None = Field(default=None, ge=0, le=1_000)
    #: Heavy atoms in the largest fused system.  FAF-Drugs4's own cap, and the
    #: same 18 that ``medchem``'s generative-design rule uses.
    max_ring_system_size: int | None = Field(default=18, ge=3, le=1_000)
    #: Atoms in the largest *single* ring.  Off by default: macrocyclic drugs
    #: are legitimate chemistry, and 12 is the conventional macrocycle
    #: threshold if a project wants to exclude them.
    max_ring_size: int | None = Field(default=None, ge=3, le=1_000)
    #: Off by default -- see the module docstring.  Rigidity is not by itself a
    #: liability, and no published rule sets a floor here.
    min_rotatable_bonds: int | None = Field(default=None, ge=0, le=1_000)
    #: Ring atoms over heavy atoms.  Off by default; olanzapine is 0.91.
    max_ring_atom_fraction: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Off by default; a 0.25 floor rejects imatinib at 0.24.
    min_fraction_csp3: float | None = Field(default=None, ge=0.0, le=1.0)
    #: Atoms shared by three rings -- the signature of a cage.  Off by default.
    max_bridgehead_atoms: int | None = Field(default=None, ge=0, le=1_000)

    @model_validator(mode="after")
    def _at_least_one_bound(self) -> Self:
        """A gate that bounds nothing passes everything, silently.

        Clearing all eight fields is a plausible way to try to disable this
        criterion, and it would produce a stage that reports a pass rate of 100%
        for every library forever.  Removing the criterion is the way to
        disable it; this refuses the configuration that only looks like it.
        """

        bounds = (
            self.max_rings,
            self.max_fused_rings,
            self.max_fused_ring_total,
            self.max_ring_system_size,
            self.max_ring_size,
            self.min_rotatable_bonds,
            self.max_ring_atom_fraction,
            self.min_fraction_csp3,
            self.max_bridgehead_atoms,
        )
        if all(bound is None for bound in bounds):
            raise ValueError(
                "ring topology gate needs at least one bound; remove the criterion "
                "instead of clearing every field"
            )
        return self


def _policy_id(config: RingTopologyGateConfig, backend_version: str) -> str:
    """The identity of the bounds applied, independent of batching."""

    policy = config.model_dump(mode="json")
    policy.pop("batch_size", None)
    return "ring-topology-policy:sha256:" + canonical_sha256(
        {"backend": "rdkit", "backend_version": backend_version, "policy": policy}
    )


def _fused_systems(molecule: Mol) -> list[tuple[int, set[int]]]:
    """Group the rings into fused systems: ``(ring count, atom set)`` each.

    Two rings are fused when they share a bond, which for ring *atom* sets means
    sharing two atoms or more.  Sharing exactly one atom is a spiro junction --
    the two rings still rotate relative to each other about that atom, so a
    spiro pair is not the rigid sheet this gate is looking for and is
    deliberately left as two systems.

    The count is however many rings RDKit's symmetrised SSSR puts in the system.
    That is the cyclomatic number, bonds minus atoms plus one, except on a
    symmetric cage, where every smallest ring of the tied size is kept rather
    than an arbitrary basis of them: bicyclo[2.2.2]octane counts 3 and
    adamantane 4, against a cyclomatic 2 and 3.  Which way that error runs
    matters more than its size, and it runs toward counting more rings, so a
    cage never slips under a cap by being symmetric.  It is also why a bridged
    bicyclic is not reliably "2": quinuclidine is 3 here and a
    ``max_fused_rings`` of 2 rejects quinine on that basis alone.
    """

    systems: list[set[int]] = []
    counts: list[int] = []
    for ring in molecule.GetRingInfo().AtomRings():
        atoms = set(ring)
        merged_count = 1
        survivors: list[set[int]] = []
        survivor_counts: list[int] = []
        for existing, count in zip(systems, counts, strict=True):
            if len(atoms & existing) >= 2:
                atoms |= existing
                merged_count += count
            else:
                survivors.append(existing)
                survivor_counts.append(count)
        systems = [*survivors, atoms]
        counts = [*survivor_counts, merged_count]
    return list(zip(counts, systems, strict=True))


def _measure(molecule: Mol) -> dict[str, float | int]:
    """Every number this gate can bound, computed once per molecule."""

    from rdkit.Chem import rdMolDescriptors

    ring_info = molecule.GetRingInfo()
    heavy_atoms = molecule.GetNumHeavyAtoms()
    systems = _fused_systems(molecule)
    ring_atoms = sum(1 for atom in molecule.GetAtoms() if atom.IsInRing())
    return {
        "rings": int(ring_info.NumRings()),
        "fused_rings": max((count for count, _ in systems), default=0),
        "fused_ring_total": sum(count for count, _ in systems if count >= 2),
        "ring_system_size": max((len(atoms) for _, atoms in systems), default=0),
        "ring_size": max((len(ring) for ring in ring_info.AtomRings()), default=0),
        "rotatable_bonds": int(rdMolDescriptors.CalcNumRotatableBonds(molecule)),
        "ring_atom_fraction": (ring_atoms / heavy_atoms) if heavy_atoms else 0.0,
        "fraction_csp3": float(rdMolDescriptors.CalcFractionCSP3(molecule)),
        "bridgehead_atoms": int(rdMolDescriptors.CalcNumBridgeheadAtoms(molecule)),
    }


def _check(
    value: float | int,
    *,
    name: str,
    minimum: float | int | None,
    maximum: float | int | None,
) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    label = name.upper()
    if minimum is not None and value < minimum:
        findings.append(
            (f"RING_TOPOLOGY_{label}_BELOW_MIN", f"{name}={value!r} is below {minimum!r}")
        )
    if maximum is not None and value > maximum:
        findings.append(
            (f"RING_TOPOLOGY_{label}_ABOVE_MAX", f"{name}={value!r} is above {maximum!r}")
        )
    return findings


#: ``(measured name, floor, ceiling)`` for every bound, in report order.
_BOUNDS: tuple[tuple[str, str | None, str | None], ...] = (
    ("rings", None, "max_rings"),
    ("fused_rings", None, "max_fused_rings"),
    ("fused_ring_total", None, "max_fused_ring_total"),
    ("ring_system_size", None, "max_ring_system_size"),
    ("ring_size", None, "max_ring_size"),
    ("rotatable_bonds", "min_rotatable_bonds", None),
    ("ring_atom_fraction", None, "max_ring_atom_fraction"),
    ("fraction_csp3", "min_fraction_csp3", None),
    ("bridgehead_atoms", None, "max_bridgehead_atoms"),
)


def _findings(
    measured: dict[str, float | int], config: RingTopologyGateConfig
) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for name, floor, ceiling in _BOUNDS:
        findings += _check(
            measured[name],
            name=name,
            minimum=None if floor is None else getattr(config, floor),
            maximum=None if ceiling is None else getattr(config, ceiling),
        )
    return findings


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Gate one contiguous range of parents, in whichever process owns it.

    Module-level and self-contained on purpose: ``spawn`` re-imports this module
    in a fresh interpreter and calls the function by name, so it may close over
    nothing.
    """

    from rdkit import Chem, rdBase

    config = RingTopologyGateConfig.model_validate(dict(task.config))
    policy_id = _policy_id(config, rdBase.rdkitVersion)
    rows_in = 0
    passed_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parents,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decisions,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            passed: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                rows_in += 1
                parent_id = row.get("parent_id")
                smiles = row.get("parent_smiles")
                molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed by the ring topology gate",
                        code="RING_TOPOLOGY_PARENT_INVALID",
                        context={"parent_id": str(parent_id)},
                    )
                measured = _measure(molecule)
                if any(
                    isinstance(value, float) and not math.isfinite(value)
                    for value in measured.values()
                ):
                    raise PluginError(
                        "RDKit produced a non-finite ring topology measurement",
                        code="RING_TOPOLOGY_NON_FINITE",
                        context={"parent_id": str(parent_id)},
                    )
                findings = _findings(measured, config)
                if findings:
                    for reason_code, detail in findings:
                        decision_rows.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": task.stage_id,
                                "outcome": "REJECT",
                                "reason_code": reason_code,
                                "rule_id": policy_id,
                                "detail": detail,
                            }
                        )
                else:
                    passed.append(row)
                    passed_count += 1
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": "RING_TOPOLOGY_PASS",
                            "rule_id": policy_id,
                            "detail": json.dumps(
                                measured,
                                ensure_ascii=False,
                                allow_nan=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    )
                decision_count += max(1, len(findings))
            if passed:
                parents.write_table(pa.Table.from_pylist(passed, schema=PARENT_V1.schema))
            decisions.write_table(pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema))
    return ShardOutcome(
        rows_in=rows_in,
        rows_out={"primary": passed_count, "decisions": decision_count},
    )


class RDKitRingTopologyGatePlugin:
    descriptor = PluginDescriptor(
        id="chemistry.rdkit_ring_topology_gate",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit ring topology and rigidity gate",
        description=(
            "Bounds on ring count, fused-system size, ring size, rotors, ring-atom "
            "fraction, sp3 fraction and bridgeheads, with full decisions."
        ),
    )
    config_model = RingTopologyGateConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid ring topology gate configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        policy_id = _policy_id(config, rdBase.rdkitVersion)
        result = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "decisions": _DECISION_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config=config.model_dump(mode="json"),
        )
        if result.rows_in == 0:
            raise PluginError(
                "ring-topology-gate input contains no parents",
                code="RING_TOPOLOGY_EMPTY_INPUT",
            )
        passed_count = result.rows_out["primary"]
        decision_count = result.rows_out["decisions"]
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": passed_count},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": decision_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": result.rows_in,
                "output_count": passed_count,
                "reject_count": result.rows_in - passed_count,
                "decision_count": decision_count,
                "policy_id": policy_id,
                "backend": "rdkit",
                "backend_version": rdBase.rdkitVersion,
                **result.response_metadata(),
            },
        )


__all__ = ["RDKitRingTopologyGatePlugin", "RingTopologyGateConfig"]
