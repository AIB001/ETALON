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
    (('dev',), (), (), {'dev':'a'}),
    (('same',), ('same',), (), {'same':'a'}),
    (('dev',), ('confirm',), ('confirm',), {'dev':'a','confirm':'b'}),
    (('dev',), ('confirm',), (), {'dev':'a'}),
])
def test_local_split_preserves_nonoverlap_and_exact_identities(development, validation, transfer, initial):
    with pytest.raises(ValueError):
        TaskSplit(development, validation, transfer, initial, 'local confirmation')
