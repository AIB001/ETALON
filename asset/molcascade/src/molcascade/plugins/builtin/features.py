"""Local, replaceable property and fingerprint feature plugins."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from collections.abc import Callable
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.chemistry.datasets import require_single_input
from molcascade.chemistry.sharding import shard_stage
from molcascade.config.canonical import canonical_json, canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import FINGERPRINT_V1, PARENT_V1, PROPERTY_V1
from molcascade.errors import PluginError
from molcascade.parallel import ShardedResult, ShardOutcome, ShardTask, iter_shard_batches
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import Cardinality, Determinism, PluginDescriptor, PluginKind

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_PROPERTY_PATH = Path("datasets/properties/part-00000.parquet")
_FINGERPRINT_PATH = Path("datasets/fingerprints/part-00000.parquet")
_FEATURE_IMPLEMENTATION_VERSION = 1


class PropertyPluginConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    include_sa_score: bool = True


class OpenBabelPropertyPluginConfig(PropertyPluginConfig):
    """Open Babel is GPL-2.0-only; selection must be explicit in deployment config."""

    allow_copyleft_backend: bool = False


class FingerprintKind(StrEnum):
    MORGAN = "morgan"
    ATOM_PAIR = "atom_pair"
    RDKIT_PATH = "rdkit_path"
    MACCS = "maccs"


class RDKitFingerprintPluginConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    kind: FingerprintKind = FingerprintKind.MORGAN
    bit_length: int = Field(default=2048, ge=128, le=65_536)
    radius: int = Field(default=2, ge=1, le=6)
    include_chirality: bool = True

    @field_validator("kind", mode="before")
    @classmethod
    def _parse_kind(cls, value: Any) -> Any:
        return FingerprintKind(value) if isinstance(value, str) else value


def _validated_config(model: type[StrictFrozenModel], request: StageRequest) -> Any:
    try:
        return model.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid feature configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _finite_or_none(value: Any, warnings: list[str], name: str) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        warnings.append(f"PROPERTY_INVALID:{name}")
        return None
    if not math.isfinite(result):
        warnings.append(f"PROPERTY_NON_FINITE:{name}")
        return None
    return result


def _int_or_none(value: Any, warnings: list[str], name: str) -> int | None:
    result = _finite_or_none(value, warnings, name)
    return int(result) if result is not None else None


@cache
def _rdkit_sa_score_backend() -> tuple[Callable[[Any], float] | None, dict[str, str]]:
    """Load RDKit's installed Contrib SA Score without assuming package layout.

    Cached per process: it reads and digests a gzipped weight table, which a
    worker would otherwise repeat for every shard it is handed.
    """

    try:
        from rdkit.Contrib.SA_Score import sascorer

        source = Path(sascorer.__file__ or "")
    except ImportError:
        try:
            from rdkit import RDConfig

            source = Path(RDConfig.RDContribDir) / "SA_Score" / "sascorer.py"
            spec = importlib.util.spec_from_file_location(
                "_molcascade_rdkit_sascorer",
                source,
            )
            if spec is None or spec.loader is None:
                return None, {}
            sascorer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sascorer)
        except (ImportError, OSError, AttributeError):
            return None, {}
    weights = source.with_name("fpscores.pkl.gz")
    if not source.is_file() or not weights.is_file():
        return None, {}
    provenance = {
        "sa_score_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "sa_score_weights_sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
    }
    return sascorer.calculateScore, provenance


def _write_shard(
    task: ShardTask,
    *,
    derived_port: str,
    derived_schema: pa.Schema,
    batch_size: int,
    calculate: Callable[[pa.RecordBatch], list[dict[str, object]]],
) -> ShardOutcome:
    """Write one shard's parent passthrough and its derived table side by side.

    The cardinality check stays here rather than in each backend: a featurizer
    is declared ``ONE_TO_ONE``, and a backend that silently dropped a molecule
    would misalign every positional join downstream.
    """

    count = 0
    with (
        pq.ParquetWriter(
            task.output_paths["primary"], PARENT_V1.schema, compression="zstd"
        ) as parent_writer,
        pq.ParquetWriter(
            task.output_paths[derived_port], derived_schema, compression="zstd"
        ) as derived_writer,
    ):
        for batch in iter_shard_batches(task, batch_size=batch_size):
            rows = calculate(batch)
            if len(rows) != batch.num_rows:
                raise PluginError(
                    "feature backend changed entity cardinality",
                    code="FEATURE_COUNT_MISMATCH",
                )
            parent_writer.write_batch(batch)
            derived_writer.write_table(pa.Table.from_pylist(rows, schema=derived_schema))
            count += batch.num_rows
    return ShardOutcome(
        rows_in=count,
        rows_out={"primary": count, derived_port: count},
    )


def _shard_features(
    *,
    worker: Callable[[ShardTask], ShardOutcome],
    request: StageRequest,
    context: StageContext,
    config: StrictFrozenModel,
    derived_port: str,
    derived_path: Path,
) -> ShardedResult:
    stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
    result = shard_stage(
        worker=worker,
        stage_input=stage_input,
        contract=PARENT_V1,
        output_paths={
            "primary": _PARENT_PATH.as_posix(),
            derived_port: derived_path.as_posix(),
        },
        context=context,
        stage_id=request.stage_id,
        config=config.model_dump(mode="json"),
    )
    if result.rows_in == 0:
        raise PluginError("feature input contains no parents", code="FEATURE_EMPTY_INPUT")
    return result


def _property_calculator_id(
    config: PropertyPluginConfig,
    backend_version: str,
    sa_provenance: dict[str, str],
) -> str:
    return "property:sha256:" + canonical_sha256(
        {
            "backend": "rdkit",
            "backend_version": backend_version,
            "implementation_version": _FEATURE_IMPLEMENTATION_VERSION,
            "include_sa_score": config.include_sa_score,
            "sa_score_assets": sa_provenance,
            "definitions": {
                "mw": "Descriptors.MolWt",
                "clogp": "Crippen.MolLogP",
                "tpsa": "CalcTPSA",
                "hbd": "CalcNumHBD",
                "hba": "CalcNumHBA",
                "rotatable_bonds": "CalcNumRotatableBonds.strict",
            },
        }
    )


def _run_property_shard(task: ShardTask) -> ShardOutcome:
    """Compute RDKit properties for one contiguous range of parents.

    Module-level and closing over nothing, because ``spawn`` re-imports this
    module in a fresh interpreter and looks the function up by name.
    """

    from rdkit import Chem, rdBase
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors

    config = PropertyPluginConfig.model_validate(dict(task.config))
    sa_calculator: Callable[[Any], float] | None = None
    sa_provenance: dict[str, str] = {}
    if config.include_sa_score:
        sa_calculator, sa_provenance = _rdkit_sa_score_backend()
    calculator_id = _property_calculator_id(config, rdBase.rdkitVersion, sa_provenance)

    def calculate(batch: pa.RecordBatch) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
            molecule = Chem.MolFromSmiles(row["parent_smiles"])
            if molecule is None:
                raise PluginError(
                    "registered parent cannot be parsed by its identity toolkit",
                    code="FEATURE_PARENT_INVALID",
                    context={"parent_id": row["parent_id"]},
                )
            warnings: list[str] = []
            sa_score: float | None = None
            if config.include_sa_score:
                if sa_calculator is None:
                    warnings.append("BACKEND_ASSET_UNAVAILABLE:sa_score")
                else:
                    try:
                        sa_score = _finite_or_none(
                            sa_calculator(molecule), warnings, "sa_score"
                        )
                    except (ValueError, RuntimeError, KeyError) as error:
                        warnings.append(f"PROPERTY_FAILED:sa_score:{type(error).__name__}")
            result.append(
                {
                    "parent_id": row["parent_id"],
                    "calculator_id": calculator_id,
                    "mw": _finite_or_none(Descriptors.MolWt(molecule), warnings, "mw"),
                    "clogp": _finite_or_none(Crippen.MolLogP(molecule), warnings, "clogp"),
                    "tpsa": _finite_or_none(
                        rdMolDescriptors.CalcTPSA(molecule), warnings, "tpsa"
                    ),
                    "hbd": int(rdMolDescriptors.CalcNumHBD(molecule)),
                    "hba": int(rdMolDescriptors.CalcNumHBA(molecule)),
                    "rotatable_bonds": int(Lipinski.NumRotatableBonds(molecule)),
                    "ring_count": int(rdMolDescriptors.CalcNumRings(molecule)),
                    "heavy_atom_count": int(molecule.GetNumHeavyAtoms()),
                    "formal_charge": int(Chem.GetFormalCharge(molecule)),
                    "fraction_csp3": _finite_or_none(
                        rdMolDescriptors.CalcFractionCSP3(molecule),
                        warnings,
                        "fraction_csp3",
                    ),
                    "sa_score": sa_score,
                    "warning_codes_json": canonical_json(sorted(set(warnings))) or None,
                }
            )
        return result

    return _write_shard(
        task,
        derived_port="properties",
        derived_schema=PROPERTY_V1.schema,
        batch_size=config.batch_size,
        calculate=calculate,
    )


class RDKitPropertyPlugin:
    descriptor = PluginDescriptor(
        id="features.rdkit_properties",
        version="0.1.0",
        kind=PluginKind.FEATURIZER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, PROPERTY_V1.id),
        output_ports={"primary": PARENT_V1.id, "properties": PROPERTY_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit physicochemical properties",
        description="Versioned core properties and optional RDKit SA score.",
    )
    config_model = PropertyPluginConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(self.config_model, request)
        sa_provenance: dict[str, str] = {}
        if config.include_sa_score:
            _, sa_provenance = _rdkit_sa_score_backend()
        calculator_id = _property_calculator_id(
            config, rdBase.rdkitVersion, sa_provenance
        )
        result = _shard_features(
            worker=_run_property_shard,
            request=request,
            context=context,
            config=config,
            derived_port="properties",
            derived_path=_PROPERTY_PATH,
        )
        count = result.rows_in
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id, result.file_paths["primary"], {"row_count": count}
                ),
                "properties": PendingOutput(
                    PROPERTY_V1.id,
                    result.file_paths["properties"],
                    {"row_count": count, "calculator_id": calculator_id},
                ),
            },
            metadata={
                "input_count": count,
                "output_count": count,
                "calculator_id": calculator_id,
                "backend": "rdkit",
                "backend_version": rdBase.rdkitVersion,
                **sa_provenance,
                **result.response_metadata(),
            },
        )


def _openbabel_calculator_id(version: str) -> str:
    return "property:sha256:" + canonical_sha256(
        {
            "backend": "openbabel",
            "backend_version": version,
            "implementation_version": _FEATURE_IMPLEMENTATION_VERSION,
            "descriptor_names": ["MW", "logP", "TPSA", "HBD", "HBA1", "rotors"],
        }
    )


def _require_openbabel(config: OpenBabelPropertyPluginConfig) -> tuple[Any, str]:
    """Refuse the backend, then import it -- in that order.

    The licence check must not be reachable only when the bindings happen to be
    installed, so it comes first and is repeated in every worker.
    """

    if not config.allow_copyleft_backend:
        raise PluginError(
            "Open Babel is GPL-2.0-only and is blocked by the default deployment policy",
            code="BACKEND_LICENSE_BLOCKED",
            hint="Enable this reviewed backend explicitly in deployment policy.",
            context={"backend": "openbabel", "license": "GPL-2.0-only"},
        )
    try:
        import openbabel
        from openbabel import pybel
    except ImportError as error:
        raise PluginError(
            "Open Babel Python bindings are not installed",
            code="BACKEND_UNAVAILABLE",
            context={"backend": "openbabel"},
        ) from error
    return pybel, str(getattr(openbabel, "__version__", "unknown"))


def _run_openbabel_shard(task: ShardTask) -> ShardOutcome:
    """Compute Open Babel descriptors for one contiguous range of parents."""

    config = OpenBabelPropertyPluginConfig.model_validate(dict(task.config))
    pybel, version = _require_openbabel(config)
    calculator_id = _openbabel_calculator_id(version)

    def calculate(batch: pa.RecordBatch) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
            try:
                molecule = pybel.readstring("smi", row["parent_smiles"])
                descriptors = molecule.calcdesc(
                    ["MW", "logP", "TPSA", "HBD", "HBA1", "rotors"]
                )
            except Exception as error:
                raise PluginError(
                    "Open Babel could not calculate a registered parent",
                    code="FEATURE_PARENT_INVALID",
                    context={
                        "parent_id": row["parent_id"],
                        "error_type": type(error).__name__,
                    },
                ) from error
            warnings = [
                "PROPERTY_NOT_SUPPORTED:fraction_csp3",
                "PROPERTY_NOT_SUPPORTED:sa_score",
            ]
            carbon_count = 0
            sp3_carbon_count = 0
            for atom in molecule.atoms:
                if atom.atomicnum == 6:
                    carbon_count += 1
                    if atom.OBAtom.GetHyb() == 3:
                        sp3_carbon_count += 1
            fraction_csp3 = sp3_carbon_count / carbon_count if carbon_count else 0.0
            result.append(
                {
                    "parent_id": row["parent_id"],
                    "calculator_id": calculator_id,
                    "mw": _finite_or_none(descriptors.get("MW"), warnings, "mw"),
                    "clogp": _finite_or_none(descriptors.get("logP"), warnings, "clogp"),
                    "tpsa": _finite_or_none(descriptors.get("TPSA"), warnings, "tpsa"),
                    "hbd": _int_or_none(descriptors.get("HBD"), warnings, "hbd"),
                    "hba": _int_or_none(descriptors.get("HBA1"), warnings, "hba"),
                    "rotatable_bonds": _int_or_none(
                        descriptors.get("rotors"), warnings, "rotatable_bonds"
                    ),
                    "ring_count": len(molecule.sssr),
                    "heavy_atom_count": int(molecule.OBMol.NumHvyAtoms()),
                    "formal_charge": int(molecule.charge),
                    "fraction_csp3": fraction_csp3,
                    "sa_score": None,
                    "warning_codes_json": canonical_json(sorted(set(warnings))),
                }
            )
        return result

    return _write_shard(
        task,
        derived_port="properties",
        derived_schema=PROPERTY_V1.schema,
        batch_size=config.batch_size,
        calculate=calculate,
    )


class OpenBabelPropertyPlugin:
    descriptor = PluginDescriptor(
        id="features.openbabel_properties",
        version="0.1.0",
        kind=PluginKind.FEATURIZER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, PROPERTY_V1.id),
        output_ports={"primary": PARENT_V1.id, "properties": PROPERTY_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="Open Babel physicochemical properties",
        description="Optional GPL-2.0 descriptor adapter with explicit provenance.",
    )
    config_model = OpenBabelPropertyPluginConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _validated_config(self.config_model, request)
        _, version = _require_openbabel(config)
        calculator_id = _openbabel_calculator_id(version)
        result = _shard_features(
            worker=_run_openbabel_shard,
            request=request,
            context=context,
            config=config,
            derived_port="properties",
            derived_path=_PROPERTY_PATH,
        )
        count = result.rows_in
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id, result.file_paths["primary"], {"row_count": count}
                ),
                "properties": PendingOutput(
                    PROPERTY_V1.id,
                    result.file_paths["properties"],
                    {"row_count": count, "calculator_id": calculator_id},
                ),
            },
            metadata={
                "input_count": count,
                "output_count": count,
                "calculator_id": calculator_id,
                "backend": "openbabel",
                "backend_version": version,
                "license": "GPL-2.0-only",
                **result.response_metadata(),
            },
        )


def _fingerprint_spec(
    config: RDKitFingerprintPluginConfig, backend_version: str
) -> tuple[int, dict[str, Any], str]:
    """Resolve the bit length the toolkit will actually produce, and name it."""

    if config.kind is FingerprintKind.MACCS and config.bit_length != 167:
        # MACCS has a fixed toolkit definition; silently accepting the generic
        # default would make the config claim a false bit length.
        raise PluginError(
            "MACCS uses a fixed 167-bit definition; set bit_length to 167",
            code="PLUGIN_CONFIG_INVALID",
        )
    actual_bit_length = 167 if config.kind is FingerprintKind.MACCS else config.bit_length
    payload = {
        "backend": "rdkit",
        "backend_version": backend_version,
        "implementation_version": _FEATURE_IMPLEMENTATION_VERSION,
        "kind": config.kind.value,
        "bit_length": actual_bit_length,
        "radius": config.radius if config.kind is FingerprintKind.MORGAN else None,
        "include_chirality": config.include_chirality,
    }
    return actual_bit_length, payload, "fingerprint:sha256:" + canonical_sha256(payload)


def _run_fingerprint_shard(task: ShardTask) -> ShardOutcome:
    """Fingerprint one contiguous range of parents."""

    from rdkit import Chem, DataStructs, rdBase
    from rdkit.Chem import MACCSkeys, rdFingerprintGenerator

    config = RDKitFingerprintPluginConfig.model_validate(dict(task.config))
    actual_bit_length, _, fingerprint_spec_id = _fingerprint_spec(
        config, rdBase.rdkitVersion
    )
    # Declared up front because the four branches bind it from three different
    # places -- two generator methods, a plain function and a module-level one --
    # and RDKit is imported lazily, so there is no concrete type to name here.
    fingerprint: Callable[[Any], Any]
    if config.kind is FingerprintKind.MORGAN:
        generator = rdFingerprintGenerator.GetMorganGenerator(
            radius=config.radius,
            fpSize=actual_bit_length,
            includeChirality=config.include_chirality,
        )
        fingerprint = generator.GetFingerprint
    elif config.kind is FingerprintKind.ATOM_PAIR:
        generator = rdFingerprintGenerator.GetAtomPairGenerator(
            fpSize=actual_bit_length,
            includeChirality=config.include_chirality,
        )
        fingerprint = generator.GetFingerprint
    elif config.kind is FingerprintKind.RDKIT_PATH:
        # The one kind with no generator object in rdFingerprintGenerator, so it
        # is wrapped to match the call shape of the other branches.
        def _rdkit_path_fingerprint(mol: Any) -> Any:
            return Chem.RDKFingerprint(mol, fpSize=actual_bit_length, useHs=False)

        fingerprint = _rdkit_path_fingerprint
    else:
        fingerprint = MACCSkeys.GenMACCSKeys

    def calculate(batch: pa.RecordBatch) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
            molecule = Chem.MolFromSmiles(row["parent_smiles"])
            if molecule is None:
                raise PluginError(
                    "registered parent cannot be parsed for fingerprinting",
                    code="FEATURE_PARENT_INVALID",
                    context={"parent_id": row["parent_id"]},
                )
            vector = fingerprint(molecule)
            if vector.GetNumBits() != actual_bit_length:
                raise PluginError(
                    "fingerprint backend returned an unexpected bit length",
                    code="FEATURE_BACKEND_CONTRACT_FAILED",
                    context={
                        "expected": actual_bit_length,
                        "actual": vector.GetNumBits(),
                    },
                )
            rows.append(
                {
                    "parent_id": row["parent_id"],
                    "fingerprint_spec_id": fingerprint_spec_id,
                    "bit_length": actual_bit_length,
                    "popcount": int(vector.GetNumOnBits()),
                    "packed_bits": bytes(DataStructs.BitVectToBinaryText(vector)),
                }
            )
        return rows

    return _write_shard(
        task,
        derived_port="fingerprints",
        derived_schema=FINGERPRINT_V1.schema,
        batch_size=config.batch_size,
        calculate=calculate,
    )


class RDKitFingerprintPlugin:
    descriptor = PluginDescriptor(
        id="features.rdkit_fingerprint",
        version="0.1.0",
        kind=PluginKind.FEATURIZER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, FINGERPRINT_V1.id),
        output_ports={"primary": PARENT_V1.id, "fingerprints": FINGERPRINT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit packed fingerprints",
        description="Morgan, AtomPair, RDKit path, or MACCS fingerprints as packed bytes.",
    )
    config_model = RDKitFingerprintPluginConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _validated_config(self.config_model, request)
        # Validated in the parent so a MACCS/bit_length contradiction is a config
        # error before a single shard is planned, not a worker crash inside one.
        _, spec_payload, fingerprint_spec_id = _fingerprint_spec(
            config, rdBase.rdkitVersion
        )
        result = _shard_features(
            worker=_run_fingerprint_shard,
            request=request,
            context=context,
            config=config,
            derived_port="fingerprints",
            derived_path=_FINGERPRINT_PATH,
        )
        count = result.rows_in
        return StageResponse(
            outputs={
                "primary": PendingOutput(
                    PARENT_V1.id, result.file_paths["primary"], {"row_count": count}
                ),
                "fingerprints": PendingOutput(
                    FINGERPRINT_V1.id,
                    result.file_paths["fingerprints"],
                    {"row_count": count, "fingerprint_spec_id": fingerprint_spec_id},
                ),
            },
            metadata={
                "input_count": count,
                "output_count": count,
                "fingerprint_spec_id": fingerprint_spec_id,
                "fingerprint_spec": json.loads(canonical_json(spec_payload)),
                "backend_version": rdBase.rdkitVersion,
                **result.response_metadata(),
            },
        )


__all__ = [
    "FingerprintKind",
    "OpenBabelPropertyPlugin",
    "OpenBabelPropertyPluginConfig",
    "PropertyPluginConfig",
    "RDKitFingerprintPlugin",
    "RDKitFingerprintPluginConfig",
    "RDKitPropertyPlugin",
]
