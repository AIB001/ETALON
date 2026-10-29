"""Prepare a new cascade from completed typed results; never launch or stop a job."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


def prepare_screen_continuation(workspace, completed_run, next_config, output_dir, *, docking_backend=None):
    from etalon.boundary.infra import load
    load('molcascade')
    import pyarrow.parquet as pq
    from molcascade.artifacts.store import LocalArtifactStore
    from molcascade.chemistry.identity import rdkit_identity_metadata
    from molcascade.chemistry.policies import IdentityPolicy
    from molcascade.plugins.builtin.docking.ligand_prep import LigandConformerConfig, _method_id
    from rdkit import rdBase

    if docking_backend is not None and (not isinstance(docking_backend, str) or not docking_backend.startswith('docking.unidock@')):
        raise ValueError('docking_backend must be an explicit Uni-Dock plugin version')
    workspace, next_config, output_dir = map(lambda p:Path(p).absolute(), (workspace,next_config,output_dir))
    if Path(completed_run).name != completed_run or completed_run in {'.','..'}:
        raise ValueError('completed_run must be one run identifier')
    state = json.loads((workspace/'runs'/f'{completed_run}.json').read_text())
    if state['status'] != 'SUCCEEDED':
        raise ValueError('upstream run has not succeeded; keep its existing handle and wait')
    store = LocalArtifactStore(workspace, create=False)
    def artifact(prefixes):
        matches = [s for s in state['stages'] if s['plugin_key'].split('@')[0] in prefixes
                   and s['status'] in {'SUCCEEDED','CACHED'} and s.get('output_ref')]
        if not matches:
            raise ValueError(f'completed run has no compatible stage for {prefixes}')
        return matches[-1]['output_ref']['artifact_id']
    registration = artifact({'chemistry.rdkit_standardize','source.registered_parents'})
    geometry = artifact({'docking.rdkit_conformers','docking.registered_conformers'})
    registered = store.verify(registration)
    prepared = store.verify(geometry)
    final = store.verify(state['output_ref']['artifact_id'])
    outputs = [o for o in final.outputs if o.contract_id=='parent/v1']
    if len(outputs) != 1:
        raise ValueError('completed result must expose one typed parent selection')
    config = json.loads(next_config.read_text())
    if config.get('kind') != 'cascade':
        raise ValueError('next_config must be a cascade JSON')
    policy = IdentityPolicy.model_validate((config.get('standardize') or {}).get('settings',{}).get('identity_policy',{}))
    metadata = registered.metadata.get('response_metadata',{})
    if any(metadata.get(k)!=v for k,v in rdkit_identity_metadata(policy).items()):
        raise ValueError('next cascade identity policy or toolkit differs from registered parents')
    geometry_count = 0
    def adapt(value):
        nonlocal geometry_count
        if isinstance(value,dict):
            backend = value.get('backend','')
            if backend in {'docking.rdkit_conformers@0.1.0','docking.registered_conformers@0.1.0'}:
                settings = dict(value.get('settings',{}))
                settings.pop('path',None);settings.pop('artifact_id',None)
                method = LigandConformerConfig.model_validate(settings)
                if prepared.metadata.get('response_metadata',{}).get('method_id') != _method_id(method,rdBase.rdkitVersion):
                    raise ValueError('next preparation method differs; do not silently reuse or recompute')
                value['backend']='docking.registered_conformers@0.1.0'
                value['settings']={**settings,'path':str(workspace),'artifact_id':geometry}
                geometry_count += 1
            elif docking_backend and backend.split('@')[0] == 'docking.unidock':
                value['backend']=docking_backend
            for item in value.values():adapt(item)
        elif isinstance(value,list):
            for item in value:adapt(item)
    adapt(config)
    if geometry_count != 1:
        raise ValueError('expected one explicit conformer preparation step in next cascade')
    output_dir.mkdir(parents=True,exist_ok=True)
    selection = output_dir/'selected-parents.tsv'
    # Only final selected IDs go into the handoff, not scores or raw SMILES.
    ids=[]
    for relative in outputs[0].file_paths:
        for batch in pq.ParquetFile(store.artifact_directory(final.artifact_id)/relative).iter_batches(columns=['parent_id']):
            ids.extend(batch.column(0).to_pylist())
    if not ids or len(set(ids))!=len(ids):
        raise ValueError('upstream selection is empty or contains duplicate parent IDs')
    content=('parent_id\n'+'\n'.join(ids)+'\n').encode()
    def write_once(path, data):
        if path.exists():
            if path.read_bytes()!=data:raise ValueError(f'existing handoff differs: {path}')
        else:
            with path.open('xb') as f:f.write(data)
    write_once(selection,content)
    config['library']={'format':'registered','path':str(workspace)}
    config['ingest']={'id':'registered','backend':'source.registered_parents@0.1.0','settings':{
        'artifact_id':registration,'identity_policy':policy.model_dump(mode='json'),
        'selection_path':str(selection),'selection_sha256':hashlib.sha256(content).hexdigest()}}
    config['standardize']=None
    target=output_dir/'cascade.json'
    write_once(target,(json.dumps(config,indent=2)+'\n').encode())
    return {'config':str(target),'library':str(workspace),'completed_run':completed_run,
        'selected_parent_count':len(ids),'registration_artifact':registration,
        'geometry_artifact':geometry,'docking_backend_override':docking_backend,'status':'prepared_not_started',
        'note':'new docking poses still require their configured checks; current jobs untouched'}
