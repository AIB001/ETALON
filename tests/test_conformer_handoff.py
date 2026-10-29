"""Typed subset handoff; synthetic failure rows explicitly test retained failures."""
import hashlib
import json
import runpy
from pathlib import Path

import pytest
from molcascade.plugins.registry import create_builtin_registry
from molcascade.runtime.runner import LocalRunner
from etalon.boundary.screen import Screen
from etalon.screening import plan_screen


def original(tmp_path, monkeypatch=None):
    if monkeypatch is not None:
        import molcascade.plugins.builtin.docking.ligand_prep as prep
        real = prep._conformer_rows
        def partly_failed(**kw):
            if kw['smiles']=='CCC':
                return [dict(parent_id=kw['parent_id'],conformer_index=0,molblock=None,
                    energy_kcal_mol=None,embed_status='EMBED_FAILED',method_id=kw['method_id'])],False
            return real(**kw)
        monkeypatch.setattr(prep,'_conformer_rows',partly_failed)
    path=tmp_path/'raw.csv'
    path.write_text('id,smiles\none,CCO\ntwo,CCC\nthree,c1ccccc1\nbad,invalid\n')
    workspace=tmp_path/'old'
    runner=LocalRunner(workspace,plugins=create_builtin_registry())
    state=runner.run({'schema_version':1,'name':'original','stages':[
        {'id':'library','slot':'source','plugin':'source.delimited_smiles@0.1.0','config':{'path':str(path)}},
        {'id':'standardize','slot':'standardize','plugin':'chemistry.rdkit_standardize@0.1.0','config':{}},
        {'id':'conformers','slot':'featurizer','plugin':'docking.rdkit_conformers@0.1.0','config':{}},
    ]},run_id='original')
    assert str(state.status)=='SUCCEEDED',state.error
    return workspace,runner,state


def test_subset_reuses_exact_geometry_including_failed_outcomes(tmp_path,monkeypatch):
    workspace,runner,state=original(tmp_path,monkeypatch)
    import molcascade.plugins.builtin.docking.ligand_prep as prep
    monkeypatch.setattr(prep,'_conformer_rows',lambda **kw:pytest.fail('reuse must not embed'))
    old=Screen(workspace)
    aid=state.output_ref.artifact_id
    geometry=old.read(aid,contract_id='ligand_conformer/v1')
    assert any(r['embed_status']=='EMBED_FAILED' for r in geometry)
    selected=[next(r for r in geometry if r['embed_status']=='EMBED_FAILED'),
              next(r for r in geometry if r['embed_status']!='EMBED_FAILED')]
    ids={r['parent_id'] for r in selected}
    selection=tmp_path/'selection.tsv';selection.write_text('parent_id\n'+'\n'.join(sorted(ids))+'\n')
    new=tmp_path/'new'
    stages=[
        {'id':'registered','slot':'source','plugin':'source.registered_parents@0.1.0','config':{
            'path':str(workspace),'artifact_id':state.stages[1].output_ref.artifact_id,
            'selection_path':str(selection),'selection_sha256':hashlib.sha256(selection.read_bytes()).hexdigest()}},
        {'id':'geometry','slot':'featurizer','plugin':'docking.registered_conformers@0.1.0','config':{
            'path':str(workspace),'artifact_id':aid}},
    ]
    new_runner=LocalRunner(new,plugins=create_builtin_registry())
    result=new_runner.run({'schema_version':1,'name':'reuse','stages':stages},run_id='reuse')
    assert str(result.status)=='SUCCEEDED',result.error
    rows=Screen(new).read(result.output_ref.artifact_id,contract_id='ligand_conformer/v1')
    assert rows==[row for row in geometry if row['parent_id'] in ids]
    metadata=new_runner.store.get_manifest(result.output_ref.artifact_id).metadata['response_metadata']
    assert metadata['geometry_rows_computed']==0
    # Changing the requested preparation seed must not reuse the old geometry.
    stages[-1]['config']['seed']=123
    from molcascade.errors import PluginError
    with pytest.raises(PluginError) as failure:
        new_runner.run({'schema_version':1,'name':'changed','stages':stages},run_id='changed')
    assert failure.value.code=='CONFORMER_REUSE_METHOD_MISMATCH'


def test_completed_run_prepares_existing_cascade_and_plan_binds_both_bundles(tmp_path):
    workspace,runner,state=original(tmp_path)
    template=tmp_path/'next.json'
    template.write_text(json.dumps({'kind':'cascade','name':'next',
        'standardize':{'id':'standardize','backend':'chemistry.rdkit_standardize@0.1.0','settings':{}},
        'finalize':{'steps':[{'id':'conformers','backend':'docking.rdkit_conformers@0.1.0','settings':{}},
                             {'id':'properties','backend':'features.rdkit_properties@0.1.0'}]}}))
    script=Path(__file__).parents[1]/'.claude/skills/etalon-execution-tuning/scripts/prepare_screen_continuation.py'
    prepare=runpy.run_path(str(script))['prepare']
    result=prepare(workspace,'original',template,tmp_path/'handoff')
    assert result==prepare(workspace,'original',template,tmp_path/'handoff')
    config=json.loads(Path(result['config']).read_text())
    assert config['standardize'] is None
    assert config['finalize']['steps'][0]['backend']=='docking.registered_conformers@0.1.0'
    report=plan_screen(result['config'],result['library'],tmp_path/'next')
    for artifact in [result['registration_artifact'],result['geometry_artifact']]:
        root=runner.store.artifact_directory(artifact)
        assert str(root/'artifact.json') in report['request']['inputs']
    screen=Screen(tmp_path/'next')
    output=screen.run(screen.plan(result['config'],result['library']),run_id='next',workers=4,devices=('cpu',))
    assert output.status=='SUCCEEDED',output
    assert [s.stage_id for s in output.stages]==['registered','conformers','properties']
    state_path=workspace/'runs/original.json'
    pending=json.loads(state_path.read_text());pending['status']='RUNNING';state_path.write_text(json.dumps(pending))
    with pytest.raises(ValueError,match='has not succeeded'):
        prepare(workspace,'original',template,tmp_path/'must-not-start')
    assert not (tmp_path/'must-not-start').exists()


def test_handoff_keeps_backend_unless_caller_selects_a_version(tmp_path):
    from etalon.screening_handoff import prepare_screen_continuation
    workspace, _, _ = original(tmp_path)
    template = tmp_path/'next.json'
    template.write_text(json.dumps({'kind':'cascade','name':'next',
        'standardize':{'settings':{}},
        'finalize':{'steps':[{'id':'conformers','backend':'docking.rdkit_conformers@0.1.0','settings':{}},
                             {'id':'dock','backend':'docking.unidock@0.2.0','settings':{}}]}}))
    preserved = prepare_screen_continuation(workspace, 'original', template, tmp_path/'preserved')
    chosen = prepare_screen_continuation(workspace, 'original', template, tmp_path/'chosen',
                                          docking_backend='docking.unidock@0.4.0')
    assert json.loads(Path(preserved['config']).read_text())['finalize']['steps'][1]['backend'] == 'docking.unidock@0.2.0'
    assert json.loads(Path(chosen['config']).read_text())['finalize']['steps'][1]['backend'] == 'docking.unidock@0.4.0'
    assert chosen['docking_backend_override'] == 'docking.unidock@0.4.0'
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from etalon.mcp.execution import register
    server = FastMCP('continuation-test')
    register(server)
    result = asyncio.run(server.call_tool('etalon_screen_prepare_continuation', {
        'workspace': str(workspace), 'completed_run': 'original',
        'next_config': str(template), 'output_dir': str(tmp_path/'mcp')}))
    assert (tmp_path/'mcp'/'cascade.json').is_file(), result
    assert json.loads((tmp_path/'mcp'/'cascade.json').read_text())['finalize']['steps'][1]['backend'] == 'docking.unidock@0.2.0'
