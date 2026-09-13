"""How much of a molecule's docking score is about *this* pocket.

A docking score is a number about one protein, and read alone it cannot
distinguish a ligand shaped for the target's site from one that scores well
against any pocket of roughly the right size.  Generative libraries are full of
the second kind, because a score-guided generator optimises exactly the quantity
the screen then reads back.  Cieplinski et al. (arXiv:2311.12035) showed the
failure directly -- molecules optimised against a docking oracle score highly
against unrelated receptors too -- and the ICML 2024 follow-up
(arXiv:2403.12987) turned the observation into the obvious control: dock the
same molecule against pockets it has no reason to bind, and read the target's
score against that background rather than against zero.

This plugin runs that control and decides nothing.  For each molecule it re-docks
the pose its docking gate accepted against every receptor in an operator-supplied
panel, then writes two ``derived_metric/v1`` rows:

``delta_score``
    ``score(target) - mean(score(panel))`` in kcal/mol.  Lower is better: a
    molecule that beats the panel by 2 kcal/mol is selective for the target,
    one that ties it is a good docker rather than a good ligand.

``panel_win_count``
    How many panel receptors scored the molecule *at least as strongly* as the
    target did.  A count rather than a mean, because one decoy pocket that wins
    outright is a specific fact that averaging hides -- and because the default
    threshold an operator wants is "none of them", which is a number a mean
    cannot express.

Both come from one docking run per panel receptor, so N receptors cost N times
the tier above.  The panel is empty by default and this criterion is off by
default for that reason: it is the most expensive evidence in the cascade and
the only one whose inputs MolCascade cannot supply.  There is no built-in decoy
set here on purpose -- DUD-E and DEKOIS carry their own licences and their own
bias, and shipping one would make a scientific choice on the operator's behalf
that they cannot see.

Three things it is not:

*Not a second opinion on the target score.*  The target's number is read from
the docking evidence, never recomputed.  This stage only ever adds receptors.

*Not comparable across scoring functions.*  The subtraction is only meaningful
if both sides are on one scale, so the panel's ``scoring`` is checked against
the ``score_kind`` recorded in the evidence and a mismatch stops the run.  Search
effort is a different matter -- it changes precision, not units -- so
``search_mode`` and ``num_modes`` travel in ``method_id`` and ``source_json``
instead of being enforced.

*Not a target of its own.*  This config deliberately declares no field named
``receptor_path``.  That name is the marker lowering keys on to push the
cascade's single target into a docking stage, and a stage that both received
the target and carried its own panel would have two receptors with equal claim
on one score row.  Panel receptors therefore arrive only through ``panel``, and
the campaign's target reaches this plugin only as evidence.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any, ClassVar, Literal

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from pydantic import Field, JsonValue, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import discover_contract_files
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DERIVED_METRIC_V1, DOCKING_SCORE_V1, PARENT_V1
from molcascade.errors import MolCascadeError, PluginError
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
from molcascade.plugins.builtin._machine_paths import (
    MachinePath,
    absolute_path,
    backend_root,
    conda_environment,
    engine_path,
    fill_machine_paths,
)
from molcascade.plugins.builtin.docking.common import (
    enforce_population_cap,
    ligand_pdbqt,
    parse_vina_poses,
    population_size,
    require_gpu_lane,
    require_meeko,
    resolved_executable,
    run_engine,
    structure_digest,
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
_DELTA_METRIC_ID = "delta_score"
_WIN_METRIC_ID = "panel_win_count"

_ENGINE = "Uni-Dock"
_ENGINE_ID = "unidock"

# One Uni-Dock call per panel receptor per batch, at roughly a tenth of a second
# per ligand per receptor.  A shard of 2 000 against a three-receptor panel is
# about ten minutes -- a reasonable amount of work to lose to an interruption,
# and still far above the ~1000 ligands the engine needs per call to be worth
# starting.
_SHARD_ROWS = 2_000

_SCORE_KINDS = {
    "vina": "VINA_KCAL_MOL",
    "vinardo": "VINARDO_KCAL_MOL",
    "ad4": "AD4_KCAL_MOL",
}

#: Where the panel's prepared receptors are cached, under the stage's own
#: staging root.  Dot-prefixed like every other scratch artefact a plugin owns,
#: and shaped as a workspace because that is what the preparation helpers take.
_PANEL_CACHE = ".panel-receptors"

#: A panel receptor's name becomes a key in ``source_json`` and a directory
#: component in scratch, so it is restricted before either happens.
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

_EXECUTABLE_HINT = (
    "Set 'executable' to the absolute path of the 'unidock' binary. The panel "
    "re-docks against the same engine the target was scored with; see "
    "'molcascade doctor' for the command that reports what is missing."
)

_BOX_FIELDS = ("center_x", "center_y", "center_z", "size_x", "size_y", "size_z")


class PanelReceptorConfig(StrictFrozenModel):
    """One pocket the molecule has no reason to bind, and where to search it.

    The site is defined the same two ways the campaign's target is: six numbers,
    or a reference ligand whose atoms are measured into a box.  Exactly one of
    them, because two site definitions that disagree have no correct resolution.

    Box size matters more here than it does for a single target.  ``delta_score``
    subtracts one search from another, and a panel receptor given a box twice the
    target's volume is being blind-docked against -- it will find something, and
    the difference will read as the molecule being unselective.  Keeping the
    panel's boxes near the target's is the operator's job, and the sizes are
    recorded in ``source_json`` so a reviewer can check it was done.
    """

    #: Short label for this pocket, carried into every row's ``source_json`` so
    #: a delta can be traced back to the receptor that produced it.
    name: str = Field(min_length=1, max_length=64)

    #: The pocket's structure as a PDB, repaired here the same way the target is
    #: unless ``prepare_receptors`` is off.  Optional only when an
    #: already-prepared PDBQT is supplied instead.
    structure_path: str = Field(default="", max_length=4096)

    #: An already-prepared PDBQT, for a pocket meeko cannot derive one from.
    #: Supplying it skips repair and conversion entirely, so what the engine
    #: reads is exactly what was handed over.
    prepared_pdbqt_path: str = Field(default="", max_length=4096)

    #: A structure whose atoms locate the site, in place of the six numbers.
    reference_ligand_path: str = Field(default="", max_length=4096)

    center_x: float | None = Field(default=None, allow_inf_nan=False, ge=-10_000.0, le=10_000.0)
    center_y: float | None = Field(default=None, allow_inf_nan=False, ge=-10_000.0, le=10_000.0)
    center_z: float | None = Field(default=None, allow_inf_nan=False, ge=-10_000.0, le=10_000.0)
    size_x: float | None = Field(default=None, allow_inf_nan=False, gt=0.0, le=200.0)
    size_y: float | None = Field(default=None, allow_inf_nan=False, gt=0.0, le=200.0)
    size_z: float | None = Field(default=None, allow_inf_nan=False, gt=0.0, le=200.0)

    @field_validator("name")
    @classmethod
    def _name_is_safe(cls, value: str) -> str:
        if _NAME_PATTERN.fullmatch(value) is None:
            raise ValueError(
                f"panel receptor name {value!r} must be letters, digits, '.', '_' or '-'; "
                "it names a directory and a JSON key, neither of which is a place to "
                "discover that an identifier contained a separator"
            )
        return value

    @field_validator("structure_path", "prepared_pdbqt_path", "reference_ligand_path")
    @classmethod
    def _paths_are_absolute(cls, value: str, info: Any) -> str:
        return value if not value else absolute_path(value, field=str(info.field_name))

    @model_validator(mode="after")
    def _one_structure_and_one_site(self) -> PanelReceptorConfig:
        if not self.structure_path and not self.prepared_pdbqt_path:
            raise ValueError(
                f"panel receptor {self.name!r} has no structure; give 'structure_path' "
                "(a PDB, repaired and converted here) or 'prepared_pdbqt_path'"
            )
        box = [getattr(self, field) for field in _BOX_FIELDS]
        given = [value is not None for value in box]
        if any(given) and not all(given):
            missing = [field for field in _BOX_FIELDS if getattr(self, field) is None]
            raise ValueError(
                f"panel receptor {self.name!r} gives a partial box; all six of "
                f"{', '.join(_BOX_FIELDS)} are required, missing {', '.join(missing)}"
            )
        if all(given) == bool(self.reference_ligand_path):
            raise ValueError(
                f"panel receptor {self.name!r} needs exactly one site definition: "
                "the six box numbers, or a 'reference_ligand_path' whose atoms "
                "define them"
            )
        return self

    @property
    def has_box(self) -> bool:
        return all(getattr(self, field) is not None for field in _BOX_FIELDS)

    def box_settings(self) -> dict[str, float]:
        """The six numbers, once they are known to be there."""

        return {field: float(getattr(self, field)) for field in _BOX_FIELDS}


#: Flat keys the HTML builder writes in place of a nested ``panel`` entry.
#:
#: Deliberately *not* declared as fields.  ``_promote_single_receptor`` consumes
#: them before field validation, so ``model_fields`` never contains a name
#: ``_target_values`` would expand -- which is what keeps the campaign's target
#: out of this stage even though the shorthand spells a receptor at top level.
_SHORTHAND_FIELDS = (
    "receptor_name",
    "structure_path",
    "prepared_pdbqt_path",
    "reference_ligand_path",
    *_BOX_FIELDS,
)


class SpecificityPanelConfig(StrictFrozenModel):
    """The panel, the engine that reads it, and which target score to subtract.

    A hand-written config lists ``panel`` outright.  The HTML builder cannot --
    its threshold fields are flat name/value pairs with no nesting -- so it writes
    one receptor's worth of keys at the top level and they are promoted into a
    one-entry panel here.  Mixing the two spellings is refused rather than
    merged, the same way ``MordredDescriptorGateConfig`` refuses it: a config
    that says both things is a config whose author was not sure which it meant.

    One pocket is a selectivity check against that pocket, which is a real
    experiment -- a paralog control is exactly this shape.  It is not a
    *background*, though, and ``delta_score`` reads as one.  Three or more
    unrelated pockets is where the mean starts meaning what its name says.

    Note what is *not* here, in ``panel`` or in the shorthand: a field named
    ``receptor_path``.  See the module docstring -- its absence is what keeps the
    campaign's target out of this stage's settings.
    """

    #: Which engine's environment overrides apply.  The panel re-docks with
    #: Uni-Dock because that is the engine whose raw Vina score the subtraction
    #: is defined against.
    engine_id: ClassVar[str] = _ENGINE_ID

    schema_version: int = Field(default=1, ge=1, le=1)

    #: Absolute path to the Uni-Dock binary, answered by the run host when left
    #: out -- from ``MOLCASCADE_UNIDOCK_EXECUTABLE`` or from where
    #: ``envs/bootstrap.sh`` installs it.
    executable: str = Field(default="", max_length=4096)

    #: The pockets to score against.  Required and non-empty: a panel stage with
    #: no panel would write a delta against an empty background, which is not a
    #: smaller experiment but a different and meaningless one.
    panel: tuple[PanelReceptorConfig, ...] = Field(min_length=1, max_length=16)

    #: Which engine's target scores to subtract from.  Left unset it resolves to
    #: the one engine present in the bound evidence, which is the common case
    #: because a docking stage writes only its own rows.
    target_engine_id: str | None = Field(default=None, min_length=1, max_length=256)
    target_receptor_id: str | None = Field(default=None, min_length=1, max_length=256)

    #: The panel's scoring function -- and, because a delta subtracts one score
    #: from another, the scale the bound evidence is required to be on.  There is
    #: deliberately no second field declaring the expected kind: two settings
    #: that must always agree are a contradiction waiting to be written down.
    scoring: Literal["vina", "vinardo", "ad4"] = "vina"
    #: Uni-Dock's exhaustiveness preset for the panel runs.  Worth matching to
    #: whatever the target stage used; a difference changes precision rather
    #: than units, so it is recorded rather than enforced.
    search_mode: Literal["fast", "balance", "detail"] = "balance"
    num_modes: int = Field(default=1, ge=1, le=20)
    seed: int = Field(default=20_260_823, ge=0, le=2**31 - 1)

    #: Repair every panel receptor the same way the campaign's target is
    #: repaired, so a delta is not a comparison between a fixed protein and
    #: three broken ones.  Off means the PDB reaches meeko exactly as supplied.
    prepare_receptors: bool = True
    keep_waters: bool = False
    keep_heterogens: bool = False

    #: Fail-closed cap.  N receptors multiply the tier above by N, so the number
    #: that matters is the population times the panel size.
    max_molecules: int = Field(default=20_000, ge=1, le=10_000_000)

    #: A hang guard per molecule per receptor, not a search budget.
    timeout_per_molecule_seconds: float = Field(default=5.0, gt=0.0, le=86_400.0)

    scratch_dir: str | None = Field(default=None, max_length=4096)
    batch_size: int = Field(default=2_048, ge=1, le=250_000)

    @field_validator("panel", mode="before")
    @classmethod
    def _arrays_to_tuples(cls, value: Any) -> Any:
        # Strict mode does not coerce, and JSON has no tuples.
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="before")
    @classmethod
    def _promote_single_receptor(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        present = [key for key in _SHORTHAND_FIELDS if key in data]
        if not present:
            return data
        promoted = dict(data)
        # A rendered form the operator left alone writes every box as "", and
        # promoting that would report a receptor with no structure when the truth
        # is that no panel was configured at all.  Blanks are dropped, and if
        # nothing survives them the shorthand was never used.
        supplied = {
            key: promoted.pop(key)
            for key in present
            if not isinstance(promoted[key], str) or promoted[key]
        }
        for key in present:
            promoted.pop(key, None)
        if not supplied:
            return promoted
        if "panel" in data:
            raise ValueError(
                "set either 'panel' or the single-receptor shorthand "
                f"({', '.join(sorted(supplied))}), not both"
            )
        receptor: dict[str, Any] = {"name": supplied.pop("receptor_name", "decoy")}
        receptor.update(supplied)
        promoted["panel"] = [receptor]
        return promoted

    @classmethod
    def installed_paths(cls) -> tuple[MachinePath, ...]:
        """The same binary Uni-Dock's own adapter looks for, found the same way."""

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

    @model_validator(mode="before")
    @classmethod
    def _fill_machine_paths(cls, data: Any) -> Any:
        return fill_machine_paths(
            data,
            engine_id=cls.engine_id,
            paths=cls.installed_paths(),
        )

    @field_validator("executable")
    @classmethod
    def _executable_is_absolute(cls, value: str) -> str:
        return engine_path(value, field="executable", engine_id=cls.engine_id)

    @field_validator("scratch_dir")
    @classmethod
    def _scratch_is_absolute(cls, value: str | None) -> str | None:
        return None if value is None else absolute_path(value, field="scratch_dir")

    @model_validator(mode="after")
    def _names_are_distinct(self) -> SpecificityPanelConfig:
        names = [receptor.name for receptor in self.panel]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise ValueError(
                "panel receptor names must be distinct -- they are the keys a delta "
                f"is traced back through; repeated: {', '.join(duplicated)}"
            )
        return self


class _PreparedReceptor:
    """One panel receptor after repair and conversion, ready for the engine."""

    __slots__ = ("box", "digest", "name", "pdbqt")

    def __init__(self, *, name: str, pdbqt: Path, digest: str, box: dict[str, float]) -> None:
        self.name = name
        self.pdbqt = pdbqt
        self.digest = digest
        self.box = box

    def as_json(self) -> dict[str, Any]:
        return {"name": self.name, "receptor_id": self.digest, **self.box}


def _validated_config(request: StageRequest) -> SpecificityPanelConfig:
    try:
        return SpecificityPanelConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid specificity-panel configuration: {error}",
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
            "specificity panel received unsupported input contracts",
            code="SPECIFICITY_PANEL_INPUT_CONTRACT_INVALID",
            context={"request_ports": unsupported},
        )
    parents = [value for value in inputs.values() if value.contract_id == PARENT_V1.id]
    evidence = [value for value in inputs.values() if value.contract_id == DOCKING_SCORE_V1.id]
    if len(parents) != 1 or len(evidence) != 1 or len(inputs) != 2:
        raise PluginError(
            "specificity panel requires exactly one parent and one docking input",
            code="SPECIFICITY_PANEL_INPUT_CARDINALITY_INVALID",
            context={
                "parent_input_count": len(parents),
                "evidence_input_count": len(evidence),
                "request_input_count": len(inputs),
            },
        )
    return parents[0], evidence[0]


def _evidence_dataset(stage_input: StageInput) -> ds.Dataset:
    files = discover_contract_files(stage_input, DOCKING_SCORE_V1)
    return ds.dataset([str(path) for path in files], format="parquet")


def _resolve_identity(dataset: ds.Dataset, *, configured: str | None, column: str) -> str:
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
            f"the specificity panel needs exactly one observed {column} when none is configured",
            code="SPECIFICITY_PANEL_IDENTITY_AMBIGUOUS",
            hint=(
                "Pin the engine whose scores the panel is subtracted from with "
                "target_engine_id, or bind the criterion to a single docking "
                "stage with evidence_from."
            ),
            context={
                "identity": column,
                "observed_count": len(observed),
                "observed_examples": examples,
            },
        )
    return observed[0]


def _require_comparable_evidence(
    dataset: ds.Dataset,
    config: SpecificityPanelConfig,
    *,
    engine_id: str,
    receptor_id: str,
) -> int:
    """Refuse a subtraction between two scales before paying for a single dock.

    Three separate things have to agree for ``score(target) - score(panel)`` to
    be a kcal/mol number: the evidence has to exist for this engine and this
    receptor, it has to be on the scale this stage is about to produce, and it
    has to run in the same direction.  Each is checked here rather than per
    molecule, because a panel run discovers all three at the end of an hour.

    Geometry is the fourth requirement and is checked the same way: the panel
    re-docks the accepted pose, and a docking stage that kept no poses leaves
    nothing to re-dock.
    """

    selected = (ds.field("engine_id") == engine_id) & (ds.field("receptor_id") == receptor_id)
    pose_count = int(dataset.count_rows(filter=selected))
    if pose_count == 0:
        raise PluginError(
            "docking evidence contains no scores from the selected engine",
            code="SPECIFICITY_PANEL_NO_MATCHING_EVIDENCE",
            hint=(
                "Bind the criterion to the docking stage that scored this "
                "population with evidence_from, or clear target_engine_id."
            ),
            context={"engine_id": engine_id, "receptor_id": receptor_id},
        )
    table = dataset.to_table(columns=["score_kind", "direction"], filter=selected)
    kinds = sorted({str(value) for value in table.column("score_kind").to_pylist()})
    directions = sorted({str(value) for value in table.column("direction").to_pylist()})
    expected = _SCORE_KINDS[config.scoring]
    mismatched: list[JsonValue] = [kind for kind in kinds if kind != expected]
    if mismatched:
        raise PluginError(
            "the target's docking scores are on a different scale than the panel will produce",
            code="SPECIFICITY_PANEL_SCALE_MISMATCH",
            hint=(
                "A delta score subtracts one docking score from another, which is "
                "only defined on one scale. Set 'scoring' to the function the "
                "bound docking stage used, or bind a different stage with "
                "evidence_from."
            ),
            context={
                "engine_id": engine_id,
                "panel_score_kind": expected,
                "observed_score_kinds": mismatched,
            },
        )
    inverted: list[JsonValue] = [value for value in directions if value != "LOWER_STRONGER"]
    if inverted:
        raise PluginError(
            "the target's docking scores declare that higher is stronger",
            code="SPECIFICITY_PANEL_DIRECTION_UNSUPPORTED",
            hint=(
                "The panel re-docks with a Vina-family scoring function, whose "
                "scores are negative binding energies. Evidence on the opposite "
                "convention cannot be subtracted from them."
            ),
            context={"engine_id": engine_id, "observed_directions": inverted},
        )
    with_geometry = int(dataset.count_rows(filter=selected & ds.field("pose_molblock").is_valid()))
    if with_geometry == 0:
        raise PluginError(
            "docking evidence stores no pose geometry, so there is nothing to re-dock",
            code="SPECIFICITY_PANEL_POSES_UNAVAILABLE",
            hint=(
                "Set keep_poses on the docking stage that feeds this criterion "
                "(or enable its pose-quality check), then re-run that stage."
            ),
            context={
                "engine_id": engine_id,
                "receptor_id": receptor_id,
                "score_count": pose_count,
            },
        )
    return with_geometry


def _prepare_panel(
    config: SpecificityPanelConfig,
    *,
    workspace: Path,
) -> list[_PreparedReceptor]:
    """Repair, convert and digest every panel receptor once, in the parent process.

    Once rather than per shard: preparation is the fragile step -- meeko stops on
    protonation, non-standard residues and missing atoms -- and a panel that
    discovered a bad receptor inside a worker would report it as N simultaneous
    shard failures after the first batch had already been docked.

    The preparation helpers live in the cascade layer and are imported here
    rather than at module scope: the plugin package is imported *by* the cascade
    package while building the builtin registry, so a module-level import would
    close the loop.
    """

    from molcascade.cascade.models import ReceptorPreparation
    from molcascade.cascade.receptor import prepare_receptor_pdb
    from molcascade.cascade.target import box_from_structure, prepare_receptor_pdbqt

    options = ReceptorPreparation(
        enabled=config.prepare_receptors,
        keep_waters=config.keep_waters,
        keep_heterogens=config.keep_heterogens,
    )
    prepared: list[_PreparedReceptor] = []
    for receptor in config.panel:
        if receptor.has_box:
            box = receptor.box_settings()
        else:
            try:
                box = box_from_structure(Path(receptor.reference_ligand_path)).settings
            except MolCascadeError as error:
                raise PluginError(
                    f"the site of panel receptor {receptor.name!r} could not be measured",
                    code="SPECIFICITY_PANEL_SITE_UNREADABLE",
                    hint=error.hint or None,
                    context={
                        "receptor_name": receptor.name,
                        "reference_ligand_path": receptor.reference_ligand_path,
                        "cause": error.code,
                    },
                ) from error

        if receptor.prepared_pdbqt_path:
            # Handed over ready: nothing is repaired, converted or second-guessed,
            # and the digest is of the file the engine will actually open.
            pdbqt = Path(receptor.prepared_pdbqt_path)
            _path, digest = structure_digest(
                str(pdbqt),
                code="SPECIFICITY_PANEL_RECEPTOR_UNREADABLE",
                hint=(
                    f"'prepared_pdbqt_path' for panel receptor {receptor.name!r} "
                    "names a file that could not be read."
                ),
            )
            prepared.append(
                _PreparedReceptor(name=receptor.name, pdbqt=pdbqt, digest=digest, box=box)
            )
            continue

        source = Path(receptor.structure_path)
        _path, digest = structure_digest(
            str(source),
            code="SPECIFICITY_PANEL_RECEPTOR_UNREADABLE",
            hint=(
                f"'structure_path' for panel receptor {receptor.name!r} names a "
                "file that could not be read."
            ),
        )
        try:
            if options.enabled:
                source, _report = prepare_receptor_pdb(
                    source,
                    options=options,
                    workspace=workspace,
                )
            pdbqt, _note = prepare_receptor_pdbqt(source, workspace=workspace)
        except MolCascadeError as error:
            # The cascade layer reports these as configuration errors, which they
            # are -- but they arrive from inside a stage, so they are re-raised
            # with the receptor that caused them named.
            raise PluginError(
                f"panel receptor {receptor.name!r} could not be prepared for docking",
                code="SPECIFICITY_PANEL_RECEPTOR_PREPARATION_FAILED",
                hint=error.hint or None,
                context={
                    "receptor_name": receptor.name,
                    "structure_path": receptor.structure_path,
                    "cause": error.code,
                },
            ) from error
        # The digest is of the structure the operator supplied, not of the file
        # meeko wrote: the delta is about that pocket, and the conversion is a
        # detail of how it was measured.  It travels in ``method_id`` instead.
        prepared.append(_PreparedReceptor(name=receptor.name, pdbqt=pdbqt, digest=digest, box=box))
    return prepared


def _method_id(
    config: SpecificityPanelConfig,
    *,
    metric_id: str,
    units: str,
    engine_id: str,
    receptor_id: str,
    panel: list[_PreparedReceptor],
) -> str:
    """Identify the measurement, not the run.

    The panel's composition is in here, which is the point: a delta against three
    kinases and a delta against three GPCRs are different quantities that would
    otherwise share a primary key and overwrite each other.  So are the boxes --
    re-running with a larger search volume on one decoy is a new measurement.
    """

    return "specificity-panel:sha256:" + canonical_sha256(
        {
            "implementation_version": _IMPLEMENTATION_VERSION,
            "metric_id": metric_id,
            "units": units,
            "direction": "LOWER_BETTER",
            "panel_engine": _ENGINE_ID,
            "target_engine_id": engine_id,
            "target_receptor_id": receptor_id,
            "pose_policy": "BEST_POSE",
            "scoring": config.scoring,
            "score_kind": _SCORE_KINDS[config.scoring],
            "search_mode": config.search_mode,
            "num_modes": config.num_modes,
            "seed": config.seed,
            "preparation": {
                "enabled": config.prepare_receptors,
                "keep_waters": config.keep_waters,
                "keep_heterogens": config.keep_heterogens,
            },
            "panel": [receptor.as_json() for receptor in panel],
        }
    )


class _TargetPose:
    """The pose a molecule was accepted on, and the score that accepted it."""

    __slots__ = ("molblock", "pose_rank", "score")

    def __init__(self, *, score: float, pose_rank: int, molblock: str | None) -> None:
        self.score = score
        self.pose_rank = pose_rank
        self.molblock = molblock


def _shard_target_poses(
    task: ShardTask,
    parent_ids: list[str],
    *,
    engine_id: str,
    receptor_id: str,
) -> dict[str, _TargetPose]:
    """Join this batch's molecules to the pose their docking gate accepted.

    Best by score, the same way the gate chose, rather than by ``pose_rank == 0``
    -- an ordering inherited from another program's output format is not the
    thing the decision was made on.  Direction is checked in the parent, so the
    comparison here is unconditionally "more negative wins".
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
            "pose_molblock",
        ],
    )
    best: dict[str, _TargetPose] = {}
    for row in table.to_pylist():
        if str(row["engine_id"]) != engine_id or str(row["receptor_id"]) != receptor_id:
            continue
        parent_id = str(row["parent_id"])
        score = float(row["score"])
        if parent_id in best and score >= best[parent_id].score:
            continue
        molblock = row["pose_molblock"]
        best[parent_id] = _TargetPose(
            score=score,
            pose_rank=int(row["pose_rank"]),
            molblock=molblock if isinstance(molblock, str) and molblock else None,
        )
    return best


def _dock_panel_batch(
    ligands: list[tuple[str, str]],
    *,
    config: SpecificityPanelConfig,
    executable: Path,
    receptor: _PreparedReceptor,
) -> dict[str, float]:
    """Dock a whole batch against one panel receptor and read its best score back.

    One number per molecule, because a panel receptor contributes a background
    level rather than a pose: whatever it does with the ligand, only how well it
    did enters the delta.  A ligand the engine returned nothing for is simply
    absent from the mapping, which is how a partial panel is detected upstream.
    """

    scores: dict[str, float] = {}
    timeout = math.ceil(len(ligands) * config.timeout_per_molecule_seconds)
    with tempfile.TemporaryDirectory(
        prefix=f"molcascade-panel-{receptor.name}-",
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
                str(receptor.pdbqt),
                "--ligand_index",
                str(index_file),
                "--center_x",
                repr(receptor.box["center_x"]),
                "--center_y",
                repr(receptor.box["center_y"]),
                "--center_z",
                repr(receptor.box["center_z"]),
                "--size_x",
                repr(receptor.box["size_x"]),
                "--size_y",
                repr(receptor.box["size_y"]),
                "--size_z",
                repr(receptor.box["size_z"]),
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
            code="SPECIFICITY_PANEL_RUN_FAILED",
            hint=_EXECUTABLE_HINT,
            context={
                "ligand_count": len(ligands),
                "receptor_name": receptor.name,
                "receptor_id": receptor.digest,
            },
        )

        for parent_id, path in written:
            output = output_dir / f"{path.stem}_out.pdbqt"
            if not output.is_file():
                continue
            poses = parse_vina_poses(output.read_text(encoding="utf-8", errors="replace"))
            if poses:
                scores[parent_id] = min(poses)
    return scores


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
    metric_id: str,
    method_id: str,
    value: float | None,
    units: str,
    status: str,
    status_detail: str | None,
    source: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "parent_id": parent_id,
        "metric_id": metric_id,
        "method_id": method_id,
        "value": value,
        "units": units,
        "direction": "LOWER_BETTER",
        "status": status,
        "status_detail": status_detail,
        "source_json": None if source is None else _source_json(source),
    }


def _unmeasured_rows(
    parent_id: str,
    *,
    methods: dict[str, str],
    status: str,
    status_detail: str,
) -> list[dict[str, Any]]:
    """Both metrics say the same thing when the molecule could not be measured."""

    return [
        _metric_row(
            parent_id=parent_id,
            metric_id=_DELTA_METRIC_ID,
            method_id=methods[_DELTA_METRIC_ID],
            value=None,
            units="KCAL_PER_MOL",
            status=status,
            status_detail=status_detail,
            source=None,
        ),
        _metric_row(
            parent_id=parent_id,
            metric_id=_WIN_METRIC_ID,
            method_id=methods[_WIN_METRIC_ID],
            value=None,
            units="COUNT",
            status=status,
            status_detail=status_detail,
            source=None,
        ),
    ]


def _panel_from_config(task: ShardTask) -> list[_PreparedReceptor]:
    """Re-read the panel the parent process prepared, without preparing it again."""

    resolved = task.config.get("resolved_panel")
    if not isinstance(resolved, list) or not resolved:
        raise PluginError(
            "specificity-panel shard was scheduled without a prepared panel",
            code="SPECIFICITY_PANEL_UNRESOLVED",
            context={"shard_index": task.index},
        )
    panel: list[_PreparedReceptor] = []
    for entry in resolved:
        assert isinstance(entry, dict)
        panel.append(
            _PreparedReceptor(
                name=str(entry["name"]),
                pdbqt=Path(str(entry["pdbqt_path"])),
                digest=str(entry["receptor_id"]),
                box={field: float(entry[field]) for field in _BOX_FIELDS},
            )
        )
    return panel


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Re-dock one contiguous range of the population against every panel receptor."""

    config = SpecificityPanelConfig.model_validate(
        {key: value for key, value in task.config.items() if key != "resolved_panel"}
    )
    require_gpu_lane(task.device, engine=_ENGINE)
    require_meeko(engine=_ENGINE)
    executable = resolved_executable(config.executable, engine=_ENGINE, hint=_EXECUTABLE_HINT)
    panel = _panel_from_config(task)
    engine_id = str(task.config.get("target_engine_id") or "")
    receptor_id = str(task.config.get("target_receptor_id") or "")
    if not engine_id or not receptor_id:
        raise PluginError(
            "specificity-panel shard was scheduled without a resolved target identity",
            code="SPECIFICITY_PANEL_UNRESOLVED",
            context={"shard_index": task.index},
        )
    methods = {
        _DELTA_METRIC_ID: _method_id(
            config,
            metric_id=_DELTA_METRIC_ID,
            units="KCAL_PER_MOL",
            engine_id=engine_id,
            receptor_id=receptor_id,
            panel=panel,
        ),
        _WIN_METRIC_ID: _method_id(
            config,
            metric_id=_WIN_METRIC_ID,
            units="COUNT",
            engine_id=engine_id,
            receptor_id=receptor_id,
            panel=panel,
        ),
    }

    input_count = 0
    measured_count = 0
    partial_count = 0
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
            targets = _shard_target_poses(
                task,
                parent_ids,
                engine_id=engine_id,
                receptor_id=receptor_id,
            )

            rows: list[dict[str, Any]] = []
            ligands: list[tuple[str, str]] = []
            for parent_id in parent_ids:
                target = targets.get(parent_id)
                if target is None:
                    unscored_count += 1
                    rows.extend(
                        _unmeasured_rows(
                            parent_id,
                            methods=methods,
                            status="NOT_APPLICABLE",
                            status_detail=(
                                f"no {engine_id} score against {receptor_id} for this parent"
                            ),
                        )
                    )
                    continue
                if target.molblock is None:
                    missing_geometry_count += 1
                    rows.extend(
                        _unmeasured_rows(
                            parent_id,
                            methods=methods,
                            status="NOT_APPLICABLE",
                            status_detail=(
                                "the accepted pose was stored without geometry, so "
                                f"there is nothing to re-dock (pose_rank {target.pose_rank})"
                            ),
                        )
                    )
                    continue
                pdbqt = ligand_pdbqt(target.molblock)
                if pdbqt is None:
                    failed_count += 1
                    rows.extend(
                        _unmeasured_rows(
                            parent_id,
                            methods=methods,
                            status="BACKEND_FAILED",
                            status_detail="meeko could not convert the accepted pose to PDBQT",
                        )
                    )
                    continue
                ligands.append((parent_id, pdbqt))

            # One engine call per receptor per batch: the panel is a small number
            # of receptors and a large number of ligands, and Uni-Dock is only
            # fast in bulk.
            panel_scores: dict[str, dict[str, float]] = {}
            for receptor in panel:
                if not ligands:
                    break
                panel_scores[receptor.name] = _dock_panel_batch(
                    ligands,
                    config=config,
                    executable=executable,
                    receptor=receptor,
                )

            for parent_id, _pdbqt in ligands:
                target = targets[parent_id]
                observed = {
                    name: scores[parent_id]
                    for name, scores in panel_scores.items()
                    if parent_id in scores
                }
                if not observed:
                    failed_count += 1
                    rows.extend(
                        _unmeasured_rows(
                            parent_id,
                            methods=methods,
                            status="BACKEND_FAILED",
                            status_detail=("no panel receptor returned a score for this molecule"),
                        )
                    )
                    continue
                background = sum(observed.values()) / len(observed)
                delta = target.score - background
                # "At least as strong" rather than "stronger": a decoy that ties
                # the target has not been beaten by it, and a strict comparison
                # would report the tie as a win for the target.
                wins = sum(1 for score in observed.values() if score <= target.score)
                complete = len(observed) == len(panel)
                if complete:
                    measured_count += 1
                    status, detail = "OK", None
                else:
                    # The value is real, but it is a delta against a smaller panel
                    # than the one ``method_id`` names.  Kept, and flagged, so a
                    # threshold does not read it as the panel it claims to be.
                    partial_count += 1
                    status = "OUT_OF_DOMAIN"
                    detail = (
                        f"only {len(observed)} of {len(panel)} panel receptors scored "
                        "this molecule, so the background is an average over a "
                        "different panel than the method declares"
                    )
                source = {
                    "target_score": target.score,
                    "target_pose_rank": target.pose_rank,
                    "panel_mean": background,
                    "panel_scores": {name: observed[name] for name in sorted(observed)},
                    "panel_receptor_ids": {
                        receptor.name: receptor.digest
                        for receptor in panel
                        if receptor.name in observed
                    },
                    "score_kind": _SCORE_KINDS[config.scoring],
                }
                rows.append(
                    _metric_row(
                        parent_id=parent_id,
                        metric_id=_DELTA_METRIC_ID,
                        method_id=methods[_DELTA_METRIC_ID],
                        value=delta,
                        units="KCAL_PER_MOL",
                        status=status,
                        status_detail=detail,
                        source=source,
                    )
                )
                rows.append(
                    _metric_row(
                        parent_id=parent_id,
                        metric_id=_WIN_METRIC_ID,
                        method_id=methods[_WIN_METRIC_ID],
                        value=float(wins),
                        units="COUNT",
                        status=status,
                        status_detail=detail,
                        source=source,
                    )
                )
            parent_writer.write_batch(batch)
            metric_writer.write_table(pa.Table.from_pylist(rows, schema=DERIVED_METRIC_V1.schema))
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": input_count, "derived_metrics": 2 * input_count},
        metadata={
            "measured_count": measured_count,
            "out_of_domain_count": partial_count,
            "backend_failed_count": failed_count,
            "unscored_count": unscored_count,
            "missing_geometry_count": missing_geometry_count,
        },
    )


class SpecificityPanelPlugin:
    """Score each molecule against a panel of unrelated pockets and report the gap."""

    descriptor = PluginDescriptor(
        id="derived.specificity_panel",
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
        display_name="Cross-pocket specificity panel",
        description=(
            "Re-docks the accepted pose against operator-supplied decoy pockets "
            "and records how much of the target score is specific to the target; "
            "evidence only, thresholds are a separate gate."
        ),
    )
    config_model = SpecificityPanelConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        parent_input, evidence_input = _classify_inputs(dict(request.inputs))
        require_meeko(engine=_ENGINE)

        enforce_population_cap(
            population_size(discover_contract_files(parent_input, PARENT_V1)),
            limit=config.max_molecules,
            engine=f"{_ENGINE} specificity panel",
        )
        dataset = _evidence_dataset(evidence_input)
        engine_id = _resolve_identity(
            dataset,
            configured=config.target_engine_id,
            column="engine_id",
        )
        receptor_id = _resolve_identity(
            dataset,
            configured=config.target_receptor_id,
            column="receptor_id",
        )
        stored_pose_count = _require_comparable_evidence(
            dataset,
            config,
            engine_id=engine_id,
            receptor_id=receptor_id,
        )
        # Prepared once, here, so a receptor meeko refuses stops the run before a
        # single molecule is docked rather than inside every worker at once.
        panel = _prepare_panel(config, workspace=context.staging_root / _PANEL_CACHE)
        methods = {
            metric_id: _method_id(
                config,
                metric_id=metric_id,
                units=units,
                engine_id=engine_id,
                receptor_id=receptor_id,
                panel=panel,
            )
            for metric_id, units in ((_DELTA_METRIC_ID, "KCAL_PER_MOL"), (_WIN_METRIC_ID, "COUNT"))
        }

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
                "target_engine_id": engine_id,
                "target_receptor_id": receptor_id,
                "resolved_panel": [
                    {**receptor.as_json(), "pdbqt_path": str(receptor.pdbqt)} for receptor in panel
                ],
            },
            shard_rows=_SHARD_ROWS,
            side_inputs={"poses": (evidence_input, DOCKING_SCORE_V1)},
        )
        if result.rows_in == 0:
            raise PluginError(
                "specificity-panel parent input contains no rows",
                code="SPECIFICITY_PANEL_EMPTY_PARENT_INPUT",
            )
        input_count = result.rows_in
        unscored_count = result.total("unscored_count")
        if unscored_count == input_count:
            raise PluginError(
                "no parent in this population has a score from the selected engine",
                code="SPECIFICITY_PANEL_NO_MATCHING_EVIDENCE",
                hint=(
                    "Pin the criterion's evidence_from to the docking stage that "
                    "scored this population, or clear target_engine_id and "
                    "target_receptor_id."
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
                    {
                        "row_count": 2 * input_count,
                        "method_id": methods[_DELTA_METRIC_ID],
                    },
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": input_count,
                "metric_ids": [_DELTA_METRIC_ID, _WIN_METRIC_ID],
                "delta_method_id": methods[_DELTA_METRIC_ID],
                "win_count_method_id": methods[_WIN_METRIC_ID],
                "panel_size": len(panel),
                "panel_receptor_ids": {receptor.name: receptor.digest for receptor in panel},
                "panel_engine_id": _ENGINE_ID,
                "target_engine_id": engine_id,
                "target_receptor_id": receptor_id,
                "score_kind": _SCORE_KINDS[config.scoring],
                "pose_policy": "BEST_POSE",
                "stored_pose_count": stored_pose_count,
                "scoring": config.scoring,
                "search_mode": config.search_mode,
                "num_modes": config.num_modes,
                "seed": config.seed,
                "measured_count": result.total("measured_count"),
                "out_of_domain_count": result.total("out_of_domain_count"),
                "backend_failed_count": result.total("backend_failed_count"),
                "unscored_count": unscored_count,
                "missing_geometry_count": result.total("missing_geometry_count"),
                "derived_not_measured": False,
                **result.response_metadata(),
            },
        )


__all__ = ["PanelReceptorConfig", "SpecificityPanelConfig", "SpecificityPanelPlugin"]
