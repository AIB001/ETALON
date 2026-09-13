"""Indexed lead-reference similarity, for reference sets the exact backend cannot hold.

The exact backend next door compares every candidate against every reference,
which is the right answer for a project's twenty leads and the wrong one for a
vendor deck.  Measured on this machine, 300 candidates against a growing
reference set:

    references     exact (RDKit)     indexed (FPSim2)
           200         785 q/s              611 q/s
         2,000         376 q/s              519 q/s
        20,000          61 q/s              221 q/s

The exact backend degrades linearly, because that is what it is; FPSim2 sorts
its fingerprints by popcount and uses the bound that a Tanimoto coefficient
cannot exceed the ratio of popcounts, so most of the reference set is skipped
without ever being compared.  The crossover is somewhere around a thousand
references, and below it the index is *slower* -- building it costs more than
the comparisons it saves.  Neither backend is the better one; they answer the
same question at different scales, which is why both are offered rather than
one replacing the other.

Two ways this backend is not bit-identical to the exact one, both deliberate
and both worth knowing before switching a running project between them:

*Coefficients are float32.*  FPSim2 returns single-precision, RDKit returns
double.  The values agree to about seven significant figures, which is far
inside any threshold anyone sets on purpose, but a molecule sitting exactly on
a boundary can fall on either side of it.

*Ties are broken explicitly here.*  FPSim2 returns equally-similar references in
whatever order its popcount-sorted scan reached them, so asking for the single
nearest neighbour would report an arbitrary one of them and a rerun could
report another.  This reads back the top few and takes the lexicographically
first reference id among those tied at the maximum, which is the same rule the
exact backend follows by construction.  The depth it reads is part of the
method identity, because it is part of the answer.

Reference:
    Félix, E.  FPSim2: simple package for fast molecular similarity searches.
    EMBL-EBI.  https://github.com/chembl/FPSim2
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError

from molcascade.chemistry.datasets import discover_contract_files, require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_sha256
from molcascade.contracts import APPLICABILITY_V1, DECISION_V1, PARENT_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.builtin.similarity import (
    _RUNTIME_KEY,
    ReferenceSimilarityConfig,
    SimilarityFailureAction,
    _load_reference_records,
    objective_outcome,
    similarity_decision_rows,
    similarity_detail,
)
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/passed_parents/part-00000.parquet")
_APPLICABILITY_PATH = Path("datasets/reference_similarity/part-00000.parquet")
_DECISION_PATH = Path("datasets/decisions/part-00000.parquet")

#: Names of the staging files the index is built through.  Both are removed
#: before the stage returns: they are derived from the reference set, they are
#: large, and leaving them behind would make a staging directory look like an
#: output.
_REFERENCE_SMI = ".fpsim2-references.smi"
_REFERENCE_INDEX = ".fpsim2-references.h5"


class Fpsim2SimilarityConfig(ReferenceSimilarityConfig):
    """The exact backend's configuration, retuned for an indexed scan.

    Same fields, same meanings, same thresholds -- switching backend is meant to
    be a change of engine and not a change of question.  Only the limits move,
    and they move because the limits are what say which engine you should be
    using in the first place.
    """

    #: The exact backend stops at ten thousand and tells you to come here.
    #: A quarter of a million is a vendor deck, which is the largest thing
    #: anyone sensibly calls a "reference set" for this question.
    max_reference_count: int = Field(default=250_000, ge=1, le=1_000_000)
    #: Still a worst case rather than a prediction: the popcount bound skips
    #: most references, but how many depends on the candidates, so the budget
    #: stays as a guardrail and simply sits far higher than the exact one.
    max_total_comparisons: int = Field(default=20_000_000_000, ge=1, le=100_000_000_000)
    #: How many neighbours to read back in order to break ties deterministically.
    #: Eight is enough for the duplicate-and-near-duplicate clusters that occur
    #: in real lead sets; a tie extending past it is still resolved the same way
    #: every run, just from a smaller window.
    tie_break_depth: int = Field(default=8, ge=1, le=1_000)
    #: FPSim2 parallelises one query's scan. Left at one by default because a
    #: cascade stage is already the unit of parallelism here, and nesting the
    #: two oversubscribes every core on the machine.
    n_workers: int = Field(default=1, ge=1, le=256)

    #: Shown when the reference set is larger than this backend allows. The
    #: exact backend points here; there is nowhere further to point.
    over_limit_hint: ClassVar[str] = (
        "Reduce the reference set, or raise max_reference_count if the machine "
        "has the memory for it."
    )


def _build_index(
    directory: Path,
    reference_rows: list[tuple[str, str]],
    config: Fpsim2SimilarityConfig,
) -> Path:
    """Write the reference set out and build the FPSim2 index file.

    The molecules written are the canonical SMILES this stage already parsed and
    validated, not the strings as supplied, so the index is built from exactly
    the structures that went into ``reference_set_id``.

    Built once, in the parent: the index is a pure function of the reference set,
    it costs more than the queries it saves below a thousand references, and
    building it per shard would multiply that cost by the shard count.  Each
    worker opens the finished file read-only.
    """

    try:
        from FPSim2.io import create_db_file
    except ImportError as error:
        raise PluginError(
            "FPSim2 is required for indexed reference similarity",
            code="FPSIM2_BACKEND_MISSING",
            hint="Install it with: pip install FPSim2",
        ) from error

    smi_path = directory / _REFERENCE_SMI
    index_path = directory / _REFERENCE_INDEX
    for path in (smi_path, index_path):
        if path.exists() or path.is_symlink():
            raise PluginError(
                "FPSim2 index scratch file already exists in staging",
                code="PLUGIN_STAGING_NOT_EMPTY",
                context={"path": path.name},
            )

    # Ids are the 1-based position in the id-sorted reference list, so the
    # numeric order FPSim2 reports ties in is the same order the reference ids
    # sort in.  That is what makes "smallest mol_id among the tied" and
    # "lexicographically first reference id among the tied" the same rule.
    with smi_path.open("w", encoding="utf-8") as stream:
        for index, (_, canonical) in enumerate(reference_rows, start=1):
            stream.write(f"{canonical}\t{index}\n")

    try:
        create_db_file(
            str(smi_path),
            str(index_path),
            "smiles",
            "Morgan",
            {
                "radius": config.fingerprint_radius,
                "fpSize": config.fingerprint_bits,
                "includeChirality": config.include_chirality,
            },
        )
    except PluginError:
        raise
    except Exception as error:
        raise PluginError(
            "could not build the FPSim2 reference index",
            code="FPSIM2_INDEX_BUILD_FAILED",
            context={"error_type": type(error).__name__, "error": str(error)[:500]},
        ) from error
    return index_path


@cache
def _engine(index_path: str, reference_set_id: str) -> Any:
    """Open the prebuilt index once per worker process.

    ``in_memory_fps`` is what makes the popcount bound cheap, and it means each
    worker holds its own copy of the fingerprints -- 64 MB for a quarter-million
    2048-bit references, which is the price of the scan being parallel at all.
    The reference-set identity is part of the memo key so a second stage in the
    same process can never be served the first one's index.
    """

    try:
        from FPSim2 import FPSim2Engine
    except ImportError as error:
        raise PluginError(
            "FPSim2 is required for indexed reference similarity",
            code="FPSIM2_BACKEND_MISSING",
            hint="Install it with: pip install FPSim2",
        ) from error
    try:
        return FPSim2Engine(index_path, in_memory_fps=True, fps_sort=True)
    except Exception as error:
        raise PluginError(
            "could not open the FPSim2 reference index",
            code="FPSIM2_INDEX_BUILD_FAILED",
            context={"error_type": type(error).__name__, "error": str(error)[:500]},
        ) from error


def _nearest(
    engine: Any,
    molecule: Any,
    reference_rows: list[tuple[str, str]],
    config: Fpsim2SimilarityConfig,
) -> tuple[float, str | None]:
    """Maximum similarity and the reference that carries it, ties resolved."""

    try:
        hits = engine.top_k(
            molecule,
            k=config.tie_break_depth,
            threshold=0.0,
            n_workers=config.n_workers,
        )
    except Exception as error:  # pragma: no cover - defensive
        raise PluginError(
            "FPSim2 similarity query failed",
            code="FPSIM2_QUERY_FAILED",
            context={"error_type": type(error).__name__, "error": str(error)[:500]},
        ) from error
    if len(hits) == 0:
        # Only reachable with an empty index, which is rejected earlier; a
        # candidate similar to nothing still comes back at zero, not absent.
        return 0.0, None
    best = max(float(hit["coeff"]) for hit in hits)
    tied = [int(hit["mol_id"]) for hit in hits if float(hit["coeff"]) == best]
    position = min(tied)
    if not 1 <= position <= len(reference_rows):  # pragma: no cover - defensive
        raise PluginError(
            "FPSim2 returned a reference position outside the reference set",
            code="FPSIM2_INDEX_INCONSISTENT",
            context={"position": position, "reference_count": len(reference_rows)},
        )
    return best, reference_rows[position - 1][0]


def _run_shard(task: ShardTask) -> ShardOutcome:
    """Query one contiguous range of parents against the prebuilt index."""

    from rdkit import Chem

    settings = dict(task.config)
    runtime = dict(settings.pop(_RUNTIME_KEY))
    config = Fpsim2SimilarityConfig.model_validate(settings)
    reference_rows = [(str(entry[0]), str(entry[1])) for entry in runtime["references"]]
    reference_set_id = str(runtime["reference_set_id"])
    method_id = str(runtime["method_id"])
    policy_id = str(runtime["policy_id"])
    engine = _engine(str(runtime["index_path"]), reference_set_id)

    input_count = 0
    output_count = 0
    reject_count = 0
    warning_count = 0
    decision_count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths["applicability"], APPLICABILITY_V1.schema, compression="zstd"
        ) as applicability_writer,
        pq.ParquetWriter(
            task.output_paths["decisions"], DECISION_V1.schema, compression="zstd"
        ) as decision_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=config.batch_size):
            retained: list[dict[str, Any]] = []
            applicability_rows: list[dict[str, Any]] = []
            decision_rows: list[dict[str, Any]] = []
            for row in batch.to_pylist():
                input_count += 1
                # Parsed here rather than handed to FPSim2 as a string: FPSim2
                # would let RDKit's None escape as a Boost argument error naming
                # a C++ signature, which is not a message anyone can act on.
                molecule = Chem.MolFromSmiles(row["parent_smiles"])
                if molecule is None:
                    raise PluginError(
                        "registered parent cannot be parsed for similarity",
                        code="REFERENCE_SIMILARITY_PARENT_INVALID",
                        context={"parent_id": str(row["parent_id"])},
                    )
                maximum, nearest_id = _nearest(engine, molecule, reference_rows, config)
                in_domain = maximum >= config.domain_similarity_threshold
                applicability_rows.append(
                    {
                        "parent_id": row["parent_id"],
                        "model_id": reference_set_id,
                        "method_id": method_id,
                        "in_domain": in_domain,
                        "raw_metric": maximum,
                        "threshold": config.domain_similarity_threshold,
                        "nearest_reference_id": nearest_id,
                        "nearest_similarity": maximum,
                    }
                )
                passes, reason, threshold = objective_outcome(config, maximum)
                rejected = (
                    not passes and config.failure_action is SimilarityFailureAction.REJECT
                )
                if not rejected:
                    retained.append(row)
                    output_count += 1
                else:
                    reject_count += 1
                if not passes and not rejected:
                    warning_count += 1
                decision_rows.extend(
                    similarity_decision_rows(
                        parent_id=row["parent_id"],
                        stage_id=task.stage_id,
                        policy_id=policy_id,
                        detail=similarity_detail(
                            config,
                            maximum=maximum,
                            nearest_id=nearest_id,
                            in_domain=in_domain,
                            threshold=threshold,
                        ),
                        reason=reason,
                        passes=passes,
                        rejected=rejected,
                    )
                )
            if retained:
                parent_writer.write_table(
                    pa.Table.from_pylist(retained, schema=PARENT_V1.schema)
                )
            applicability_writer.write_table(
                pa.Table.from_pylist(applicability_rows, schema=APPLICABILITY_V1.schema)
            )
            decision_writer.write_table(
                pa.Table.from_pylist(decision_rows, schema=DECISION_V1.schema)
            )
            decision_count += len(decision_rows)
    return ShardOutcome(
        rows_in=input_count,
        rows_out={
            "primary": output_count,
            "applicability": input_count,
            "decisions": decision_count,
        },
        metadata={"reject_count": reject_count, "warning_count": warning_count},
    )


class Fpsim2ReferenceSimilarityPlugin:
    """Maximum Tanimoto similarity to a large lead set, through a popcount index."""

    descriptor = PluginDescriptor(
        id="applicability.fpsim2_reference_similarity",
        version="0.1.0",
        kind=PluginKind.APPLICABILITY,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, APPLICABILITY_V1.id, DECISION_V1.id),
        output_ports={
            "primary": PARENT_V1.id,
            "applicability": APPLICABILITY_V1.id,
            "decisions": DECISION_V1.id,
        },
        cardinality=Cardinality.FILTER,
        # Same reasoning as the exact backend: a file-backed reference set may
        # be deliberately unpinned, so cache reuse is not safe to assume.
        determinism=Determinism.BEST_EFFORT,
        display_name="FPSim2 indexed lead similarity",
        description=(
            "Maximum Morgan/Tanimoto similarity to a large frozen reference set using "
            "FPSim2's popcount-bounded index, with the same applicability, analogue and "
            "novelty semantics as the exact backend."
        ),
    )
    config_model = Fpsim2SimilarityConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import Chem, rdBase

        try:
            config = self.config_model.model_validate(dict(request.config))
        except ValidationError as error:
            raise PluginError(
                f"invalid FPSim2 reference-similarity configuration: {error}",
                code="PLUGIN_CONFIG_INVALID",
                context={"error_count": error.error_count()},
            ) from error
        stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        context.staging_root.mkdir(parents=True, exist_ok=True)
        records, source_provenance = _load_reference_records(
            config,
            staging_root=context.staging_root,
        )
        if not records:
            raise PluginError(
                "reference set contains no molecules",
                code="REFERENCE_SET_EMPTY",
            )
        if len(records) > config.max_reference_count:
            raise PluginError(
                "reference set exceeds the configured count limit",
                code="REFERENCE_SET_TOO_MANY_RECORDS",
                hint=config.over_limit_hint,
                context={
                    "reference_count": len(records),
                    "limit": config.max_reference_count,
                },
            )

        # Parsed and canonicalised in the parent, like the exact backend: the
        # index is built from these strings and the shards resolve tie-broken
        # positions back through them, so both have to be the same list.
        reference_rows: list[tuple[str, str]] = []
        seen_ids: set[str] = set()
        for record in records:
            if record.reference_id in seen_ids:
                raise PluginError(
                    "reference identifiers must be unique",
                    code="REFERENCE_SET_DUPLICATE_ID",
                    context={"reference_id": record.reference_id},
                )
            seen_ids.add(record.reference_id)
            molecule = Chem.MolFromSmiles(record.smiles)
            if molecule is None:
                raise PluginError(
                    "reference set contains an invalid SMILES",
                    code="REFERENCE_SET_SMILES_INVALID",
                    context={"reference_id": record.reference_id},
                )
            canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
            reference_rows.append((record.reference_id, canonical))
        reference_rows.sort(key=lambda row: row[0])

        # Identical to the exact backend's, and deliberately so: the same
        # reference set has the same identity whichever engine reads it.
        reference_set_id = "reference-set:sha256:" + canonical_sha256(
            {"records": [(row[0], row[1]) for row in reference_rows]}
        )
        # The method id is *not* identical, and must not be: single precision
        # and a bounded tie window are properties of how the number was reached,
        # so a value produced here is not interchangeable with one produced
        # there even when the two agree.
        method_id = "reference-similarity:sha256:" + canonical_sha256(
            {
                "backend": "fpsim2",
                "backend_version": _fpsim2_version(),
                "rdkit_version": rdBase.rdkitVersion,
                "fingerprint": "morgan",
                "radius": config.fingerprint_radius,
                "bits": config.fingerprint_bits,
                "include_chirality": config.include_chirality,
                "tie_break_depth": config.tie_break_depth,
                "coefficient_precision": "float32",
                "reference_set_id": reference_set_id,
                "domain_similarity_threshold": config.domain_similarity_threshold,
            }
        )
        policy_id = "reference-similarity-policy:sha256:" + canonical_sha256(
            {
                "objective": config.objective.value,
                "analogue_min_similarity": config.analogue_min_similarity,
                "novelty_max_similarity": config.novelty_max_similarity,
                "failure_action": config.failure_action.value,
                "method_id": method_id,
            }
        )

        # The budget is settled from the Parquet footers before a molecule is
        # read.  It stays a worst case -- the popcount bound skips most of the
        # reference set -- but a guardrail that only fires halfway through the
        # run is not a guardrail.
        total_rows = sum(
            pq.ParquetFile(path).metadata.num_rows
            for path in discover_contract_files(stage_input, PARENT_V1)
        )
        comparisons = total_rows * len(reference_rows)
        if comparisons > config.max_total_comparisons:
            raise PluginError(
                "reference-similarity comparison budget exceeded",
                code="REFERENCE_SIMILARITY_BUDGET_EXCEEDED",
                hint=(
                    "Reduce the candidate or reference count, or raise "
                    "max_total_comparisons."
                ),
                context={
                    "comparison_count": comparisons,
                    "limit": config.max_total_comparisons,
                },
            )

        index_path = _build_index(context.staging_root, reference_rows, config)
        try:
            result = shard_stage(
                worker=_run_shard,
                stage_input=stage_input,
                contract=PARENT_V1,
                output_paths={
                    "primary": _PARENT_PATH.as_posix(),
                    "applicability": _APPLICABILITY_PATH.as_posix(),
                    "decisions": _DECISION_PATH.as_posix(),
                },
                context=context,
                stage_id=request.stage_id,
                config={
                    **config.model_dump(mode="json"),
                    _RUNTIME_KEY: {
                        "references": [[row[0], row[1]] for row in reference_rows],
                        "reference_set_id": reference_set_id,
                        "method_id": method_id,
                        "policy_id": policy_id,
                        "index_path": str(index_path),
                    },
                },
            )
        finally:
            # The index is reference-set-derived scratch, and it is big.  It has
            # to go whether the stage succeeded or failed, and it cannot survive
            # into the committed artifact.
            for name in (_REFERENCE_SMI, _REFERENCE_INDEX):
                (context.staging_root / name).unlink(missing_ok=True)

        input_count = result.rows_in
        if input_count == 0:
            raise PluginError(
                "reference-similarity input contains no parents",
                code="REFERENCE_SIMILARITY_EMPTY_INPUT",
            )
        output_count = result.rows_out.get("primary", 0)
        reject_count = result.total("reject_count")
        warning_count = result.total("warning_count")

        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id, result.file_paths["primary"], {"row_count": output_count}
                ),
                "applicability": PendingOutput(
                    APPLICABILITY_V1.id,
                    result.file_paths["applicability"],
                    {
                        "row_count": input_count,
                        "method_id": method_id,
                        "reference_set_id": reference_set_id,
                    },
                ),
                "decisions": PendingOutput(
                    DECISION_V1.id, result.file_paths["decisions"], {"policy_id": policy_id}
                ),
            },
            metadata={
                "input_count": input_count,
                "output_count": output_count,
                "reject_count": reject_count,
                "warning_count": warning_count,
                "reference_count": len(reference_rows),
                "comparison_count": comparisons,
                "reference_set_id": reference_set_id,
                "reference_source": source_provenance,
                "method_id": method_id,
                "policy_id": policy_id,
                "objective": config.objective.value,
                "backend": "fpsim2",
                "backend_version": _fpsim2_version(),
                "cacheable": False,
                **result.response_metadata(),
            },
        )


def _fpsim2_version() -> str:
    try:
        from FPSim2 import __version__

        return str(__version__)
    except ImportError:  # pragma: no cover - only when the package is absent
        return "unknown"


__all__ = ["Fpsim2ReferenceSimilarityPlugin", "Fpsim2SimilarityConfig"]
