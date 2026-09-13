"""Structural liabilities judged by the ``medchem`` alert collections.

RDKit's ``FilterCatalog`` ships four catalogs.  The ``medchem`` collection
carries twenty-three, including BMS, Dundee, Glaxo, Inpharmatica, MLSMR,
SureChEMBL, and the Novartis screening-deck rules, so this adapter exists to
give the alerts criterion a second, much wider answer rather than to replace
the first.

Three decisions shape the code below.

*Alerts stay triage.*  ``medchem`` reports a four-level ``status`` --
``exclude``, ``flag``, ``annotations``, ``ok`` -- and a boolean ``pass_filter``
derived from it.  This adapter does not adopt ``pass_filter`` as the verdict.
A substructure match is evidence that a chemist should look, not proof that a
molecule fails, so each status maps to a project-configurable IGNORE / WARN /
REJECT action exactly as the RDKit alert catalogs do.

*A misspelled alert set must not silently pass everything.*  ``medchem``
selects rules by filtering its table on the requested names; a name that
matches nothing yields an empty rule table and therefore a filter that accepts
every molecule.  A screening stage that quietly stops filtering is worse than
one that fails, so requested names are checked against the installed
collection first.

*MolCascade owns the parallelism.*  ``medchem`` will happily spawn its own
process pool.  Nested pools inside a stage worker fight over cores and make
row order depend on scheduling, so every call here is made with ``n_jobs=1``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.chemistry.alerts import parse_for_alert_matching
from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin.medchem_support import (
    hash_data_file,
    import_medchem,
    local_data_path,
    medchem_version,
    result_column,
)
from molcascade.plugins.builtin.structural_alerts import CatalogAction
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")

_ALERTS_DB_FILENAME = "common_alerts_collection.csv"

#: ``medchem`` reports these and nothing else.  An unrecognised value means the
#: installed version changed its vocabulary, which is a reason to stop rather
#: than to guess which way the new label leans.
_KNOWN_STATUSES = frozenset({"exclude", "flag", "annotations", "ok"})

_NIBR_FAMILY = "NIBR"

#: Not a medchem status.  This adapter assigns it to a NIBR exclusion that is
#: only about which compound class the molecule belongs to, so that the decision
#: row says which of the two kinds of exclusion happened instead of leaving a
#: reader to look the rule names up.
_COMPOUND_CLASS_STATUS = "compound_class"


class MedchemAlertsConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    decision_buffer_size: int = Field(default=50_000, ge=1, le=1_000_000)

    #: Names from ``medchem``'s common-alerts collection.  Accepts a YAML list
    #: or the comma-separated string the builder's choice field produces.
    alert_sets: tuple[str, ...] = ("BMS", "Dundee", "Glaxo")
    alerts_db_path: str | None = None

    #: The Novartis screening-deck rules are a separate catalog with their own
    #: severity arithmetic, so they are enabled independently.
    use_nibr: bool = False
    nibr_reject_at_severity: int = Field(default=10, ge=1, le=100)

    #: 67 of NIBR's 444 rules exclude a molecule for *belonging to a compound
    #: class* -- steroid, peptide, nucleoside, glycoside, retinoid, fatty acid,
    #: isotopically labelled -- rather than for carrying a liability.  Novartis
    #: marks them in its own data file, and excludes them because a physical
    #: screening deck does not want pharmacologically promiscuous or
    #: assay-confounding matter in it.  That is a deck-composition argument, and
    #: it does not transfer unexamined to a virtual cascade aimed at a named
    #: target: at ``REJECT`` this deletes every steroid, which on a panel of
    #: approved oral drugs costs dexamethasone and prednisolone outright.
    #:
    #: Defaults to ``REJECT`` so the adapter applies NIBR as Novartis wrote it;
    #: the shipped cascade downgrades it, and says why.  Liability rules are
    #: untouched by this either way -- ranitidine's nitroalkane still rejects.
    nibr_compound_class_action: CatalogAction = CatalogAction.REJECT

    exclude_action: CatalogAction = CatalogAction.REJECT
    flag_action: CatalogAction = CatalogAction.WARN
    annotation_action: CatalogAction = CatalogAction.IGNORE

    @field_validator("alert_sets", mode="before")
    @classmethod
    def _accept_list_or_comma_separated(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return tuple(value) if isinstance(value, list) else value

    @field_validator(
        "exclude_action",
        "flag_action",
        "annotation_action",
        "nibr_compound_class_action",
        mode="before",
    )
    @classmethod
    def _parse_action(cls, value: Any) -> Any:
        return CatalogAction(value) if isinstance(value, str) else value

    @field_validator("alert_sets")
    @classmethod
    def _reject_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        folded = [entry.casefold() for entry in value]
        if len(set(folded)) != len(folded):
            raise ValueError("alert_sets contains the same collection twice")
        return value

    def actions(self) -> dict[str, CatalogAction]:
        return {
            "exclude": self.exclude_action,
            "flag": self.flag_action,
            "annotations": self.annotation_action,
            "ok": CatalogAction.IGNORE,
            _COMPOUND_CLASS_STATUS: self.nibr_compound_class_action,
        }


def _validated_config(request: StageRequest) -> MedchemAlertsConfig:
    try:
        config = MedchemAlertsConfig.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid medchem alert configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error
    if not config.alert_sets and not config.use_nibr:
        raise PluginError(
            "medchem alert stage would apply no rules at all",
            code="MEDCHEM_ALERTS_NO_RULES",
            hint="Name at least one alert set, or enable use_nibr.",
        )
    return config


def _resolve_alerts_db(configured: str | None) -> Path:
    if configured is None:
        return local_data_path(_ALERTS_DB_FILENAME)
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise PluginError(
            "alerts_db_path must be an absolute path",
            code="MEDCHEM_ALERTS_DB_INVALID",
            context={"path": configured},
        )
    if candidate.is_symlink() or not candidate.is_file():
        raise PluginError(
            "alerts_db_path must be an existing regular file and not a symlink",
            code="MEDCHEM_ALERTS_DB_INVALID",
            context={"path": configured},
        )
    return candidate


def _known_alert_sets(filters_module: Any) -> dict[str, str]:
    """Map casefolded collection names to their canonical spelling."""

    try:
        table = filters_module.CommonAlertsFilters.list_default_available_alerts()
        names = [str(name) for name in table["rule_set_name"].tolist()]
    except Exception as error:
        raise PluginError(
            "the installed medchem cannot list its alert collections",
            code="MEDCHEM_BACKEND_UNAVAILABLE",
            hint="Check that medchem and its bundled data files installed cleanly.",
        ) from error
    return {name.casefold(): name for name in names}


def _build_filters(config: MedchemAlertsConfig) -> tuple[Any, Any, tuple[str, ...]]:
    """Construct the requested filters, refusing names that select nothing."""

    from medchem import structural

    common = None
    resolved: tuple[str, ...] = ()
    if config.alert_sets:
        available = _known_alert_sets(structural)
        unknown = [name for name in config.alert_sets if name.casefold() not in available]
        if unknown:
            raise PluginError(
                "unknown medchem alert collection requested",
                code="MEDCHEM_ALERT_SET_UNKNOWN",
                hint=f"Available collections: {', '.join(sorted(available.values()))}.",
                context={"unknown": unknown},
            )
        resolved = tuple(available[name.casefold()] for name in config.alert_sets)
        kwargs: dict[str, Any] = {"alerts_set": list(resolved)}
        if config.alerts_db_path is not None:
            kwargs["alerts_db_path"] = config.alerts_db_path
        common = structural.CommonAlertsFilters(**kwargs)
        if len(getattr(common, "alerts_df", ())) == 0:
            raise PluginError(
                "the requested medchem alert collections contain no rules",
                code="MEDCHEM_ALERTS_EMPTY",
                context={"alert_sets": list(resolved)},
            )
    nibr = structural.NIBRFilters() if config.use_nibr else None
    return common, nibr, resolved


#: ``NIBR||<rule>||<severity code>||<covalent>||<compound class>``.  This is the
#: layout of Novartis' published rule file, carried through into the RDKit
#: catalog medchem builds from it, and the rule names in it are byte-identical
#: to the tokens that come back in the ``reasons`` column -- ``_min(1)`` suffix
#: and all -- so the two can be matched without normalising either.
_NIBR_DESCRIPTION_FIELDS = 5
#: Novartis' code for "exclude", as opposed to 1 (flag) and 0 (annotate).
_NIBR_EXCLUDE_CODE = "2"


def _nibr_rule_index(nibr: Any) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(compound-class rules, exclude-level rules)`` from NIBR itself.

    Reading this out of the catalog rather than hard-coding a list of rule names
    means the split is Novartis' opinion about their own rules, not ours, and
    that it stays correct when medchem ships an updated file.
    """

    catalog = getattr(nibr, "catalog", None)
    if catalog is None:
        raise PluginError(
            "the installed medchem exposes no NIBR rule catalog",
            code="MEDCHEM_NIBR_CATALOG_UNAVAILABLE",
            hint="A newer medchem may have changed how NIBRFilters is built.",
        )
    compound_class: set[str] = set()
    exclude_level: set[str] = set()
    for index in range(catalog.GetNumEntries()):
        fields = str(catalog.GetEntryWithIdx(index).GetDescription()).split("||")
        if len(fields) != _NIBR_DESCRIPTION_FIELDS:
            # Guessing which field means what is how a filter starts deleting
            # the wrong molecules quietly, so stop instead.
            raise PluginError(
                "a NIBR rule is not in the layout this adapter can read",
                code="MEDCHEM_NIBR_RULE_UNREADABLE",
                hint="A newer medchem may have changed its NIBR rule format.",
                context={"field_count": len(fields)},
            )
        name = fields[1]
        if fields[4] == "1":
            compound_class.add(name)
        if fields[2] == _NIBR_EXCLUDE_CODE:
            exclude_level.add(name)
    if not compound_class or not exclude_level:
        raise PluginError(
            "the installed NIBR rule set marks no compound classes or no exclusions",
            code="MEDCHEM_NIBR_RULE_UNREADABLE",
            hint="A newer medchem may have changed its NIBR rule format.",
            context={
                "compound_class_rules": len(compound_class),
                "exclude_rules": len(exclude_level),
            },
        )
    return frozenset(compound_class), frozenset(exclude_level)


def _nibr_rule_names(reasons: str) -> frozenset[str]:
    """Split a ``reasons`` cell back into the rule names that produced it."""

    return frozenset(part.strip() for part in reasons.split(";") if part.strip())


def _status_of(value: Any, family: str) -> str:
    status = str(value)
    if status not in _KNOWN_STATUSES:
        raise PluginError(
            "medchem reported a status this adapter does not recognise",
            code="MEDCHEM_STATUS_UNKNOWN",
            hint="A newer medchem may have changed its status vocabulary.",
            context={"family": family, "status": status},
        )
    return status


def _reason_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _reason_code(family: str, status: str, reasons: str) -> str:
    digest = hashlib.sha256(reasons.encode("utf-8")).hexdigest().upper()
    return f"MEDCHEM_{family.upper()}_{status.upper()}_{digest}"


def _detail(family: str, status: str, reasons: str, action: CatalogAction, **extra: Any) -> str:
    payload: dict[str, Any] = {
        "action": action.value,
        "family": family,
        "interpretation": "substructure triage flag; not experimental proof",
        "reasons": reasons,
        "status": status,
    }
    payload.update(extra)
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _evaluate(
    molecules: list[Any],
    *,
    common: Any,
    nibr: Any,
    reject_at_severity: int,
    compound_class_rules: frozenset[str] = frozenset(),
    exclude_level_rules: frozenset[str] = frozenset(),
) -> list[list[tuple[str, str, str, dict[str, Any]]]]:
    """Return one list of ``(family, status, reasons, extra)`` per molecule."""

    findings: list[list[tuple[str, str, str, dict[str, Any]]]] = [
        [] for _ in range(len(molecules))
    ]
    if not molecules:
        return findings

    if common is not None:
        # n_jobs=1 keeps the worker single-threaded and the row order fixed.
        frame = common(molecules, n_jobs=1, progress=False, scheduler="threads")
        statuses = result_column(frame, "status", len(molecules), "common")
        reasons = result_column(frame, "reasons", len(molecules), "common")
        for index, (status_value, reason_value) in enumerate(
            zip(statuses, reasons, strict=True)
        ):
            status = _status_of(status_value, "common")
            if status == "ok":
                continue
            findings[index].append(("common", status, _reason_text(reason_value), {}))

    if nibr is not None:
        frame = nibr(molecules, n_jobs=1, progress=False, scheduler="threads")
        statuses = result_column(frame, "status", len(molecules), _NIBR_FAMILY)
        reasons = result_column(frame, "reasons", len(molecules), _NIBR_FAMILY)
        severities = result_column(frame, "severity", len(molecules), _NIBR_FAMILY)
        covalent = result_column(frame, "n_covalent_motif", len(molecules), _NIBR_FAMILY)
        for index, (status_value, reason_value, severity_value, covalent_value) in enumerate(
            zip(statuses, reasons, severities, covalent, strict=True)
        ):
            status = _status_of(status_value, _NIBR_FAMILY)
            severity = int(severity_value)
            reasons_text = _reason_text(reason_value)
            # Accumulated severity is its own axis: a molecule can collect
            # enough "flag" rules to be worse than one "exclude" rule, and
            # the project decides where that line sits.
            if severity >= reject_at_severity:
                matched = _nibr_rule_names(reasons_text)
                exclusions = matched & exclude_level_rules
                # An exclusion is reported as class membership only when
                # *every* exclude-level rule behind it was a class rule.
                # One genuine liability alongside and it stays an exclusion
                # -- a compound class is never allowed to speak for a
                # molecule that has something real wrong with it.  Lower
                # severities that merely accumulated were not exclusions in
                # the first place, so they do not change the reading, and
                # they stay in the reported reasons either way.
                if exclusions and exclusions <= compound_class_rules:
                    status = _COMPOUND_CLASS_STATUS
                else:
                    status = "exclude"
            elif status == "ok":
                continue
            findings[index].append(
                (
                    _NIBR_FAMILY,
                    status,
                    reasons_text,
                    {
                        "severity": severity,
                        "n_covalent_motif": int(covalent_value),
                        "reject_at_severity": reject_at_severity,
                    },
                )
            )
    return findings


def _policy_id(
    config: MedchemAlertsConfig,
    *,
    backend_version: str,
    resolved_sets: tuple[str, ...],
    rules_digest: str,
) -> str:
    actions = config.actions()
    return "medchem-alert-policy:sha256:" + canonical_sha256(
        {
            "alert_sets": list(resolved_sets),
            "backend": "medchem",
            "backend_version": backend_version,
            "nibr_enabled": config.use_nibr,
            "nibr_reject_at_severity": config.nibr_reject_at_severity,
            "rules_sha256": rules_digest,
            "semantics": "substructure-triage-alert-not-experimental-proof",
            "status_actions": {status: action.value for status, action in actions.items()},
        }
    )


#: What the parent worked out and a shard would only recompute: the digest of
#: the alert database, and the policy identity that digest is part of.  Popped
#: before validation because ``MedchemAlertsConfig`` is strict, and never seen by
#: ``stage_cache_key`` -- that hashes the pipeline's stage config, not this.
_RUNTIME_KEY = "molcascade.runtime"


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Screen one contiguous range of parents against the enabled catalogs."""

    settings = dict(task.config)
    runtime = dict(settings.pop(_RUNTIME_KEY))
    config = MedchemAlertsConfig.model_validate(settings)
    policy_id = str(runtime["policy_id"])
    # Rebuilt per worker: the filters hold compiled RDKit catalogs, which do not
    # cross a process boundary, and building them is cheap next to matching.
    common, nibr, _ = _build_filters(config)
    compound_class_rules, exclude_level_rules = (
        _nibr_rule_index(nibr) if nibr is not None else (frozenset(), frozenset())
    )
    actions = config.actions()

    counters = {
        "input_count": 0,
        "retained_count": 0,
        "reject_count": 0,
        "warning_decision_count": 0,
        "reject_decision_count": 0,
        "decision_count": 0,
    }
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
                molecule = parse_for_alert_matching(smiles)
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed for medchem alerts",
                        code="MEDCHEM_PARENT_INVALID",
                        context={"parent_id": str(row.get("parent_id"))},
                    )
                molecules.append(molecule)
            counters["input_count"] += len(rows)

            findings = _evaluate(
                molecules,
                common=common,
                nibr=nibr,
                reject_at_severity=config.nibr_reject_at_severity,
                compound_class_rules=compound_class_rules,
                exclude_level_rules=exclude_level_rules,
            )

            retained_rows: list[dict[str, Any]] = []
            for row, per_molecule in zip(rows, findings, strict=True):
                parent_id = row.get("parent_id")
                rejected = False
                emitted = 0
                for family, status, reasons, extra in per_molecule:
                    action = actions[status]
                    if action is CatalogAction.IGNORE:
                        continue
                    if action is CatalogAction.REJECT:
                        rejected = True
                        counters["reject_decision_count"] += 1
                    else:
                        counters["warning_decision_count"] += 1
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": action.value.upper(),
                            "reason_code": _reason_code(family, status, reasons),
                            "rule_id": policy_id,
                            "detail": _detail(family, status, reasons, action, **extra),
                        }
                    )
                    counters["decision_count"] += 1
                    emitted += 1
                if emitted == 0:
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": "MEDCHEM_ALERTS_CLEAR",
                            "rule_id": policy_id,
                            "detail": (
                                "No acted-upon match in any enabled medchem "
                                "alert collection."
                            ),
                        }
                    )
                    counters["decision_count"] += 1
                if rejected:
                    counters["reject_count"] += 1
                else:
                    counters["retained_count"] += 1
                    retained_rows.append(row)
                if len(decision_rows) >= config.decision_buffer_size:
                    flush_decisions()
            if retained_rows:
                parent_writer.write_table(
                    pa.Table.from_pylist(retained_rows, schema=PARENT_V1.schema)
                )
        flush_decisions()
    return ShardOutcome(
        rows_in=counters["input_count"],
        rows_out={
            "primary": counters["retained_count"],
            "decisions": counters["decision_count"],
        },
        metadata=dict(counters),
    )


class MedchemAlertsPlugin:
    """Screen parents against the medchem alert collections and NIBR rules."""

    descriptor = PluginDescriptor(
        id="chemistry.medchem_alerts",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="medchem alert collections",
        description=(
            "Twenty-three curated alert collections (BMS, Dundee, Glaxo, "
            "Inpharmatica, MLSMR, SureChEMBL, PAINS and more) plus the Novartis "
            "screening-deck rules, with configurable IGNORE/WARN/REJECT actions."
        ),
    )
    config_model = MedchemAlertsConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(request)
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        medchem = import_medchem()
        # Built and read in the parent as well, so an unknown collection name or
        # an unreadable NIBR rule file stops the stage before a shard is
        # scheduled rather than in every worker at once.
        _, nibr, resolved_sets = _build_filters(config)
        if nibr is not None:
            _nibr_rule_index(nibr)
        alerts_db = _resolve_alerts_db(config.alerts_db_path)
        rules_digest = hash_data_file(alerts_db, code="MEDCHEM_ALERTS_DB_INVALID")
        policy_id = _policy_id(
            config,
            backend_version=medchem_version(medchem),
            resolved_sets=resolved_sets,
            rules_digest=rules_digest,
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
            config={
                **config.model_dump(mode="json"),
                _RUNTIME_KEY: {"policy_id": policy_id},
            },
        )
        if result.rows_in == 0:
            raise PluginError(
                "medchem alert input contains no parents",
                code="MEDCHEM_ALERTS_EMPTY_INPUT",
            )
        counters = {
            name: result.total(name)
            for name in (
                "input_count",
                "retained_count",
                "reject_count",
                "warning_decision_count",
                "reject_decision_count",
                "decision_count",
            )
        }

        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": counters["retained_count"]},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": counters["decision_count"], "policy_id": policy_id},
                ),
            },
            metadata={
                **counters,
                "output_count": counters["retained_count"],
                "alert_sets": list(resolved_sets),
                "nibr_enabled": config.use_nibr,
                "rules_sha256": rules_digest,
                "policy_id": policy_id,
                "backend": "medchem",
                "backend_version": medchem_version(medchem),
                "network_or_download_invoked_by_adapter": False,
                **result.response_metadata(),
            },
        )


__all__ = ["MedchemAlertsConfig", "MedchemAlertsPlugin"]
