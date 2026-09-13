"""How much internal energy the docked conformer is carrying.

A docking engine searches for the pose that scores best against the receptor.
Nothing in that search asks whether the ligand would ever adopt the torsions it
found on its own, so a strong score can be paid for entirely by the molecule
bending into a shape it does not occupy in solution.  Gu, Kang, Irwin and
Shoichet (10.1021/acs.jcim.1c00368) showed this is common enough in
high-throughput docking to be worth filtering on rather than rescoring with.

This plugin measures it and decides nothing.  For the pose that decided a
molecule -- the best-scoring one, the same pose its docking gate accepted -- it
reports

    strain = MMFF94s(pose, relaxed under position restraints)
           - min MMFF94s(free conformer ensemble)

in kcal/mol as ``derived_metric/v1`` evidence.  The restrained relaxation is
what makes the number about torsions instead of about bond lengths: a pose read
back from a docking engine carries bond and angle noise worth tens of kcal/mol
that no conformational preference explains, and letting the geometry settle
within 0.25 A removes it while leaving the torsional strain in place.

Four things it is not:

*Not Gu/Shoichet's number.*  Their threshold of 6.5 is in Torsion Energy Units,
read out of a torsion library built from CSD statistics -- a different quantity
on a different scale that happens to be a small positive real, which is exactly
why it must not be reused as a kcal/mol threshold.  A TEU backend would need
Open Babel (GPL-2.0) and the ChemInfTools ``strainfilter`` app, whose
``Torsion_Strain.py`` silently omits rows it cannot type; that is a separate
backend behind the ``backend`` field, deliberately left for later rather than
approximated here.  The default ``maximum`` on the gate side is a kcal/mol
number measured on this project's own poses.

*Not a shape or ring filter.*  Measured on 674 real poses from this project's
STK17B run, strain correlates with fused-ring count at r = -0.014 and with
aromatic-ring count at r = -0.038 -- a flat fused aromatic system has almost no
torsions to strain, so its strain is *lower* than a flexible molecule's.  Ring
topology is the ring-topology gate's job.

*Not the pose-quality checker's internal-energy test.*  That one is a boolean
inside the docking engine, off by default because it costs 351 ms per pose and
is brittle.  This is an auditable row with a status, a method identity, and a
threshold an operator sets.

*Not free.*  About 950 ms per molecule per core at twelve reference conformers.
It belongs after the docking gate for that reason: on the STK17B run that is
4,740 molecules rather than 21,873, roughly eight minutes across ten lanes
instead of six core-hours.

What it reads like on real poses: measured through this module on 200 of the
1,352 molecules this project shortlisted for STK17B, the median is 2.7 kcal/mol
and the 90th percentile 13.1, with 65% at or below 8.  Eleven per cent come out
negative, which is a statement about the reference search rather than about the
pose -- and it is two populations, not one: eight of those twenty-two were
exactly -0.0, poses already sitting at their own global minimum, while the rest
run from -0.03 down to -4.7 where twelve conformers failed to find the minimum.
Hence ``negative_tolerance_kcal_mol``: inside the noise band a negative strain
is zero strain, below it the row is flagged and keeps its value.
"""

from __future__ import annotations

import json
import math
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from pydantic import Field, JsonValue, ValidationError, field_validator

from molcascade.chemistry.datasets import discover_contract_files
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DERIVED_METRIC_V1, DOCKING_SCORE_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import (
    ShardOutcome,
    ShardTask,
    iter_shard_batches,
    read_side_input,
)
from molcascade.plugins.api import (
    PendingOutput,
    StageContext,
    StageInput,
    StageRequest,
    StageResponse,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_METRIC_PATH = Path("datasets/derived_metrics/part-00000.parquet")
_IMPLEMENTATION_VERSION = 1
_METRIC_ID = "pose_strain"

# A second of work per molecule means a 10 000-row shard is nearly three hours
# to lose to an interruption.  Sized so a shard is a few minutes, matching what
# ligand preparation does for the same reason.
_SHARD_ROWS = 250

# RDKit reads a seed of 0 as "pick one", which would make the reference
# ensemble -- and therefore the strain -- unreproducible.
_MAX_SEED = 2**31 - 1


class PoseStrainBackend(StrEnum):
    """Where the strain number comes from.

    One member today.  It stays an enumeration rather than becoming an implied
    constant so that the ``units`` written alongside every value is a
    declaration tied to a named method, and so that adding the torsion-library
    backend later is an addition rather than a reinterpretation of existing
    rows.
    """

    MMFF94S_LOCAL = "mmff94s_local"


class PoseStrainConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    backend: PoseStrainBackend = PoseStrainBackend.MMFF94S_LOCAL
    #: Which engine's poses to measure.  Left unset it resolves to the one
    #: engine present in the bound evidence.  Worth pinning deliberately when a
    #: tier ran two engines: KarmaDock's stored pose is its force-field-corrected
    #: output, so its strain has already been relaxed away by its own adapter,
    #: while Uni-Dock's is the raw searched pose that this measurement is about.
    engine_id: str | None = Field(default=None, min_length=1, max_length=256)
    receptor_id: str | None = Field(default=None, min_length=1, max_length=256)
    #: Reference conformers per molecule.  The strain is a difference against
    #: the *lowest* one found, so too few conformers overestimates how good the
    #: pose is by failing to find the real minimum, which shows up as a negative
    #: strain rather than as a small one.
    reference_conformers: int = Field(default=12, ge=1, le=256)
    prune_rms_threshold: float = Field(default=0.5, ge=0.0, le=5.0)
    #: Position restraint on every atom of the pose: how far it may move, and
    #: how hard it is held.  Loose enough to shed bond and angle noise, tight
    #: enough that no torsion rotates.
    restraint_tolerance_angstrom: float = Field(default=0.25, ge=0.0, le=2.0)
    restraint_force_constant: float = Field(default=500.0, ge=1.0, le=100_000.0)
    max_minimize_iterations: int = Field(default=800, ge=1, le=20_000)
    #: How far below zero a strain may fall and still be read as zero.  A pose
    #: that already sits at its own global minimum measures the same energy as
    #: the reference does, and which of the two comes out lower is then float
    #: noise: on 200 poses from this project's shortlist, 8 of the 22 negative
    #: values were exactly -0.0.  Those are the best possible poses, so a rule
    #: that flagged them would reject exactly the geometry it is looking for.
    #: Anything below this band is a reference search that genuinely failed.
    negative_tolerance_kcal_mol: float = Field(default=0.05, ge=0.0, le=1.0)
    seed: int = Field(default=20_260_823, ge=0, le=_MAX_SEED)
    batch_size: int = Field(default=256, ge=1, le=250_000)

    @field_validator("backend", mode="before")
    @classmethod
    def _parse_backend(cls, value: object) -> object:
        # Strict validation wants the member itself, and a config that arrived
        # as JSON -- every builder export, and every shard re-validating its own
        # dumped settings -- only has the string.
        if isinstance(value, str):
            try:
                return PoseStrainBackend(value)
            except ValueError as error:
                raise ValueError(
                    "unknown pose-strain backend "
                    f"{value!r}; expected one of {', '.join(PoseStrainBackend)}"
                ) from error
        return value


def _validated_config(request: StageRequest) -> PoseStrainConfig:
    try:
        return PoseStrainConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid pose-strain configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _classify_inputs(inputs: dict[str, StageInput]) -> tuple[StageInput, StageInput]:
    allowed = {PARENT_V1.id, DOCKING_SCORE_V1.id}
    unsupported: list[JsonValue] = [
        port for port in sorted(inputs) if inputs[port].contract_id not in allowed
    ]
    if unsupported:
        raise PluginError(
            "pose strain received unsupported input contracts",
            code="POSE_STRAIN_INPUT_CONTRACT_INVALID",
            context={"request_ports": unsupported},
        )
    parents = [value for value in inputs.values() if value.contract_id == PARENT_V1.id]
    evidence = [value for value in inputs.values() if value.contract_id == DOCKING_SCORE_V1.id]
    if len(parents) != 1 or len(evidence) != 1 or len(inputs) != 2:
        raise PluginError(
            "pose strain requires exactly one parent and one docking input",
            code="POSE_STRAIN_INPUT_CARDINALITY_INVALID",
            context={
                "parent_input_count": len(parents),
                "evidence_input_count": len(evidence),
                "request_input_count": len(inputs),
            },
        )
    return parents[0], evidence[0]


def _method_id(
    config: PoseStrainConfig,
    rdkit_version: str,
    *,
    engine_id: str,
    receptor_id: str,
) -> str:
    """Identify the measurement, not the run.

    The seed is in here because the reference ensemble is a stochastic search:
    two seeds are two measurements of the same molecule, and a table that could
    not say which one it holds would let them be compared as if they were one.
    Shard size and worker count are absent, so the same poses give the same
    numbers on a laptop and on a node.
    """

    return "pose-strain-mmff94s:sha256:" + canonical_sha256(
        {
            "backend": config.backend.value,
            "backend_version": rdkit_version,
            "implementation_version": _IMPLEMENTATION_VERSION,
            "metric_id": _METRIC_ID,
            "method": ("MMFF94s restrained-pose energy minus free ETKDGv3 ensemble minimum"),
            "units": "KCAL_PER_MOL",
            "direction": "LOWER_BETTER",
            "engine_id": engine_id,
            "receptor_id": receptor_id,
            "pose_policy": "BEST_POSE",
            "reference_conformers": config.reference_conformers,
            "prune_rms_threshold": config.prune_rms_threshold,
            "restraint_tolerance_angstrom": config.restraint_tolerance_angstrom,
            "restraint_force_constant": config.restraint_force_constant,
            "max_minimize_iterations": config.max_minimize_iterations,
            "negative_tolerance_kcal_mol": config.negative_tolerance_kcal_mol,
            "seed": config.seed,
        }
    )


def _evidence_dataset(stage_input: StageInput) -> ds.Dataset:
    files = discover_contract_files(stage_input, DOCKING_SCORE_V1)
    return ds.dataset([str(path) for path in files], format="parquet")


def _resolve_identity(
    dataset: ds.Dataset,
    *,
    configured: str | None,
    column: str,
) -> str:
    if configured is not None:
        return configured
    observed = sorted(
        {
            str(value)
            for value in dataset.to_table(columns=[column]).column(column).to_pylist()
            if value is not None
        }
    )
    if len(observed) != 1:
        examples: list[JsonValue] = list(observed[:8])
        raise PluginError(
            f"pose strain requires exactly one observed {column} when none is configured",
            code="POSE_STRAIN_IDENTITY_AMBIGUOUS",
            hint=(
                "Pin the engine whose poses should be measured, or bind the "
                "criterion to a single docking stage with evidence_from."
            ),
            context={
                "identity": column,
                "observed_count": len(observed),
                "observed_examples": examples,
            },
        )
    return observed[0]


def _require_stored_poses(
    dataset: ds.Dataset,
    *,
    engine_id: str,
    receptor_id: str,
) -> int:
    """Stop before scheduling a shard if the geometry was never kept.

    This is the plugin's most likely failure and it is invisible from inside a
    worker: ``pose_molblock`` is populated only when the engine was asked to
    keep poses or to check them, and is nulled again afterwards otherwise.  A
    run that discovered that per molecule would report a full table of failures
    after paying for every shard, which reads like a strained library rather
    than like a missing column.
    """

    selected = (ds.field("engine_id") == engine_id) & (ds.field("receptor_id") == receptor_id)
    pose_count = int(dataset.count_rows(filter=selected))
    if pose_count == 0:
        raise PluginError(
            "docking evidence contains no poses from the selected engine",
            code="POSE_STRAIN_NO_MATCHING_EVIDENCE",
            hint=(
                "Bind the criterion to the docking stage that scored this "
                "population with evidence_from, or clear engine_id."
            ),
            context={"engine_id": engine_id, "receptor_id": receptor_id},
        )
    with_geometry = int(dataset.count_rows(filter=selected & ds.field("pose_molblock").is_valid()))
    if with_geometry == 0:
        raise PluginError(
            "docking evidence stores no pose geometry, so strain cannot be measured",
            code="POSE_STRAIN_POSES_UNAVAILABLE",
            hint=(
                "Set keep_poses on the docking stage that feeds this criterion "
                "(or enable its pose-quality check), then re-run that stage."
            ),
            context={
                "engine_id": engine_id,
                "receptor_id": receptor_id,
                "pose_count": pose_count,
            },
        )
    return with_geometry


class _BestPose:
    """The pose that decided a molecule, and its stored geometry if any."""

    __slots__ = ("molblock", "pose_rank", "score")

    def __init__(self, *, score: float, pose_rank: int, molblock: str | None) -> None:
        self.score = score
        self.pose_rank = pose_rank
        self.molblock = molblock


def _shard_poses(
    task: ShardTask,
    parent_ids: list[str],
    *,
    engine_id: str,
    receptor_id: str,
) -> dict[str, _BestPose]:
    """Join this batch's molecules to the pose their docking gate accepted.

    Best by score, with the sense taken from the row's own ``direction``, which
    is how the docking gate chooses too.  Taking ``pose_rank == 0`` instead
    would trust an ordering invariant inherited from another program's output
    format, and would silently disagree with the gate the moment it did not
    hold.  A pose that lacks stored geometry is still the pose that decided the
    molecule, so it is reported as unmeasurable rather than replaced by the next
    one down -- a strain computed from a rejected conformer would describe a
    molecule the run never kept.
    """

    table = read_side_input(
        task,
        "poses",
        keys=parent_ids,
        columns=[
            "parent_id",
            "engine_id",
            "receptor_id",
            "pose_rank",
            "score",
            "direction",
            "pose_molblock",
        ],
    )
    best: dict[str, _BestPose] = {}
    for row in table.to_pylist():
        if str(row["engine_id"]) != engine_id or str(row["receptor_id"]) != receptor_id:
            continue
        parent_id = str(row["parent_id"])
        score = float(row["score"])
        stronger = score < best[parent_id].score if parent_id in best else True
        if str(row["direction"]) == "HIGHER_STRONGER" and parent_id in best:
            stronger = score > best[parent_id].score
        if not stronger:
            continue
        molblock = row["pose_molblock"]
        best[parent_id] = _BestPose(
            score=score,
            pose_rank=int(row["pose_rank"]),
            molblock=molblock if isinstance(molblock, str) and molblock else None,
        )
    return best


def _pose_energy(molecule: Any, config: PoseStrainConfig) -> float | None:
    """MMFF94s energy of the pose after the noise is relaxed out of it."""

    from rdkit.Chem import rdForceFieldHelpers

    properties = rdForceFieldHelpers.MMFFGetMoleculeProperties(molecule, mmffVariant="MMFF94s")
    if properties is None:
        return None
    field = rdForceFieldHelpers.MMFFGetMoleculeForceField(molecule, properties)
    if field is None:
        return None
    for index in range(molecule.GetNumAtoms()):
        field.MMFFAddPositionConstraint(
            index,
            config.restraint_tolerance_angstrom,
            config.restraint_force_constant,
        )
    field.Minimize(maxIts=config.max_minimize_iterations)
    # A fresh field over the relaxed coordinates: the restrained one's energy
    # includes the restraint terms, which are an artefact of the measurement.
    free = rdForceFieldHelpers.MMFFGetMoleculeForceField(molecule, properties)
    if free is None:
        return None
    energy = float(free.CalcEnergy())
    return energy if math.isfinite(energy) else None


def _ensemble_minimum(molecule: Any, config: PoseStrainConfig) -> tuple[float | None, int]:
    """Lowest MMFF94s energy among freely embedded conformers of the same molecule."""

    from rdkit import Chem
    from rdkit.Chem import rdDistGeom, rdForceFieldHelpers

    reference = Chem.Mol(molecule)
    reference.RemoveAllConformers()
    params: Any = rdDistGeom.ETKDGv3()
    params.randomSeed = config.seed
    params.useSmallRingTorsions = True
    params.useMacrocycleTorsions = True
    params.enforceChirality = True
    # Parallelism is the shard pool's business; RDKit's own threads here would
    # oversubscribe every core several times over.
    params.numThreads = 1
    if config.reference_conformers > 1 and config.prune_rms_threshold > 0.0:
        params.pruneRmsThresh = config.prune_rms_threshold
    conformer_ids = rdDistGeom.EmbedMultipleConfs(
        reference,
        numConfs=config.reference_conformers,
        params=params,
    )
    if len(conformer_ids) == 0:
        return None, 0
    results = rdForceFieldHelpers.MMFFOptimizeMoleculeConfs(
        reference,
        mmffVariant="MMFF94s",
        maxIters=config.max_minimize_iterations,
        numThreads=1,
    )
    energies = [float(energy) for _converged, energy in results if math.isfinite(float(energy))]
    if not energies:
        return None, len(conformer_ids)
    return min(energies), len(conformer_ids)


def _strain(
    molblock: str, config: PoseStrainConfig
) -> tuple[float | None, str | None, dict[str, Any]]:
    """Measure one pose, or say in words why it could not be measured."""

    from rdkit import Chem

    try:
        molecule = Chem.MolFromMolBlock(molblock, removeHs=False, sanitize=True)
    except (RuntimeError, ValueError):
        molecule = None
    if molecule is None or molecule.GetNumConformers() == 0:
        return None, "stored pose could not be parsed as a molecule", {}
    try:
        # Added with coordinates so the restrained relaxation places them; a
        # pose written without hydrogens has no MMFF energy worth comparing.
        molecule = Chem.AddHs(molecule, addCoords=True)
    except (RuntimeError, ValueError):
        return None, "hydrogens could not be added to the stored pose", {}

    try:
        pose_energy = _pose_energy(molecule, config)
    except (RuntimeError, ValueError):
        pose_energy = None
    if pose_energy is None:
        return None, "MMFF94s has no parameters for the stored pose", {}

    try:
        ensemble_minimum, conformer_count = _ensemble_minimum(molecule, config)
    except (RuntimeError, ValueError):
        ensemble_minimum, conformer_count = None, 0
    if ensemble_minimum is None:
        return None, "no reference conformer could be embedded and minimised", {}

    value = pose_energy - ensemble_minimum
    if not math.isfinite(value):
        return None, "strain is not a finite number", {}
    return (
        value,
        None,
        {
            "pose_energy_kcal_mol": pose_energy,
            "ensemble_minimum_kcal_mol": ensemble_minimum,
            "reference_conformers_embedded": conformer_count,
        },
    )


def _source_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _metric_row(
    *,
    parent_id: str,
    method_id: str,
    value: float | None,
    status: str,
    status_detail: str | None,
    source: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "parent_id": parent_id,
        "metric_id": _METRIC_ID,
        "method_id": method_id,
        "value": value,
        "units": "KCAL_PER_MOL",
        "direction": "LOWER_BETTER",
        "status": status,
        "status_detail": status_detail,
        "source_json": None if source is None else _source_json(source),
    }


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Measure one contiguous range of the population in whichever process owns it."""

    from rdkit import rdBase

    config = PoseStrainConfig.model_validate(dict(task.config))
    if config.engine_id is None or config.receptor_id is None:
        raise PluginError(
            "pose-strain shard was scheduled without a resolved engine identity",
            code="POSE_STRAIN_IDENTITY_UNRESOLVED",
            context={"shard_index": task.index},
        )
    engine_id = config.engine_id
    receptor_id = config.receptor_id
    method_id = _method_id(
        config,
        rdBase.rdkitVersion,
        engine_id=engine_id,
        receptor_id=receptor_id,
    )

    input_count = 0
    measured_count = 0
    negative_count = 0
    failed_count = 0
    unscored_count = 0
    missing_geometry_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["derived_metrics"],
            DERIVED_METRIC_V1.schema,
            compression="zstd",
        ) as metric_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            parent_ids = [str(value) for value in batch.column("parent_id").to_pylist()]
            input_count += len(parent_ids)
            poses = _shard_poses(
                task,
                parent_ids,
                engine_id=engine_id,
                receptor_id=receptor_id,
            )
            rows: list[dict[str, Any]] = []
            for parent_id in parent_ids:
                pose = poses.get(parent_id)
                if pose is None:
                    unscored_count += 1
                    rows.append(
                        _metric_row(
                            parent_id=parent_id,
                            method_id=method_id,
                            value=None,
                            status="NOT_APPLICABLE",
                            status_detail=(
                                f"no {engine_id} pose against {receptor_id} for this parent"
                            ),
                            source=None,
                        )
                    )
                    continue
                if pose.molblock is None:
                    missing_geometry_count += 1
                    rows.append(
                        _metric_row(
                            parent_id=parent_id,
                            method_id=method_id,
                            value=None,
                            status="NOT_APPLICABLE",
                            status_detail=(
                                "the accepted pose was stored without geometry "
                                f"(pose_rank {pose.pose_rank})"
                            ),
                            source=None,
                        )
                    )
                    continue
                value, failure, measurement = _strain(pose.molblock, config)
                if value is None:
                    failed_count += 1
                    rows.append(
                        _metric_row(
                            parent_id=parent_id,
                            method_id=method_id,
                            value=None,
                            status="BACKEND_FAILED",
                            status_detail=failure,
                            source=None,
                        )
                    )
                    continue
                measured_count += 1
                source = {
                    "score": pose.score,
                    "pose_rank": pose.pose_rank,
                    **measurement,
                }
                if value < -config.negative_tolerance_kcal_mol:
                    # Not "better than any conformer": the reference search
                    # failed to find the minimum it was supposed to measure
                    # against.  The measured value is kept so the size of the
                    # failure is visible, and the status stops it being read as
                    # the best strain in the library.  Values inside the noise
                    # band are left OK and unclamped -- the number is what was
                    # measured either way, and only its reading changes.
                    negative_count += 1
                    rows.append(
                        _metric_row(
                            parent_id=parent_id,
                            method_id=method_id,
                            value=value,
                            status="OUT_OF_DOMAIN",
                            status_detail=(
                                "no reference conformer reached the energy of the "
                                "relaxed pose, so this strain is a lower bound on "
                                "the reference search's failure, not a strain"
                            ),
                            source=source,
                        )
                    )
                    continue
                rows.append(
                    _metric_row(
                        parent_id=parent_id,
                        method_id=method_id,
                        value=value,
                        status="OK",
                        status_detail=None,
                        source=source,
                    )
                )
            parent_writer.write_batch(batch)
            metric_writer.write_table(pa.Table.from_pylist(rows, schema=DERIVED_METRIC_V1.schema))
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "derived_metrics": input_count},
        metadata={
            "measured_count": measured_count,
            "out_of_domain_count": negative_count,
            "backend_failed_count": failed_count,
            "unscored_count": unscored_count,
            "missing_geometry_count": missing_geometry_count,
        },
    )


class PoseStrainPlugin:
    """Measure the internal strain of the pose each molecule was accepted on."""

    descriptor = PluginDescriptor(
        id="derived.pose_strain",
        version="0.1.0",
        kind=PluginKind.FEATURIZER,
        inputs=(PARENT_V1.id, DOCKING_SCORE_V1.id),
        outputs=(PARENT_V1.id, DERIVED_METRIC_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "derived_metrics": DERIVED_METRIC_V1.id,
        },
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.SEEDED,
        display_name="Docked-pose internal strain",
        description=(
            "MMFF94s local strain of the best-scoring pose in kcal/mol; evidence "
            "only, thresholds are a separate gate."
        ),
    )
    config_model = PoseStrainConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(request)
        parent_input, evidence_input = _classify_inputs(dict(request.inputs))
        # Resolved and checked in the parent, so a run whose docking stage did
        # not keep its geometry stops here rather than in every worker at once.
        dataset = _evidence_dataset(evidence_input)
        engine_id = _resolve_identity(dataset, configured=config.engine_id, column="engine_id")
        receptor_id = _resolve_identity(
            dataset,
            configured=config.receptor_id,
            column="receptor_id",
        )
        stored_pose_count = _require_stored_poses(
            dataset,
            engine_id=engine_id,
            receptor_id=receptor_id,
        )
        method_id = _method_id(
            config,
            rdBase.rdkitVersion,
            engine_id=engine_id,
            receptor_id=receptor_id,
        )
        result = shard_stage(
            worker=_run_shard,
            stage_input=parent_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "derived_metrics": _METRIC_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config={
                **config.model_dump(mode="json"),
                "engine_id": engine_id,
                "receptor_id": receptor_id,
            },
            shard_rows=_SHARD_ROWS,
            side_inputs={"poses": (evidence_input, DOCKING_SCORE_V1)},
        )
        if result.rows_in == 0:
            raise PluginError(
                "pose-strain parent input contains no rows",
                code="POSE_STRAIN_EMPTY_PARENT_INPUT",
            )
        input_count = result.rows_in
        unscored_count = result.total("unscored_count")
        if unscored_count == input_count:
            raise PluginError(
                "no parent in this population has a pose from the selected engine",
                code="POSE_STRAIN_NO_MATCHING_EVIDENCE",
                hint=(
                    "Pin the criterion's evidence_from to the docking stage that "
                    "scored this population, or clear engine_id and receptor_id."
                ),
                context={
                    "engine_id": engine_id,
                    "receptor_id": receptor_id,
                    "parent_count": input_count,
                },
            )
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": input_count},
                ),
                "derived_metrics": PendingOutput(
                    DERIVED_METRIC_V1.id,
                    result.file_paths["derived_metrics"],
                    {"row_count": input_count, "method_id": method_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "metric_id": _METRIC_ID,
                "method_id": method_id,
                "backend": config.backend.value,
                "backend_version": rdBase.rdkitVersion,
                "units": "KCAL_PER_MOL",
                "direction": "LOWER_BETTER",
                "engine_id": engine_id,
                "receptor_id": receptor_id,
                "pose_policy": "BEST_POSE",
                "stored_pose_count": stored_pose_count,
                "seed": config.seed,
                "reference_conformers": config.reference_conformers,
                "measured_count": result.total("measured_count"),
                "out_of_domain_count": result.total("out_of_domain_count"),
                "backend_failed_count": result.total("backend_failed_count"),
                "unscored_count": unscored_count,
                "missing_geometry_count": result.total("missing_geometry_count"),
                "derived_not_measured": False,
                **result.response_metadata(),
            },
        )


__all__ = ["PoseStrainBackend", "PoseStrainConfig", "PoseStrainPlugin"]
