"""Reuse a verified registration bundle, including its rejected source records."""
from pathlib import Path
import shutil
import csv

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from pydantic import Field, model_validator
from molcascade.artifacts.hashing import sha256_file
from molcascade.artifacts.store import LocalArtifactStore
from molcascade.chemistry.identity import rdkit_identity_metadata
from molcascade.chemistry.policies import IdentityPolicy
from molcascade.config.models import StrictFrozenModel
from molcascade.contracts import PARENT_V1, PARENT_SOURCE_MAP_V1, DECISION_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import PendingOutput, StageResponse
from molcascade.plugins.manifest import PluginDescriptor, PluginKind, Cardinality, Determinism


class RegisteredSourceConfig(StrictFrozenModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    path: str  # Existing MolCascade artifact-store/workspace root, never a CSV.
    artifact_id: str
    identity_policy: IdentityPolicy = Field(default_factory=IdentityPolicy)
    batch_size: int = Field(default=65536, ge=1)  # Shared library reader setting.
    selection_path: str | None = None  # CSV/TSV/Parquet with parent_id; order remains bundle order.
    selection_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def pinned_selection(self):
        if (self.selection_path is None) != (self.selection_sha256 is None):
            raise ValueError('selection_path and selection_sha256 must be supplied together')
        return self


def _selection(config):
    if config.selection_path is None:
        return None
    path = Path(config.selection_path)
    if sha256_file(path) != config.selection_sha256:
        raise PluginError('parent selection changed', code='REGISTERED_SELECTION_CHANGED')
    if path.suffix.lower() == '.parquet':
        values = pq.read_table(path, columns=['parent_id']).column('parent_id').to_pylist()
    else:
        with path.open(encoding='utf-8-sig', newline='') as handle:
            values = [row['parent_id'] for row in csv.DictReader(
                handle, delimiter='\t' if path.suffix.lower() in {'.tsv', '.tab'} else ',')]
    if not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise PluginError('parent selection must contain nonempty IDs', code='REGISTERED_SELECTION_INVALID')
    if len(set(values)) != len(values):
        raise PluginError('parent selection contains duplicate IDs', code='REGISTERED_SELECTION_INVALID')
    if sha256_file(path) != config.selection_sha256:
        raise PluginError('parent selection changed while reading', code='REGISTERED_SELECTION_CHANGED')
    return set(values)


class RegisteredParentSourcePlugin:
    config_model = RegisteredSourceConfig
    descriptor = PluginDescriptor(
        id='source.registered_parents', version='0.1.0', kind=PluginKind.SOURCE,
        inputs=(), outputs=(PARENT_V1.id, PARENT_SOURCE_MAP_V1.id, DECISION_V1.id),
        output_ports={'primary': PARENT_V1.id, 'mapping': PARENT_SOURCE_MAP_V1.id, 'decisions': DECISION_V1.id},
        cardinality=Cardinality.MANY_TO_ONE, determinism=Determinism.DETERMINISTIC,
        display_name='Registered parent bundle',
        description='Reuse identity-compatible, verified parents with source mappings and decisions.',
    )

    def execute(self, request, context):
        config = self.config_model.model_validate(dict(request.config))
        store = LocalArtifactStore(config.path, create=False)
        manifest = store.verify(config.artifact_id)
        metadata = manifest.metadata.get('response_metadata', {})
        expected = rdkit_identity_metadata(config.identity_policy)
        if manifest.producer.plugin_id not in {'chemistry.rdkit_standardize', 'source.registered_parents'}:
            raise PluginError('input is not a registration bundle', code='REGISTERED_SOURCE_PRODUCER_MISMATCH')
        if any(metadata.get(k) != v for k, v in expected.items()):
            raise PluginError('registered input uses a different identity policy or toolkit; use raw input to re-standardize',
                              code='REGISTERED_SOURCE_IDENTITY_MISMATCH')
        root = store.artifact_directory(config.artifact_id)
        selected = _selection(config)
        wanted = pa.array(sorted(selected), type=pa.string()) if selected is not None else None
        seen = set()
        counts = {}
        outputs = {}
        for port, contract in self.descriptor.output_ports.items():
            ref = manifest.dataset_ref(port)
            if ref.contract_id != contract:
                raise PluginError('registration bundle has an incompatible port', code='REGISTERED_SOURCE_CONTRACT_MISMATCH')
            for relative in ref.file_paths:
                target = context.staging_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if selected is not None and port in {'primary', 'mapping'}:
                    source = pq.ParquetFile(root / relative)
                    with pq.ParquetWriter(target, source.schema_arrow, compression='zstd') as writer:
                        for batch in source.iter_batches(batch_size=config.batch_size):
                            table = pa.Table.from_batches([batch])
                            table = table.filter(pc.is_in(table.column('parent_id'), value_set=wanted))
                            if port == 'primary':
                                seen.update(table.column('parent_id').to_pylist())
                            writer.write_table(table)
                else:
                    # Keep all original rejection decisions, explicitly origin-scoped.
                    shutil.copyfile(root / relative, target)
                counts[port] = counts.get(port, 0) + pq.ParquetFile(target).metadata.num_rows
            outputs[port] = PendingOutput(contract_id=contract, file_paths=ref.file_paths,
                metadata={**dict(next(o for o in manifest.outputs if o.port == port).metadata),
                          'row_count': counts[port]})
        if selected is not None and seen != selected:
            raise PluginError('some selected parents are absent from the registered bundle',
                code='REGISTERED_SELECTION_MISSING', context={'missing_count': len(selected - seen)})
        # Keep the original registration manifest once, not a recursively growing
        # chain of copied manifests or old worker counts reported as current work.
        origin = metadata.get('registration_origin') or manifest.model_dump(mode='json')
        scientific = {k: v for k, v in metadata.items()
                      if k in expected or k in {'identity_policy', 'input_contract_id', 'format_counts',
                                               'decision_scope', 'selection_sha256'}
                      or k.endswith('_count') and not k.startswith('shard')}
        if selected is not None:
            scientific = {k: v for k, v in scientific.items()
                          if not k.endswith('_count') and k != 'format_counts'}
            scientific.update(output_count=counts['primary'], selected_parent_count=len(selected),
                mapping_count=counts['mapping'], decision_count=counts['decisions'],
                decision_scope='original_registration', selection_sha256=config.selection_sha256)
        return StageResponse(outputs=outputs, metadata={**scientific,
            'reuse_mode': 'verified_registered_bundle', 'source_artifact_id': config.artifact_id,
            'source_store': str(Path(config.path).resolve()), 'chemistry_rows_computed': 0,
            'registration_origin': origin})
