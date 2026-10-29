"""Real RDKit inputs: locators may change; chemistry, method and evidence may not."""
import copy
import hashlib
import shutil
from dataclasses import replace

import pytest

from molcascade.artifacts import ArtifactIntegrityError
from molcascade.errors import PluginError
from molcascade.plugins.registry import create_builtin_registry
from molcascade.runtime.cache import stage_cache_key
from molcascade.runtime.runner import LocalRunner
from etalon.boundary.screen import Screen
from test_conformer_handoff import original


@pytest.fixture
def case(tmp_path):
    workspace, origin_runner, origin = original(tmp_path)
    rows = Screen(workspace).read(origin.output_ref.artifact_id, contract_id='parent/v1')
    selection = tmp_path/'selected.tsv'
    selection.write_text('parent_id\n'+'\n'.join(r['parent_id'] for r in rows[:2])+'\n')
    config = {'schema_version':1, 'name':'handoff', 'stages':[
        {'id':'registered','slot':'source','plugin':'source.registered_parents@0.1.0',
         'config':{'path':str(workspace),'artifact_id':origin.stages[1].output_ref.artifact_id,
                   'selection_path':str(selection),
                   'selection_sha256':hashlib.sha256(selection.read_bytes()).hexdigest()}},
        {'id':'geometry','slot':'featurizer','plugin':'docking.registered_conformers@0.1.0',
         'config':{'path':str(workspace),'artifact_id':origin.output_ref.artifact_id}},
        {'id':'properties','slot':'featurizer','plugin':'features.rdkit_properties@0.1.0',
         'config':{'include_sa_score':False}},
    ]}
    runner = LocalRunner(tmp_path/'consumer', plugins=create_builtin_registry())
    first = runner.run(config, run_id='first')
    assert str(first.status) == 'SUCCEEDED'
    return runner, config, first, workspace, origin_runner, origin


def test_moved_selection_and_store_reuse_entire_chain(case,tmp_path,monkeypatch):
    runner, config, first, workspace, _, _ = case
    moved = tmp_path/'relocated'; shutil.copytree(workspace,moved)
    selected = tmp_path/'other'/'same.tab'; selected.parent.mkdir()
    old = config['stages'][0]['config']['selection_path']
    shutil.copyfile(old,selected)
    config['stages'][0]['config']['selection_path'] = str(selected)
    for s in config['stages'][:2]: s['config']['path'] = str(moved)
    # Current locators must suffice; the original selection no longer exists.
    from pathlib import Path
    Path(old).unlink()
    for s in config['stages']:
        plugin = runner.plugins.get(s['plugin'])
        monkeypatch.setattr(type(plugin),'execute',lambda *a,**k:pytest.fail('cache hit executed a plugin'))
    second = runner.run(config,run_id='moved')
    assert all(str(s.status)=='CACHED' and s.attempts==0 for s in second.stages)
    assert [s.output_ref for s in second.stages] == [s.output_ref for s in first.stages]


def test_changed_content_at_same_path_does_not_reuse(case):
    runner, config, first, _, _, _ = case
    from pathlib import Path
    p=Path(config['stages'][0]['config']['selection_path'])
    p.write_text('\n'.join(p.read_text().splitlines()[:2])+'\n')
    # A stale declared digest must fail even though the old cache exists.
    with pytest.raises(PluginError,match='selection changed'):
        runner.run(config,run_id='stale-pin')
    config['stages'][0]['config']['selection_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    changed=runner.run(config,run_id='changed-content')
    assert all(str(s.status)=='SUCCEEDED' for s in changed.stages)
    assert changed.output_ref != first.output_ref
    assert len(Screen(runner.store.root).read(changed.output_ref.artifact_id,contract_id='parent/v1')) == 1


def test_changed_method_does_not_reuse(case):
    runner, config, first, _, _, _ = case
    changed=copy.deepcopy(config)
    changed['stages'][-1]['config']['include_sa_score']=True
    result=runner.run(changed,run_id='changed-properties')
    assert [str(s.status) for s in result.stages] == ['CACHED','CACHED','SUCCEEDED']
    assert result.output_ref != first.output_ref
    changed['stages'][1]['config']['seed']=42
    with pytest.raises(PluginError) as failure:
        runner.run(changed,run_id='changed-preparation')
    assert failure.value.code=='CONFORMER_REUSE_METHOD_MISMATCH'


def test_current_source_corruption_cannot_hide_behind_cache(case):
    runner, config, _, _, origin_runner, origin = case
    ref=origin.stages[1].output_ref
    p=origin_runner.store.artifact_directory(ref.artifact_id)/ref.file_paths[0]
    with p.open('ab') as handle: handle.write(b'corrupt')
    with pytest.raises(ArtifactIntegrityError):
        runner.run(config,run_id='corrupt-input')


def test_parser_and_plugin_version_remain_in_key(case):
    runner, config, _, _, _, _ = case
    stage=runner.compile(config).stages[0]
    changed=replace(stage, config={**stage.config,'selection_path':'same.csv'})
    assert stage_cache_key(stage)!=stage_cache_key(changed)
    version=replace(stage, descriptor=stage.descriptor.model_copy(update={'version':'0.2.0'}),
                    plugin_key='source.registered_parents@0.2.0')
    assert stage_cache_key(stage)!=stage_cache_key(version)
    content=replace(stage, config={**stage.config,'artifact_id':'artifact:sha256:'+'0'*64})
    assert stage_cache_key(stage)!=stage_cache_key(content)


def test_corrupt_geometry_bundle_cannot_hide_behind_cache(case):
    runner, config, _, _, origin_runner, origin = case
    manifest=origin_runner.store.get_manifest(origin.output_ref.artifact_id)
    ref=manifest.dataset_ref('conformers')
    p=origin_runner.store.artifact_directory(ref.artifact_id)/ref.file_paths[0]
    with p.open('ab') as handle: handle.write(b'corrupt')
    with pytest.raises(ArtifactIntegrityError):
        runner.run(config,run_id='corrupt-geometry')
