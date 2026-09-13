"""The Walters ``rd_filters`` alert collection, applied set by set.

MolCascade already ships RDKit's own ``FilterCatalog`` (PAINS/BRENK/NIH/ZINC)
and ``medchem``'s curated lists.  This adapter adds a third, and the reason is
not redundancy: the 1,251 SMARTS in ``alert_collection.csv`` are the specific
union that a large body of published triage work actually used, assembled by
Pat Walters from eight separate sources -- BMS (Bruns & Watson), Dundee,
Glaxo, Inpharmatica, LINT, MLSMR, PAINS (Baell & Holloway) and SureChEMBL.
Reproducing a filtering step from a paper that says "we used rd_filters" means
having *these* rules, not an equivalent-looking set.

Two design choices differ from upstream on purpose.

Upstream returns the first matching rule and stops.  That is right for a
command-line triage script and wrong for a cascade, where the interesting
question is usually *which* liability a molecule has, so this adapter reports
every rule set that matched.

Upstream treats every enabled set as a rejection.  Here each set carries its
own action, because the sets do not mean the same thing.  A Glaxo reactive
alkyl halide is a statement about chemistry that will not survive an assay
plate; a PAINS match is a statement about a *hypothesis* of assay interference
that its own author has repeatedly warned is over-applied.  Collapsing both
into "reject" throws away the distinction the sets were built to make.

Which sets reject was settled by measurement, not by taste.  Run against a
panel of twenty-four approved oral drugs spanning chemotypes, the eight sets
flag wildly different fractions of them::

    Glaxo   4%    Inpharmatica 13%    Dundee  33%
    BMS     8%    LINT         13%    MLSMR   50%
    PAINS   0%    SureChEMBL   13%

A default that rejected on Dundee would delete aspirin, paracetamol, warfarin,
gefitinib and tamoxifen; one that rejected on MLSMR would delete half the
panel.  Glaxo and BMS are the two sets whose hits on that panel are things a
screening deck should genuinely exclude -- a beta-lactam and a polyiodinated
aryl ether -- so those two reject and the remaining six warn.  ``PAINS``
flagging none of the panel is the expected result and not a reason to promote
it: the set targets assay-interference chemotypes, which is a different
question from whether a molecule is drug-like.  ``tests/plugins`` keeps the
panel as a regression test, so a future change to these defaults has to face
the same evidence.

The file itself is data.  It is read with :mod:`csv` and compiled with RDKit's
SMARTS parser; nothing in it is executed, and the vendored ``rd_filters.py``
next to it is never imported.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import stat
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.assets import is_asset_reference, resolve_reference
from molcascade.chemistry.alerts import parse_for_alert_matching
from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import AssetError, PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")

#: The canonical location once ``molcascade assets fetch rd_filters`` has run.
DEFAULT_ALERTS_REFERENCE = "asset:rd_filters/data/alert_collection.csv"

#: The shipped file is 117 kB.  The ceiling is a guard against being pointed at
#: something that is not an alert table at all, not a capability limit.
_MAX_ALERT_FILE_BYTES = 32 * 1024 * 1024
_REQUIRED_COLUMNS = ("rule_id", "rule_set_name", "description", "smarts", "max")

#: Upstream's eight sets.  Named here so a configuration that misspells one is
#: rejected at validation time rather than silently filtering nothing.
RULE_SETS: tuple[str, ...] = (
    "BMS",
    "Dundee",
    "Glaxo",
    "Inpharmatica",
    "LINT",
    "MLSMR",
    "PAINS",
    "SureChEMBL",
)

#: A rule set name maps to a config field by lowercasing it, which keeps the
#: configuration readable (``pains_action``) without hard-coding the pairing
#: twice.
_FIELD_FOR_SET = {name: f"{name.lower()}_action" for name in RULE_SETS}


class AlertAction(StrEnum):
    """What a match in one rule set does to the molecule."""

    IGNORE = "ignore"
    WARN = "warn"
    REJECT = "reject"


class RdFiltersAlertConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    decision_buffer_size: int = Field(default=50_000, ge=1, le=1_000_000)

    #: ``asset:rd_filters/data/alert_collection.csv`` or an absolute path to a
    #: copy of it.  An asset reference is verified against its pinned digest
    #: before this stage ever opens the file.
    alerts_path: str = Field(default=DEFAULT_ALERTS_REFERENCE, min_length=1, max_length=4096)

    #: Optional pin for a file given by absolute path.  Asset references carry
    #: their own digest, so this is only needed for a private alert table.
    expected_alerts_sha256: str | None = Field(default=None, min_length=64, max_length=64)

    # Reactive chemistry, and only reactive chemistry, deletes a molecule.  The
    # two sets below flag 4% and 8% of a panel of approved oral drugs, and the
    # molecules they flag there -- a beta-lactam, a polyiodinated aryl ether --
    # are ones a screening deck should genuinely exclude.  See the module
    # docstring for why the other six sets warn instead.
    glaxo_action: AlertAction = AlertAction.REJECT
    bms_action: AlertAction = AlertAction.REJECT
    # Interference and screening-deck hygiene: real signal, weaker claim, heavy
    # overlap with each other, and high false-positive rates on marketed drugs.
    # Flag, do not delete.
    dundee_action: AlertAction = AlertAction.WARN
    pains_action: AlertAction = AlertAction.WARN
    surechembl_action: AlertAction = AlertAction.WARN
    inpharmatica_action: AlertAction = AlertAction.WARN
    lint_action: AlertAction = AlertAction.WARN
    mlsmr_action: AlertAction = AlertAction.WARN

    @field_validator(
        "glaxo_action",
        "bms_action",
        "dundee_action",
        "pains_action",
        "surechembl_action",
        "inpharmatica_action",
        "lint_action",
        "mlsmr_action",
        mode="before",
    )
    @classmethod
    def _parse_action(cls, value: Any) -> Any:
        return AlertAction(value) if isinstance(value, str) else value

    @field_validator("expected_alerts_sha256")
    @classmethod
    def _hash_is_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.strip().lower()
        if len(lowered) != 64 or any(character not in "0123456789abcdef" for character in lowered):
            raise ValueError("expected_alerts_sha256 must be 64 lowercase hex characters")
        return lowered

    def actions(self) -> dict[str, AlertAction]:
        return {name: getattr(self, _FIELD_FOR_SET[name]) for name in RULE_SETS}


def _validated_config(request: StageRequest) -> RdFiltersAlertConfig:
    try:
        return RdFiltersAlertConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid rd_filters alert configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _read_alert_bytes(configured: str, expected_sha256: str | None) -> tuple[Path, bytes, str]:
    """Read the alert table as a regular file and hash exactly those bytes."""

    if is_asset_reference(configured):
        try:
            path = resolve_reference(configured)
        except AssetError as error:
            raise PluginError(
                str(error),
                code="RD_FILTERS_ALERTS_PATH_INVALID",
                hint=error.hint or "Run 'molcascade assets fetch rd_filters'.",
                context={"alerts_path": configured},
            ) from error
    else:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise PluginError(
                "alerts_path must be an absolute path or an 'asset:' reference",
                code="RD_FILTERS_ALERTS_PATH_INVALID",
                hint=(
                    "Run 'molcascade assets fetch rd_filters' and use "
                    f"'{DEFAULT_ALERTS_REFERENCE}', or give the full path to your own "
                    "alert_collection.csv."
                ),
                context={"alerts_path": configured},
            )

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PluginError(
            "rd_filters alert file could not be opened as a regular file",
            code="RD_FILTERS_ALERTS_UNREADABLE",
            hint=(
                "MolCascade never downloads during a run. Fetch the table with "
                "'molcascade assets fetch rd_filters' first."
            ),
            context={"alerts_path": str(path), "error_type": type(error).__name__},
        ) from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise PluginError(
                "alerts_path is not a regular file",
                code="RD_FILTERS_ALERTS_UNREADABLE",
                context={"alerts_path": str(path)},
            )
        if info.st_size > _MAX_ALERT_FILE_BYTES:
            raise PluginError(
                "rd_filters alert file is larger than this adapter will read",
                code="RD_FILTERS_ALERTS_TOO_LARGE",
                context={"size_bytes": info.st_size, "limit_bytes": _MAX_ALERT_FILE_BYTES},
            )
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise PluginError(
            "rd_filters alert file does not match its pinned digest",
            code="RD_FILTERS_ALERTS_DIGEST_MISMATCH",
            context={
                "alerts_path": str(path),
                "expected_sha256": expected_sha256,
                "observed_sha256": digest,
            },
        )
    return path, payload, digest


@dataclass(frozen=True, slots=True)
class _Rule:
    """One row of the alert table, after validation."""

    rule_set: str
    description: str
    smarts: str
    maximum: int


def _parse_rules(payload: bytes) -> tuple[dict[str, _Rule], dict[str, int]]:
    """Turn the CSV into ``rule_id -> _Rule`` plus a per-set rule count.

    A rule whose SMARTS RDKit cannot compile is a hard error rather than a
    skipped line.  Upstream prints a warning and carries on, which is fine for
    an interactive script; in a screening run it would mean the number of rules
    applied depended silently on the RDKit build, and two runs pinned to the
    same alert digest could then disagree about which molecules survived.
    """

    from rdkit import Chem

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise PluginError(
            "rd_filters alert file is not valid UTF-8",
            code="RD_FILTERS_ALERTS_UNPARSEABLE",
        ) from error

    reader = csv.DictReader(io.StringIO(text))
    missing = [column for column in _REQUIRED_COLUMNS if column not in (reader.fieldnames or ())]
    if missing:
        raise PluginError(
            "rd_filters alert file is missing required columns: " + ", ".join(missing),
            code="RD_FILTERS_ALERTS_UNPARSEABLE",
            context={"columns": list(reader.fieldnames or ())},
        )

    rules: dict[str, _Rule] = {}
    sizes: dict[str, int] = {}
    for line_number, row in enumerate(reader, start=2):
        # Upstream drops blank lines with ``dropna``; the same tolerance here.
        if not any((row.get(column) or "").strip() for column in _REQUIRED_COLUMNS):
            continue
        rule_id = (row.get("rule_id") or "").strip()
        smarts = (row.get("smarts") or "").strip()
        rule_set = (row.get("rule_set_name") or "").strip()
        description = (row.get("description") or "").strip()
        if not rule_id or not smarts or not rule_set:
            raise PluginError(
                f"rd_filters alert file has an incomplete rule on line {line_number}",
                code="RD_FILTERS_ALERTS_UNPARSEABLE",
                context={"line": line_number},
            )
        if rule_id in rules:
            raise PluginError(
                f"rd_filters alert file repeats rule_id {rule_id!r}",
                code="RD_FILTERS_ALERTS_UNPARSEABLE",
                context={"rule_id": rule_id, "line": line_number},
            )
        try:
            maximum = int((row.get("max") or "0").strip() or "0")
        except ValueError as error:
            raise PluginError(
                f"rd_filters alert file has a non-integer 'max' on line {line_number}",
                code="RD_FILTERS_ALERTS_UNPARSEABLE",
                context={"line": line_number},
            ) from error
        if maximum < 0:
            raise PluginError(
                f"rd_filters alert file has a negative 'max' on line {line_number}",
                code="RD_FILTERS_ALERTS_UNPARSEABLE",
                context={"line": line_number},
            )
        if Chem.MolFromSmarts(smarts) is None:
            raise PluginError(
                f"rd_filters rule {rule_id} has a SMARTS this RDKit cannot compile",
                code="RD_FILTERS_ALERTS_SMARTS_INVALID",
                hint="The vendored table compiles cleanly; check for local edits.",
                context={"rule_id": rule_id, "rule_set": rule_set},
            )
        rules[rule_id] = _Rule(rule_set, description, smarts, maximum)
        sizes[rule_set] = sizes.get(rule_set, 0) + 1

    if not rules:
        raise PluginError(
            "rd_filters alert file contains no rules",
            code="RD_FILTERS_ALERTS_UNPARSEABLE",
        )
    return rules, sizes


def _compile_catalog(rules: dict[str, _Rule], enabled: frozenset[str]) -> Any:
    """Compile the enabled rules into one RDKit ``FilterCatalog``.

    One catalog rather than one per set: ``GetMatches`` is a single C++ call
    whose cost is dominated by the substructure search, and each entry is named
    for its rule id, so which set matched is recovered without a second pass
    over the molecule.

    Upstream fires a rule when ``len(GetSubstructMatches(patt)) > max``.  A
    ``SmartsMatcher`` with ``minCount = max + 1`` is exactly that test, applied
    inside RDKit rather than in Python.
    """

    from rdkit.Chem import FilterCatalog

    catalog = FilterCatalog.FilterCatalog()
    for rule_id in sorted(rules):
        rule = rules[rule_id]
        if rule.rule_set not in enabled:
            continue
        matcher = FilterCatalog.SmartsMatcher(rule_id, rule.smarts, rule.maximum + 1)
        catalog.AddEntry(FilterCatalog.FilterCatalogEntry(rule_id, matcher))
    return catalog


def _reason_code(rule_set: str, description: str) -> str:
    digest = hashlib.sha256(f"{rule_set}|{description}".encode()).hexdigest().upper()
    return f"RD_FILTERS_ALERT_{rule_set.upper()}_{digest}"


@cache
def _prepared(
    alerts_path: str, expected_sha256: str | None, enabled: tuple[str, ...]
) -> tuple[Path, str, dict[str, _Rule], dict[str, int], Any]:
    """Read, hash, parse and compile the alert table once per worker process.

    1,251 SMARTS compiled per shard would cost more than matching them.  The key
    is the pinned digest as well as the path, so a re-pinned table can never be
    served from a memo built on the previous bytes.
    """

    path, payload, digest = _read_alert_bytes(alerts_path, expected_sha256)
    rules, sizes = _parse_rules(payload)
    return path, digest, rules, sizes, _compile_catalog(rules, frozenset(enabled))


def _enabled_sets(config: RdFiltersAlertConfig) -> tuple[str, ...]:
    actions = config.actions()
    return tuple(
        sorted(name for name, action in actions.items() if action is not AlertAction.IGNORE)
    )


def _policy_id(
    config: RdFiltersAlertConfig,
    *,
    rdkit_version: str,
    alerts_digest: str,
    applied_sizes: dict[str, int],
) -> str:
    actions = config.actions()
    return "rd-filters-alert-policy:sha256:" + canonical_sha256(
        {
            "backend": "rd_filters",
            "backend_version": f"rdkit {rdkit_version}",
            "alerts_sha256": alerts_digest,
            "rule_set_actions": {name: actions[name].value for name in RULE_SETS},
            "rule_counts": applied_sizes,
            "semantics": "substructure-triage-alert-not-experimental-proof",
        }
    )


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Match one contiguous range of parents against the compiled catalog."""

    from rdkit import rdBase

    config = RdFiltersAlertConfig.model_validate(dict(task.config))
    actions = config.actions()
    enabled = _enabled_sets(config)
    _, alerts_digest, rules, catalog_sizes, catalog = _prepared(
        config.alerts_path, config.expected_alerts_sha256, enabled
    )
    applied_sizes = {name: catalog_sizes.get(name, 0) for name in enabled}
    policy_id = _policy_id(
        config,
        rdkit_version=rdBase.rdkitVersion,
        alerts_digest=alerts_digest,
        applied_sizes=applied_sizes,
    )

    input_count = 0
    retained_count = 0
    rejected_count = 0
    warning_decision_count = 0
    reject_decision_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decision_writer,
    ):
        decision_rows: list[dict[str, Any]] = []

        def flush_decisions() -> None:
            if decision_rows:
                decision_writer.write_table(
                    pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
                )
                decision_rows.clear()

        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            retained_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                input_count += 1
                parent_id = row.get("parent_id")
                smiles = row.get("parent_smiles")
                molecule = parse_for_alert_matching(smiles)
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed by RDKit",
                        code="RD_FILTERS_ALERTS_PARENT_INVALID",
                        context={"parent_id": str(parent_id)},
                    )
                try:
                    matched_ids = [
                        str(entry.GetDescription()) for entry in catalog.GetMatches(molecule)
                    ]
                except (RuntimeError, ValueError) as error:
                    raise PluginError(
                        "rd_filters alert matching failed",
                        code="RD_FILTERS_ALERTS_MATCH_FAILED",
                        context={"parent_id": str(parent_id)},
                    ) from error

                # Sorted by (set, description) so the decision rows for a
                # molecule are in the same order on every run and on every
                # machine.
                findings = sorted(
                    {
                        (rules[rule_id].rule_set, rules[rule_id].description)
                        for rule_id in matched_ids
                        if rule_id in rules
                    }
                )
                rejected = any(
                    actions[rule_set] is AlertAction.REJECT for rule_set, _ in findings
                )
                if rejected:
                    rejected_count += 1
                else:
                    retained_count += 1
                    retained_rows.append(row)

                if not findings:
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": "RD_FILTERS_ALERTS_CLEAR",
                            "rule_id": policy_id,
                            "detail": "No match in any enabled rd_filters alert set.",
                        }
                    )
                    decision_count += 1
                else:
                    for rule_set, description in findings:
                        action = actions[rule_set]
                        if action is AlertAction.WARN:
                            warning_decision_count += 1
                        else:
                            reject_decision_count += 1
                        decision_rows.append(
                            {
                                "entity_id": parent_id,
                                "entity_kind": "PARENT",
                                "stage_id": task.stage_id,
                                "outcome": action.value.upper(),
                                "reason_code": _reason_code(rule_set, description),
                                "rule_id": policy_id,
                                "detail": json.dumps(
                                    {
                                        "action": action.value,
                                        "alert": description,
                                        "collection": "rd_filters",
                                        "interpretation": (
                                            "substructure triage flag; not "
                                            "experimental proof"
                                        ),
                                        "rule_set": rule_set,
                                    },
                                    ensure_ascii=True,
                                    allow_nan=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            }
                        )
                        decision_count += 1
                if len(decision_rows) >= config.decision_buffer_size:
                    flush_decisions()
            if retained_rows:
                parent_writer.write_table(
                    pa.Table.from_pylist(retained_rows, schema=PARENT_V1.schema)
                )
        flush_decisions()
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": retained_count, "decisions": decision_count},
        metadata={
            "reject_count": rejected_count,
            "warning_decision_count": warning_decision_count,
            "reject_decision_count": reject_decision_count,
        },
    )


class RdFiltersAlertsPlugin:
    """Apply the eight rd_filters alert sets with per-set actions."""

    descriptor = PluginDescriptor(
        id="chemistry.rd_filters_alerts",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="rd_filters alert collection (Walters)",
        description=(
            "1,251 published SMARTS alerts across BMS, Dundee, Glaxo, Inpharmatica, "
            "LINT, MLSMR, PAINS and SureChEMBL, each set with its own "
            "ignore/warn/reject action."
        ),
    )
    config_model = RdFiltersAlertConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)

        enabled = _enabled_sets(config)
        # Read, hashed, parsed and compiled in the parent as well, so a missing
        # asset or an unparseable alert table stops the stage before a shard is
        # scheduled rather than in every worker at once.  The memo makes the
        # inline single-shard path pay for this exactly once.
        alerts_path, alerts_digest, _, catalog_sizes, _ = _prepared(
            config.alerts_path, config.expected_alerts_sha256, enabled
        )

        unknown = sorted(set(catalog_sizes) - set(RULE_SETS))
        if unknown:
            raise PluginError(
                "rd_filters alert file names rule sets this adapter has no action for: "
                + ", ".join(unknown),
                code="RD_FILTERS_ALERTS_UNKNOWN_RULE_SET",
                hint="Only the eight upstream sets are configurable.",
                context={"rule_sets": unknown},
            )
        if not enabled:
            raise PluginError(
                "every rd_filters rule set is set to 'ignore', so this stage would "
                "read the whole library and decide nothing",
                code="RD_FILTERS_ALERTS_NO_RULE_SET_ENABLED",
                hint="Enable at least one set, or remove the stage from the cascade.",
            )

        applied_sizes = {name: catalog_sizes.get(name, 0) for name in enabled}
        policy_id = _policy_id(
            config,
            rdkit_version=rdBase.rdkitVersion,
            alerts_digest=alerts_digest,
            applied_sizes=applied_sizes,
        )

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
        input_count = result.rows_in
        if input_count == 0:
            raise PluginError(
                "rd_filters alert input contains no parents",
                code="RD_FILTERS_ALERTS_EMPTY_INPUT",
            )
        retained_count = result.rows_out.get("primary", 0)
        decision_count = result.rows_out.get("decisions", 0)
        rejected_count = result.total("reject_count")
        warning_decision_count = result.total("warning_decision_count")
        reject_decision_count = result.total("reject_decision_count")

        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": retained_count},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": decision_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": retained_count,
                "reject_count": rejected_count,
                "warning_decision_count": warning_decision_count,
                "reject_decision_count": reject_decision_count,
                "decision_count": decision_count,
                "policy_id": policy_id,
                "rule_counts": applied_sizes,
                "rule_total": sum(applied_sizes.values()),
                "alerts_path": str(alerts_path),
                "alerts_sha256": alerts_digest,
                "backend": "rd_filters",
                "backend_version": f"rdkit {rdBase.rdkitVersion}",
                **result.response_metadata(),
            },
        )


__all__ = [
    "DEFAULT_ALERTS_REFERENCE",
    "RULE_SETS",
    "AlertAction",
    "RdFiltersAlertConfig",
    "RdFiltersAlertsPlugin",
]
