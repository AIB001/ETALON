"""ChEMBL Structure Pipeline's structure checker, as a validity gate.

This is the second opinion the chemistry tier was missing.  RDKit's hard gate
asks whether RDKit can make sense of a structure; this asks whether *InChI* can.
The distinction is the whole point of the backend -- ``check_molblock`` runs the
IUPAC InChI C library over the molecule and reports what it had to change to
produce a formula, so it catches disagreements a single toolkit cannot see by
definition.  Both toolkits accepting a structure is evidence; RDKit accepting
its own output is not.

Two things about how the check is driven are decisions rather than plumbing.

The checker reads a mol block, and a parent registered from SMILES has no
conformer -- which looks like it would collect the ``zero_coordinates`` penalty
for free.  It does not: ``MolToMolBlock`` lays out 2D coordinates when the
molecule arrives without them, so the geometry checks see a real depiction and
stay quiet.  That was worth confirming rather than assuming, because the
alternative was excluding a third of the catalogue by hand.

The severity of a molecule is the *worst* issue found, never the sum.  Upstream
documents the penalty as the seriousness of one issue on a 0-9 scale, so three
advisory findings are three advisories -- adding them into a six would
manufacture a hard reject out of arithmetic rather than out of chemistry.

Reference:
    Bento, A.P., Hersey, A., Felix, E. et al.  An open source chemical structure
    curation pipeline using RDKit.  J Cheminform 12, 51 (2020).
    https://doi.org/10.1186/s13321-020-00456-1
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
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

#: Upstream's own bands, read off the penalties the checker classes declare.
#: Six is where the catalogue puts structures that are not molecules anybody
#: meant to draw -- radicals off the known list, zero atoms, embedded 3D
#: coordinates, polymers.  Five and below is drawing and stereo commentary,
#: and two is advisory: "InChI omitted undefined stereo" describes most of a
#: generated library, so rejecting on it would empty the funnel at tier one.
_DEFAULT_REJECT_AT = 6


class ChemblStructureCheckConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    #: Reject when the most serious single finding reaches this penalty.
    reject_at_penalty: int = Field(default=_DEFAULT_REJECT_AT, ge=1, le=9)
    #: Sub-threshold findings are chemistry the reviewer may still want to see,
    #: so they are carried on the PASS row rather than dropped.
    record_advisories: bool = True


def _backend() -> tuple[Any, Any]:
    """Import the checker, or say which distribution is missing.

    Called in the parent *and* in every worker: the parent so a missing backend
    stops the stage before a shard is scheduled, the worker because a spawned
    process inherits no imports from the one that planned the work.
    """

    try:
        import chembl_structure_pipeline
        from chembl_structure_pipeline import checker
    except ImportError as error:  # pragma: no cover - exercised by availability probe
        raise PluginError(
            "the ChEMBL Structure Pipeline checker is not installed",
            code="CHEMBL_CHECK_BACKEND_MISSING",
            context={"distribution": "chembl-structure-pipeline"},
        ) from error
    return chembl_structure_pipeline, checker


def _policy_id(
    config: ChemblStructureCheckConfig, *, backend_version: str, rdkit_version: str
) -> str:
    """Identity of the verdict: the thresholds *and* what computed them."""

    policy = config.model_dump(mode="json")
    policy.pop("batch_size", None)
    # The InChI verdicts come out of RDKit's bundled InChI build, so the
    # RDKit version is part of what produced this answer, not background.
    return "chembl-structure-check-policy:sha256:" + canonical_sha256(
        {
            "backend": "chembl_structure_pipeline",
            "backend_version": backend_version,
            "rdkit_version": rdkit_version,
            "policy": policy,
        }
    )


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Check one contiguous range of parents in whichever process owns it."""

    from rdkit import Chem, rdBase

    chembl_structure_pipeline, checker = _backend()
    config = ChemblStructureCheckConfig.model_validate(dict(task.config))
    backend_version = str(getattr(chembl_structure_pipeline, "__version__", "unknown"))
    policy_id = _policy_id(
        config, backend_version=backend_version, rdkit_version=rdBase.rdkitVersion
    )

    input_count = 0
    passed_count = 0
    decision_count = 0
    advisory_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parents,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decisions,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            passed: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                input_count += 1
                parent_id = row.get("parent_id")
                smiles = row.get("parent_smiles")
                molecule = Chem.MolFromSmiles(smiles if isinstance(smiles, str) else "")
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed by the ChEMBL check",
                        code="CHEMBL_CHECK_PARENT_INVALID",
                        context={"parent_id": str(parent_id)},
                    )
                try:
                    # No conformer on the molecule means RDKit lays out a 2D
                    # depiction here, which is what the geometry checks need in
                    # order to be fair.
                    molblock = Chem.MolToMolBlock(molecule)
                except Exception as error:
                    raise PluginError(
                        "registered parent could not be written as a mol block",
                        code="CHEMBL_CHECK_MOLBLOCK_FAILED",
                        context={
                            "parent_id": str(parent_id),
                            "error": type(error).__name__,
                        },
                    ) from error

                findings = tuple(checker.check_molblock(molblock))
                # check_molblock sorts worst-first; taking the max explicitly
                # says so rather than relying on it.
                severity = max((int(p) for p, _ in findings), default=0)
                detail = json.dumps(
                    {
                        "max_penalty": severity,
                        "findings": [
                            {"penalty": int(p), "issue": str(e)} for p, e in findings
                        ],
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )

                if severity >= config.reject_at_penalty:
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "REJECT",
                            "reason_code": f"CHEMBL_STRUCTURE_PENALTY_{severity}",
                            "rule_id": policy_id,
                            "detail": detail,
                        }
                    )
                else:
                    if findings:
                        advisory_count += 1
                    passed.append(row)
                    passed_count += 1
                    decision_rows.append(
                        {
                            "entity_id": parent_id,
                            "entity_kind": "PARENT",
                            "stage_id": task.stage_id,
                            "outcome": "PASS",
                            "reason_code": "CHEMBL_STRUCTURE_CHECK_PASS",
                            "rule_id": policy_id,
                            "detail": (
                                detail
                                if config.record_advisories
                                else json.dumps(
                                    {"max_penalty": severity}, separators=(",", ":")
                                )
                            ),
                        }
                    )
                decision_count += 1
            if passed:
                parents.write_table(pa.Table.from_pylist(passed, schema=PARENT_V1.schema))
            decisions.write_table(
                pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
            )
    return ShardOutcome(
        rows_in=input_count,
        rows_out={"primary": passed_count, "decisions": decision_count},
        metadata={"advisory_count": advisory_count},
    )


class ChemblStructureCheckPlugin:
    descriptor = PluginDescriptor(
        id="chemistry.chembl_structure_check",
        version="0.1.0",
        kind=PluginKind.GATE,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, DECISION_V1.id),
        output_ports={"primary": PARENT_V1.id, "decisions": DECISION_V1.id},
        cardinality=Cardinality.FILTER,
        determinism=Determinism.DETERMINISTIC,
        display_name="ChEMBL Structure Pipeline checker",
        description=(
            "EBI's curation checker: InChI and RDKit must agree that the structure is "
            "one a chemist meant to draw, scored on the published 0-9 penalty scale."
        ),
    )
    config_model = ChemblStructureCheckConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        chembl_structure_pipeline, _ = _backend()

        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid ChEMBL structure check configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error

        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        backend_version = str(getattr(chembl_structure_pipeline, "__version__", "unknown"))
        policy_id = _policy_id(
            config, backend_version=backend_version, rdkit_version=rdBase.rdkitVersion
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
                "ChEMBL structure check input contains no parents",
                code="CHEMBL_CHECK_EMPTY_INPUT",
            )
        passed_count = result.rows_out.get("primary", 0)
        decision_count = result.rows_out.get("decisions", 0)
        advisory_count = result.total("advisory_count")

        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id,
                    result.file_paths["primary"],
                    {"row_count": passed_count},
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id,
                    result.file_paths["decisions"],
                    {"row_count": decision_count, "policy_id": policy_id},
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": passed_count,
                "reject_count": input_count - passed_count,
                "decision_count": decision_count,
                # Molecules kept despite a finding: the number a reviewer should
                # look at if the threshold is ever argued about.
                "advisory_count": advisory_count,
                "policy_id": policy_id,
                "backend": "chembl_structure_pipeline",
                "backend_version": backend_version,
                "rdkit_version": rdBase.rdkitVersion,
                **result.response_metadata(),
            },
        )


__all__ = ["ChemblStructureCheckConfig", "ChemblStructureCheckPlugin"]
