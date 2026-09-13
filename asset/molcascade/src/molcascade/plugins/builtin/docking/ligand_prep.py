"""Generate the 3D ligand geometry the searching docking engines in the tier read.

Embedding is done once, here, rather than inside each engine adapter.  Two
reasons, and both are about being able to believe the scores afterwards: a
consensus between Uni-Dock and GNINA only means something if the two saw
identical input geometry, and ETKDG plus an MMFF minimisation is the single
largest CPU cost in the docking level -- paying it once instead of twice is most
of the level's wall-clock.

KarmaDock is not one of the readers.  It takes SMILES on its command line and
embeds its own conformer internally, which is why its adapter binds ``parent/v1``
rather than this table: subscribing to a geometry it never reads would record an
input it did not have.

A molecule that cannot be embedded is *not* dropped.  It keeps a row with a null
molblock and the reason in ``embed_status``, so the score gate downstream
rejects it for missing evidence, in the open, the same way every other gate in
this project rejects a molecule.  Silently shrinking the population inside a
preparation step would make the funnel report a filter it never declared.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import LIGAND_CONFORMER_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_CONFORMER_PATH = Path("datasets/ligand_conformers/part-00000.parquet")
_IMPLEMENTATION_VERSION = 1

# Embedding runs at tens of milliseconds per molecule, not microseconds, so a
# 50 000-row shard would be the better part of an hour of work to lose to an
# interruption.  A smaller unit costs a few more parquet footers and buys a
# resume granularity that matches how long the work actually takes.  This is a
# ceiling: ``run_sharded`` still honours a caller asking for something finer.
_SHARD_ROWS = 10_000

# RDKit treats a seed of 0 as "pick one", which would make a run unreproducible
# for exactly the molecules whose derived seed happened to hash to zero.
_MAX_SEED = 2**31 - 1


class LigandConformerConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=2_048, ge=1, le=250_000)
    #: Conformers kept per molecule.  Docking engines re-search torsions
    #: themselves, so more than a handful buys little and costs linearly.
    conformers_per_molecule: int = Field(default=1, ge=1, le=64)
    #: Attempts before giving up on a molecule; ETKDG occasionally needs several
    #: for macrocycles and heavily bridged systems.
    embed_attempts: int = Field(default=3, ge=1, le=20)
    minimize: bool = True
    max_minimize_iterations: int = Field(default=500, ge=1, le=20_000)
    #: Discard conformers closer than this RMSD to one already kept.  Ignored
    #: when only one conformer is requested.
    prune_rms_threshold: float = Field(default=0.5, ge=0.0, le=5.0)
    seed: int = Field(default=20_260_823, ge=0, le=_MAX_SEED)


def _validated_config(request: StageRequest) -> LigandConformerConfig:
    try:
        return LigandConformerConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid ligand-conformer configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _method_id(config: LigandConformerConfig, rdkit_version: str) -> str:
    """Identify the geometry, not the run.

    Everything that changes a coordinate belongs in here; nothing that changes
    only throughput does.  Shard size and worker count are deliberately absent,
    which is what lets the same molecules embed to the same bytes on a laptop
    and on a 64-core node.
    """

    return "ligand-conformer-etkdgv3:sha256:" + canonical_sha256(
        {
            "backend": "rdkit",
            "backend_version": rdkit_version,
            "implementation_version": _IMPLEMENTATION_VERSION,
            "method": "ETKDGv3 embedding with optional MMFF94s minimisation",
            "conformers_per_molecule": config.conformers_per_molecule,
            "embed_attempts": config.embed_attempts,
            "minimize": config.minimize,
            "max_minimize_iterations": config.max_minimize_iterations,
            "prune_rms_threshold": config.prune_rms_threshold,
            "seed": config.seed,
        }
    )


def _parent_seed(seed: int, parent_id: str) -> int:
    """Derive a per-molecule seed that does not move when shards do.

    Seeding from a row index would give a molecule different coordinates
    depending on which shard it landed in, so the same library would embed
    differently after a resume.  Deriving from the parent identity instead makes
    the geometry a property of the molecule and the configured seed alone.
    """

    material = f"{seed}:{parent_id}".encode()
    derived = int.from_bytes(hashlib.sha256(material).digest()[:4], "big")
    return derived % _MAX_SEED + 1


def _embed_parameters(config: LigandConformerConfig, seed: int) -> Any:
    from rdkit.Chem import rdDistGeom

    params = rdDistGeom.ETKDGv3()
    params.randomSeed = seed
    params.useSmallRingTorsions = True
    params.useMacrocycleTorsions = True
    params.enforceChirality = True
    # One thread per conformer job: parallelism is the shard pool's business,
    # and RDKit's own threads would oversubscribe every core several times over.
    params.numThreads = 1
    if config.conformers_per_molecule > 1 and config.prune_rms_threshold > 0.0:
        params.pruneRmsThresh = config.prune_rms_threshold
    return params


def _conformer_rows(
    *,
    parent_id: str,
    smiles: str,
    config: LigandConformerConfig,
    method_id: str,
) -> tuple[list[dict[str, Any]], bool]:
    """Embed one molecule and return its rows plus whether any geometry survived."""

    from rdkit import Chem
    from rdkit.Chem import rdDistGeom, rdForceFieldHelpers

    failure = [
        {
            "parent_id": parent_id,
            "conformer_index": 0,
            "molblock": None,
            "energy_kcal_mol": None,
            "embed_status": "EMBED_FAILED",
            "method_id": method_id,
        }
    ]
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise PluginError(
            "registered parent cannot be parsed for conformer generation",
            code="LIGAND_PREP_PARENT_INVALID",
            hint="Standardize the library before the docking level.",
            context={"parent_id": parent_id},
        )
    molecule = Chem.AddHs(molecule)
    base_seed = _parent_seed(config.seed, parent_id)

    conformer_ids: list[int] = []
    for attempt in range(config.embed_attempts):
        params = _embed_parameters(config, base_seed + attempt)
        try:
            conformer_ids = list(
                rdDistGeom.EmbedMultipleConfs(
                    molecule,
                    numConfs=config.conformers_per_molecule,
                    params=params,
                )
            )
        except (RuntimeError, ValueError):
            conformer_ids = []
        if conformer_ids:
            break
    if not conformer_ids:
        return failure, False

    energies: dict[int, float | None] = dict.fromkeys(conformer_ids)
    status = "EMBEDDED"
    if config.minimize:
        status = "MINIMIZED"
        # MMFF94s covers most of drug space but not, say, a platinum complex,
        # and RDKit reports the gap by returning ``(-1, -1.0)`` rather than by
        # raising.  Read naively that is a converged -1 kcal/mol, which would
        # put a sentinel in the energy column, so the parameter check runs
        # first.  The ETKDG geometry is still a valid starting structure, so it
        # is kept and labelled rather than thrown away for want of a field.
        results: Any = []
        if rdForceFieldHelpers.MMFFHasAllMoleculeParams(molecule):
            try:
                results = rdForceFieldHelpers.MMFFOptimizeMoleculeConfs(
                    molecule,
                    mmffVariant="MMFF94s",
                    maxIters=config.max_minimize_iterations,
                    numThreads=1,
                )
            except (RuntimeError, ValueError):
                results = []
                status = "MINIMIZE_FAILED"
        else:
            status = "MINIMIZE_FAILED"
        for conformer_id, result in zip(conformer_ids, results, strict=False):
            not_converged, energy = int(result[0]), float(result[1])
            if not_converged != 0:
                # 1 means the geometry improved but ran out of iterations, which
                # leaves a real energy; only a negative code is a sentinel.
                status = "MINIMIZE_FAILED"
            if not_converged >= 0 and math.isfinite(energy):
                energies[conformer_id] = energy

    # Best-first, and stable when energies are absent or equal.  ``pose_rank``
    # semantics downstream assume index 0 is the geometry to dock if only one
    # can be afforded.
    ordered = sorted(
        conformer_ids,
        key=lambda conformer_id: (
            energies[conformer_id] is None,
            energies[conformer_id] if energies[conformer_id] is not None else 0.0,
            conformer_id,
        ),
    )
    rows = [
        {
            "parent_id": parent_id,
            "conformer_index": index,
            "molblock": Chem.MolToMolBlock(molecule, confId=conformer_id),
            "energy_kcal_mol": energies[conformer_id],
            "embed_status": status,
            "method_id": method_id,
        }
        for index, conformer_id in enumerate(ordered)
    ]
    return rows, True


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Embed one contiguous range of parents in whichever process owns it."""

    from rdkit import rdBase

    config = LigandConformerConfig.model_validate(dict(task.config))
    method_id = _method_id(config, rdBase.rdkitVersion)
    input_count = 0
    conformer_count = 0
    failed_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["conformers"],
            LIGAND_CONFORMER_V1.schema,
            compression="zstd",
        ) as conformer_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            rows: list[dict[str, Any]] = []
            for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
                input_count += 1
                parent_id = str(row.get("parent_id"))
                smiles = row.get("parent_smiles")
                embedded, ok = _conformer_rows(
                    parent_id=parent_id,
                    smiles=smiles if isinstance(smiles, str) else "",
                    config=config,
                    method_id=method_id,
                )
                rows.extend(embedded)
                if ok:
                    conformer_count += len(embedded)
                else:
                    failed_count += 1
            parent_writer.write_batch(batch)
            conformer_writer.write_table(
                pa.Table.from_pylist(rows, schema=LIGAND_CONFORMER_V1.schema)
            )
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "conformers": conformer_count + failed_count},
        metadata={
            "conformer_count": conformer_count,
            "embed_failed_count": failed_count,
        },
    )


class RDKitLigandConformerPlugin:
    """Embed 3D ligand geometry with ETKDGv3 and optional MMFF94s refinement."""

    descriptor = PluginDescriptor(
        id="docking.rdkit_conformers",
        version="0.1.0",
        kind=PluginKind.FEATURIZER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, LIGAND_CONFORMER_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "conformers": LIGAND_CONFORMER_V1.id,
        },
        cardinality=Cardinality.ONE_TO_MANY,
        determinism=Determinism.SEEDED,
        display_name="RDKit 3D ligand preparation",
        description=(
            "ETKDGv3 conformer embedding with optional MMFF94s minimisation, "
            "shared by every docking engine in the tier so their scores stay "
            "comparable."
        ),
    )
    config_model = LigandConformerConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        method_id = _method_id(config, rdBase.rdkitVersion)
        result = shard_stage(
            worker=_run_shard,
            stage_input=stage_input,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "conformers": _CONFORMER_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config=config.model_dump(mode="json"),
            shard_rows=_SHARD_ROWS,
        )
        if result.rows_in == 0:
            raise PluginError(
                "ligand preparation input contains no parents",
                code="LIGAND_PREP_EMPTY_INPUT",
            )
        input_count = result.rows_in
        failed_count = result.total("embed_failed_count")
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": input_count},
                ),
                "conformers": PendingOutput(
                    LIGAND_CONFORMER_V1.id,
                    result.file_paths["conformers"],
                    {
                        "row_count": result.rows_out.get("conformers", 0),
                        "method_id": method_id,
                    },
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "method_id": method_id,
                "conformer_count": result.total("conformer_count"),
                "embed_failed_count": failed_count,
                "conformers_per_molecule": config.conformers_per_molecule,
                "minimize": config.minimize,
                "seed": config.seed,
                "backend": "rdkit",
                "backend_version": rdBase.rdkitVersion,
                **result.response_metadata(),
            },
        )


__all__ = ["LigandConformerConfig", "RDKitLigandConformerPlugin"]
