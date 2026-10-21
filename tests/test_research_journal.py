"""Transactional diagnostic evidence and budgets; all scientific inputs below are fixtures."""
from dataclasses import replace
import json

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.active.store import BudgetExhausted, StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact, Change, Contrast, Coupling, Experiment, Observation, ResearchJournal, Revision, TaskSplit
from etalon.research.journal import audit_json
from etalon.research.diagnostics import exact_program
from etalon.research.runtime import compile_first_segment, record_program
from test_research_diagnostics import xor_model


def setup(tmp_path, *, budget=10):
    store = CampaignStore(tmp_path / 'campaign.sqlite')
    store.configure(CampaignSpec('unmeasured', budget, 'credit', 'research-fixture'),
                    [Endpoint('unmeasured', 'test', 'not_measured', 'count', 'fixture/1', 1, queryable=False)])
    path = tmp_path / 'evidence.json'; path.write_text(json.dumps({'metric': 3, 'status': 'fixture'}))
    artifact = Artifact(str(path), **file_identity(path), source_kind='fixture', role='diagnostic', bindings={'receptor_state_id': 'fixed-fixture'})
    journal = ResearchJournal(store); journal.record(artifact)
    return store, journal, artifact


def experiment(task, artifact, *, variant='0'):
    return Experiment(task, 'file_audit', {'receptor_state_id': 'fixed-fixture', 'condition': variant},
                      (artifact.id,), 'structural-proxy/fixture', 'instance-' + variant, ('score',), Coupling())


def test_real_file_bytes_are_audited_and_reimports_do_not_create_labels(tmp_path):
    store, journal, artifact = setup(tmp_path)
    pair = audit_json(journal, artifact, task_id='dev', selectors={'metric': ('metric',)})
    before = store.events()
    assert audit_json(journal, artifact, task_id='dev', selectors={'metric': ('metric',)}) == pair
    assert store.events() == before and not store.observations() and store.balance()['spent'] == 0
    assert journal.get(pair[1])['values']['metric'] == 3
    assert journal.accounting(pair[0])['local_audit_compute_cost'] is None
    __import__('pathlib').Path(artifact.path).write_text('corrupted')
    with pytest.raises(StateError, match='hash'): journal.record(artifact)


def test_contrast_retains_missing_cell_and_shared_state_without_independent_uncertainty(tmp_path):
    _, journal, artifact = setup(tmp_path)
    cells = {}
    for cell, value in [('00', 1), ('01', 2), ('10', 3)]:
        ex = experiment('dev', artifact, variant=cell); journal.record(ex)
        obs = Observation(ex.id, 'measured', {'score': value}, {'score': 'count'}, (artifact.id,)); journal.record(obs)
        cells[cell] = obs.id
    cells['11'] = None
    coupling = Coupling((artifact.id,), ('receptor_state_id',), (), ('shared input induces correlated errors',))
    contrast = Contrast('dev', 'factor interaction', cells, 'score', coupling, 'structural-proxy/fixture')
    journal.record(contrast)
    result = journal.factorial(contrast.id)
    assert result['interaction'] is None and result['missing_or_failed'] == {'11': 'missing'}
    ex = experiment('dev', artifact, variant='11'); journal.record(ex)
    obs = Observation(ex.id, 'measured', {'score': 7}, {'score': 'count'}, (artifact.id,)); journal.record(obs)
    full = replace(contrast, cells={**cells, '11': obs.id}); journal.record(full)
    assert journal.factorial(full.id)['interaction'] == 3
    assert journal.factorial(full.id)['uncertainty'] is None
    with pytest.raises(ValueError, match='numeric label'):
        Observation(ex.id, 'failed', {'score': 0}, {'score': 'count'}, (artifact.id,), 'failed preparation')


def test_joint_revision_materializes_multiple_fields_and_preserves_old_configuration(tmp_path):
    _, journal, artifact = setup(tmp_path)
    contrast = Contrast('dev', 'still unresolved', dict.fromkeys(['00','01','10','11']), 'score', Coupling(), 'method')
    journal.record(contrast)
    base = {'design': {'graph': 'old'}, 'preparation': {'poses': 1}, 'evaluation': {'method': 'fixed'}, 'budget': {'diagnostic': 2}}
    revision = Revision('dev', digest(base), (
        Change('preparation', ('poses',), 1, 8, (artifact.id,), 'compare alternate starting poses'),
        Change('budget', ('diagnostic',), 2, 5, (artifact.id,), 'fund diagnostic panel')), 'proposed test scope', (contrast.id,))
    result = journal.apply_revision(revision, base)
    assert base['preparation']['poses'] == 1 and result['configuration']['preparation']['poses'] == 8
    assert result['configuration']['budget']['diagnostic'] == 5 and result['status'] == 'proposed_not_promoted'
    with pytest.raises(StateError, match='base'): journal.apply_revision(revision, result['configuration'])


def test_phi_lineage_preserves_frozen_tasks_and_rejects_validation_leakage(tmp_path):
    _, journal, artifact = setup(tmp_path)
    split = TaskSplit(('dev',), ('validate',), ('transfer',), {k: 'frozen-' + k for k in ('dev','validate','transfer')}, 'independent geometry reference')
    phi0 = journal.policy_version(split, {'search': 'exact', 'depth': 1})
    ex, obs = audit_json(journal, artifact, task_id='dev', selectors={'metric': ('metric',)})
    phi1 = journal.policy_version(split, {'search': 'exact', 'depth': 2}, parent_id=phi0, development_evidence=(obs,))
    assert journal.get(phi1)['transfer_status'] == 'not_evaluated'
    _, held = audit_json(journal, artifact, task_id='transfer', selectors={'metric': ('metric',)})
    with pytest.raises(StateError, match='cannot train'): journal.policy_version(split, {'depth': 3}, parent_id=phi1, development_evidence=(held,))
    secret = replace(artifact, role='sealed_holdout'); journal.record(secret)
    with pytest.raises(StateError, match='holdout'): audit_json(journal, secret, task_id='dev', selectors={'metric': ('metric',)})
    with pytest.raises(StateError, match='initial'): journal.record_validation(phi1, 'transfer', initial_configuration_sha256='changed', evidence=(secret.id,), comparison={})


def test_diagnostic_reservations_share_campaign_budget_and_unknown_cost_stays_pending(tmp_path):
    store, _, _ = setup(tmp_path, budget=5)
    args = ('readout-1', 'a'*64, 4)
    assert store.reserve_resource_execution(*args, unit='credit', provenance={'scope':'fixture'})['new_reservation']
    assert store.balance()['remaining'] == 1
    assert not store.reserve_resource_execution(*args, unit='credit', provenance={'scope':'fixture'})['new_reservation']
    with pytest.raises(BudgetExhausted): store.reserve_resource_execution('other', 'b'*64, 2, unit='credit', provenance={})
    with pytest.raises(StateError, match='different'): store.reserve_resource_execution('readout-1', 'b'*64, 4, unit='credit', provenance={})
    store.settle_resource_execution('readout-1', measured_cost=None, receipt={'status':'outcome_unknown'})
    assert store.balance()['reserved'] == 4
    store.settle_resource_execution('readout-1', measured_cost=3, receipt={'status':'failed','measured':True})
    assert store.balance()['spent'] == 3 and store.balance()['reserved'] == 0 and not store.observations()
    events = store.events()
    store.settle_resource_execution('readout-1', measured_cost=3, receipt={'status':'failed','measured':True})
    assert store.events() == events
    with pytest.raises(StateError, match='immutable'): store.settle_resource_execution('readout-1', measured_cost=0, receipt={})


def test_runtime_compiles_only_the_selected_first_segment(tmp_path):
    _, journal, artifact = setup(tmp_path)
    model = xor_model(); plan = exact_program(model, budget=1, max_depth=2, cost_weight=1)
    identifier = record_program(journal, model, plan)
    binding = {'x': {'operation':'design.import','arguments':{'database':str(journal.store.path), 'bundle_path':artifact.path,'mode':'index_only'}}}
    segment = compile_first_segment(journal, identifier, binding)
    assert len(segment['workflow']['nodes']) == 1 and segment['status'] == 'compiled_not_executed'
    with pytest.raises(StateError, match='binding'): compile_first_segment(journal, identifier, {})


def test_different_preparation_methods_share_a_fixed_measurement_definition(tmp_path):
    _, journal, artifact = setup(tmp_path)
    cells={}
    for i,cell in enumerate(('00','01','10','11')):
        ex=replace(experiment('dev',artifact,variant=cell),method_id='preparation-'+cell)
        journal.record(ex)
        obs=Observation(ex.id,'measured',{'score':i},{'score':'count'},(artifact.id,),measurement_method_id='fixed-J/1')
        journal.record(obs);cells[cell]=obs.id
    contrast=Contrast('dev','compare preparation under fixed J',cells,'score',Coupling(),'fixed-J/1')
    journal.record(contrast)
    assert journal.factorial(contrast.id)['interaction']==0
    with pytest.raises(StateError,match='measurement'):
        journal.record(replace(contrast,method_id='different-J/1'))
