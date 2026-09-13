"""Shortlist export-record plugins.

Contract outputs remain typed Parquet so the runner can validate and publish
them atomically.  A later materialization command writes the record stream to
``.smi`` or ``.sdf`` without rerunning chemistry.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, ValidationError, field_validator

from molcascade.chemistry.datasets import iter_contract_batches, require_single_input
from molcascade.config.canonical import canonical_json, canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, SHORTLIST_EXPORT_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import PendingOutput, StageContext, StageRequest, StageResponse
from molcascade.plugins.manifest import (
    Cardinality,
    Determinism,
    PluginDescriptor,
    PluginKind,
)

_PARENT_PATH = Path("datasets/parents/part-00000.parquet")
_EXPORT_PATH = Path("datasets/export_records/part-00000.parquet")
_WINDOWS_RESERVED = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _portable_filename(value: str) -> str:
    if (
        not value
        or value != PurePath(value).name
        or value.endswith((" ", "."))
        or any(character in value for character in '<>:"/\\|?*\x00')
        or value.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED
    ):
        raise ValueError("filename must be one portable basename")
    return value


class SmilesExportConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=16_384, ge=1, le=250_000)
    filename: str = "shortlist.smi"
    include_header: bool = True

    _validate_filename = field_validator("filename")(_portable_filename)


class SDFExportConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    batch_size: int = Field(default=8_192, ge=1, le=100_000)
    filename: str = "shortlist.sdf"
    force_v3000: bool = False

    _validate_filename = field_validator("filename")(_portable_filename)


def _config(model: type[StrictFrozenModel], request: StageRequest) -> Any:
    try:
        return model.model_validate(dict(request.config))
    except ValidationError as error:
        raise PluginError(
            f"invalid shortlist export configuration: {error}",
            code="PLUGIN_CONFIG_INVALID",
            context={"error_count": error.error_count()},
        ) from error


def _execute_export(
    *,
    request: StageRequest,
    context: StageContext,
    config: SmilesExportConfig | SDFExportConfig,
    backend: str,
    backend_version: str,
    record_format: str,
) -> StageResponse:
    """Write the shortlist and describe what it is, without over-claiming.

    Note what is deliberately absent from the returned metadata: whether the run
    included docking.  This stage sees one input port and its own config; it has
    no view of the tiers above it, so any answer it gave would be a guess that
    happened to be right for the pipelines shipped today.  The pipeline records
    it once, computed from the stages actually compiled -- see
    ``cascade/lower.py`` -- and that is the copy a methods section should quote.
    """

    from rdkit import Chem

    stage_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
    context.staging_root.mkdir(parents=True, exist_ok=True)
    for relative in (_PARENT_PATH, _EXPORT_PATH):
        destination = context.staging_root / relative
        if destination.exists() or destination.is_symlink():
            raise PluginError(
                f"export output already exists: {relative.as_posix()}",
                code="PLUGIN_STAGING_NOT_EMPTY",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
    export_spec_id = "export-spec:sha256:" + canonical_sha256(
        {
            "backend": backend,
            "backend_version": backend_version,
            "record_format": record_format,
            "config": config.model_dump(mode="json"),
        }
    )
    parent_destination = context.staging_root / _PARENT_PATH
    export_destination = context.staging_root / _EXPORT_PATH
    count = 0
    try:
        with (
            pq.ParquetWriter(
                parent_destination, PARENT_V1.schema, compression="zstd"
            ) as parents,
            pq.ParquetWriter(
                export_destination,
                SHORTLIST_EXPORT_V1.schema,
                compression="zstd",
            ) as records,
        ):
            for batch in iter_contract_batches(
                stage_input,
                PARENT_V1,
                batch_size=config.batch_size,
            ):
                exported: list[dict[str, Any]] = []
                for row in batch.select(["parent_id", "parent_smiles"]).to_pylist():
                    parent_id = row["parent_id"]
                    parent_smiles = row["parent_smiles"]
                    warnings: list[str] = []
                    if record_format == "SMILES_TSV":
                        structure_record = f"{parent_smiles}\t{parent_id}"
                    else:
                        molecule = Chem.MolFromSmiles(parent_smiles)
                        if molecule is None:
                            raise PluginError(
                                "registered parent cannot be rendered for SDF export",
                                code="EXPORT_PARENT_INVALID",
                                context={"parent_id": parent_id},
                            )
                        molecule.SetProp("_Name", parent_id)
                        assert isinstance(config, SDFExportConfig)
                        try:
                            structure_record = Chem.MolToMolBlock(
                                molecule,
                                forceV3000=config.force_v3000,
                            ).rstrip("\r\n")
                        except (ValueError, RuntimeError) as error:
                            raise PluginError(
                                "RDKit could not render an SDF mol block",
                                code="EXPORT_RENDER_FAILED",
                                context={
                                    "parent_id": parent_id,
                                    "error_type": type(error).__name__,
                                },
                            ) from error
                    exported.append(
                        {
                            "parent_id": parent_id,
                            "export_spec_id": export_spec_id,
                            "record_format": record_format,
                            "record_name": parent_id,
                            "parent_smiles": parent_smiles,
                            "structure_record": structure_record,
                            "warning_codes_json": canonical_json(warnings) if warnings else None,
                        }
                    )
                parents.write_batch(batch)
                records.write_table(
                    pa.Table.from_pylist(exported, schema=SHORTLIST_EXPORT_V1.schema)
                )
                count += batch.num_rows
        if count == 0:
            raise PluginError("shortlist export input is empty", code="EXPORT_EMPTY_INPUT")
    except BaseException:
        parent_destination.unlink(missing_ok=True)
        export_destination.unlink(missing_ok=True)
        raise
    return StageResponse(
        outputs={
            "primary": PendingOutput(
                PARENT_V1.id,
                (_PARENT_PATH.as_posix(),),
                {"row_count": count},
            ),
            "export_records": PendingOutput(
                SHORTLIST_EXPORT_V1.id,
                (_EXPORT_PATH.as_posix(),),
                {
                    "row_count": count,
                    "export_spec_id": export_spec_id,
                    "record_format": record_format,
                    "suggested_filename": config.filename,
                },
            ),
        },
        metadata={
            "input_count": count,
            "output_count": count,
            "export_spec_id": export_spec_id,
            "record_format": record_format,
            "suggested_filename": config.filename,
            "backend": backend,
            "backend_version": backend_version,
        },
    )


class NativeSmilesShortlistPlugin:
    descriptor = PluginDescriptor(
        id="export.native_smiles_shortlist",
        version="0.1.0",
        kind=PluginKind.EXPORTER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, SHORTLIST_EXPORT_V1.id),
        output_ports={"primary": PARENT_V1.id, "export_records": SHORTLIST_EXPORT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="SMILES shortlist handoff",
        description="Typed SMILES/parent-ID records for a local shortlist handoff.",
    )
    config_model = SmilesExportConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        config = _config(self.config_model, request)
        return _execute_export(
            request=request,
            context=context,
            config=config,
            backend="molcascade.native_smiles",
            backend_version="1",
            record_format="SMILES_TSV",
        )


class RDKitSDFShortlistPlugin:
    descriptor = PluginDescriptor(
        id="export.rdkit_sdf_shortlist",
        version="0.1.0",
        kind=PluginKind.EXPORTER,
        inputs=(PARENT_V1.id,),
        outputs=(PARENT_V1.id, SHORTLIST_EXPORT_V1.id),
        output_ports={"primary": PARENT_V1.id, "export_records": SHORTLIST_EXPORT_V1.id},
        cardinality=Cardinality.ONE_TO_ONE,
        determinism=Determinism.DETERMINISTIC,
        display_name="RDKit SDF shortlist handoff",
        description="RDKit-rendered mol blocks in a typed, materializable shortlist contract.",
    )
    config_model = SDFExportConfig

    def execute(self, request: StageRequest, context: StageContext) -> StageResponse:
        from rdkit import rdBase

        config = _config(self.config_model, request)
        return _execute_export(
            request=request,
            context=context,
            config=config,
            backend="rdkit.sdf",
            backend_version=rdBase.rdkitVersion,
            record_format="SDF_MOLBLOCK",
        )


__all__ = [
    "NativeSmilesShortlistPlugin",
    "RDKitSDFShortlistPlugin",
    "SDFExportConfig",
    "SmilesExportConfig",
]
