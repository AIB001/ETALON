"""Engineering fixtures only: method attribution is never molecular/scientific validation."""
from dataclasses import replace
from pathlib import Path
import json

import pytest

from etalon.active.schema import canonical
from etalon.active.store import StateError
from etalon.boundary.prism_v1 import file_identity
from etalon.research import (
    Artifact, MethodEvaluation, MethodUse, MethodVersion, TaskSplit, method_record_from_dict,
)
from test_research_journal import setup


def version(name='candidate', **kwargs):
    return MethodVersion(name, ('domain_skill',), {'manifest_sha256': 'a' * 64}, **kwargs)


def evidence(journal, path, payload, **kwargs):
    path.write_text(json.dumps(payload), encoding='utf-8')
    artifact = Artifact(str(path), **file_identity(path), source_kind='fixture',
                        role=kwargs.pop('role', 'diagnostic'), bindings=kwargs.pop('bindings', {}))
    journal.record(artifact)
    return artifact


def test_method_record_parser_is_closed_and_records_are_immutable(tmp_path):
    store, journal, artifact = setup(tmp_path)
    first = version(evidence_refs=(artifact.id,))
    identifier = journal.record(method_record_from_dict(first.as_dict()))
    before = store.events()
    assert journal.record(first) == identifier
    assert store.events() == before
    newer = replace(first, content_ref={'manifest_sha256': 'b' * 64}, parents=(identifier,))
    assert journal.record(newer) != identifier
    assert journal.get(identifier)['content_ref']['manifest_sha256'] == 'a' * 64
    assert not store.observations()
    with pytest.raises(StateError, match='rewritten'):
        with store.connection(write=True) as db:
            journal._put(db, identifier, newer.as_dict())
    with pytest.raises(ValueError, match='schemas'):
        method_record_from_dict({'schema': 'metadata/1', 'anything': 'unsafe'})
    with pytest.raises(ValueError, match='fields'):
        method_record_from_dict({**first.as_dict(), 'promotion': 'passed'})


def test_method_use_supports_empty_partial_combined_and_late_attribution(tmp_path):
    store, journal, _ = setup(tmp_path)
    a, b = journal.record(version('A')), journal.record(version('B'))
    subject = ({'kind': 'request', 'id': 'future-request', 'scope': str(store.path)},)
    skipped = MethodUse(subject, reason='direct tool is sufficient', host_conditions={'model': None})
    skipped_id = journal.record(skipped)
    assert journal.resolve_method_use_refs([]) == []
    combined = MethodUse(subject, ({'method_version_ref': a, 'scope': 'read only'},
                                  {'method_version_ref': b}), parents=(skipped_id,),
                         reason='late attribution, not frozen decision')
    combined_id = journal.record(combined)
    assert journal.resolve_method_use_refs([combined_id])[0]['parents'] == [skipped_id]
    assert journal.get(skipped_id)['selected_methods'] == []
    with pytest.raises((StateError, ValueError)):
        journal.resolve_method_use_refs([a])
    with pytest.raises(StateError, match='unknown'):
        journal.record(replace(combined, selected_methods=({'method_version_ref': 'missing'},)))
    with pytest.raises(StateError, match='scope'):
        journal.record(replace(skipped, subject_refs=({'kind': 'request', 'id': 'r', 'scope': '/other'},)))
    with pytest.raises(StateError, match='action'):
        journal.record(replace(skipped, subject_refs=({'kind': 'action', 'id': 'missing', 'scope': str(store.path)},)))
    with pytest.raises(StateError, match='telemetry'):
        journal.record(replace(skipped, basis='tool_observed'))


def evaluation_fixture(tmp_path):
    store, journal, artifact = setup(tmp_path)
    s, c = journal.record(version('candidate')), journal.record(version('static'))
    protocol = evidence(journal, tmp_path / 'protocol.json', {'metric': 'fixture-loss', 'cost_scope': 'all'})
    initial = evidence(journal, tmp_path / 'initial.json', {'M': 'fixed', 'tools': 'fixed', 'budget': 3})
    split = TaskSplit(('dev',), ('val',), ('transfer',),
                      {'dev': 'd', 'val': 'v', 'transfer': 't'}, 'fixture-loss')
    journal.record(split)
    value = MethodEvaluation((s,), (c,), protocol.id, split.id, initial.id, (), (),
                             'diagnostic', 'insufficient evidence', 'synthetic engineering only')
    return store, journal, value


def independent_receipt(tmp_path, journal, value, *, pre_seen=False):
    payload = {'loss': None, 'reason': 'unknown counterfactual remains unknown'}
    if pre_seen:
        evidence(journal, tmp_path / 'seen.json', payload)
    binding = {k: value.as_dict()[k] for k in
               ('subjects', 'comparators', 'protocol_ref', 'task_split_ref', 'initial_conditions_ref')}
    split = TaskSplit(**journal.get(value.task_split_ref))
    policy = journal.policy_version(split, {'method_evaluation': json.loads(canonical(binding))})
    measured = evidence(journal, tmp_path / 'released.json', payload, role='independent_validation',
                        bindings={'policy_id': policy, 'task_id': 'val'})
    receipt = journal.record_validation(policy, 'val', initial_configuration_sha256='v',
                                        evidence=(measured.id,), comparison={'loss': None})
    journal.store.reserve_resource_execution('fixture-failure', 'e' * 64, 2, unit='credit',
                                            provenance={'fixture': True})
    journal.store.settle_resource_execution('fixture-failure', measured_cost=1,
                                           receipt={'status': 'failed', 'value': None})
    return replace(value, assessment_role='independent_validation', result_refs=(receipt,),
                   accounting_refs=('fixture-failure',))


def test_self_reported_independence_is_rejected_but_diagnostics_remain_available(tmp_path):
    _, journal, value = evaluation_fixture(tmp_path)
    identifier = journal.record(value)
    assert journal.get(identifier)['conclusion'] == 'insufficient evidence'
    with pytest.raises(StateError, match='actual results'):
        journal.record(replace(value, assessment_role='independent_validation'))
    journal.store.reserve_resource_execution('actual-accounting', 'f' * 64, 1, unit='credit', provenance={})
    with pytest.raises(StateError, match='validation receipt'):
        journal.record(replace(value, assessment_role='independent_validation',
                               result_refs=(value.protocol_ref,), accounting_refs=('actual-accounting',)))
    with pytest.raises(StateError, match='accounting reference'):
        journal.record(replace(value, accounting_refs=(value.initial_conditions_ref,)))


def test_frozen_method_comparison_preserves_unknown_failure_cost_and_old_method_bindings(tmp_path):
    store, journal, value = evaluation_fixture(tmp_path)
    valid = independent_receipt(tmp_path, journal, value)
    before = store.balance()
    identifier = journal.record(valid)
    assert store.balance() == before and before['spent'] == 1
    assert journal.get(journal.get(identifier)['result_refs'][0])['comparison']['loss'] is None
    old = journal.get(valid.subjects[0])
    updated = journal.record(replace(method_record_from_dict(old),
                                    content_ref={'manifest_sha256': 'b' * 64}, parents=valid.subjects))
    with pytest.raises(StateError, match='bind methods'):
        journal.record(replace(valid, subjects=(updated,)))
    events = store.events()
    assert journal.record(valid) == identifier
    assert store.events() == events


def test_relabelled_seen_bytes_cannot_become_independent_test(tmp_path):
    _, journal, value = evaluation_fixture(tmp_path)
    attempted = independent_receipt(tmp_path, journal, value, pre_seen=True)
    with pytest.raises(StateError, match='seen artifact bytes'):
        journal.record(attempted)


def test_trigger_event_is_committed_and_cannot_certify_use(tmp_path):
    from etalon.runtime.api import read_events
    store, journal, _ = setup(tmp_path)
    page = read_events({'kind': 'campaign', 'database': str(store.path)})
    ref = {**page['events'][-1]['event_ref'], 'log_ref': page['log_ref']}
    value = MethodUse(({'kind': 'task', 'id': 't', 'scope': str(store.path)},), trigger_event_ref=ref)
    identifier = journal.record(value)
    assert journal.get(identifier)['basis'] == 'host_reported'
    with pytest.raises(StateError, match='committed'):
        journal.record(replace(value, trigger_event_ref={**ref, 'sequence': 999999}))


def test_mutated_nested_payload_is_revalidated_before_persisting(tmp_path):
    _, journal, _ = setup(tmp_path)
    value = version()
    value.content_ref.clear()
    with pytest.raises(ValueError, match='content reference'):
        journal.record(value)


def test_content_verified_requires_actual_saved_bytes_and_never_implies_use(tmp_path):
    from etalon.skills import persist_snapshot

    store, journal, _ = setup(tmp_path)
    source = tmp_path / 'custom'
    source.mkdir()
    (source / 'SKILL.md').write_text('---\nname: custom\ndescription: Fixture skill\n---\nRead evidence.\n')
    (source / 'references').mkdir()
    (source / 'references' / 'rule.md').write_text('Keep unknown values null.')
    ref = persist_snapshot('custom', tmp_path / 'snapshot', bundle_ref={'root': str(source)})
    value = replace(version(), content_ref=ref, verification_basis='content_verified')
    identifier = journal.record(value)
    assert journal.get(identifier)['verification_basis'] == 'content_verified'
    assert not store.observations()
    # A host declaration alone never gets promoted to byte verification.
    with pytest.raises(ValueError, match='content_ref'):
        journal.record(replace(version(), verification_basis='content_verified'))
    original = Path(ref['manifest_path']).parent / 'bundle' / 'references' / 'rule.md'
    original.write_text('Changed bytes')
    with pytest.raises(ValueError):
        journal.record(value)
    # The old record describes verification at insertion; reads preserve its historical identity.
    assert journal.get(identifier)['content_ref'] == ref


def test_upstream_calibration_preserves_unknown_direction_and_rejects_constant_ranking():
    from etalon.campaign.calibrate import PanelMember, separation
    panel = [PanelMember('active', True), PanelMember('inactive', False)]
    scores = [{'parent_id': name, 'engine_id': 'fixture', 'score': 1, 'direction': 'maximize'}
              for name in ('active', 'inactive')]
    result = separation(scores, panel)[0]
    assert result.direction == 'HIGHER_STRONGER' and result.separates is False
    scores[0]['direction'] = 'unknown'
    result = separation(scores, panel)[0]
    assert result.direction == 'MIXED' and result.best_active is None and result.separates is None
    for row in scores:
        row['score'] = float('nan')
    assert separation(scores, panel) == ()


def test_upstream_enrichment_counts_molecules_not_repeated_poses():
    from etalon.campaign.calibrate import PanelMember, enrichment
    panel = [PanelMember('active', True), PanelMember('inactive', False)]
    scores = [{'parent_id': name, 'engine_id': 'fixture', 'score': score, 'direction': 'minimize'}
              for name, score in (('active', 1), ('inactive', 5))]
    population = [*scores, {**scores[0], 'score': 2}, {**scores[0], 'score': float('nan')}]
    result = enrichment(scores, panel, population)[0]
    assert result.population == 2 and result.population_unit == 'molecules'
    assert result.population_dropped == 1 and result.best == 0.5
