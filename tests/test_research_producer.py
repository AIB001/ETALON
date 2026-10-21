"""Real local CLI over producer-generated SYNTHETIC fixtures, enabled by explicit source path."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.store import BudgetExhausted, StateError
from etalon.boundary.prism_v1 import CLITransport, TransportError
from etalon.research import ResearchJournal
from etalon.research.producer import DiagnosticClient, execute


@pytest.fixture
def diagnostic(tmp_path):
    source = os.environ.get('ETALON_TEST_PRISM_SOURCE')
    if not source: pytest.skip('set ETALON_TEST_PRISM_SOURCE to explicit producer checkout/snapshot')
    script = """import json,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from test_design import make_case
from test_structural_diagnostic import request_for
print(json.dumps(request_for(make_case(Path(sys.argv[1]),growth=True))))
"""
    proc = subprocess.run([sys.executable,'-c',script,str(tmp_path/'producer-fixture')],cwd=source,
                          capture_output=True,text=True,check=True)
    request = json.loads(proc.stdout)
    client = DiagnosticClient(CLITransport(sys.executable,str(Path(source).resolve()),str(tmp_path/'requests'),timeout=90))
    store=CampaignStore(tmp_path/'campaign.sqlite')
    store.configure(CampaignSpec('unmeasured',120,'wall_second','research-fixture'),
                    [Endpoint('unmeasured','fixture','not_measured','count','fixture/1',0,queryable=False)])
    return request,client,ResearchJournal(store),tmp_path/'jobs'


def test_public_cpu_cli_admission_and_read_only_replay(diagnostic,monkeypatch):
    request,client,journal,root=diagnostic
    first=execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
    assert first['producer_status']=='COMPLETED' and first['scientific_judgment']=='unresolved'
    before=journal.store.balance(); assert before['spent']>0 and before['reserved']==0
    def forbidden(*args,**kwargs):raise AssertionError('replay cannot dispatch')
    monkeypatch.setattr(client,'run',forbidden)
    again=execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
    assert not again['additional_execution_on_this_call'] and journal.store.balance()==before
    assert first['observation_ids']==again['observation_ids'] and not journal.store.observations()
    result=json.loads(Path(first['result_path']).read_text())
    assert journal.get(first['experiment_id'])['method_id']==result['method_id']


def test_budget_and_holdout_refuse_before_producer_dispatch(diagnostic,monkeypatch):
    request,client,journal,root=diagnostic
    journal.store.reserve_resource_execution('other','f'*64,100,unit='wall_second',provenance={})
    def forbidden(*args,**kwargs):raise AssertionError('refusal cannot dispatch')
    monkeypatch.setattr(client,'run',forbidden)
    with pytest.raises(BudgetExhausted):execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
    request['inputs']['design_spec']['role']='sealed_holdout'
    request=client.bind(request)['request']
    with pytest.raises(StateError,match='held-out'):execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')


def test_exact_prepare_slots_are_structural_evidence_not_affinity_labels(diagnostic):
    request,client,journal,root=diagnostic
    frozen=execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
    identity=json.loads(Path(frozen['result_path']).read_text())['input_identity']
    script="""import json,sys
from prism.design.schema import load_design
from prism.design.validate import ground_design
from prism.design.edits import execute_edits
from prism.design.chem import canonical_smiles
g=ground_design(load_design(sys.argv[1]),for_execution=True)
s=g.spec['editing']['attachment_rules'][0]['retained_atom_map']
edits=[dict(op='add_atom',atom_map=100,element='C',formal_charge=0),dict(op='add_atom',atom_map=101,element='O',formal_charge=0),dict(op='add_bond',atom_maps=[s,100],order=1),dict(op='add_bond',atom_maps=[100,101],order=1)]
mol,_=execute_edits(g,edits)
print(json.dumps({'label':'synthetic exact growth','edits':edits,'expected_smiles':canonical_smiles(mol)}))
"""
    intervention=client.transport._run([sys.executable,'-c',script,request['inputs']['design_spec']['path']])
    request.update(operation='prepare',operation_nature='intervention',action_id='prepare',idempotency_key='prepare',
                   input_identity=identity,intervention=intervention)
    request=client.bind(request)['request']
    result=execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
    assert result['producer_status']=='COMPLETED' and len(result['observation_ids'])==2
    assert journal.get(result['experiment_id'])['changes'][0]['after']==intervention
    observations=[journal.get(identifier) for identifier in result['observation_ids']]
    assert any(o['status']=='measured' and o['values']['geometry']['exact_graph'] for o in observations)
    assert not journal.store.observations() and journal.store.balance()['spent']>0
    # Materialize two preparation conditions for the SAME graph; missing D0 cells stay missing.
    first_path=root.parent/'first-admission.json';first_path.write_text(json.dumps(result))
    request.update(action_id='prepare-second',idempotency_key='prepare-second',seed=request['seed']+100)
    request=client.bind(request)['request']
    second=execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
    second_path=root.parent/'second-admission.json';second_path.write_text(json.dumps(second))
    import importlib.util
    source=Path(__file__).parents[1]/'examples/diagnostic_contrast_round3.py'
    spec=importlib.util.spec_from_file_location('contrast_driver',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    report=module.freeze(journal.store.path,[first_path,second_path],root.parent/'proposal.json',task_id='fixture',added_atom_map=101)
    assert report['contrast']['interaction'] is None
    assert report['contrast']['missing_or_failed']=={'00':'missing','01':'missing'}
    assert report['decision']['delta']=={} and report['decision']['status']=='proposed_unresolved_not_promoted'
    assert not report['independent_validation_accessed']


def test_unknown_transport_retains_reservation_and_never_redispatches(diagnostic,monkeypatch):
    from etalon.active.runner import ExecutionPending
    request,client,journal,root=diagnostic
    def disconnected(*args,**kwargs):raise TransportError('explicit fixture lost response')
    monkeypatch.setattr(client,'run',disconnected)
    with pytest.raises(ExecutionPending):execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
    assert journal.store.balance()['reserved']==60
    def forbidden(*args,**kwargs):raise AssertionError('unknown outcome cannot redispatch')
    monkeypatch.setattr(client,'run',forbidden)
    with pytest.raises(ExecutionPending):execute(journal,client,request,root=root,task_id='fixture',source_kind='fixture')
