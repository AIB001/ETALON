"""Actual producer CLI on clearly synthetic fixtures, no Vina environment assumed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import pytest
from etalon.active import CampaignSpec,CampaignStore,Endpoint
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import CLITransport
from etalon.research import ResearchJournal
from etalon.research.pose import PoseClient,execute,slot_identity,validation_request_binding


def test_failed_and_not_attempted_slot_identity_is_requested_target_not_source():
    target={'molecule_id':'catechol','chemical_state_id':'catechol-neutral','receptor_state_id':'receptor','coordinate_frame_id':'frame'}
    for status in ('FAILED','NOT_ATTEMPTED','REJECTED'):
        binding=slot_identity(target,None)
        assert binding['chemical_state_id']=='catechol-neutral' and 'pose_id' not in binding
    with pytest.raises(StateError,match='target chemical identity'):
        slot_identity(target,{**target,'chemical_state_id':'source-phenol','pose_id':'old-source-pose'})
    assert slot_identity(target,{**target,'pose_id':'actually-produced'})['pose_id']=='actually-produced'


def test_legacy_validation_guard_allows_settled_receipt_only(tmp_path):
    from test_research_journal import setup
    from etalon.active.schema import digest
    store,journal,_=setup(tmp_path)
    frozen={'freeze_sha256':'a'*64};bound={'semantic':{'fixture':1},'execution_id':'historical'}
    with pytest.raises(StateError,match='prior receipt'):
        validation_request_binding(journal,frozen,bound,'heldout')
    store.reserve_resource_execution('historical',digest(bound['semantic']),2,unit='credit',
        provenance={'task_id':'heldout','stage':'validation','decision_sha256':'a'*64})
    with pytest.raises(StateError,match='prior receipt'):
        validation_request_binding(journal,frozen,bound,'heldout')
    store.settle_resource_execution('historical',measured_cost=1,receipt={'status':'fixture'})
    assert validation_request_binding(journal,frozen,bound,'heldout')=='legacy_acknowledged_replay_only'
    with pytest.raises(StateError,match='not frozen'):
        validation_request_binding(journal,{**frozen,'validation_request_sha256s':[]},bound,'heldout')


def test_public_pose_cli_preserves_e0_without_scorer_and_never_recharges(tmp_path,monkeypatch):
    source=os.environ.get('ETALON_TEST_PRISM_SOURCE')
    if not source:pytest.skip('explicit producer snapshot required')
    # Parallel component development must not mutate the executed test snapshot.
    snapshot=tmp_path/'producer-snapshot';snapshot.mkdir()
    for name in ('prism','tests'):
        shutil.copytree(Path(source)/name,snapshot/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    source=str(snapshot)
    script="""import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_design import make_case
from test_pose_diagnostic import request
from prism.design.chem import canonical_smiles
q,g=request(make_case(Path(sys.argv[1]),growth=True))
q['intervention']['expected_smiles']=canonical_smiles(g.molecule)
from prism.pose_diagnostic.contract import bind
print(json.dumps(bind(q)))
"""
    p=subprocess.run([sys.executable,'-c',script,str(tmp_path/'case')],cwd=source,capture_output=True,text=True,check=True)
    request=json.loads(p.stdout)
    client=PoseClient(CLITransport(sys.executable,str(Path(source).resolve()),str(tmp_path/'requests'),timeout=90))
    store=CampaignStore(tmp_path/'campaign.sqlite')
    store.configure(CampaignSpec('unmeasured',300,'wall_second','fixture'),
                    [Endpoint('unmeasured','fixture','not_measured','count','fixture/1',0,queryable=False)])
    journal=ResearchJournal(store);root=tmp_path/'jobs'
    frozen=execute(journal,client,request,root=root,task_id='fixture',stage='smoke',source_kind='fixture')
    identity=json.loads(Path(frozen['result_path']).read_text())['input_identity']
    request.update(operation='prepare',operation_nature='intervention',input_identity=identity,action_id='prepare',idempotency_key='prepare')
    request=client.bind(request)['request']
    receipt=execute(journal,client,request,root=root,task_id='fixture',stage='development',source_kind='fixture')
    assert receipt['slots'] and any(s['observations']['E0']['status']=='measured' for s in receipt['slots'])
    assert all(s['observations']['E1']['status']!='measured' for s in receipt['slots'])
    assert not store.observations()
    before=store.balance();before_events=len(store.events())
    def no_run(*args,**kwargs):raise AssertionError('replay must not dispatch')
    monkeypatch.setattr(client,'run',no_run)
    request={**request,'inputs':dict(reversed(list(request['inputs'].items())))}
    replay=execute(journal,client,request,root=root,task_id='fixture',stage='development',source_kind='existing_real_artifact')
    assert replay['replayed'] and store.balance()==before
    assert len(store.events())==before_events
    assert replay['slots']==receipt['slots'] and replay['result_artifact_id']==receipt['result_artifact_id']
    assert replay['replay_context']['historical_source_kind']=='fixture'
    assert journal.get(replay['result_artifact_id'])['source_kind']=='fixture'
    with pytest.raises(StateError,match='frozen development decision'):
        execute(journal,client,request,root=root,task_id='fixture',stage='validation',source_kind='fixture')
