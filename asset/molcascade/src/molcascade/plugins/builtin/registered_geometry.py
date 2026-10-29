"""Hand selected parents their existing, method-compatible prepared geometry."""
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from molcascade.artifacts.store import LocalArtifactStore
from molcascade.chemistry.datasets import discover_contract_files, require_single_input
from molcascade.contracts import PARENT_V1, LIGAND_CONFORMER_V1
from molcascade.errors import PluginError
from molcascade.plugins.api import PendingOutput, StageResponse
from molcascade.plugins.builtin.docking.ligand_prep import LigandConformerConfig, _method_id
from molcascade.plugins.manifest import PluginDescriptor, PluginKind, Cardinality, Determinism


class RegisteredConformerConfig(LigandConformerConfig):
    path: str
    artifact_id: str


class RegisteredConformerPlugin:
    config_model = RegisteredConformerConfig
    descriptor = PluginDescriptor(
        id='docking.registered_conformers', version='0.1.0', kind=PluginKind.FEATURIZER,
        inputs=(PARENT_V1.id,), outputs=(PARENT_V1.id, LIGAND_CONFORMER_V1.id),
        output_ports={'primary': PARENT_V1.id, 'conformers': LIGAND_CONFORMER_V1.id},
        cardinality=Cardinality.ONE_TO_MANY, determinism=Determinism.DETERMINISTIC,
        display_name='Reuse prepared ligand conformers',
        description='Select verified geometry for these exact parents and preparation method, including failed rows.',
    )

    def execute(self, request, context):
        from rdkit import rdBase
        config = self.config_model.model_validate(dict(request.config))
        expected_method = _method_id(config, rdBase.rdkitVersion)
        store = LocalArtifactStore(config.path, create=False)
        manifest = store.verify(config.artifact_id)
        if manifest.producer.plugin_id not in {'docking.rdkit_conformers', 'docking.registered_conformers'}:
            raise PluginError('input is not a prepared conformer bundle', code='CONFORMER_REUSE_PRODUCER_MISMATCH')
        metadata = manifest.metadata.get('response_metadata', {})
        if metadata.get('method_id') != expected_method:
            raise PluginError('preparation method or toolkit differs; generate under a new method explicitly',
                              code='CONFORMER_REUSE_METHOD_MISMATCH')
        root = store.artifact_directory(config.artifact_id)
        refs = {p:manifest.dataset_ref(p) for p in ('primary','conformers')}
        for port, contract in self.descriptor.output_ports.items():
            if refs[port].contract_id != contract:
                raise PluginError('incompatible geometry bundle port', code='CONFORMER_REUSE_CONTRACT_MISMATCH')
        parent_input = require_single_input(request.inputs, contract_id=PARENT_V1.id)
        parent_files = discover_contract_files(parent_input, PARENT_V1)
        parents = ds.dataset(parent_files, format='parquet').to_table()
        if not parents.num_rows:
            raise PluginError('conformer reuse received no parents', code='LIGAND_PREP_EMPTY_INPUT')
        ids = parents.column('parent_id').combine_chunks()
        wanted = set(ids.to_pylist())
        if len(wanted) != parents.num_rows:
            raise PluginError('conformer reuse requires unique parents', code='CONFORMER_REUSE_PARENT_MISMATCH')
        origin = ds.dataset([root/p for p in refs['primary'].file_paths], format='parquet').to_table(
            filter=ds.field('parent_id').isin(ids))
        # Identity alone must not bless changed chemical state or parent contents.
        if not parents.sort_by('parent_id').equals(origin.sort_by('parent_id')):
            raise PluginError('requested parents differ from the geometry source', code='CONFORMER_REUSE_PARENT_MISMATCH')
        primary = []
        for index, path in enumerate(parent_files):
            relative = f'datasets/parents/part-{index:05d}.parquet'
            destination = context.staging_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            primary.append(relative)
        conformers = []
        seen, failed = set(), set()
        rows = usable = 0
        for index, relative in enumerate(refs['conformers'].file_paths):
            output = f'datasets/conformers/part-{index:05d}.parquet'
            destination = context.staging_root / output
            destination.parent.mkdir(parents=True, exist_ok=True)
            with pq.ParquetWriter(destination, LIGAND_CONFORMER_V1.schema, compression='zstd') as writer:
                for batch in pq.ParquetFile(root/relative).iter_batches(batch_size=config.batch_size):
                    table = pa.Table.from_batches([batch])
                    table = table.filter(pc.is_in(table.column('parent_id'), value_set=ids))
                    if not table.num_rows:
                        continue
                    if pc.any(pc.not_equal(table.column('method_id'), expected_method)).as_py():
                        raise PluginError('conformer row method differs', code='CONFORMER_REUSE_METHOD_MISMATCH')
                    seen.update(table.column('parent_id').to_pylist())
                    failures = table.filter(pc.equal(table.column('embed_status'), 'EMBED_FAILED'))
                    failed.update(failures.column('parent_id').to_pylist())
                    rows += table.num_rows
                    usable += table.num_rows - failures.num_rows
                    writer.write_table(table)
            conformers.append(output)
        if seen != wanted:
            raise PluginError('geometry bundle has no outcome for some selected parents',
                code='CONFORMER_REUSE_MISSING', context={'missing_count':len(wanted-seen)})
        return StageResponse(outputs={
            'primary': PendingOutput(PARENT_V1.id, tuple(primary), {'row_count':parents.num_rows}),
            'conformers': PendingOutput(LIGAND_CONFORMER_V1.id, tuple(conformers),
                                      {'row_count':rows,'method_id':expected_method}),
        }, metadata={
            'input_count':parents.num_rows, 'output_count':parents.num_rows,
            'method_id':expected_method, 'conformer_count':usable, 'embed_failed_count':len(failed),
            'source_geometry_artifact_id':config.artifact_id, 'source_store':str(Path(config.path).resolve()),
            'reused_parent_count':parents.num_rows, 'geometry_rows_computed':0,
            'backend':'rdkit', 'backend_version':rdBase.rdkitVersion,
            'reuse_mode':'verified_prepared_subset',
        })
