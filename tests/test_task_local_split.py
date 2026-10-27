"""A local improvement can be evaluated without inventing a transfer task."""
import pytest
from etalon.research import TaskSplit


def test_empty_transfer_is_explicit_and_roundtrips():
    value = TaskSplit(('dev',), ('confirm',), (),
        {'dev':'configuration-a', 'confirm':'configuration-b'},
        'SND1 local confirmation; no cross-target transfer claim')
    assert value.as_dict()['transfer'] == ()
    assert TaskSplit(**value.as_dict()).id == value.id


@pytest.mark.parametrize('development,validation,transfer,initial', [
    ((), ('confirm',), (), {'confirm':'b'}),
    (('same',), ('same',), (), {'same':'a'}),
    (('dev',), ('confirm',), ('confirm',), {'dev':'a','confirm':'b'}),
    (('dev',), ('confirm',), (), {'dev':'a'}),
])
def test_local_split_preserves_nonoverlap_and_exact_identities(development, validation, transfer, initial):
    with pytest.raises(ValueError):
        TaskSplit(development, validation, transfer, initial, 'local confirmation')


def test_diagnostic_only_split_does_not_invent_validation_or_allow_independence(tmp_path):
    from dataclasses import replace
    from test_research_methods import evaluation_fixture
    from etalon.active.store import StateError
    _,journal,evaluation=evaluation_fixture(tmp_path)
    split=TaskSplit(('seen',),(),(),{'seen':'actual-input'},'Local diagnostic only')
    split_id=journal.record(split)
    value=replace(evaluation,task_split_ref=split_id)
    assert journal.record(value)
    policy=journal.policy_version(split,{'choice':'local-only'})
    with pytest.raises(StateError,match='held-out task'):
        journal.record_validation(policy,'seen',initial_configuration_sha256='actual-input',
                                  evidence=(),comparison={'local':True})
