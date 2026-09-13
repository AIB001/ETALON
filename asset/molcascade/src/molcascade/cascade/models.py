"""Tier-first cascade authoring models.

A :class:`~molcascade.config.models.PipelineConfig` is a flat, topologically
ordered stage list.  That shape is correct for execution and terrible for
authoring: a screening cascade is naturally a funnel of *tiers*, each tier
holding several *criteria* which are combined either in series or in parallel,
and each criterion carrying its own threshold.

This module makes the tier a first-class configuration object.  The cascade is
the file a scientist edits (in the browser or by hand); the pipeline is a
derived artifact produced by :mod:`molcascade.cascade.lower`.  Nothing in the
runtime, artifact, cache, or provenance layer changes, because lowering emits
exactly the same ordered stage list those layers already validate.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from molcascade.config.models import StrictFrozenModel, _freeze_json_mapping

CASCADE_SCHEMA_VERSION = 2

#: Molecules handed to the next stage -- docking, or a finer screen such as
#: PRISM.  The number is set by what receives it, not by what produces it: a
#: shortlist this size is one overnight docking campaign on a single modern GPU,
#: which is the unit of work the handoff is designed around.  Going lower throws
#: away recall for no gain in the downstream schedule; going much higher stops
#: fitting in the night.
#:
#: It lives here, at the bottom of the cascade layer, because the field default
#: and the starter cascade must not be able to disagree -- they did, at 20,000
#: and 15,000, for as long as each carried its own literal.
DEFAULT_TARGET_COUNT = 45_000

_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_PLUGIN_REF_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*@\d+\.\d+\.\d+[0-9A-Za-z.+-]*$")
#: A data contract reference, ``family/vN``.  Contract ids carry a slash, so
#: they are not identifiers and cannot reuse ``_IDENTIFIER_RE``.
_CONTRACT_REF_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,95}/v\d+$")


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            "must start with a letter and contain only letters, digits, '.', '_' or '-'"
        )
    return value


class TierMode(StrEnum):
    """How the criteria inside one tier are combined.

    ``SERIAL`` and the three parallel modes compile to materially different
    graphs, not to different labels on the same graph.  In ``SERIAL`` a
    criterion only ever sees the survivors of its predecessor; in the parallel
    modes every criterion sees the same population entering the tier and their
    complete decision streams meet at one explicit policy join.
    """

    SERIAL = "serial"
    ALL = "all"
    ANY = "any"
    AT_LEAST = "at_least"

    @property
    def is_parallel(self) -> bool:
        return self is not TierMode.SERIAL

    @property
    def join_mode(self) -> str:
        """Return the ``policy.native_decision_join`` mode for this tier mode."""

        if self is TierMode.ALL:
            return "all_required"
        if self is TierMode.ANY:
            return "any_required"
        if self is TierMode.AT_LEAST:
            return "min_pass_count"
        raise ValueError("serial tiers do not use a decision join")


class LibraryFormat(StrEnum):
    """Accepted shapes of the molecule library handed to a cascade."""

    AUTO = "auto"
    DELIMITED = "delimited"
    XLSX = "xlsx"
    SDF = "sdf"
    PARQUET = "parquet"
    MOL2_DIRECTORY = "mol2_directory"


class LibraryConfig(StrictFrozenModel):
    """Where the molecules come from and how to read them.

    ``path`` is optional on purpose.  The intended workflow is that the browser
    builder produces a reusable *screening policy* and the command line supplies
    the library for each run, so the same configuration can screen many
    generated batches without being edited.
    """

    path: str | None = Field(default=None, min_length=1, max_length=4096)
    format: LibraryFormat = LibraryFormat.AUTO
    smiles_column: str | None = Field(default=None, min_length=1, max_length=256)
    id_column: str | None = Field(default=None, min_length=1, max_length=256)
    delimiter: str | None = Field(default=None, min_length=1, max_length=4)
    has_header: bool | None = None
    sheet_name: str | None = Field(default=None, min_length=1, max_length=256)
    #: Leading rows to discard before the header, for exports that open with a
    #: title or a merged section banner.  Applies to delimited files and
    #: worksheets; the readers that have no rows to skip ignore it.
    skip_rows: int | None = Field(default=None, ge=0, le=1_048_575)
    batch_size: int = Field(default=65_536, ge=1, le=1_000_000)

    @field_validator("format", mode="before")
    @classmethod
    def _parse_format(cls, value: Any) -> Any:
        return LibraryFormat(value) if isinstance(value, str) else value

    @field_validator("path")
    @classmethod
    def _reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(character in value for character in "\r\n\x00"):
            raise ValueError("library path must not contain control characters")
        return value


class ThresholdDirection(StrEnum):
    """Which side of a numeric window keeps a molecule."""

    HIGHER_BETTER = "higher_better"
    LOWER_BETTER = "lower_better"
    WINDOW = "window"


class GateConfig(StrictFrozenModel):
    """Turn one criterion's numeric evidence into an explicit decision.

    Evidence producers (ADMET models, SA score, custom ML models) deliberately
    do not filter.  A criterion becomes a filter only when it carries a gate,
    which is where the tier's threshold lives.
    """

    backend: str = Field(min_length=1, max_length=512)
    settings: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("backend")
    @classmethod
    def _validate_backend(cls, value: str) -> str:
        if not _PLUGIN_REF_RE.fullmatch(value):
            raise ValueError("backend must be an exact 'plugin.id@major.minor.patch' reference")
        return value

    @field_validator("settings")
    @classmethod
    def _freeze_settings(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)


class CriterionConfig(StrictFrozenModel):
    """One screening criterion inside a tier.

    ``criterion`` names the *scientific question* (for example ``pains_alerts``)
    and ``backend`` names the *exact tool* answering it.  Keeping the two
    separate is what lets a user swap RDKit for ``medchem``, ``rd_filters``, or
    their own model without rebuilding the cascade.

    ``evidence_from`` maps a data contract this criterion consumes to the id of
    the stage that should supply it.  It is only needed when more than one
    earlier stage produces that contract -- two docking engines both emitting
    ``docking_score/v1``, say -- in which case lowering refuses to guess.
    Naming the source here rather than inferring it from tier order puts the
    choice in the exported plan, where a methods section can quote it.
    """

    id: str = Field(min_length=1, max_length=128)
    criterion: str | None = Field(default=None, min_length=1, max_length=128)
    backend: str = Field(min_length=1, max_length=512)
    label: str | None = Field(default=None, min_length=1, max_length=256)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    gate: GateConfig | None = None
    enabled: bool = True
    evidence_from: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("evidence_from")
    @classmethod
    def _validate_evidence_from(cls, value: dict[str, str]) -> dict[str, str]:
        for contract_id, stage_id in value.items():
            if not _CONTRACT_REF_RE.fullmatch(contract_id):
                raise ValueError(
                    f"evidence_from key {contract_id!r} must be a contract reference "
                    "such as 'docking_score/v1'"
                )
            _validate_identifier(stage_id)
        # Copied but not recursively frozen: unlike ``settings`` this is a flat
        # map of two identifier strings, read once while lowering and never
        # handed to a plugin, so there is no nested structure to protect.
        return dict(value)

    @field_validator("criterion")
    @classmethod
    def _validate_criterion(cls, value: str | None) -> str | None:
        return None if value is None else _validate_identifier(value)

    @field_validator("backend")
    @classmethod
    def _validate_backend(cls, value: str) -> str:
        if not _PLUGIN_REF_RE.fullmatch(value):
            raise ValueError("backend must be an exact 'plugin.id@major.minor.patch' reference")
        return value

    @field_validator("settings")
    @classmethod
    def _freeze_settings(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)

    @property
    def gate_stage_id(self) -> str:
        return f"{self.id}__gate"


class TierConfig(StrictFrozenModel):
    """One level of the funnel."""

    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=160)
    mode: TierMode = TierMode.SERIAL
    minimum_passes: int | None = Field(default=None, ge=1, le=1_000)
    criteria: tuple[CriterionConfig, ...] = ()
    note: str | None = Field(default=None, max_length=2048)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("mode", mode="before")
    @classmethod
    def _parse_mode(cls, value: Any) -> Any:
        return TierMode(value) if isinstance(value, str) else value

    @field_validator("criteria", mode="before")
    @classmethod
    def _accept_json_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _validate_tier(self) -> Self:
        seen: set[str] = set()
        for criterion in self.criteria:
            if criterion.id in seen:
                raise ValueError(f"duplicate criterion id {criterion.id!r} in tier {self.id!r}")
            seen.add(criterion.id)
        if self.mode is TierMode.AT_LEAST:
            if self.minimum_passes is None:
                raise ValueError(
                    f"tier {self.id!r} uses 'at_least' and must set minimum_passes"
                )
            active = sum(1 for criterion in self.criteria if criterion.enabled)
            if active and self.minimum_passes > active:
                raise ValueError(
                    f"tier {self.id!r} requires {self.minimum_passes} passing criteria "
                    f"but only {active} are enabled"
                )
        elif self.minimum_passes is not None:
            raise ValueError(
                f"tier {self.id!r} sets minimum_passes but its mode is not 'at_least'"
            )
        return self

    @property
    def active_criteria(self) -> tuple[CriterionConfig, ...]:
        return tuple(criterion for criterion in self.criteria if criterion.enabled)

    @property
    def join_stage_id(self) -> str:
        return f"{self.id}__policy"


class StepConfig(StrictFrozenModel):
    """A fixed, single-tool stage such as ingest, standardization or export."""

    id: str = Field(min_length=1, max_length=128)
    backend: str = Field(min_length=1, max_length=512)
    settings: dict[str, JsonValue] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        return _validate_identifier(value)

    @field_validator("backend")
    @classmethod
    def _validate_backend(cls, value: str) -> str:
        if not _PLUGIN_REF_RE.fullmatch(value):
            raise ValueError("backend must be an exact 'plugin.id@major.minor.patch' reference")
        return value

    @field_validator("settings")
    @classmethod
    def _freeze_settings(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)


class FinalizeConfig(StrictFrozenModel):
    """The tail of the funnel: annotate, budget-select, and export a shortlist.

    ``target_count`` is authoritative.  Lowering writes it into whichever step
    declares a ``target_count`` setting so that the shortlist size is edited in
    exactly one place.
    """

    target_count: int = Field(default=DEFAULT_TARGET_COUNT, ge=1, le=100_000_000)
    seed: int = Field(default=20_260_823, ge=0, le=2**63 - 1)
    steps: tuple[StepConfig, ...] = ()

    @field_validator("steps", mode="before")
    @classmethod
    def _accept_json_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate finalize step id {step.id!r}")
            seen.add(step.id)
        return self


class BoxConfig(StrictFrozenModel):
    """The search volume, in angstrom, for the engines that search one.

    All six numbers are required.  A default edge length would be a guess about
    the size of someone else's binding site, and a box that is too small silently
    truncates the search while a box that is too large turns a pocket study into
    a blind-docking run -- neither of which announces itself in the scores.
    """

    center_x: float = Field(allow_inf_nan=False, ge=-10_000.0, le=10_000.0)
    center_y: float = Field(allow_inf_nan=False, ge=-10_000.0, le=10_000.0)
    center_z: float = Field(allow_inf_nan=False, ge=-10_000.0, le=10_000.0)
    size_x: float = Field(allow_inf_nan=False, gt=0.0, le=200.0)
    size_y: float = Field(allow_inf_nan=False, gt=0.0, le=200.0)
    size_z: float = Field(allow_inf_nan=False, gt=0.0, le=200.0)

    @property
    def settings(self) -> dict[str, float]:
        """The box as the engine configs spell it, in a fixed order."""

        return {
            "center_x": self.center_x,
            "center_y": self.center_y,
            "center_z": self.center_z,
            "size_x": self.size_x,
            "size_y": self.size_y,
            "size_z": self.size_z,
        }


class ReceptorPreparation(StrictFrozenModel):
    """What to do to the operator's PDB before any engine reads it.

    A crystal structure is a model of resolved density, not of a protein.  Long
    side chains routinely have none, so the deposition stops at CB -- and every
    consumer of that file then behaves differently without saying so.  meeko
    refuses to build a receptor at all; GNINA and KarmaDock dock happily against
    a protein with holes in its surface; PoseBusters measures clashes against
    the same holes.  The one documented way past meeko's refusal was its
    ``--allow_bad_res``, which does not relax the check but deletes the residue,
    backbone included: on STK17B that silently removed twelve of 278.

    So the receptor is repaired rather than truncated, and the defaults here are
    the recipe that makes the four consumers agree.  What each switch turns off
    is a scientific claim, which is why there are only three of them and why
    none of them is about adding atoms that were never measured.
    """

    #: Off is the escape hatch, not a second opinion: the structure goes to
    #: every engine exactly as supplied.  Worth using when the PDB has already
    #: been prepared by hand -- and worth knowing that meeko will then refuse
    #: any structure with an incomplete residue in it.
    enabled: bool = True

    #: Crystallographic waters are removed by default because a water modelled
    #: in the site is an atom the docking box has to route around, and no engine
    #: here treats it as displaceable.  Keep them when a specific bridging water
    #: is part of the hypothesis -- at which point it should be argued for
    #: rather than inherited from the deposition.
    keep_waters: bool = False

    #: Co-crystallised ligands, sugars, buffers and cryoprotectants are removed
    #: for the same reason.  Metals are never removed by this switch: a
    #: catalytic zinc deleted as a "heterogen" changes every score in the tier
    #: and shows up in none of them.
    keep_heterogens: bool = False


class TargetConfig(StrictFrozenModel):
    """The protein a cascade's docking tier is pointed at.

    One target per cascade, not one per engine.  Three engines each carrying
    their own receptor could be handed three different proteins -- or, far more
    likely, three preparations of the same one -- and the tier's consensus would
    then be a comparison between numbers that are not about the same thing.
    Lowering writes these fields into every docking stage that declares them, so
    the disagreement is structurally impossible rather than merely discouraged.

    The site arrives in one of three forms: an explicit ``box``, a
    ``reference_ligand_path`` whose atoms define one, or a ``pocket_path``
    holding the residues that line it.  At least one, and never both files --
    two structures each claiming to locate the site are two answers to one
    question, and picking a winner silently would be deciding the experiment.

    A box *alongside* one of those files is the ordinary resolved state rather
    than a contradiction: ``resolve_target`` derives the box from the file so the
    engines that read six numbers and the engine that reads a ligand search the
    same volume.  When both were authored by hand instead,
    :func:`~molcascade.cascade.target.resolve_target` checks that they agree
    before either reaches a stage.

    ``receptor_sha256`` is what binds a docking score to bytes rather than to a
    path.  Present, it is verified and a mismatch stops the run; absent, this
    run establishes it.  It is checked against the file the *operator* named and
    then re-stated for the prepared structure, so the pin means what the
    operator meant by it and the worker still verifies the bytes it docks.
    """

    name: str = Field(min_length=1, max_length=256)

    #: The structure the *user* supplied, in PDB.  It is read and hashed here,
    #: and repaired -- not rewritten -- before any engine sees it: solvent and
    #: co-crystallised matter are removed and side chains the crystallographer
    #: could not resolve are rebuilt, each change named in the target notes.
    #: What is *not* guessed stays not guessed: unresolved loops are reported
    #: and left absent, protonation and tautomers are the engines' business,
    #: and metals are kept.  See :class:`ReceptorPreparation`.
    receptor_path: str = Field(min_length=1, max_length=4096)

    receptor_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    box: BoxConfig | None = None

    #: A ligand bound in the site, in mol2/sdf/mol.  Its atoms' extent plus a
    #: padding becomes the box, the ``--autobox_ligand`` convention.
    reference_ligand_path: str | None = Field(default=None, min_length=1, max_length=4096)

    #: The residues lining the site, as a PDB.  Their extent becomes the box.
    pocket_path: str | None = Field(default=None, min_length=1, max_length=4096)

    #: Uni-Dock reads PDBQT, and MolCascade derives one with meeko.  Set this
    #: when that preparation cannot cope with the structure -- odd residues and
    #: unusual protonation are where it fails -- rather than editing the PDB.
    receptor_pdbqt_path: str | None = Field(default=None, min_length=1, max_length=4096)

    #: KarmaDock reads an already-selected pocket.  Set this when the geometric
    #: selection picks up the wrong chain or misses a residue that matters.
    pocket_pdb_path: str | None = Field(default=None, min_length=1, max_length=4096)

    #: How the receptor is cleaned and repaired before anything derives from it.
    #: Not per-engine, for the same reason the target is not per-engine: three
    #: preparations of one protein make the tier's consensus a comparison
    #: between numbers that are not about the same thing.
    preparation: ReceptorPreparation = ReceptorPreparation()

    @field_validator(
        "receptor_path",
        "reference_ligand_path",
        "pocket_path",
        "receptor_pdbqt_path",
        "pocket_pdb_path",
    )
    @classmethod
    def _paths_are_absolute(cls, value: str | None) -> str | None:
        """Refuse a path that means a different file from a different directory.

        A cascade is re-run from wherever someone happens to be standing, and a
        receptor resolved against the working directory would make the run's
        identity depend on it.  The CLI expands what the user typed before it
        gets here, so this only ever fires on a hand-written file.
        """

        if value is None:
            return None
        candidate = Path(value.strip()).expanduser()
        if not value.strip() or not candidate.is_absolute():
            raise ValueError("must be an absolute path")
        return str(candidate)

    @model_validator(mode="after")
    def _site_is_locatable_and_unambiguous(self) -> Self:
        if self.box is None and self.reference_ligand_path is None and self.pocket_path is None:
            raise ValueError(
                f"target {self.name!r} names a receptor but no binding site; give one of "
                "'box', 'reference_ligand_path' or 'pocket_path'"
            )
        if self.reference_ligand_path is not None and self.pocket_path is not None:
            raise ValueError(
                f"target {self.name!r} locates the binding site with two different "
                "structures ('reference_ligand_path' and 'pocket_path'); keep the one "
                "that is actually in the site"
            )
        return self


class CascadeConfig(StrictFrozenModel):
    """Version 2 tier-first screening cascade.

    ``kind`` discriminates this file from a legacy flat pipeline configuration
    so a single loader can accept either without guessing.
    """

    schema_version: Literal[2] = CASCADE_SCHEMA_VERSION
    kind: Literal["cascade"] = "cascade"
    name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    library: LibraryConfig = Field(default_factory=LibraryConfig)
    ingest: StepConfig
    standardize: StepConfig | None = None
    tiers: tuple[TierConfig, ...] = ()
    finalize: FinalizeConfig = Field(default_factory=FinalizeConfig)

    #: The protein the docking tier is pointed at, when there is one.  Optional
    #: because a cascade is authored long before a target is chosen, and because
    #: most cascades have no docking tier at all -- the requirement is enforced
    #: at run time, against the stages that actually need it, rather than by
    #: making every cascade carry a receptor it will never open.
    target: TargetConfig | None = None

    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("tiers", mode="before")
    @classmethod
    def _accept_json_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("metadata")
    @classmethod
    def _freeze_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _freeze_json_mapping(value)

    @model_validator(mode="after")
    def _validate_identity_space(self) -> Self:
        reserved = {self.ingest.id}
        if self.standardize is not None:
            if self.standardize.id in reserved:
                raise ValueError("standardize step id must differ from the ingest step id")
            reserved.add(self.standardize.id)
        tier_ids: set[str] = set()
        for tier in self.tiers:
            if tier.id in tier_ids:
                raise ValueError(f"duplicate tier id {tier.id!r}")
            tier_ids.add(tier.id)
            for stage_id in (tier.join_stage_id,):
                if stage_id in reserved:
                    raise ValueError(f"tier {tier.id!r} collides with an existing stage id")
                reserved.add(stage_id)
            for criterion in tier.criteria:
                for stage_id in (criterion.id, criterion.gate_stage_id):
                    if stage_id in reserved:
                        raise ValueError(
                            f"criterion {criterion.id!r} in tier {tier.id!r} collides with "
                            "another stage id"
                        )
                    reserved.add(stage_id)
        for step in self.finalize.steps:
            if step.id in reserved:
                raise ValueError(f"finalize step {step.id!r} collides with another stage id")
            reserved.add(step.id)
        if not any(tier.active_criteria for tier in self.tiers if tier.enabled) and not any(
            step.enabled for step in self.finalize.steps
        ):
            raise ValueError(
                "a cascade must contain at least one enabled criterion or finalize step"
            )
        return self

    @property
    def active_tiers(self) -> tuple[TierConfig, ...]:
        return tuple(tier for tier in self.tiers if tier.enabled and tier.active_criteria)


__all__ = [
    "CASCADE_SCHEMA_VERSION",
    "DEFAULT_TARGET_COUNT",
    "BoxConfig",
    "CascadeConfig",
    "CriterionConfig",
    "FinalizeConfig",
    "GateConfig",
    "LibraryConfig",
    "LibraryFormat",
    "ReceptorPreparation",
    "StepConfig",
    "TargetConfig",
    "ThresholdDirection",
    "TierConfig",
    "TierMode",
]
