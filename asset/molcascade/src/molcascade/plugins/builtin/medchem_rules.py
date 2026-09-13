"""Physicochemical rule sets from ``medchem``.

The built-in drug-likeness stage answers one question with one answer:
Lipinski's rule of five plus QED, computed by RDKit.  That is the right default
and the wrong monoculture.  ``medchem`` ships twenty-odd published rule sets --
Veber, Ghose, Egan, Muegge, Oprea, Xu, REOS, Pfizer 3/75, GSK 4/400, ZINC,
lead-like, CNS, respiratory, and the generative-design rules -- and a project
screening a generated library usually wants a specific combination of them
rather than the single most famous one.

Two things are worth knowing about how this adapter reads those rules.

*Combination is part of the question.*  ``medchem`` reports every rule
separately and also reports ``pass_all`` and ``pass_any``.  This adapter
exposes that choice directly: a criterion can demand every rule, any rule, or
at least *k* of them.  A tier already offers the same choice across criteria;
offering it here too means "Lipinski or Veber" does not need two tiers to say.

*A rule set that matches nothing is a bug, not a permissive filter.*  Rule
names are validated against the installed catalogue before any molecule is
read, because a stage that quietly stops rejecting is worse than one that
stops.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator, model_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin.medchem_support import (
    import_medchem,
    medchem_version,
    result_column,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")


class RuleCombination(StrEnum):
    """How several rule sets combine into one verdict."""

    ALL = "all"
    ANY = "any"
    AT_LEAST = "at_least"


class MedchemRulesConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    decision_buffer_size: int = Field(default=50_000, ge=1, le=1_000_000)

    #: Rule names from ``RuleFilters.list_available_rules_names()``.  Accepts a
    #: YAML list or the comma-separated string the builder's choice field emits.
    rules: tuple[str, ...] = ("rule_of_five",)
    combination: RuleCombination = RuleCombination.ALL
    minimum_passes: int = Field(default=1, ge=1, le=64)

    @field_validator("rules", mode="before")
    @classmethod
    def _accept_list_or_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value) if isinstance(value, list) else value

    @field_validator("combination", mode="before")
    @classmethod
    def _parse_combination(cls, value: Any) -> Any:
        return RuleCombination(value) if isinstance(value, str) else value

    @field_validator("rules")
    @classmethod
    def _require_distinct_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("name at least one rule set")
        folded = [entry.casefold() for entry in value]
        if len(set(folded)) != len(folded):
            raise ValueError("rules contains the same rule set twice")
        return value

    @model_validator(mode="after")
    def _quorum_must_be_reachable(self) -> MedchemRulesConfig:
        if self.combination is RuleCombination.AT_LEAST and self.minimum_passes > len(self.rules):
            raise ValueError(
                f"minimum_passes {self.minimum_passes} exceeds the "
                f"{len(self.rules)} configured rule sets, so nothing could pass"
            )
        return self

    def required_passes(self) -> int:
        if self.combination is RuleCombination.ALL:
            return len(self.rules)
        if self.combination is RuleCombination.ANY:
            return 1
        return self.minimum_passes


def _validated_config(request: StageRequest) -> MedchemRulesConfig:
    try:
        return MedchemRulesConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid medchem rule configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _available_rules(rule_filters: Any) -> dict[str, str]:
    try:
        names = [str(name) for name in rule_filters.list_available_rules_names()]
    except Exception as error:
        raise PluginError(
            "the installed medchem cannot list its rule sets",
            code="MEDCHEM_BACKEND_UNAVAILABLE",
            hint="Check that medchem and its bundled data files installed cleanly.",
        ) from error
    return {name.casefold(): name for name in names}


def _build_rule_filter(config: MedchemRulesConfig) -> tuple[Any, tuple[str, ...]]:
    from medchem.rules import RuleFilters

    available = _available_rules(RuleFilters)
    unknown = [name for name in config.rules if name.casefold() not in available]
    if unknown:
        raise PluginError(
            "unknown medchem rule set requested",
            code="MEDCHEM_RULE_UNKNOWN",
            hint=f"Available rule sets: {', '.join(sorted(available.values()))}.",
            context={"unknown": unknown},
        )
    resolved = tuple(available[name.casefold()] for name in config.rules)
    return RuleFilters(rule_list=list(resolved)), resolved


def _reason_code(failed: tuple[str, ...]) -> str:
    if not failed:
        return "MEDCHEM_RULES_PASS"
    digest = hashlib.sha256(";".join(failed).encode("utf-8")).hexdigest().upper()
    return f"MEDCHEM_RULES_FAIL_{digest}"


def _detail(
    outcomes: dict[str, bool],
    *,
    passed: int,
    required: int,
    combination: RuleCombination,
) -> str:
    return json.dumps(
        {
            "combination": combination.value,
            "passed_rule_count": passed,
            "required_rule_count": required,
            "rules": {name: bool(value) for name, value in outcomes.items()},
        },
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _policy_id(
    config: MedchemRulesConfig,
    *,
    backend_version: str,
    resolved: tuple[str, ...],
    required: int,
) -> str:
    return "medchem-rule-policy:sha256:" + canonical_sha256(
        {
            "backend": "medchem",
            "backend_version": backend_version,
            "combination": config.combination.value,
            "required_rule_count": required,
            "rules": list(resolved),
            "semantics": "published-physicochemical-rule-sets",
        }
    )


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Apply the rule sets to one contiguous range of parents."""

    from rdkit import Chem

    config = MedchemRulesConfig.model_validate(dict(task.config))
    medchem = import_medchem()
    # Rebuilt here rather than shipped from the parent: ``RuleFilters`` holds
    # compiled callables that do not survive the process boundary, and the rule
    # catalogue is the same in every worker because it is the same install.
    rule_filter, resolved = _build_rule_filter(config)
    required = config.required_passes()
    policy_id = _policy_id(
        config,
        backend_version=medchem_version(medchem),
        resolved=resolved,
        required=required,
    )

    input_count = 0
    retained_count = 0
    reject_count = 0
    decision_count = 0
    per_rule_failures = dict.fromkeys(resolved, 0)
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
            rows = batch.to_pylist()
            molecules = []
            for row in rows:
                smiles = row.get("parent_smiles")
                molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed for medchem rules",
                        code="MEDCHEM_PARENT_INVALID",
                        context={"parent_id": str(row.get("parent_id"))},
                    )
                molecules.append(molecule)
            input_count += len(rows)

            # n_jobs=1: MolCascade owns the parallelism, and a nested pool would
            # make row order depend on scheduling.
            frame = rule_filter(
                molecules,
                n_jobs=1,
                progress=False,
                scheduler="threads",
                keep_props=False,
            )
            columns = {
                name: result_column(frame, name, len(rows), "rules") for name in resolved
            }

            retained_rows: list[dict[str, Any]] = []
            for index, row in enumerate(rows):
                outcomes = {name: bool(columns[name][index]) for name in resolved}
                failed = tuple(name for name, ok in outcomes.items() if not ok)
                for name in failed:
                    per_rule_failures[name] += 1
                passed = len(resolved) - len(failed)
                accepted = passed >= required
                if accepted:
                    retained_count += 1
                    retained_rows.append(row)
                else:
                    reject_count += 1
                decision_rows.append(
                    {
                        "entity_id": row.get("parent_id"),
                        "entity_kind": "PARENT",
                        "stage_id": task.stage_id,
                        "outcome": "PASS" if accepted else "REJECT",
                        "reason_code": _reason_code(() if accepted else failed),
                        "rule_id": policy_id,
                        "detail": _detail(
                            outcomes,
                            passed=passed,
                            required=required,
                            combination=config.combination,
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
        # A per-rule breakdown is a mapping, not a counter, so it travels whole
        # and the parent adds the shards together key by key.
        metadata={
            "reject_count": reject_count,
            "per_rule_failure_count": dict(per_rule_failures),
        },
    )


class MedchemRulesPlugin:
    """Apply published physicochemical rule sets as one combined verdict."""

    descriptor = PluginDescriptor(
        id="chemistry.medchem_rules",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="medchem physicochemical rule sets",
        description=(
            "Published rule sets (Lipinski, Veber, Ghose, Egan, Oprea, Xu, REOS, "
            "Pfizer 3/75, GSK 4/400, ZINC, lead-like, CNS and more) combined with "
            "all / any / at-least-k semantics."
        ),
    )
    config_model = MedchemRulesConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        medchem = import_medchem()
        # Built in the parent as well, so an unknown rule name stops the stage
        # before a shard is scheduled rather than in every worker at once.
        _, resolved = _build_rule_filter(config)
        required = config.required_passes()
        policy_id = _policy_id(
            config,
            backend_version=medchem_version(medchem),
            resolved=resolved,
            required=required,
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
                "medchem rule input contains no parents",
                code="MEDCHEM_RULES_EMPTY_INPUT",
            )
        retained_count = result.rows_out.get("primary", 0)
        decision_count = result.rows_out.get("decisions", 0)
        reject_count = result.total("reject_count")
        per_rule_failures = dict.fromkeys(resolved, 0)
        for shard in result.shard_metadata:
            for name, count in dict(shard.get("per_rule_failure_count") or {}).items():
                per_rule_failures[name] = per_rule_failures.get(name, 0) + int(count)

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
                "reject_count": reject_count,
                "decision_count": decision_count,
                "rules": list(resolved),
                "combination": config.combination.value,
                "required_rule_count": required,
                # Which rule is doing the rejecting is the first thing anyone
                # asks when a funnel narrows more than expected.
                "per_rule_failure_count": dict(per_rule_failures),
                "policy_id": policy_id,
                "backend": "medchem",
                "backend_version": medchem_version(medchem),
                "network_or_download_invoked_by_adapter": False,
                **result.response_metadata(),
            },
        )


__all__ = ["MedchemRulesConfig", "MedchemRulesPlugin", "RuleCombination"]
