"""Dock a shard of ligands with Uni-Dock and record what came back.

Uni-Dock is AutoDock Vina's search rewritten as a CUDA kernel: the same
scoring functions, the same box, the same ``REMARK VINA RESULT`` output, but
thousands of ligands resident on the card at once.  That shape decides almost
everything about this adapter.

It has *no device flag*.  The card it uses is whichever one
``CUDA_VISIBLE_DEVICES`` leaves visible, which is exactly the pinning the shard
pool has already applied to this process -- so a lane reaches the engine by
being inherited, not by being passed.  It also has no CPU path, so a CPU lane
is refused up front rather than discovered as a crash.

And it is only fast in bulk: below roughly a thousand ligands per call the
setup cost dominates, so a batch here is a batch for the engine too, and the
shard is large.  The cost of that choice is that one unparseable ligand must
not take a thousand others down with it, which is why ligand preparation
failures are counted and skipped rather than raised.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, field_validator

from molcascade.chemistry.datasets import discover_contract_files, require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.contracts import DOCKING_SCORE_V1, LIGAND_CONFORMER_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin.docking.common import (
    BoxedDockingEngineConfig,
    MachinePath,
    absolute_path,
    backend_root,
    conda_environment,
    enforce_population_cap,
    ligand_pdbqt,
    parse_vina_poses,
    population_size,
    ranked,
    require_gpu_lane,
    require_meeko,
    resolved_executable,
    run_engine,
    shard_geometry,
    structure_digest,
    validated_config,
    verified_receptor,
)
from molcascade.plugins.builtin.docking.pose_quality import (
    PoseQualitySession,
    pose_quality_metadata,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_SCORE_PATH = Path("datasets/docking_scores/part-00000.parquet")
_ENGINE = "Uni-Dock"
_ENGINE_ID = "unidock"
# Bumped whenever a change here alters the numbers or the poses recorded, so the
# `method_id` on every score row distinguishes them.  Provenance only: the
# descriptor version has to move with it to invalidate a warm cache.  v2: repair
# the pose against the receptor and check it before the score row is written.
_IMPLEMENTATION_VERSION = 2

# Uni-Dock runs at roughly a tenth of a second per ligand, so 20 000 rows is a
# little over half an hour of work -- a sane amount to lose to an interruption
# and far more than the ~1000 the engine needs per call to be efficient.  A
# ceiling, not an override: a caller asking for finer shards still gets them.
_SHARD_ROWS = 20_000

_SCORE_KINDS = {
    "vina": "VINA_KCAL_MOL",
    "vinardo": "VINARDO_KCAL_MOL",
    "ad4": "AD4_KCAL_MOL",
}

_EXECUTABLE_HINT = (
    "Set 'executable' to the absolute path of the 'unidock' binary. MolCascade "
    "does not build or install it; see 'molcascade doctor' for the command that "
    "reports what is missing."
)


class UniDockConfig(BoxedDockingEngineConfig):
    """Uni-Dock's own settings on top of the shared target block."""

    engine_id: ClassVar[str] = _ENGINE_ID

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """Uni-Dock is a conda package, so its own environment is where it is."""

        return (
            MachinePath(
                field="executable",
                label="the Uni-Dock binary",
                candidates=(
                    *conda_environment(_ENGINE_ID, "bin/unidock"),
                    backend_root() / "bin" / "unidock",
                ),
                remedy="bash envs/bootstrap.sh unidock",
            ),
        )

    #: The receptor as Uni-Dock reads it. Derived from ``receptor_path`` with
    #: meeko once at preflight and cached by digest, or supplied directly with
    #: '--receptor-pdbqt' when the automatic preparation cannot cope. Required,
    #: because deriving it here would mean re-deriving it in every worker and
    #: discovering a preparation failure one shard at a time.
    receptor_pdbqt_path: str = Field(min_length=1, max_length=4096)

    #: Vina's empirical function, Vinardo's reparameterisation of it, or AD4.
    #: They are different scales, which is why the score contract records which
    #: one produced a number rather than treating them as interchangeable.
    scoring: Literal["vina", "vinardo", "ad4"] = "vina"
    #: Uni-Dock's own presets for exhaustiveness and step count. Exposed instead
    #: of the two underlying numbers because setting either by hand silently
    #: overrides the preset, which is a confusing way to lose a setting.
    search_mode: Literal["fast", "balance", "detail"] = "balance"
    #: Which prepared conformer to dock. Vina-family search re-generates torsions
    #: itself, so the starting geometry matters far less here than it does for a
    #: pose predictor -- one conformer is the honest default.
    conformer_index: int = Field(default=0, ge=0, le=63)
    #: Keep the docked pose in the score row.  On by default: ``molcascade
    #: trace`` writes the shortlist's poses as SDF and has nothing to write
    #: without them, and a docking run whose structures were silently discarded
    #: is the surprise, not the reverse.  The cost is the reason it stays a
    #: setting -- 45 000 poses is a few hundred megabytes of molblock -- so turn
    #: it off for a screen whose scores are all anyone will read.
    keep_poses: bool = True
    #: A hang guard, not a search budget. Uni-Dock spends about 0.1 s per ligand.
    timeout_per_molecule_seconds: float = Field(default=5.0, gt=0.0, le=86_400.0)

    @field_validator("receptor_pdbqt_path")
    @classmethod
    def _pdbqt_is_absolute(cls, value: str) -> str:
        return absolute_path(value, field="receptor_pdbqt_path")


def _method_id(config: UniDockConfig, *, receptor_id: str, prepared_id: str) -> str:
    """Identify the computation, not the run.

    Both digests belong here.  The receptor's says which protein this is a
    number about; the PDBQT's says which preparation of it produced the number,
    and two meeko runs that differ in protonation are not the same computation
    even though they are the same target.
    """

    return "docking-unidock:sha256:" + canonical_sha256(
        {
            "engine": _ENGINE_ID,
            "implementation_version": _IMPLEMENTATION_VERSION,
            "pose_quality": config.pose_quality.identity(),
            "receptor_id": receptor_id,
            "receptor_pdbqt_id": prepared_id,
            "scoring": config.scoring,
            "search_mode": config.search_mode,
            "num_modes": config.num_modes,
            "conformer_index": config.conformer_index,
            "seed": config.seed,
            **config.box,
        }
    )


def _prepared_receptor(config: UniDockConfig) -> tuple[Path, str]:
    """Locate and digest the PDBQT this adapter will hand to the engine."""

    path = Path(config.receptor_pdbqt_path)
    if path.suffix.lower() != ".pdbqt":
        raise PluginError(
            f"{_ENGINE} reads PDBQT receptors and was given {path.suffix or 'no'} suffix",
            code="DOCKING_RECEPTOR_FORMAT_INVALID",
            hint=(
                "Point '--receptor' at the protein PDB and MolCascade derives the "
                "PDBQT with meeko at preflight, or pass an already-prepared one "
                "with '--receptor-pdbqt'."
            ),
            context={"receptor_pdbqt_path": str(path)},
        )
    return structure_digest(
        config.receptor_pdbqt_path,
        code="DOCKING_RECEPTOR_UNREADABLE",
        hint=(
            "This is the PDBQT derived from your receptor. Re-run the preflight "
            "if it was removed, or pass one with '--receptor-pdbqt'."
        ),
    )


def _pose_molblocks(text: str, count: int) -> list[str | None]:
    """Rebuild docked poses as molblocks, or record that they could not be.

    Meeko reconstructs bond orders and non-polar hydrogens from the ``REMARK
    SMILES`` map it wrote into the input, which survives docking in most builds
    and not all.  When it does not, the score -- which is the evidence this
    stage exists to produce -- is unaffected, so the pose column goes null and
    the count says how often.
    """

    from meeko import PDBQTMolecule, RDKitMolCreate
    from rdkit import Chem

    blank: list[str | None] = [None] * count
    try:
        pdbqt_molecule = PDBQTMolecule(text, is_dlg=False, skip_typing=True)
        molecules = RDKitMolCreate.from_pdbqt_mol(pdbqt_molecule)
    except (RuntimeError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return blank
    if not molecules or molecules[0] is None:
        return blank
    molecule = molecules[0]
    rebuilt: list[str | None] = []
    for pose in range(count):
        if pose >= molecule.GetNumConformers():
            rebuilt.append(None)
            continue
        try:
            rebuilt.append(Chem.MolToMolBlock(molecule, confId=pose))
        except (RuntimeError, ValueError):
            rebuilt.append(None)
    return rebuilt


def _dock_batch(
    ligands: list[tuple[str, str]],
    *,
    config: UniDockConfig,
    executable: Path,
    receptor_pdbqt: Path,
    receptor_id: str,
    method_id: str,
) -> tuple[list[dict[str, Any]], int]:
    """Run the engine once over a whole batch and read its poses back.

    Ligands are written under an index-derived name rather than under their
    ``parent_id``.  The names become paths and a filename is not a place to
    find out that an identifier contained a separator; the mapping back is kept
    here in memory instead.

    ``receptor_pdbqt`` is what the engine opens; ``receptor_id`` is the digest
    of the structure the user supplied.  They are two different files on
    purpose -- the score is about the protein, not about the conversion.
    """

    rows: list[dict[str, Any]] = []
    unscored = 0
    timeout = math.ceil(len(ligands) * config.timeout_per_molecule_seconds)
    with tempfile.TemporaryDirectory(
        prefix="molcascade-unidock-",
        dir=config.scratch_dir,
    ) as scratch_name:
        scratch = Path(scratch_name)
        ligand_dir = scratch / "ligands"
        output_dir = scratch / "poses"
        ligand_dir.mkdir()
        output_dir.mkdir()

        written: list[tuple[str, Path]] = []
        for index, (parent_id, pdbqt) in enumerate(ligands):
            path = ligand_dir / f"ligand-{index:06d}.pdbqt"
            path.write_text(pdbqt, encoding="utf-8")
            written.append((parent_id, path))
        index_file = scratch / "ligand_index.txt"
        index_file.write_text(
            "\n".join(str(path) for _parent, path in written) + "\n", encoding="utf-8"
        )

        run_engine(
            [
                str(executable),
                "--receptor",
                str(receptor_pdbqt),
                "--ligand_index",
                str(index_file),
                *config.box_arguments(),
                "--dir",
                str(output_dir),
                "--scoring",
                config.scoring,
                "--search_mode",
                config.search_mode,
                "--num_modes",
                str(config.num_modes),
                "--seed",
                str(config.seed),
            ],
            cwd=scratch,
            timeout=timeout,
            engine=_ENGINE,
            code="DOCKING_UNIDOCK_RUN_FAILED",
            hint=_EXECUTABLE_HINT,
            context={"ligand_count": len(ligands), "receptor_id": receptor_id},
        )

        for parent_id, path in written:
            output = output_dir / f"{path.stem}_out.pdbqt"
            if not output.is_file():
                unscored += 1
                continue
            text = output.read_text(encoding="utf-8", errors="replace")
            scores = parse_vina_poses(text)
            if not scores:
                unscored += 1
                continue
            # The checker needs the same geometry the operator would keep, so
            # either request parses it; the column is nulled afterwards if only
            # the checker wanted it.
            wants_pose = config.keep_poses or config.pose_quality.enabled
            molblocks = _pose_molblocks(text, len(scores)) if wants_pose else [None] * len(scores)
            for rank, block in enumerate(ranked(scores, limit=config.num_modes, descending=False)):
                rows.append(
                    {
                        "parent_id": parent_id,
                        "engine_id": _ENGINE_ID,
                        "receptor_id": receptor_id,
                        "pose_rank": rank,
                        "score": scores[block],
                        "score_kind": _SCORE_KINDS[config.scoring],
                        "direction": "LOWER_STRONGER",
                        "secondary_score": None,
                        "pose_molblock": molblocks[block],
                        "method_id": method_id,
                    }
                )
    return rows, unscored


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Dock one contiguous range of the population in whichever process owns it."""

    config = UniDockConfig.model_validate(dict(task.config))
    require_gpu_lane(task.device, engine=_ENGINE)
    require_meeko(engine=_ENGINE)
    executable = resolved_executable(config.executable, engine=_ENGINE, hint=_EXECUTABLE_HINT)
    _receptor, receptor_id = verified_receptor(config)
    prepared, prepared_id = _prepared_receptor(config)
    method_id = _method_id(config, receptor_id=receptor_id, prepared_id=prepared_id)
    # One session for the shard: the receptor is read once however many batches
    # follow, and the counters accumulate across them.
    pose_session = PoseQualitySession(config.pose_quality, receptor_path=config.receptor_path)

    input_count = 0
    score_count = 0
    missing_geometry = 0
    ligand_failed = 0
    unscored = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["scores"], DOCKING_SCORE_V1.schema, compression="zstd"
        ) as score_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            parent_ids = [str(value) for value in batch.column("parent_id").to_pylist()]
            input_count += len(parent_ids)
            parent_writer.write_batch(batch)

            geometry = shard_geometry(task, parent_ids, index=config.conformer_index)
            ligands: list[tuple[str, str]] = []
            for parent_id in parent_ids:
                molblock = geometry.get(parent_id)
                if molblock is None:
                    # No usable conformer: the preparation stage already recorded
                    # why, and the score gate downstream rejects a molecule with
                    # no evidence in the open.
                    missing_geometry += 1
                    continue
                pdbqt = ligand_pdbqt(molblock)
                if pdbqt is None:
                    ligand_failed += 1
                    continue
                ligands.append((parent_id, pdbqt))
            if not ligands:
                continue

            rows, batch_unscored = _dock_batch(
                ligands,
                config=config,
                executable=executable,
                receptor_pdbqt=prepared,
                receptor_id=receptor_id,
                method_id=method_id,
            )
            unscored += batch_unscored
            # Repaired and judged before the rows are written, so what lands in
            # the score table is what passed.
            rows = pose_session.apply(rows, keep_poses=config.keep_poses)
            score_count += len(rows)
            if rows:
                score_writer.write_table(pa.Table.from_pylist(rows, schema=DOCKING_SCORE_V1.schema))
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "scores": score_count},
        metadata={
            "scored_pose_count": score_count,
            "missing_geometry_count": missing_geometry,
            "ligand_prep_failed_count": ligand_failed,
            "unscored_count": unscored,
            "receptor_id": receptor_id,
            "receptor_pdbqt_id": prepared_id,
            "method_id": method_id,
            **pose_session.report.metadata(),
        },
    )


class UniDockPlugin:
    """Score a shortlist against one receptor with GPU-accelerated Vina docking."""

    descriptor = PluginDescriptor(
        id="docking.unidock",
        version="0.2.0",
        kind=PluginKind.DOCK,
        inputs=(PARENT_V1.id, LIGAND_CONFORMER_V1.id),
        outputs=(PARENT_V1.id, DOCKING_SCORE_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "scores": DOCKING_SCORE_V1.id,
        },
        cardinality=Cardinality.ONE_TO_MANY,
        determinism=Determinism.SEEDED,
        display_name="Uni-Dock (GPU Vina)",
        description=(
            "GPU-batched AutoDock Vina search against a prepared receptor. "
            "Produces docking scores as evidence; a separate gate decides what "
            "they are allowed to reject."
        ),
    )
    config_model = UniDockConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = validated_config(request, UniDockConfig, engine=_ENGINE, hint=_EXECUTABLE_HINT)
        parents = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        conformers = require_single_input(request.inputs, contract_id=LIGAND_CONFORMER_V1.id)

        # The cap is checked here rather than per shard: ten shards each under
        # the limit would otherwise add up to a run nobody agreed to.
        enforce_population_cap(
            population_size(discover_contract_files(parents, PARENT_V1)),
            limit=config.max_molecules,
            engine=_ENGINE,
        )
        _receptor, receptor_id = verified_receptor(config)
        _prepared, prepared_id = _prepared_receptor(config)
        method_id = _method_id(config, receptor_id=receptor_id, prepared_id=prepared_id)

        result = shard_stage(
            worker=_run_shard,
            stage_input=parents,
            contract=PARENT_V1,
            output_paths={
                "primary": _PARENT_PATH.as_posix(),
                "scores": _SCORE_PATH.as_posix(),
            },
            context=context,
            stage_id=request.stage_id,
            config=config.model_dump(mode="json"),
            shard_rows=_SHARD_ROWS,
            side_inputs={"conformers": (conformers, LIGAND_CONFORMER_V1)},
        )
        if result.rows_in == 0:
            raise PluginError(
                "docking input contains no parents",
                code="DOCKING_EMPTY_INPUT",
            )
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": result.rows_in},
                ),
                "scores": PendingOutput(
                    DOCKING_SCORE_V1.id,
                    result.file_paths["scores"],
                    {
                        "row_count": result.rows_out.get("scores", 0),
                        "method_id": method_id,
                        "receptor_id": receptor_id,
                    },
                ),
            },
            metadata={
                "input_count": result.rows_in,
                "output_count": result.rows_in,
                "engine_id": _ENGINE_ID,
                "method_id": method_id,
                "receptor_id": receptor_id,
                "receptor_path": config.receptor_path,
                "receptor_pdbqt_id": prepared_id,
                "receptor_pdbqt_path": config.receptor_pdbqt_path,
                "scoring": config.scoring,
                "search_mode": config.search_mode,
                "num_modes": config.num_modes,
                "seed": config.seed,
                "scored_pose_count": result.total("scored_pose_count"),
                "missing_geometry_count": result.total("missing_geometry_count"),
                "ligand_prep_failed_count": result.total("ligand_prep_failed_count"),
                "unscored_count": result.total("unscored_count"),
                **pose_quality_metadata(result.total, config=config.pose_quality),
                **config.box,
                **result.response_metadata(),
            },
        )


__all__ = ["UniDockConfig", "UniDockPlugin"]
