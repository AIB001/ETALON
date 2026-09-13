"""Hand a structure to a simulation stack, or refuse to pretend you have one.

The shortlist exporters beside this one answer "which molecules survived".  They
build their records from ``parent_smiles``, which is correct for their purpose and
wrong for this one: a SMILES carries no coordinates, no hydrogens and no chosen
protonation state, and the SDF exporter therefore emits a flat depiction -- every
z exactly zero, every hydrogen implicit, the layout arbitrary.  Measured on
propranolol: 19 heavy atoms, 0 explicit hydrogens, z range 0.00 to 0.00.

That record parameterises.  A force-field generator accepts it, a solvation step
accepts it, a simulation runs, and the analysis comes back with contact
proportions of zero and a binding energy near zero with a standard deviation
beside it.  Nothing fails.  The receiving side's own validator checks that the
file exists, is non-empty, has a recognised suffix and a positive atom count in
its counts line -- all true of a drawing.

The information to do better is already in the run.  ``ligand_prep`` calls
``Chem.AddHs`` before embedding and publishes the result as
``ligand_conformer/v1``; a docking stage publishes ``pose_molblock`` bound to a
``receptor_id`` in ``docking_score/v1``.  This adapter reads those instead of
re-deriving from a name, and publishes ``md_system_input/v1``, whose non-null
columns and declared enums make the fields a producer would otherwise leave
unsaid into fields it cannot leave empty.

Two behaviours are the point rather than details.

**It prefers a pose, then a conformer, and never invents.**  A molecule the run
produced no geometry for keeps a row with a null molblock, ``coordinate_source``
of ``NONE`` and ``status`` of ``NO_GEOMETRY``.  It is not quietly dropped, because
a missing row reads downstream as a molecule that was fine, and it is not filled
in from SMILES, because a depiction that parameterises is worse than an absence
that stops.

**Stereochemistry is re-derived from the coordinates rather than copied from the
name.**  ``enforceChirality`` constrains the centres a SMILES already assigns and
settles the rest by distance geometry from a seeded hash, so the molecule in these
coordinates is one isomer of the set its name designates.  ``stereo_smiles``
describes the isomer; ``parent_smiles`` stays beside it so a consumer can see they
differ.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.chemistry.datasets import iter_contract_batches, require_single_input
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import (
    DOCKING_SCORE_V1,
    LIGAND_CONFORMER_V1,
    MD_SYSTEM_INPUT_V1,
    PARENT_V1,
)
from molcascade.errors import PluginError
from molcascade.plugins.api import (
    PendingOutput,
    StageContext,
    StageRequest,
    StageResponse,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_HANDOFF_PATH = Path("datasets/md_system_inputs/part-00000.parquet")

#: Which geometry wins when a run produced both.  A docked pose is a hypothesis
#: about where the molecule sits in a particular receptor; a free conformer is a
#: hypothesis about its shape alone.  For anything that will put the ligand back
#: in the pocket the pose is the one to carry, and it carries its receptor with it.
_PREFERENCE = ("DOCKED_POSE", "EMBEDDED_CONFORMER")


class MdHandoffConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=4_096, ge=1, le=250_000)

    #: Which geometry to prefer when both a pose and a conformer exist.
    #: ``docked_pose`` is the default because a record headed for a protein-ligand
    #: system should arrive in the pocket it was scored in.
    prefer: str = "docked_pose"

    #: Pose rank to take.  0 is the top-scoring pose, which is the geometry the
    #: docking tier's number is about; a higher rank is a different hypothesis and
    #: selecting one is a decision worth naming rather than defaulting into.
    pose_rank: int = Field(default=0, ge=0, le=64)

    #: What produced the protonation state these coordinates carry.  There is no
    #: protonation predictor in this project, so the honest default says where the
    #: state came from rather than claiming one was computed.  Set it when an
    #: upstream step really did decide: a pKa prediction, a manual curation, a
    #: fixed pH assumption recorded elsewhere.
    protonation_state_id: str = Field(
        default="INHERITED_FROM_STANDARDIZER", min_length=1, max_length=128
    )

    @field_validator("prefer")
    @classmethod
    def _known_preference(cls, value: str) -> str:
        if value not in ("docked_pose", "embedded_conformer"):
            raise ValueError("prefer must be docked_pose or embedded_conformer")
        return value

    def preference(self) -> tuple[str, ...]:
        if self.prefer == "embedded_conformer":
            return tuple(reversed(_PREFERENCE))
        return _PREFERENCE


def _validated_config(request: StageRequest) -> MdHandoffConfig:
    try:
        return MdHandoffConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid MD handoff configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _method_id(config: MdHandoffConfig) -> str:
    return "md-handoff:sha256:" + canonical_sha256(
        {
            "prefer": config.prefer,
            "pose_rank": config.pose_rank,
            "protonation_state_id": config.protonation_state_id,
            "semantics": "declared-geometry-handoff-not-a-binding-claim",
        }
    )


def _read_geometry(
    request: StageRequest,
    *,
    pose_rank: int,
    batch_size: int,
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """``parent_id`` to ``(molblock, receptor_id)`` for poses, and to molblock for conformers.

    Both side inputs are optional: a ligand-only cascade has conformers and no
    poses, a cascade that stops at 2D has neither.  What it must not do is invent
    the missing one, so an absent side input yields an empty mapping and the
    molecules fall through to ``NO_GEOMETRY``.
    """

    poses: dict[str, tuple[str, str]] = {}
    conformers: dict[str, str] = {}

    for name, stage_input in request.inputs.items():
        contract_id = getattr(stage_input, "contract_id", None)
        if contract_id == DOCKING_SCORE_V1.id:
            for batch in iter_contract_batches(
                stage_input, DOCKING_SCORE_V1, batch_size=batch_size
            ):
                columns = ["parent_id", "pose_rank", "pose_molblock", "receptor_id"]
                for row in batch.select(columns).to_pylist():
                    if int(row["pose_rank"] or 0) != pose_rank:
                        continue
                    molblock = row.get("pose_molblock")
                    if not molblock:
                        continue
                    # First writer wins: a tier docking with two engines publishes
                    # this contract twice, and silently blending two engines'
                    # geometry for one molecule would produce a record that is
                    # neither engine's answer.
                    poses.setdefault(
                        str(row["parent_id"]),
                        (str(molblock), str(row["receptor_id"])),
                    )
        elif contract_id == LIGAND_CONFORMER_V1.id:
            for batch in iter_contract_batches(
                stage_input, LIGAND_CONFORMER_V1, batch_size=batch_size
            ):
                columns = ["parent_id", "conformer_index", "molblock"]
                for row in batch.select(columns).to_pylist():
                    if int(row["conformer_index"] or 0) != 0:
                        continue
                    molblock = row.get("molblock")
                    if molblock:
                        conformers.setdefault(str(row["parent_id"]), str(molblock))
        elif contract_id not in (PARENT_V1.id, None):
            raise PluginError(
                "the MD handoff received an input it cannot read",
                code="MD_HANDOFF_INPUT_UNEXPECTED",
                context={"port": name, "contract_id": str(contract_id)},
            )
    return poses, conformers


def _describe(molblock: str, parent_smiles: str) -> dict[str, Any]:
    """Count what is actually in the coordinates, and name the isomer they are.

    Every field here is read from the structure rather than asserted about it,
    because the whole failure this adapter exists to prevent is a record whose
    declared properties and actual contents disagree.
    """

    from rdkit import Chem, rdBase

    with rdBase.BlockLogs():
        # removeHs=False: the hydrogen count is the reason for reading this, so
        # stripping them before counting would answer a different question.
        molecule = Chem.MolFromMolBlock(molblock, sanitize=True, removeHs=False)
        if molecule is None or molecule.GetNumConformers() == 0:
            return {
                "status": "UNREADABLE",
                "status_detail": "RDKit could not read the molblock as a 3D structure",
                "heavy_atom_count": None,
                "hydrogen_count": None,
                "formal_charge": None,
                "hydrogens": "UNKNOWN",
                "stereo_smiles": None,
                "flat": None,
            }
        hydrogen_count = sum(
            1 for atom in molecule.GetAtoms() if atom.GetAtomicNum() == 1
        )
        heavy_atom_count = sum(
            1 for atom in molecule.GetAtoms() if atom.GetAtomicNum() > 1
        )
        polar = sum(
            1
            for atom in molecule.GetAtoms()
            if atom.GetAtomicNum() == 1
            and any(
                neighbour.GetAtomicNum() in (7, 8, 9, 15, 16)
                for neighbour in atom.GetNeighbors()
            )
        )
        conformer = molecule.GetConformer()
        flat = all(
            abs(conformer.GetAtomPosition(index).z) < 1e-6
            for index in range(molecule.GetNumAtoms())
        )
        Chem.AssignStereochemistryFrom3D(molecule)
        charge = Chem.GetFormalCharge(molecule)
        # Hydrogens removed for this string only, and the count above is taken
        # from the full molecule. The purpose of stereo_smiles is to be compared
        # against parent_smiles, and an explicit-hydrogen SMILES differs from a
        # parent name in every position -- which would bury the one difference
        # that matters under dozens that do not.
        stereo_smiles = Chem.MolToSmiles(Chem.RemoveHs(molecule))

    if hydrogen_count == 0:
        hydrogens = "IMPLICIT"
    elif polar == hydrogen_count:
        hydrogens = "POLAR_ONLY"
    else:
        hydrogens = "EXPLICIT_ALL"
    return {
        "status": "OK",
        "status_detail": None,
        "heavy_atom_count": heavy_atom_count,
        "hydrogen_count": hydrogen_count,
        "formal_charge": charge,
        "hydrogens": hydrogens,
        "stereo_smiles": stereo_smiles,
        "flat": flat,
    }


class MdHandoffPlugin:
    """Publish the geometry a run computed, with every property declared."""

    descriptor = PluginDescriptor(
        id="handoff.md_system_input",
        version="0.1.0",
        kind=PluginKind.EXPORTER,
        inputs=(PARENT_V1.id, LIGAND_CONFORMER_V1.id, DOCKING_SCORE_V1.id),
        outputs=(PARENT_V1.id, MD_SYSTEM_INPUT_V1.id),
        output_ports={"primary": PARENT_V1.id, "handoff": MD_SYSTEM_INPUT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="MD system input handoff",
        description=(
            "Publish the docked pose or embedded conformer a run already computed "
            "as md_system_input/v1, with the coordinate source, hydrogen state, "
            "formal charge, stereochemistry and receptor digest declared rather "
            "than left for a simulation stack to assume. A molecule with no "
            "geometry keeps a row saying so instead of being filled in from SMILES."
        ),
    )
    config_model = MdHandoffConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        parents = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        method_id = _method_id(config)
        poses, conformers = _read_geometry(
            request, pose_rank=config.pose_rank, batch_size=config.batch_size
        )

        context.staging_root.mkdir(parents=True, exist_ok=True)
        for relative in (_PARENT_PATH, _HANDOFF_PATH):
            destination = context.staging_root / relative
            if destination.exists() or destination.is_symlink():
                raise PluginError(
                    f"handoff output already exists: {relative.as_posix()}",
                    code="PLUGIN_STAGING_NOT_EMPTY",
                    context={"path": relative.as_posix()},
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
        parent_path = context.staging_root / _PARENT_PATH
        handoff_path = context.staging_root / _HANDOFF_PATH

        counters = {
            "input_count": 0,
            "output_count": 0,
            "from_pose": 0,
            "from_conformer": 0,
            "no_geometry": 0,
            "unreadable": 0,
            "flat_coordinates": 0,
            "implicit_hydrogens": 0,
        }
        with (
            pq.ParquetWriter(parent_path, PARENT_V1.schema, compression="zstd") as parent_writer,
            pq.ParquetWriter(
                handoff_path, MD_SYSTEM_INPUT_V1.schema, compression="zstd"
            ) as handoff_writer,
        ):
            for batch in iter_contract_batches(
                parents, PARENT_V1, batch_size=config.batch_size
            ):
                rows = batch.to_pylist()
                counters["input_count"] += len(rows)
                records: list[dict[str, Any]] = []
                for row in rows:
                    parent_id = str(row["parent_id"])
                    parent_smiles = str(row["parent_smiles"])
                    molblock: str | None = None
                    receptor_id: str | None = None
                    source = "NONE"
                    for candidate in config.preference():
                        if candidate == "DOCKED_POSE" and parent_id in poses:
                            molblock, receptor_id = poses[parent_id]
                            source = "DOCKED_POSE"
                            break
                        if candidate == "EMBEDDED_CONFORMER" and parent_id in conformers:
                            molblock = conformers[parent_id]
                            source = "EMBEDDED_CONFORMER"
                            break

                    if molblock is None:
                        counters["no_geometry"] += 1
                        records.append(
                            {
                                "parent_id": parent_id,
                                "method_id": method_id,
                                "molblock": None,
                                "coordinate_source": "NONE",
                                "hydrogens": "UNKNOWN",
                                "heavy_atom_count": None,
                                "hydrogen_count": None,
                                "formal_charge": None,
                                "stereo_smiles": None,
                                "parent_smiles": parent_smiles,
                                "protonation_state_id": config.protonation_state_id,
                                "receptor_id": None,
                                "status": "NO_GEOMETRY",
                                "status_detail": (
                                    "this run published no pose and no conformer for "
                                    "this molecule; a depiction built from its SMILES "
                                    "would parameterise and would not be a structure"
                                ),
                            }
                        )
                        counters["output_count"] += 1
                        continue

                    described = _describe(molblock, parent_smiles)
                    if described["status"] == "UNREADABLE":
                        counters["unreadable"] += 1
                    else:
                        counters["from_pose" if source == "DOCKED_POSE" else "from_conformer"] += 1
                        if described["flat"]:
                            counters["flat_coordinates"] += 1
                            # A flat structure is reported as a depiction whatever
                            # port it arrived on: the declared source has to describe
                            # the coordinates rather than their provenance, or the
                            # field an agent trusts would be the one that lies.
                            source = "TWO_D_DEPICTION"
                        if described["hydrogens"] == "IMPLICIT":
                            counters["implicit_hydrogens"] += 1
                    records.append(
                        {
                            "parent_id": parent_id,
                            "method_id": method_id,
                            "molblock": molblock,
                            "coordinate_source": source,
                            "hydrogens": described["hydrogens"],
                            "heavy_atom_count": described["heavy_atom_count"],
                            "hydrogen_count": described["hydrogen_count"],
                            "formal_charge": described["formal_charge"],
                            "stereo_smiles": described["stereo_smiles"],
                            "parent_smiles": parent_smiles,
                            "protonation_state_id": config.protonation_state_id,
                            "receptor_id": receptor_id,
                            "status": described["status"],
                            "status_detail": described["status_detail"],
                        }
                    )
                    counters["output_count"] += 1
                if records:
                    handoff_writer.write_table(
                        pa.Table.from_pylist(records, schema=MD_SYSTEM_INPUT_V1.schema)
                    )
                parent_writer.write_table(
                    pa.Table.from_pylist(rows, schema=PARENT_V1.schema)
                )

        if counters["input_count"] == 0:
            raise PluginError(
                "the MD handoff input contains no parents",
                code="MD_HANDOFF_EMPTY_INPUT",
            )
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    (_PARENT_PATH.as_posix(),),
                    {"row_count": counters["input_count"]},
                ),
                "handoff": PendingOutput(
                    MD_SYSTEM_INPUT_V1.id,
                    (_HANDOFF_PATH.as_posix(),),
                    {"row_count": counters["output_count"], "method_id": method_id},
                ),
            },
            metadata={
                **counters,
                "method_id": method_id,
                "prefer": config.prefer,
                "pose_rank": config.pose_rank,
                "protonation_state_id": config.protonation_state_id,
                "geometry_invented": False,
                "network_or_download_invoked_by_adapter": False,
            },
        )


__all__ = ["MdHandoffConfig", "MdHandoffPlugin", "_describe"]


class MdHandoffFromConformerPlugin(MdHandoffPlugin):
    """The same handoff, for a run that has no receptor.

    Two registrations of one implementation, because the difference between them is not
    in the code -- it is in what the stage is allowed to require.

    A pose is only meaningful against the receptor it was scored in, so the pose handoff
    declares ``docking_score/v1`` as an input and a cascade without a docking tier is
    refused before it starts. That refusal is correct where a pose is what the campaign
    means, and wrong as the only option: a ligand's own embedded conformer is a
    legitimate thing to simulate -- solvated on its own, or re-posed later by something
    other than this run -- and demanding a docking score for it would refuse the whole
    population for a dependency the science does not have.

    There is no optional-input concept in a plugin descriptor, and inventing one would
    change how every stage's inputs are wired for the sake of one case. Two descriptors
    say the same thing in the vocabulary that already exists, and they say it where a
    reader will see it: the two appear side by side in the catalogue as two answers to
    one question, which is what they are.
    """

    descriptor = PluginDescriptor(
        id="handoff.md_system_input_conformer",
        version="0.1.0",
        kind=PluginKind.EXPORTER,
        # No docking score. Everything else is identical, including the outputs, so a
        # consumer downstream cannot tell which mode produced the record -- and must not
        # need to, because the record states its own coordinate_source.
        inputs=(PARENT_V1.id, LIGAND_CONFORMER_V1.id),
        outputs=(PARENT_V1.id, MD_SYSTEM_INPUT_V1.id),
        output_ports={"primary": PARENT_V1.id, "handoff": MD_SYSTEM_INPUT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="MD system input handoff (conformer only)",
        description=(
            "Publish the embedded conformer a run already computed as "
            "md_system_input/v1, for a campaign with no receptor. Identical to the "
            "pose handoff except that it does not require a docking score, so it can "
            "run in a cascade that never docked. A molecule RDKit could not embed "
            "keeps a row saying so rather than being filled in from SMILES."
        ),
    )

