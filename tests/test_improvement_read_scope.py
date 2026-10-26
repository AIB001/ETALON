"""One improvement read shares decoding, while originals and negatives survive."""
from pathlib import Path

import pytest

from etalon.research import improvement as imp, read_scope
from etalon.research.evidence_refs import EvidenceError
from test_research_continuation import case, publish
from test_research_improvement import prepared


def test_retrieval_keeps_counterevidence_and_scoped_exclusions_in_one_read(case, prepared, monkeypatch):
    p = prepared
    ref = imp.record_experience(p['j'], patch_ref=p['patch'], outcome='unresolved',
        lesson='Fixture: potentially reusable, with unresolved negative evidence',
        invalid_when='Changed objective', capability_id='etalon.evidence',
        support_refs=[case[1]], counter_refs=[case[1]], directory=p['root']/'content',
        source_id='research', source_kind='fixture')
    workspace = publish(case)
    scopes = []
    original = read_scope.ReadScope.__init__
    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        scopes.append(self)
    monkeypatch.setattr(read_scope.ReadScope, '__init__', init)
    result = imp.retrieve_experience(workspace, source_ids=['research'],
        capability_id='etalon.evidence',
        conditions={**p['conditions'], 'endpoint':'new-objective'},
        related_condition_keys=['problem_kind'])
    assert len(scopes) == 1
    assert not result['matches'] and result['next_cursor'] is None
    assert result['excluded'][0]['ref'] == ref
    assert result['related'][0]['experience']['counter_refs'] == [case[1]]
    assert result['related'][0]['experience']['outcome'] == 'unresolved'
    assert result['related'][0]['unmatched_conditions'] == ['endpoint']
    assert read_scope.CURRENT.get() is None


def test_context_rechecks_source_changed_after_last_nested_read(case, prepared, monkeypatch):
    p = prepared
    payload = p['artifact']({'schema':'fixture-original/1', 'value':'A'})
    workspace = publish(case)
    original = imp.read_material
    def read_then_change(ws, ref):
        result = original(ws, ref)
        if ref == payload:
            p['artifact']({'schema':'fixture-new-record/1', 'value':'new source content'})
        return result
    monkeypatch.setattr(imp, 'read_material', read_then_change)
    with pytest.raises(EvidenceError):
        imp.build_revision_context(workspace, task_id='scope-fixture', scope='fixture',
            conditions=p['conditions'], target_ref=p['base'], problem_refs=[payload])
    assert read_scope.CURRENT.get() is None


def test_generic_material_does_not_carry_a_trusted_read_to_next_call(case, prepared):
    p = prepared
    ref = p['artifact']({'schema':'fixture-original/1', 'value':'A'})
    workspace = publish(case)
    first = imp.read_material(workspace, ref)
    assert first['complete'] and first['content']['value'] == 'A'
    path = Path(p['j'].get(ref['record_id'])['path'])
    path.write_bytes(path.read_bytes().replace(b'"A"', b'"B"'))
    with pytest.raises(EvidenceError):
        imp.read_material(workspace, ref)
    assert read_scope.CURRENT.get() is None
