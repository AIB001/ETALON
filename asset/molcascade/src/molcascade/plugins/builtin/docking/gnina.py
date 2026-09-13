"""Rescore a shard of ligands with GNINA and record what came back.

GNINA is the expensive engine in the tier and the tier is arranged around that
fact.  It runs a Monte-Carlo search like Uni-Dock and then puts a 3D
convolutional network over the resulting poses, which is where its accuracy and
its cost both come from: two to ten seconds a ligand against Uni-Dock's tenth of
one.  So the shipped cascade puts it *after* the two fast engines rather than
beside them -- it rescores what they agreed on, and a few thousand survivors is
under an hour where the whole shortlist would be days.

Three things follow from its command line.

It is the only engine here with a real CPU path, and that path is thousands of
times slower rather than merely slower.  A CPU lane is therefore refused unless
the operator turned it on by name, so nobody discovers overnight that a run they
believed was on a card was not.

It takes ``--device``, but the shard pool has already pinned this process to one
card with ``CUDA_VISIBLE_DEVICES``, so the assigned card is the only one this
process can see and it is always device ``0``.  Passing the lane's own index
would name a card that is not there.

And it emits three numbers per pose -- an empirical affinity, a CNN pose score
and a CNN affinity -- on three different scales, two of which get better going up
while the third gets better going down.  Which one is *the* score is a choice, so
it is a setting, and the choice travels in the score row rather than being
implied by the engine's name.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal, NamedTuple

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, model_validator

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
    backend_root,
    conda_environment,
    enforce_population_cap,
    finite_number,
    population_size,
    ranked,
    require_pdb_receptor,
    resolved_executable,
    run_engine,
    shard_geometry,
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
_ENGINE = "GNINA"
_ENGINE_ID = "gnina"
# Bumped whenever a change here alters the numbers or the poses recorded, so the
# `method_id` on every score row distinguishes them.  Provenance only: the
# descriptor version has to move with it to invalidate a warm cache.  v2: repair
# the pose against the receptor and check it before the score row is written.
# v3: stop storing the batch-local ligand name in the pose, which made a
# molecule's recorded bytes depend on how the work was sharded.
_IMPLEMENTATION_VERSION = 3

# Five hundred rows is fifteen to ninety minutes of CNN rescoring -- a tolerable
# amount of work to lose to an interruption, and the shard is the unit that
# resume replays.  Two orders of magnitude below Uni-Dock's shard for the same
# wall-clock, which is the cost difference between the two engines made
# structural.  A ceiling, not an override.
_SHARD_ROWS = 500

#: Ligand names are index-derived rather than taken from ``parent_id``: the name
#: is what GNINA echoes back in its output SDF and it is the only handle the
#: scores are mapped through, so it stays something this module minted.
_NAME_TEMPLATE = "lig-{index:06d}"

#: Which SDF tag is the score, what scale that puts it on, and which way better
#: lies.  ``direction`` travels in every row because two of these three disagree
#: with the third about the sign of an improvement.
_RANKINGS: dict[str, tuple[str, str, str]] = {
    "affinity": ("minimizedAffinity", "VINA_KCAL_MOL", "LOWER_STRONGER"),
    "cnn_score": ("CNNscore", "CNN_SCORE", "HIGHER_STRONGER"),
    "cnn_affinity": ("CNNaffinity", "CNN_AFFINITY", "HIGHER_STRONGER"),
}

#: The number kept alongside the chosen one.  Reporting the empirical affinity
#: next to a CNN score is what lets a reviewer see the two disagreeing.
_SECONDARY: dict[str, str] = {
    "affinity": "CNNaffinity",
    "cnn_score": "minimizedAffinity",
    "cnn_affinity": "minimizedAffinity",
}

_EXECUTABLE_HINT = (
    "Set 'executable' to the absolute path of the 'gnina' binary. MolCascade "
    "does not build or install it, and it is GPL-2.0, so a run has to permit "
    "copyleft backends with '--allow-copyleft' before it will start; see "
    "'molcascade doctor' for what is missing."
)


class GninaConfig(BoxedDockingEngineConfig):
    """GNINA's own settings on top of the shared target block.

    ``receptor_path`` is handed to ``-r`` unchanged, so this is the one engine
    in the tier whose ``receptor_id`` is the digest of the very bytes it opened.
    """

    engine_id: ClassVar[str] = _ENGINE_ID

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """GNINA ships a single release binary, not a package.

        ``envs/bootstrap.sh`` downloads it into the shared backend root and
        writes a small wrapper beside it, so the wrapper is what gets looked for
        -- it is the thing that sets the library path the static build needs.
        """

        return (
            MachinePath(
                field="executable",
                label="the GNINA binary",
                candidates=(
                    backend_root() / "bin" / "gnina",
                    *conda_environment(_ENGINE_ID, "bin/gnina"),
                ),
                remedy="bash envs/bootstrap.sh gnina",
                note=(
                    "GNINA is GPL-2.0-or-later, so the run also needs "
                    "'--allow-copyleft' before it will start."
                ),
            ),
        )

    #: How much CNN there is. ``rescore`` runs the empirical search and then
    #: scores the resulting poses with the network, which is the setting the
    #: published accuracy figures are for; ``refinement`` optimises against the
    #: network as well and costs several times more; ``none`` turns GNINA into
    #: a slower Uni-Dock.
    cnn_scoring: Literal[
        "none", "rescore", "refinement", "metrorescore", "metrorefine", "all"
    ] = "rescore"

    #: Which built-in network, e.g. ``crossdock_default2018`` or its
    #: ``_ensemble`` form. ``None`` leaves GNINA on its own default, which is
    #: what the version of the binary decides -- naming one is how a campaign
    #: stops depending on that. The weights are compiled into the binary, so
    #: there is nothing to download and nothing to digest.
    cnn: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.]{0,63}$")

    #: Monte-Carlo search effort, before any CNN work. Vina's own default is 8.
    exhaustiveness: int = Field(default=8, ge=1, le=512)

    #: Which number is *the* score for this stage. The other two are still
    #: computed; one of them is kept as ``secondary_score`` and the rest is in
    #: the pose, if poses were kept.
    rank_by: Literal["cnn_affinity", "cnn_score", "affinity"] = "cnn_affinity"

    #: Which prepared conformer to dock. Shared with Uni-Dock so that a
    #: consensus between the two is about one geometry: GNINA searches from it
    #: rather than treating it as the answer, but the starting point is still
    #: part of what produced the number.
    conformer_index: int = Field(default=0, ge=0, le=63)

    #: Keep the docked pose in the score row.  On by default, as everywhere in
    #: this tier: ``molcascade trace`` exports the kept poses as SDF and cannot
    #: reconstruct them afterwards.
    keep_poses: bool = True

    #: Run on CPU when no card was assigned. Off by default and deliberately
    #: awkward to turn on: GNINA's CPU path works, and a shortlist that would
    #: take an hour on a card takes weeks on it.
    allow_cpu: bool = False

    #: Threads for the CPU path. Ignored on a CUDA lane, where the pool has
    #: already decided how many processes share the machine.
    cpu_threads: int = Field(default=1, ge=1, le=256)

    #: A hang guard sized for the slowest sensible setting, not a search budget:
    #: CNN rescoring costs two to ten seconds a ligand.
    timeout_per_molecule_seconds: float = Field(default=60.0, gt=0.0, le=86_400.0)

    #: The fixed cost of the call -- CUDA context, network weights -- charged
    #: once however small the shard is, which is why it cannot come out of a
    #: per-molecule budget.
    startup_seconds: float = Field(default=300.0, gt=0.0, le=86_400.0)

    @model_validator(mode="after")
    def _ranking_is_computed(self) -> GninaConfig:
        """Refuse to rank by a number the requested settings never produce.

        With ``cnn_scoring`` off GNINA writes no CNN tags at all, so a CNN
        ranking would not mis-rank the shortlist -- it would silently produce an
        empty one, every molecule counted as unscored.  That is a configuration
        mistake worth catching where it was made.
        """

        if self.cnn_scoring == "none" and self.rank_by != "affinity":
            raise ValueError(
                f"rank_by={self.rank_by!r} needs a CNN score, but cnn_scoring is 'none'; "
                "either turn the network on or rank by 'affinity'"
            )
        return self


def _method_id(config: GninaConfig, *, receptor_id: str) -> str:
    """Identify the computation: which protein, which box, which network.

    ``allow_cpu`` and ``cpu_threads`` are deliberately absent.  They say where
    the work ran, not what was computed, and this project keeps machine settings
    out of artifact identity so the same cascade does not become a different
    experiment on a different host.
    """

    return "docking-gnina:sha256:" + canonical_sha256(
        {
            "engine": _ENGINE_ID,
            "implementation_version": _IMPLEMENTATION_VERSION,
            "pose_quality": config.pose_quality.identity(),
            "receptor_id": receptor_id,
            "cnn_scoring": config.cnn_scoring,
            "cnn": config.cnn,
            "exhaustiveness": config.exhaustiveness,
            "num_modes": config.num_modes,
            "rank_by": config.rank_by,
            "conformer_index": config.conformer_index,
            "seed": config.seed,
            **config.box,
        }
    )


def _lane_arguments(device: str, *, config: GninaConfig) -> list[str]:
    """Turn the lane this process was given into GNINA's own device flags.

    On a CUDA lane the answer is always ``0``.  The shard pool set
    ``CUDA_VISIBLE_DEVICES`` before this process started, so the assigned card is
    the only one visible and it is renumbered to zero; passing the lane's global
    index would select a card outside the mask, which GNINA reports as a missing
    device rather than as the misconfiguration it is.
    """

    if device.startswith("cuda:"):
        return ["--device", "0"]
    if not config.allow_cpu:
        raise PluginError(
            f"{_ENGINE} was assigned lane {device!r} and CPU docking was not permitted",
            code="DOCKING_GPU_REQUIRED",
            hint=(
                "GNINA is the one engine in this tier with a CPU path, and it is "
                "thousands of times slower there -- a shortlist that takes an hour "
                "on a card takes weeks. Run with '--device cuda' on a machine with "
                "an NVIDIA card, or set 'allow_cpu' on this stage if you meant it."
            ),
            context={"engine": _ENGINE, "device": device},
        )
    return ["--no_gpu", "--cpu", str(config.cpu_threads)]


def _ligand_record(molblock: str, name: str) -> str | None:
    """Retitle one prepared conformer as an SDF record, or say it is unusable.

    The title line is rewritten rather than the molecule re-parsed: the
    preparation stage wrote these blocks with RDKit and GNINA parses them
    itself, so a round trip through a third reader would only add a way to fail.
    Everything past ``M  END`` is dropped, because a properties block inherited
    from upstream would arrive in the output as tags this module then tried to
    read as scores.
    """

    lines = molblock.splitlines()
    end = next((index for index, line in enumerate(lines) if line.startswith("M  END")), None)
    if end is None or end < 3:
        return None
    lines = lines[: end + 1]
    lines[0] = name
    return "\n".join(lines) + "\n$$$$\n"


def _parse_records(text: str) -> list[tuple[str, str | None, dict[str, float | None]]]:
    """Split GNINA's output SDF into ``(name, molblock, tags)`` in file order.

    Parsed as text rather than through RDKit for two reasons.  A docked pose can
    carry a valence RDKit refuses to sanitise, and losing a *score* because its
    pose would not round-trip through a chemistry toolkit is the wrong failure.
    And the bytes GNINA wrote are the pose; re-perceiving and re-writing them
    would store something subtly different from what was scored.
    """

    records: list[tuple[str, str | None, dict[str, float | None]]] = []
    for chunk in text.split("$$$$"):
        # Every record after the first begins with the newline that terminated
        # the delimiter line. Stripping exactly that keeps the title line at
        # index 0 even when the title is empty.
        body = chunk.removeprefix("\r\n").removeprefix("\n")
        if not body.strip():
            continue
        lines = body.splitlines()
        name = lines[0].strip()

        molblock: str | None = None
        end = next((index for index, line in enumerate(lines) if line.startswith("M  END")), None)
        if end is not None:
            molblock = "\n".join(lines[: end + 1]) + "\n"

        tags: dict[str, float | None] = {}
        index = 0
        while index < len(lines):
            line = lines[index]
            if line.startswith(">") and "<" in line:
                remainder = line.split("<", 1)[1]
                if ">" in remainder:
                    key = remainder.split(">", 1)[0]
                    value = lines[index + 1].strip() if index + 1 < len(lines) else None
                    tags[key] = finite_number(value)
                    index += 2
                    continue
            index += 1
        records.append((name, molblock, tags))
    return records


def _untitled(molblock: str | None) -> str | None:
    """Drop the scratch name from a pose that is about to be stored.

    ``lig-000007`` is minted per batch and means nothing outside the call that
    made it -- so a molecule's stored bytes would depend on which shard it
    landed in, and an exported SDF would repeat the same handful of titles once
    per shard.  The name is still the key GNINA's output is grouped by; it just
    has no business surviving into an artifact, where ``parent_id`` is the
    identity and the SD tags carry it.  Blanking rather than substituting is
    what Uni-Dock and KarmaDock already write.
    """

    if molblock is None:
        return None
    lines = molblock.splitlines()
    if not lines:
        return molblock
    lines[0] = ""
    return "\n".join(lines) + "\n"


class _Pose(NamedTuple):
    """One returned pose: the number ranked on, its companion, and the geometry."""

    score: float
    secondary: float | None
    molblock: str | None


def _dock_batch(
    ligands: list[tuple[str, str]],
    *,
    parent_ids: Mapping[str, str],
    config: GninaConfig,
    executable: Path,
    receptor: Path,
    receptor_id: str,
    method_id: str,
    lane: list[str],
) -> tuple[list[dict[str, Any]], int, int]:
    """Run the engine once over a batch and read its poses back.

    Returns the score rows, the count of ligands GNINA returned no usable pose
    for, and the count whose pose was asked for and could not be recovered.
    """

    rows: list[dict[str, Any]] = []
    poses_missing = 0
    tag, score_kind, direction = _RANKINGS[config.rank_by]
    secondary_tag = _SECONDARY[config.rank_by]
    timeout = math.ceil(config.startup_seconds + len(ligands) * config.timeout_per_molecule_seconds)

    with tempfile.TemporaryDirectory(
        prefix="molcascade-gnina-",
        dir=config.scratch_dir,
    ) as scratch_name:
        scratch = Path(scratch_name)
        ligand_file = scratch / "ligands.sdf"
        output_file = scratch / "poses.sdf"
        ligand_file.write_text("".join(record for _name, record in ligands), encoding="utf-8")

        command = [
            str(executable),
            "-r",
            str(receptor),
            "-l",
            str(ligand_file),
            *config.box_arguments(),
            "-o",
            str(output_file),
            "--seed",
            str(config.seed),
            "--num_modes",
            str(config.num_modes),
            "--exhaustiveness",
            str(config.exhaustiveness),
            "--cnn_scoring",
            config.cnn_scoring,
            *(["--cnn", config.cnn] if config.cnn is not None else []),
            *lane,
        ]
        run_engine(
            command,
            cwd=scratch,
            timeout=timeout,
            engine=_ENGINE,
            code="DOCKING_GNINA_RUN_FAILED",
            hint=_EXECUTABLE_HINT,
            context={"ligand_count": len(ligands), "receptor_id": receptor_id},
        )

        # Poses for one ligand are consecutive and share its name, but grouping
        # by name rather than by position is what keeps the mapping right when
        # GNINA drops a ligand it could not place at all.  The companion number
        # travels with its own pose, so a CNN score is never reported beside an
        # affinity from a different conformation -- that would read as the two
        # scoring functions disagreeing when they were not asked the same
        # question.
        grouped: dict[str, list[_Pose]] = {}
        if output_file.is_file():
            text = output_file.read_text(encoding="utf-8", errors="replace")
            # The checker needs the same geometry the operator would keep, so
            # either request keeps it; the column is nulled afterwards if only
            # the checker wanted it.
            wants_pose = config.keep_poses or config.pose_quality.enabled
            for name, molblock, tags in _parse_records(text):
                if name not in parent_ids:
                    continue
                score = tags.get(tag)
                if score is None:
                    continue
                grouped.setdefault(name, []).append(
                    _Pose(
                        score,
                        tags.get(secondary_tag),
                        _untitled(molblock) if wants_pose else None,
                    )
                )
                if wants_pose and molblock is None:
                    poses_missing += 1

    unscored = 0
    descending = direction == "HIGHER_STRONGER"
    for name, _record in ligands:
        poses = grouped.get(name)
        if not poses:
            unscored += 1
            continue
        scores = [pose.score for pose in poses]
        for rank, chosen in enumerate(
            ranked(scores, limit=config.num_modes, descending=descending)
        ):
            pose = poses[chosen]
            rows.append(
                {
                    "parent_id": parent_ids[name],
                    "engine_id": _ENGINE_ID,
                    "receptor_id": receptor_id,
                    "pose_rank": rank,
                    "score": pose.score,
                    "score_kind": score_kind,
                    "direction": direction,
                    "secondary_score": pose.secondary,
                    "pose_molblock": pose.molblock,
                    "method_id": method_id,
                }
            )
    return rows, unscored, poses_missing


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Rescore one contiguous range of the population in whichever process owns it."""

    config = GninaConfig.model_validate(dict(task.config))
    lane = _lane_arguments(task.device, config=config)
    executable = resolved_executable(config.executable, engine=_ENGINE, hint=_EXECUTABLE_HINT)
    _require_pdb_receptor(config)
    receptor, receptor_id = verified_receptor(config)
    method_id = _method_id(config, receptor_id=receptor_id)
    # One session for the shard: the receptor is read once however many batches
    # follow, and the counters accumulate across them.
    pose_session = PoseQualitySession(config.pose_quality, receptor_path=config.receptor_path)

    input_count = 0
    score_count = 0
    missing_geometry = 0
    unusable_geometry = 0
    unscored = 0
    poses_missing = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["scores"], DOCKING_SCORE_V1.schema, compression="zstd"
        ) as score_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            parents = [str(value) for value in batch.column("parent_id").to_pylist()]
            input_count += len(parents)
            parent_writer.write_batch(batch)

            geometry = shard_geometry(task, parents, index=config.conformer_index)
            ligands: list[tuple[str, str]] = []
            names: dict[str, str] = {}
            for parent_id in parents:
                molblock = geometry.get(parent_id)
                if molblock is None:
                    # No usable conformer: the preparation stage already
                    # recorded why, and the score gate downstream rejects a
                    # molecule with no evidence in the open.
                    missing_geometry += 1
                    continue
                name = _NAME_TEMPLATE.format(index=len(ligands))
                record = _ligand_record(molblock, name)
                if record is None:
                    unusable_geometry += 1
                    continue
                names[name] = parent_id
                ligands.append((name, record))
            if not ligands:
                continue

            rows, batch_unscored, batch_missing = _dock_batch(
                ligands,
                parent_ids=names,
                config=config,
                executable=executable,
                receptor=receptor,
                receptor_id=receptor_id,
                method_id=method_id,
                lane=lane,
            )
            unscored += batch_unscored
            poses_missing += batch_missing
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
            "unusable_geometry_count": unusable_geometry,
            "unscored_count": unscored,
            "pose_missing_count": poses_missing,
            "receptor_id": receptor_id,
            "method_id": method_id,
            **pose_session.report.metadata(),
        },
    )


def _require_pdb_receptor(config: GninaConfig) -> None:
    """GNINA opens the operator's structure directly, so the format is its own."""

    require_pdb_receptor(
        config.receptor_path,
        engine=_ENGINE,
        hint=(
            "Give '--receptor' a protein PDB with hydrogens. This is the same "
            "file the rest of the docking tier is pointed at, so converting it "
            "is a decision about the target, not about this engine."
        ),
    )


class GninaPlugin:
    """Rescore a shortlist against one receptor with GNINA's CNN docking."""

    descriptor = PluginDescriptor(
        id="docking.gnina",
        version="0.3.0",
        kind=PluginKind.DOCK,
        inputs=(PARENT_V1.id, LIGAND_CONFORMER_V1.id),
        outputs=(PARENT_V1.id, DOCKING_SCORE_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "scores": DOCKING_SCORE_V1.id,
        },
        cardinality=Cardinality.ONE_TO_MANY,
        determinism=Determinism.SEEDED,
        display_name="GNINA 1.3 (CNN rescoring)",
        description=(
            "Monte-Carlo docking followed by 3D convolutional rescoring of the "
            "poses. The most accurate and by far the most expensive engine in "
            "the tier, and GPL-2.0, so a run must permit copyleft backends."
        ),
    )
    config_model = GninaConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = validated_config(request, GninaConfig, engine=_ENGINE, hint=_EXECUTABLE_HINT)
        parents = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        conformers = require_single_input(request.inputs, contract_id=LIGAND_CONFORMER_V1.id)

        # The cap is checked here rather than per shard: ten shards each under
        # the limit would otherwise add up to a run nobody agreed to. It matters
        # most for this engine, where the difference is days.
        enforce_population_cap(
            population_size(discover_contract_files(parents, PARENT_V1)),
            limit=config.max_molecules,
            engine=_ENGINE,
        )
        _require_pdb_receptor(config)
        _receptor, receptor_id = verified_receptor(config)
        method_id = _method_id(config, receptor_id=receptor_id)

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
                "cnn_scoring": config.cnn_scoring,
                "cnn": config.cnn,
                "exhaustiveness": config.exhaustiveness,
                "rank_by": config.rank_by,
                "score_kind": _RANKINGS[config.rank_by][1],
                "num_modes": config.num_modes,
                "seed": config.seed,
                "scored_pose_count": result.total("scored_pose_count"),
                "missing_geometry_count": result.total("missing_geometry_count"),
                "unusable_geometry_count": result.total("unusable_geometry_count"),
                "unscored_count": result.total("unscored_count"),
                "pose_missing_count": result.total("pose_missing_count"),
                **pose_quality_metadata(result.total, config=config.pose_quality),
                **config.box,
                **result.response_metadata(),
            },
        )


__all__ = ["GninaConfig", "GninaPlugin"]
