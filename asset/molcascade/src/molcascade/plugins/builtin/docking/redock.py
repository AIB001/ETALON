"""Independent re-search and fixed-frame pose consistency, never pose accuracy.

The original rank-zero pose remains the exported pose. Uni-Dock searches a
fresh ETKDG conformer of the parent-verified docked stereoisomer with a different
seed. Heavy-atom RMSD
uses all chemically valid graph mappings without translating or rotating either
ligand. The conventional 2 A crystal-redocking cutoff is an engineering default
here, not a validation that two agreeing predictions are experimentally correct.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, model_validator

from molcascade.chemistry.datasets import discover_contract_files, require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.contracts import DERIVED_METRIC_V1, DOCKING_SCORE_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches, read_side_input
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin.docking.common import (
    enforce_population_cap,
    ligand_pdbqt,
    population_size,
    require_gpu_lane,
    require_meeko,
    resolved_executable,
    verified_receptor,
)
from molcascade.plugins.builtin.docking.unidock import (
    UniDockConfig,
    _dock_batch,
    _prepared_receptor,
)
from molcascade.plugins.builtin.docking.unidock import (
    _method_id as source_method_id,
)
from molcascade.plugins.manifest import Cardinality, Determinism, PluginDescriptor, PluginKind

METRIC_ID = "dock_redock_rmsd"
_HINT = "Configure the Uni-Dock executable or MOLCASCADE_UNIDOCK_EXECUTABLE."
_PARENT_PATH = "datasets/parents/part-00000.parquet"
_METRIC_PATH = "datasets/derived_metrics/part-00000.parquet"


class RedockConfig(UniDockConfig):
    source_engine_id: Literal["unidock"] = "unidock"
    source_seed: int = Field(default=20_260_823, ge=0, le=2**31 - 1)
    seed: int = Field(default=20_260_824, ge=1, le=2**31 - 1)
    max_symmetry_matches: int = Field(default=100_000, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def _independent_search(self) -> RedockConfig:
        if self.seed == self.source_seed:
            raise ValueError("redock seed must differ from source_seed")
        if not self.keep_poses:
            raise ValueError("redock needs keep_poses=true to measure pose consistency")
        return self


def fixed_frame_rmsd(
    dock_block: str, redock_block: str, *, max_matches: int = 100_000
) -> tuple[float | None, str | None]:
    """Symmetry-corrected heavy-atom RMSD with chirality and connectivity intact.

    Exhaustive graph mapping is bounded. Hitting that bound fails closed rather
    than reporting an unproven minimum. Ligand superposition is never used.
    """
    from rdkit import Chem

    try:
        dock = Chem.MolFromMolBlock(dock_block, removeHs=True, sanitize=True)
        redock = Chem.MolFromMolBlock(redock_block, removeHs=True, sanitize=True)
        if dock is None or redock is None:
            return None, "pose could not be parsed"
        # Remove isotope-labelled hydrogens as well: this is a heavy-atom metric.
        params = Chem.RemoveHsParameters()
        params.removeIsotopes = True
        dock, redock = Chem.RemoveHs(dock, params), Chem.RemoveHs(redock, params)
        if not dock.GetNumAtoms() or not dock.GetNumConformers() or not redock.GetNumConformers():
            return None, "pose has no heavy atoms or coordinates"
        # Exact identity prevents substructure matches to another chemical state.
        if Chem.MolToSmiles(dock, isomericSmiles=True) != Chem.MolToSmiles(
            redock, isomericSmiles=True
        ):
            return None, "pose chemical identity or stereochemistry differs"
        matches = redock.GetSubstructMatches(
            dock, uniquify=False, useChirality=True, maxMatches=max_matches + 1
        )
        if len(matches) > max_matches:
            return None, "symmetry mapping limit exceeded"
        if not matches:
            return None, "no chemically valid atom mapping"
        left = np.asarray(dock.GetConformer().GetPositions(), dtype=float)
        right = np.asarray(redock.GetConformer().GetPositions(), dtype=float)
        if not np.isfinite(left).all() or not np.isfinite(right).all():
            return None, "nonfinite pose coordinates"
        rmsd = min(
            float(np.sqrt(np.mean(np.sum((left - right[list(m)]) ** 2, axis=1)))) for m in matches
        )
        return (rmsd, None) if math.isfinite(rmsd) else (None, "nonfinite RMSD")
    except (RuntimeError, ValueError, IndexError):
        return None, "pose comparison failed"


def redock_input_smiles(parent_smiles: str, dock_block: str) -> str | None:
    """Preserve the docked stereoisomer while verifying the parent's chemistry.

    An unspecified parent stereocentre may already have been instantiated during
    initial conformer preparation. Re-embedding that unspecified SMILES could
    choose its enantiomer; repeat search must test the same chemical state.
    Coordinates are used only to identify that state, then discarded by ETKDG.
    """
    from rdkit import Chem

    try:
        parent = Chem.MolFromSmiles(parent_smiles)
        pose = Chem.MolFromMolBlock(dock_block, removeHs=True, sanitize=True)
        if parent is None or pose is None:
            return None
        parent = Chem.RemoveHs(parent)
        parent_graph, pose_graph = Chem.Mol(parent), Chem.Mol(pose)
        Chem.RemoveStereochemistry(parent_graph)
        Chem.RemoveStereochemistry(pose_graph)
        if Chem.MolToSmiles(parent_graph) != Chem.MolToSmiles(pose_graph):
            return None
        # Query chirality must be satisfied; unspecified parent centres impose
        # no constraint, but explicit parent stereochemistry is never relaxed.
        if not pose.HasSubstructMatch(parent, useChirality=True):
            return None
        return str(Chem.MolToSmiles(pose, isomericSmiles=True))
    except (RuntimeError, ValueError):
        return None


def fresh_conformer(smiles: str, *, parent_id: str, seed: int) -> str | None:
    """Build input independently of the docked coordinates, stable across shards."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    try:
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            return None
        molecule = Chem.AddHs(molecule)
        params = AllChem.ETKDGv3()
        digest = hashlib.sha256(f"{seed}:{parent_id}".encode()).digest()
        params.randomSeed = int.from_bytes(digest[:4], "big") % (2**31 - 2) + 1
        params.numThreads = 1
        params.enforceChirality = True
        if AllChem.EmbedMolecule(molecule, params) != 0:
            return None
        return str(Chem.MolToMolBlock(molecule))
    except (RuntimeError, ValueError):
        return None


def _method_id(config: RedockConfig, *, receptor_id: str, prepared_id: str) -> str:
    from rdkit import rdBase

    return "dock-redock-rmsd:sha256:" + canonical_sha256(
        {
            "implementation_version": 1,
            "rdkit_version": rdBase.rdkitVersion,
            "receptor_id": receptor_id,
            "prepared_receptor_id": prepared_id,
            "source_engine_id": config.source_engine_id,
            "source_method_id": source_method_id(
                config.model_copy(update={"seed": config.source_seed}),
                receptor_id=receptor_id,
                prepared_id=prepared_id,
            ),
            "source_seed": config.source_seed,
            "seed": config.seed,
            "scoring": config.scoring,
            "search_mode": config.search_mode,
            "num_modes": config.num_modes,
            "max_symmetry_matches": config.max_symmetry_matches,
            "comparison": "fixed_receptor_frame_heavy_atoms_graph_symmetry_top1",
            "input": "fresh_parent_verified_docked_stereoisomer_etkdgv3",
            **config.box,
        }
    )


def _reference_poses(
    task: ShardTask,
    parents: list[str],
    config: RedockConfig,
    receptor_id: str,
    expected_method: str,
) -> dict[str, dict[str, Any]]:
    rows = read_side_input(task, "poses", keys=parents).to_pylist()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["engine_id"] != config.source_engine_id or row["pose_rank"] != 0:
            continue
        if row["receptor_id"] != receptor_id:
            raise PluginError(
                "redock and dock use different receptor coordinates",
                code="REDOCK_RECEPTOR_MISMATCH",
            )
        if row["method_id"] != expected_method:
            raise PluginError(
                "source docking settings differ from the declared redock source",
                code="REDOCK_SOURCE_METHOD_MISMATCH",
                hint="Copy source docking settings to redock and set source_seed to its seed.",
            )
        parent = str(row["parent_id"])
        if parent in result:
            raise PluginError("multiple rank-zero source poses", code="REDOCK_POSE_AMBIGUOUS")
        result[parent] = row
    return result


def _metric(
    parent: str,
    method: str,
    reference: dict[str, Any] | None,
    redocked: dict[str, Any] | None,
    config: RedockConfig,
    input_smiles: str | None = None,
) -> dict[str, Any]:
    original = None if reference is None else reference.get("pose_molblock")
    repeated = None if redocked is None else redocked.get("pose_molblock")
    value, failure = (None, "source pose or independent redock pose is missing")
    repeat_score = None if redocked is None else redocked.get("score")
    if repeat_score is not None and not math.isfinite(float(repeat_score)):
        repeat_score = None
    if original and repeated and repeat_score is not None:
        value, failure = fixed_frame_rmsd(
            original, repeated, max_matches=config.max_symmetry_matches
        )
    if repeated and repeat_score is None:
        failure = "independent redock score is missing or nonfinite"
    source = {
        "source_engine_id": config.source_engine_id,
        "source_seed": config.source_seed,
        "redock_seed": config.seed,
        "redock_input_isomeric_smiles": input_smiles,
        "source_method_id": None if reference is None else reference["method_id"],
        "source_pose_rank": 0,
        "source_pose_sha256": hashlib.sha256(original.encode()).hexdigest() if original else None,
        "redock_pose_molblock": repeated,
        "redock_score": repeat_score,
        "comparison": "top1_to_top1_fixed_receptor_frame_heavy_atom_graph_symmetry",
        "ligand_alignment": False,
        "interpretation": "prediction_consistency_not_crystal_pose_accuracy",
        "retained_pose": "original_docking_pose",
    }
    return {
        "parent_id": parent,
        "metric_id": METRIC_ID,
        "method_id": method,
        "value": value,
        "units": "ANGSTROM",
        "direction": "LOWER_BETTER",
        "status": "OK" if value is not None else "BACKEND_FAILED",
        "status_detail": failure,
        "source_json": json.dumps(source, sort_keys=True, allow_nan=False),
    }


def _run_shard(task: ShardTask) -> ShardOutcome:
    config = RedockConfig.model_validate(dict(task.config))
    require_gpu_lane(task.device, engine="Uni-Dock redock")
    require_meeko(engine="Uni-Dock redock")
    executable = resolved_executable(config.executable, engine="Uni-Dock", hint=_HINT)
    _, receptor_id = verified_receptor(config)
    prepared, prepared_id = _prepared_receptor(config)
    method = _method_id(config, receptor_id=receptor_id, prepared_id=prepared_id)
    expected_method = source_method_id(
        config.model_copy(update={"seed": config.source_seed}),
        receptor_id=receptor_id,
        prepared_id=prepared_id,
    )
    count = measured = 0
    with (
        pq.ParquetWriter(task.output_paths["primary"], PARENT_V1.schema) as parents_writer,
        pq.ParquetWriter(task.output_paths["derived_metrics"], DERIVED_METRIC_V1.schema) as writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            parents = batch.to_pylist()
            ids = [str(row["parent_id"]) for row in parents]
            references = _reference_poses(task, ids, config, receptor_id, expected_method)
            ligands = []
            input_smiles: dict[str, str] = {}
            for row in parents:
                parent = str(row["parent_id"])
                if not references.get(parent, {}).get("pose_molblock"):
                    continue
                state = redock_input_smiles(
                    str(row["parent_smiles"]), references[parent]["pose_molblock"]
                )
                if state is None:
                    continue
                input_smiles[parent] = state
                block = fresh_conformer(state, parent_id=parent, seed=config.seed)
                pdbqt = ligand_pdbqt(block) if block else None
                if pdbqt:
                    ligands.append((parent, pdbqt))
            rows, _unscored = (
                _dock_batch(
                    ligands,
                    config=config,
                    executable=executable,
                    receptor_pdbqt=prepared,
                    receptor_id=receptor_id,
                    method_id=method,
                )
                if ligands
                else ([], 0)
            )
            # No relaxation or cherry-picking among modes: compare top-ranked raw
            # re-search geometry to the original retained (quality-checked) pose.
            redocked = {str(row["parent_id"]): row for row in rows if row["pose_rank"] == 0}
            metrics = [
                _metric(
                    parent,
                    method,
                    references.get(parent),
                    redocked.get(parent),
                    config,
                    input_smiles.get(parent),
                )
                for parent in ids
            ]
            measured += sum(row["status"] == "OK" for row in metrics)
            count += len(ids)
            parents_writer.write_batch(batch)
            writer.write_table(pa.Table.from_pylist(metrics, schema=DERIVED_METRIC_V1.schema))
    return ShardOutcome(
        rows_in=count,
        rows_out={"primary": count, "derived_metrics": count},
        metadata={"measured_count": measured, "failed_count": count - measured},
    )


class RedockPlugin:
    descriptor = PluginDescriptor(
        id="docking.redock_consistency",
        version="0.1.0",
        kind=PluginKind.DOCK,
        inputs=(PARENT_V1.id, DOCKING_SCORE_V1.id),
        outputs=(PARENT_V1.id, DERIVED_METRIC_V1.id),
        output_ports={"primary": PARENT_V1.id, "derived_metrics": DERIVED_METRIC_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.SEEDED,
        display_name="Independent redock pose consistency",
        description=(
            "Fresh-input Uni-Dock re-search; fixed-frame symmetry-corrected heavy-atom RMSD."
        ),
    )
    config_model = RedockConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = RedockConfig.model_validate(dict(request.config))
        parents = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        poses = require_single_input(request.inputs, contract_id=DOCKING_SCORE_V1.id)
        enforce_population_cap(
            population_size(discover_contract_files(parents, PARENT_V1)),
            limit=config.max_molecules,
            engine="Uni-Dock redock",
        )
        _, receptor_id = verified_receptor(config)
        _, prepared_id = _prepared_receptor(config)
        method = _method_id(config, receptor_id=receptor_id, prepared_id=prepared_id)
        result = shard_stage(
            worker=_run_shard,
            stage_input=parents,
            contract=PARENT_V1,
            output_paths={"primary": _PARENT_PATH, "derived_metrics": _METRIC_PATH},
            context=context,
            stage_id=request.stage_id,
            config=config.model_dump(mode="json"),
            shard_rows=2_000,
            side_inputs={"poses": (poses, DOCKING_SCORE_V1)},
        )
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id, result.file_paths["primary"], {"row_count": result.rows_in}
                ),
                "derived_metrics": PendingOutput(
                    DERIVED_METRIC_V1.id,
                    result.file_paths["derived_metrics"],
                    {"row_count": result.rows_in, "method_id": method},
                ),
            },
            metadata={
                "input_count": result.rows_in,
                "output_count": result.rows_in,
                "method_id": method,
                "metric_id": METRIC_ID,
                "units": "ANGSTROM",
                "source_seed": config.source_seed,
                "redock_seed": config.seed,
                "measured_count": result.total("measured_count"),
                "failed_count": result.total("failed_count"),
                "retained_pose": "original_docking_pose",
                **result.response_metadata(),
            },
        )
