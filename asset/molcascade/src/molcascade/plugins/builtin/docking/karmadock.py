"""Score a shard of molecules with KarmaDock and record what came back.

KarmaDock is the odd one in this tier and it is worth being precise about why.
Uni-Dock and GNINA *search* for a pose inside a box the operator drew; KarmaDock
*predicts* one with a graph network.  So it takes SMILES rather than conformers,
generates its own geometry internally, and has no box at all -- which is why this
adapter binds only ``parent/v1`` and why handing it the tier's shared conformer
table would be recording that it used geometry it never read.

Three consequences of its command line shape run through everything below.

It loads a Torch checkpoint and initialises CUDA once per invocation and then
scores at roughly fifty molecules a second, so a shard is one call: splitting a
shard into batches would pay the startup cost once per batch and turn the
fastest engine in the tier into the slowest.

And it derives its own pocket, writing ``<protein>_pocket.pdb`` beside whatever
``--protein_file`` points at.  Pointed straight at the operator's receptor that
would litter their directory, and -- worse -- two shards on two GPUs would race
to write the same derived file and one of them could read it half-written.  So
each shard stages its own copy of the receptor in scratch and lets KarmaDock
derive the pocket there.

Finally, ``--out_corrected`` writes *two* poses per ligand rather than one and
they are not interchangeable.  ``_POSE_SUFFIX`` below says which one this
adapter reads and why the other one is not usable as a structure.
"""

from __future__ import annotations

import csv
import math
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, field_validator

from molcascade.chemistry.datasets import discover_contract_files, require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.contracts import DOCKING_SCORE_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin.docking.common import (
    DockingEngineConfig,
    MachinePath,
    absolute_path,
    backend_root,
    conda_environment,
    enforce_population_cap,
    engine_path,
    finite_number,
    population_size,
    require_gpu_lane,
    require_pdb_receptor,
    resolved_executable,
    run_engine,
    structure_digest,
    validated_config,
    verified_receptor,
)
from molcascade.plugins.builtin.docking.pose_quality import (
    apply_pose_quality,
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
_ENGINE = "KarmaDock"
_ENGINE_ID = "karmadock"
# Bumped whenever a change here alters the numbers or the poses recorded, so the
# `method_id` on every score row distinguishes them.  That is provenance only: it
# is *not* in `stage_cache_key`, which hashes the descriptor and the config, so
# bumping this alone leaves a warm cache serving the old results.  The descriptor
# version has to move with it.  v2: read the align-corrected pose and its
# matching score instead of the force-field-corrected pair.  v3: repair the pose
# against the receptor and check it before the score row is written.
_IMPLEMENTATION_VERSION = 3
_SCORE_KIND = "KARMADOCK_MDN"

# One call per shard, so the shard is as large as the default allows: the model
# load and CUDA init are the fixed cost, and at ~0.02 s a molecule 50 000 rows
# is under twenty minutes of work.  A ceiling, not an override.
_SHARD_ROWS = 50_000

#: KarmaDock's own script, relative to the checkout it lives in.
_SCRIPT = Path("utils") / "virtual_screening.py"

#: Ligand names are index-derived, because KarmaDock puts the name straight into
#: a pose filename.  Six digits covers a shard several times over.
_NAME_TEMPLATE = "lig-{index:06d}"

#: Which of the two poses ``--out_corrected`` writes per ligand is read back.
#: ``_pred_ff_corrected.sdf`` is the network's raw coordinates given ten MMFF
#: iterations, and ten is nowhere near enough: measured over a real shard its
#: aromatic rings sit up to 1.1 A out of plane and its MMFF strain reaches six
#: figures, because nothing constrains a graph network to bond lengths or to
#: planarity.  ``_pred_align_corrected.sdf`` instead starts from KarmaDock's own
#: ETKDG plus MMFF94s conformer and moves *that* onto the prediction using
#: rotatable-bond dihedrals and a rigid-body fit -- neither of which can alter a
#: bond length, an angle or a ring -- so it is a chemically sound structure by
#: construction.  It pays for that in placement, being a clean conformer fitted
#: to a distorted prediction, which is why the score reported alongside it is
#: the one KarmaDock measured on this pose rather than on the raw prediction.
_POSE_SUFFIX = "_pred_align_corrected.sdf"

_EXECUTABLE_HINT = (
    "Set 'executable' to the absolute path of the python inside KarmaDock's own "
    "environment and 'repo_path' to the checkout it runs from. KarmaDock pins "
    "rdkit 2022.09, which is why it cannot share this interpreter; see "
    "'molcascade doctor' for what is missing."
)


class KarmaDockConfig(DockingEngineConfig):
    """KarmaDock's own settings on top of the shared target block.

    No box, deliberately.  ``BoxedDockingEngineConfig`` is not inherited because
    KarmaDock locates the site from the reference ligand's atoms and would
    ignore a box, and lowering writes only the fields a plugin declares -- so a
    cascade cannot hand this engine a search volume it silently drops.
    """

    engine_id: ClassVar[str] = _ENGINE_ID

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """Two paths, because KarmaDock is a checkout as well as an environment.

        Both are properties of the installation and neither changes between
        campaigns, so both can be answered by the host.  The checkout is checked
        by its contents rather than its name: an empty ``KarmaDock/`` is a
        directory, and the failure it produces if the run starts anyway is
        Torch's, several minutes in.  ``trained_models/karmadock_screening.pkl``
        is committed to their repository, so the clone *is* the weight download
        -- there is no separate fetch, and the preflight says so.
        """

        return (
            MachinePath(
                field="executable",
                label="the python inside KarmaDock's own environment",
                candidates=conda_environment(_ENGINE_ID, "bin/python"),
                remedy="bash envs/bootstrap.sh karmadock",
                note=(
                    "It pins rdkit==2022.09.1 against this project's rdkit>=2024.9, "
                    "so this is never this environment's interpreter."
                ),
            ),
            MachinePath(
                field="repo_path",
                label="the KarmaDock checkout",
                kind="directory",
                candidates=(backend_root() / "KarmaDock",),
                contents=(
                    "utils/virtual_screening.py",
                    "trained_models/karmadock_screening.pkl",
                ),
                remedy="bash envs/bootstrap.sh karmadock",
                note=(
                    "The checkpoint is committed to the repository, so cloning it is "
                    "the whole download; there is no 'molcascade assets fetch' for it."
                ),
            ),
        )

    #: The KarmaDock checkout. Its ``utils/virtual_screening.py`` is the entry
    #: point and its ``trained_models/`` is where the weights have to sit --
    #: the script has no flag for either, so both are found by location.
    #: ``MOLCASCADE_KARMADOCK_REPO_PATH`` can supply it instead.
    repo_path: str = Field(default="", max_length=4096)

    #: A ligand bound in the site, in mol2. KarmaDock reads only its
    #: coordinates: their centroid is the pocket centre and their extent is
    #: what the pocket residues are selected around.
    reference_ligand_path: str = Field(min_length=1, max_length=4096)

    #: An already-selected pocket, staged where KarmaDock expects to find its
    #: own. Set this when the geometric selection picks up the wrong chain or
    #: misses a residue that matters; leave it unset and KarmaDock derives one.
    pocket_pdb_path: str | None = Field(default=None, max_length=4096)

    #: The checkpoint KarmaDock will load, recorded for identity only. There is
    #: no flag to point the script elsewhere, so this must be the file already
    #: inside ``repo_path``; naming it here is what puts the weights into
    #: ``method_id`` instead of leaving two model versions indistinguishable.
    weights_path: str | None = Field(default=None, max_length=4096)

    #: Ligands per forward pass. This is GPU memory, not search effort.
    engine_batch_size: int = Field(default=64, ge=1, le=4_096)

    #: KarmaDock predicts one pose per ligand, so there is nothing to rank.
    num_modes: int = Field(default=1, ge=1, le=1)

    #: Keep the corrected pose in the score row.  On by default for the same
    #: reason as everywhere else in this tier: ``molcascade trace`` exports the
    #: kept poses as SDF, and a run that discarded them has no way to say so
    #: except by producing an empty file.
    keep_poses: bool = True

    #: Only poses scoring at least this well are written at all. Ignored unless
    #: ``keep_poses`` is set, and zero means every ligand KarmaDock scored.
    pose_score_threshold: float = Field(default=0.0, ge=0.0, le=1_000.0)

    #: Per-molecule budget, on top of ``startup_seconds``.
    timeout_per_molecule_seconds: float = Field(default=0.5, gt=0.0, le=86_400.0)

    #: The fixed cost of the call: importing Torch, initialising CUDA and
    #: loading the checkpoint. It is charged once no matter how small the shard
    #: is, so it cannot come out of a per-molecule budget -- a four-ligand
    #: shard would otherwise be given two seconds to start a GPU.
    startup_seconds: float = Field(default=900.0, gt=0.0, le=86_400.0)

    @field_validator("repo_path")
    @classmethod
    def _repo_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="repo_path", engine_id=cls.engine_id)

    @field_validator("reference_ligand_path")
    @classmethod
    def _reference_is_absolute(cls, value: str) -> str:
        return absolute_path(value, field="reference_ligand_path")

    @field_validator("pocket_pdb_path")
    @classmethod
    def _pocket_is_absolute(cls, value: str | None) -> str | None:
        return None if value is None else absolute_path(value, field="pocket_pdb_path")

    @field_validator("weights_path")
    @classmethod
    def _weights_are_absolute(cls, value: str | None) -> str | None:
        return None if value is None else absolute_path(value, field="weights_path")


def _method_id(
    config: KarmaDockConfig,
    *,
    receptor_id: str,
    reference_id: str,
    pocket_id: str | None,
    weights_id: str | None,
) -> str:
    """Identify the computation: which protein, which site, which model.

    The pocket digest is here rather than in ``receptor_id`` for the same
    reason Uni-Dock's PDBQT is: an overridden pocket is a different computation
    against the same target, not a different target.
    """

    return "docking-karmadock:sha256:" + canonical_sha256(
        {
            "engine": _ENGINE_ID,
            "implementation_version": _IMPLEMENTATION_VERSION,
            "receptor_id": receptor_id,
            "reference_ligand_id": reference_id,
            "pocket_id": pocket_id,
            "weights_id": weights_id,
            "seed": config.seed,
            "pose_quality": config.pose_quality.identity(),
        }
    )


def _screening_script(config: KarmaDockConfig) -> Path:
    """Locate KarmaDock's entry point inside its checkout."""

    script = Path(config.repo_path) / _SCRIPT
    if not script.is_file():
        raise PluginError(
            f"{_ENGINE}'s screening script was not found inside the configured checkout",
            code="DOCKING_KARMADOCK_SCRIPT_MISSING",
            hint=_EXECUTABLE_HINT,
            context={"repo_path": config.repo_path, "expected": str(script)},
        )
    return script


def _require_pdb_receptor(config: KarmaDockConfig) -> None:
    """Unlike Uni-Dock, this engine opens the operator's structure directly.

    So the pocket it derives is only as good as its own PDB reader, and that
    reader has no way to say it recognised nothing.
    """

    require_pdb_receptor(
        config.receptor_path,
        engine=_ENGINE,
        hint=(
            "Give '--receptor' a protein PDB. This is the same file the rest "
            "of the docking tier is pointed at, so converting it is a decision "
            "about the target, not about this engine."
        ),
    )


def _reference_ligand(config: KarmaDockConfig) -> tuple[Path, str]:
    """Locate and digest the ligand that defines the site.

    The suffix check is not pedantry: KarmaDock parses this file with a mol2
    reader that returns nothing at all for other formats, and an empty
    coordinate array becomes a pocket centred on the origin rather than an
    error.  A silently wrong site is the one failure this tier cannot detect
    from its own output.
    """

    path = Path(config.reference_ligand_path)
    if path.suffix.lower() != ".mol2":
        raise PluginError(
            f"{_ENGINE} reads its reference ligand as mol2 and was given "
            f"{path.suffix or 'no'} suffix",
            code="DOCKING_REFERENCE_LIGAND_FORMAT_INVALID",
            hint=(
                "Pass the bound ligand as mol2. An sdf or mol reference is "
                "converted at preflight; this stage will not guess at a format "
                "whose reader fails by returning nothing."
            ),
            context={"reference_ligand_path": str(path)},
        )
    return structure_digest(
        config.reference_ligand_path,
        code="DOCKING_REFERENCE_LIGAND_UNREADABLE",
        hint=(
            "This is the ligand whose coordinates locate the binding site. "
            "KarmaDock needs one -- it has no box to fall back on."
        ),
    )


def _staged_receptor(config: KarmaDockConfig, scratch: Path) -> Path:
    """Copy the receptor into scratch so the derived pocket lands there too.

    KarmaDock builds its pocket filename by string-replacing ``.pdb`` in the
    protein path.  The copy is named so that replacement produces a new file --
    a receptor whose name does not end in ``.pdb`` would make the replacement a
    no-op, and KarmaDock would then treat the *whole protein* as the pocket and
    say nothing about it.
    """

    staged = scratch / "receptor.pdb"
    shutil.copyfile(config.receptor_path, staged)
    if config.pocket_pdb_path is not None:
        # KarmaDock derives a pocket only when this exact path is absent.
        shutil.copyfile(config.pocket_pdb_path, scratch / "receptor_pocket.pdb")
    return staged


def _read_scores(path: Path) -> dict[str, tuple[float, float | None]]:
    """Read ``score.csv`` back as ``name -> (score, rescore of the kept pose)``.

    KarmaDock writes three columns: the score on its raw prediction, and that
    score recomputed on each of the two corrected poses.  The primary is the
    prediction, which is what the gate compares; the secondary is deliberately
    the one measured on ``_POSE_SUFFIX``, so the number recorded and the
    structure exported describe the same geometry.

    A ligand KarmaDock could not build a graph for is absent from this file
    rather than present with a null, so a missing key is the normal way a
    molecule goes unscored.
    """

    if not path.is_file():
        return {}
    scores: dict[str, tuple[float, float | None]] = {}
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            name = (row.get("pdb_id") or "").strip()
            primary = finite_number(row.get("karma_score"))
            if not name or primary is None:
                continue
            scores[name] = (primary, finite_number(row.get("karma_score_aligned")))
    return scores


def _pose_molblock(directory: Path, name: str) -> str | None:
    """Read one corrected pose back, or record that it could not be."""

    from rdkit import Chem

    path = directory / f"{name}{_POSE_SUFFIX}"
    if not path.is_file():
        return None
    try:
        molecule = Chem.MolFromMolFile(str(path), removeHs=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if molecule is None:
        return None
    try:
        return str(Chem.MolToMolBlock(molecule))
    except (RuntimeError, ValueError):
        return None


def _dock_shard(
    ligands: list[tuple[str, str]],
    *,
    parent_ids: Mapping[str, str],
    config: KarmaDockConfig,
    executable: Path,
    script: Path,
    receptor_id: str,
    reference: Path,
    method_id: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """Run KarmaDock once over the whole shard and read its scores back.

    Returns the score rows, the count of ligands it returned nothing for, and
    the count whose pose was asked for and could not be read.
    """

    rows: list[dict[str, Any]] = []
    poses_missing = 0
    timeout = math.ceil(
        config.startup_seconds + len(ligands) * config.timeout_per_molecule_seconds
    )
    with tempfile.TemporaryDirectory(
        prefix="molcascade-karmadock-",
        dir=config.scratch_dir,
    ) as scratch_name:
        scratch = Path(scratch_name)
        output_dir = scratch / "scores"
        output_dir.mkdir()
        staged = _staged_receptor(config, scratch)

        smi_file = scratch / "ligands.smi"
        smi_file.write_text(
            "".join(f"{smiles} {name}\n" for name, smiles in ligands), encoding="utf-8"
        )

        command = [
            str(executable),
            "-u",
            str(script),
            "--ligand_smi",
            str(smi_file),
            "--protein_file",
            str(staged),
            "--crystal_ligand_file",
            str(reference),
            "--out_dir",
            str(output_dir),
            "--batch_size",
            str(config.engine_batch_size),
            "--random_seed",
            str(config.seed),
            "--score_threshold",
            repr(float(config.pose_score_threshold)),
        ]
        # The checker needs the same file the operator would keep, so either
        # request produces it.  Nulling the column afterwards is cheap; docking
        # a shard again because the geometry was never written is not.
        if config.keep_poses or config.pose_quality.enabled:
            command.append("--out_corrected")

        run_engine(
            command,
            cwd=scratch,
            timeout=timeout,
            engine=_ENGINE,
            code="DOCKING_KARMADOCK_RUN_FAILED",
            hint=_EXECUTABLE_HINT,
            context={"ligand_count": len(ligands), "receptor_id": receptor_id},
        )

        scored = _read_scores(output_dir / "score.csv")
        for name, _smiles in ligands:
            found = scored.get(name)
            if found is None:
                continue
            score, secondary = found
            wants_pose = config.keep_poses or config.pose_quality.enabled
            molblock = _pose_molblock(output_dir, name) if wants_pose else None
            if wants_pose and molblock is None:
                poses_missing += 1
            rows.append(
                {
                    "parent_id": parent_ids[name],
                    "engine_id": _ENGINE_ID,
                    "receptor_id": receptor_id,
                    "pose_rank": 0,
                    "score": score,
                    "score_kind": _SCORE_KIND,
                    "direction": "HIGHER_STRONGER",
                    "secondary_score": secondary,
                    "pose_molblock": molblock,
                    "method_id": method_id,
                }
            )
    return rows, len(ligands) - len(rows), poses_missing


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Score one contiguous range of the population in whichever process owns it."""

    config = KarmaDockConfig.model_validate(dict(task.config))
    require_gpu_lane(task.device, engine=_ENGINE)
    executable = resolved_executable(config.executable, engine=_ENGINE, hint=_EXECUTABLE_HINT)
    script = _screening_script(config)
    _require_pdb_receptor(config)
    _receptor, receptor_id = verified_receptor(config)
    reference, reference_id = _reference_ligand(config)
    pocket_id = _pocket_digest(config)
    weights_id = _weights_digest(config)
    method_id = _method_id(
        config,
        receptor_id=receptor_id,
        reference_id=reference_id,
        pocket_id=pocket_id,
        weights_id=weights_id,
    )

    input_count = 0
    empty_smiles = 0
    # The whole shard is docked in one call, so the ligands are collected first
    # and the parents are streamed straight through as they are read.
    ligands: list[tuple[str, str]] = []
    names: dict[str, str] = {}
    with pq.ParquetWriter(
        task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
    ) as parent_writer:
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            parent_writer.write_batch(batch)
            parent_ids = batch.column("parent_id").to_pylist()
            smiles_column = batch.column("parent_smiles").to_pylist()
            for parent_id, smiles in zip(parent_ids, smiles_column, strict=True):
                input_count += 1
                text = str(smiles or "").strip()
                if not text or any(character.isspace() for character in text):
                    # The .smi format is whitespace-separated, so a SMILES with
                    # a space in it would arrive as a different molecule with a
                    # different name. Refusing one row is better than that.
                    empty_smiles += 1
                    continue
                name = _NAME_TEMPLATE.format(index=len(ligands))
                names[name] = str(parent_id)
                ligands.append((name, text))

    rows: list[dict[str, Any]] = []
    unscored = 0
    poses_missing = 0
    if ligands:
        rows, unscored, poses_missing = _dock_shard(
            ligands,
            parent_ids=names,
            config=config,
            executable=executable,
            script=script,
            receptor_id=receptor_id,
            reference=reference,
            method_id=method_id,
        )

    # Repair and judge before the rows are written, so what lands in the score
    # table is what passed.  A discarded pose leaves the molecule with no score,
    # which the tier's evidence gate already treats as missing -- the funnel
    # shows the drop and `pose_check_failed__*` names the check responsible.
    rows, pose_report = apply_pose_quality(
        rows,
        config=config.pose_quality,
        receptor_path=config.receptor_path,
        keep_poses=config.keep_poses,
    )

    with pq.ParquetWriter(
        task.output_paths["scores"], DOCKING_SCORE_V1.schema, compression="zstd"
    ) as score_writer:
        if rows:
            score_writer.write_table(pa.Table.from_pylist(rows, schema=DOCKING_SCORE_V1.schema))

    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "scores": len(rows)},
        metadata={
            "scored_pose_count": len(rows),
            "unscored_count": unscored,
            "unusable_smiles_count": empty_smiles,
            "pose_missing_count": poses_missing,
            "receptor_id": receptor_id,
            "method_id": method_id,
            **pose_report.metadata(),
        },
    )


def _pocket_digest(config: KarmaDockConfig) -> str | None:
    if config.pocket_pdb_path is None:
        return None
    _path, digest = structure_digest(
        config.pocket_pdb_path,
        code="DOCKING_POCKET_UNREADABLE",
        hint=(
            "This is the pocket selection that replaces KarmaDock's own. Remove "
            "'pocket_pdb_path' to let it derive one from the reference ligand."
        ),
    )
    return digest


def _weights_digest(config: KarmaDockConfig) -> str | None:
    if config.weights_path is None:
        return None
    _path, digest = structure_digest(
        config.weights_path,
        code="DOCKING_WEIGHTS_UNREADABLE",
        hint=(
            "This is the KarmaDock checkpoint, recorded so two model versions "
            "cannot produce scores that look interchangeable. It must be the "
            "file inside the checkout that KarmaDock itself loads."
        ),
    )
    return digest


class KarmaDockPlugin:
    """Score a shortlist against one receptor with KarmaDock's pose prediction."""

    descriptor = PluginDescriptor(
        id="docking.karmadock",
        # 0.2.0 reads the align-corrected pose rather than the force-field one.
        # `stage_cache_key` hashes this descriptor and the stage config, and the
        # change was to neither, so without the bump every workspace with a warm
        # cache would go on serving the distorted geometry forever.  It also
        # renames the registry key, which is the point: a config pinning
        # `@0.1.0` should fail to resolve rather than quietly get different
        # structures from the ones it was written against.
        version="0.3.0",
        kind=PluginKind.DOCK,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DOCKING_SCORE_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "scores": DOCKING_SCORE_V1.id,
        },
        cardinality=Cardinality.ONE_TO_MANY,
        determinism=Determinism.SEEDED,
        display_name="KarmaDock (GPU pose prediction)",
        description=(
            "Deep-learning pose prediction and mixture-density scoring against a "
            "prepared receptor. Takes SMILES and builds its own geometry, so it "
            "does not use the tier's shared conformers."
        ),
    )
    config_model = KarmaDockConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = validated_config(request, KarmaDockConfig, engine=_ENGINE, hint=_EXECUTABLE_HINT)
        parents = require_single_input(request.inputs, contract_id=PARENT_V1.id)

        # The cap is checked here rather than per shard: ten shards each under
        # the limit would otherwise add up to a run nobody agreed to.
        enforce_population_cap(
            population_size(discover_contract_files(parents, PARENT_V1)),
            limit=config.max_molecules,
            engine=_ENGINE,
        )
        _screening_script(config)
        _require_pdb_receptor(config)
        _receptor, receptor_id = verified_receptor(config)
        _reference, reference_id = _reference_ligand(config)
        method_id = _method_id(
            config,
            receptor_id=receptor_id,
            reference_id=reference_id,
            pocket_id=_pocket_digest(config),
            weights_id=_weights_digest(config),
        )

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
                "reference_ligand_id": reference_id,
                "reference_ligand_path": config.reference_ligand_path,
                "pocket_source": "configured" if config.pocket_pdb_path else "derived",
                "seed": config.seed,
                "scored_pose_count": result.total("scored_pose_count"),
                "unscored_count": result.total("unscored_count"),
                "unusable_smiles_count": result.total("unusable_smiles_count"),
                "pose_missing_count": result.total("pose_missing_count"),
                **pose_quality_metadata(result.total, config=config.pose_quality),
                **result.response_metadata(),
            },
        )


__all__ = ["KarmaDockConfig", "KarmaDockPlugin"]
