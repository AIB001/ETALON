"""Composable numerical methods on synthetic evidence, without a workflow/controller."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.authority import NotAuthorized, authorize_gate, require_gate
from etalon.boundary.prism_v1 import file_identity
from etalon.boundary.screen import ScreenResult
from etalon.campaign.calibrate import PanelMember, calibrate, enrichment, separation
from etalon.research import Artifact, Contrast, Coupling, Experiment, Observation, ResearchJournal


PANEL = (PanelMember('active', True, 'synthetic fixture'),
         PanelMember('inactive', False, 'synthetic fixture'))


class SyntheticPanelReader:
    """Only supplied fixture readouts; deliberately has no execution/generation methods."""
    def __init__(self, *, lost=(), registered=2):
        self.lost, self.registered = lost, registered
        self.calls = []

    def recall(self, run_id, *, panel_size):
        self.calls.append('recall')
        return {'registered': self.registered, 'tiers': [
            {'tier_id': 'score_filter', 'title': 'synthetic score gate',
             'entering': self.registered, 'surviving': self.registered - len(self.lost),
             'lost': list(self.lost)}], 'finalize': [], 'notes': ['synthetic input; no actual screening']}

    def parent_names(self, result):
        self.calls.append('parent_names')
        return {member.parent_id: member.parent_id for member in PANEL}

    def artifacts_carrying(self, result, contract_id):
        self.calls.append('artifacts_carrying')
        assert contract_id == 'docking_score/v1'
        return ('synthetic-score-artifact',)

    def read(self, artifact_id, *, contract_id):
        self.calls.append('read')
        assert artifact_id == 'synthetic-score-artifact'
        return [{'parent_id': member.parent_id, 'engine_id': 'fixture-score',
                 'score': -8.0, 'score_kind': 'VINA_KCAL_MOL',
                 'direction': 'minimize', 'unit': 'kcal/mol'} for member in PANEL]


def calibration_scenario():
    """Real calibration algorithms on explicit synthetic inputs, no scientific dispatch."""
    result = ScreenResult('fixture-panel', 'revision-original', 'SUCCEEDED', ())
    reader = SyntheticPanelReader(lost=('active',))
    measured = calibrate(reader, result, PANEL)
    assert measured.admissible is False and measured.rankable == ()
    assert measured.recall == 0 and measured.deleted_actives == ('active',)
    assert reader.calls == ['recall', 'parent_names', 'artifacts_carrying', 'read']
    before = measured.as_dict()
    with pytest.raises(NotAuthorized):
        authorize_gate(measured)
    # The caller can stop here or choose another gate. No plan or execution is required.
    decision = {'choice': 'stop_using_proposed_gate', 'reason': measured.refusals(),
                'new_dispatches': 0, 'authorization': None}
    assert measured.as_dict() == before

    valid_reader = SyntheticPanelReader()
    retained = calibrate(valid_reader, result, PANEL)
    with pytest.raises(NotAuthorized, match='no gate authorization'):
        require_gate(None, result.revision_id)
    token = authorize_gate(retained)  # Explicit separate call, never a calibrate side effect.
    assert require_gate(token, result.revision_id) is token
    with pytest.raises(NotAuthorized, match='configuration changed'):
        require_gate(token, 'revision-changed-after-panel')
    empty = calibrate(SyntheticPanelReader(registered=0),
                      replace(result, revision_id='revision-empty'), PANEL)
    assert empty.recall is None and empty.admissible is False
    with pytest.raises(NotAuthorized):
        authorize_gate(empty)
    return {'input': {'panel': [{'id': p.parent_id, 'known_active': p.known_active} for p in PANEL],
                      'unit': 'kcal/mol', 'direction': 'minimize', 'kind': 'synthetic'},
            'measured': before, 'caller_decision': decision,
            'retained_but_not_rankable': retained.as_dict(),
            'unknown_empty_panel': empty.as_dict(),
            'revision_mismatch_rejected': True, 'algorithm_only_no_dispatch': True}


def build_research_fixture(root: Path):
    """A durable synthetic journal for a host to inspect with the ordinary Python SDK."""
    root.mkdir(parents=True, exist_ok=False)
    store = CampaignStore(root / 'campaign.sqlite')
    store.configure(CampaignSpec('fixture', 3, 'credit', 'factorial-only-fixture'), [
        Endpoint('fixture', 'test', 'not_measured', 'count', 'fixture/1', 1, queryable=False)])
    path = root / 'synthetic-readouts.json'
    path.write_text(json.dumps({'00': 1, '01': 2, '10': 3, '11': 7}), encoding='utf-8')
    artifact = Artifact(str(path.absolute()), **file_identity(path), source_kind='fixture',
                        role='diagnostic', bindings={'condition_scope': 'synthetic'})
    journal = ResearchJournal(store)
    journal.record(artifact)
    cells = {}
    for cell, value in json.loads(path.read_text()).items():
        experiment = Experiment('fixture-task', 'file_audit',
                                {'condition_scope': 'synthetic', 'cell': cell}, (artifact.id,),
                                'fixed-readout/1', 'fixture-' + cell, ('score',), Coupling())
        journal.record(experiment)
        observation = Observation(experiment.id, 'measured', {'score': value},
                                  {'score': 'count'}, (artifact.id,))
        journal.record(observation)
        cells[cell] = observation.id
    coupling = Coupling((artifact.id,), ('condition_scope',), (),
                        ('shared synthetic input; errors cannot be assumed independent',))
    full = Contrast('fixture-task', 'descriptive two-factor interaction', cells, 'score',
                    coupling, 'fixed-readout/1')
    journal.record(full)
    missing = replace(full, cells={**cells, '11': None})
    journal.record(missing)
    # Different units are kept as original evidence but must not be numerically combined.
    old = journal.get(cells['11'])
    mismatched = Observation(old['experiment_id'], 'measured', {'score': 7},
                             {'score': 'angstrom'}, (artifact.id,))
    journal.record(mismatched)
    mixed = replace(full, cells={**cells, '11': mismatched.id})
    journal.record(mixed)
    refs = {'database': str(store.path), 'complete_contrast': full.id,
            'missing_contrast': missing.id, 'mixed_unit_contrast': mixed.id,
            'artifact': artifact.id, 'artifact_path': artifact.path,
            'scientific_scope': 'synthetic engineering fixture; no independent effect'}
    (root / 'inputs.json').write_text(json.dumps(refs, indent=2), encoding='utf-8')
    return store, journal, refs


def test_standalone_gate_calibration_does_not_authorize_or_require_campaign():
    output = calibration_scenario()
    assert output['caller_decision']['choice'] == 'stop_using_proposed_gate'


def test_standalone_direction_ties_nan_support_and_molecule_counting():
    rows = [{'parent_id': name, 'engine_id': 'fixture', 'score': score, 'direction': 'minimize'}
            for name, score in [('active', 1), ('inactive', 2)]]
    assert separation(rows, PANEL)[0].separates is True
    assert separation([{**row, 'direction': 'maximize'} for row in rows], PANEL)[0].separates is False
    tied = [{**row, 'score': 1} for row in rows]
    assert separation(tied, PANEL)[0].separates is False
    mixed = [rows[0], {**rows[1], 'direction': 'maximize'}]
    assert separation(mixed, PANEL)[0].separates is None
    assert separation(rows[:1], PANEL)[0].separates is None
    assert separation([{**row, 'score': float('nan')} for row in rows], PANEL) == ()
    population = [*rows, {**rows[0], 'score': 3}, {**rows[0], 'score': float('nan')}]
    placement = enrichment(rows, PANEL, population)[0]
    assert placement.population == 2 and placement.population_unit == 'molecules'
    assert placement.population_dropped == 1 and placement.best == 0.5


def test_existing_factorial_function_is_readonly_and_keeps_missing_uncertain(tmp_path):
    store, journal, refs = build_research_fixture(tmp_path / 'factorial')
    before = {'events': store.events(), 'balance': store.balance(), 'actions': store.actions(),
              'observations': store.observations()}
    value = journal.factorial(refs['complete_contrast'])
    assert value['status'] == 'computed' and value['interaction'] == 3
    assert value['uncertainty'] is None and value['unit'] == 'count'
    unresolved = journal.factorial(refs['missing_contrast'])
    assert unresolved['status'] == 'unresolved' and unresolved['interaction'] is None
    assert unresolved['missing_or_failed'] == {'11': 'missing'}
    with pytest.raises(ValueError, match='units differ'):
        journal.factorial(refs['mixed_unit_contrast'])
    after = {'events': store.events(), 'balance': store.balance(), 'actions': store.actions(),
             'observations': store.observations()}
    assert before == after
    assert not after['actions'] and not after['observations']
    assert not any(event['body'].get('record', {}).get('schema', '').startswith('etalon-method-')
                   for event in after['events'])
    ro = ResearchJournal(CampaignStore(refs['database'], read_only=True))
    assert ro.factorial(refs['complete_contrast']) == value


def test_factorial_evidence_rejects_holdout_as_diagnostic_input(tmp_path):
    _, journal, refs = build_research_fixture(tmp_path / 'role')
    artifact = Artifact(**journal.get(refs['artifact']))
    held = replace(artifact, role='independent_validation')
    journal.record(held)
    experiment = Experiment('fixture-task', 'file_audit', {'condition_scope': 'synthetic'},
                            (held.id,), 'fixed-readout/1', 'invalid-role', ('score',), Coupling())
    with pytest.raises(StateError, match='independent/holdout'):
        journal.record(experiment)
    # Descriptive calculation does not promote previous evidence to causal/independent results.
    assert 'no independence or causal identification' in journal.factorial(refs['complete_contrast'])['scope']
