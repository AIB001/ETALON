from queue import Empty

import pytest

from molcascade.parallel import shards


def test_worker_waits_for_queued_device(monkeypatch):
    monkeypatch.setattr(shards, '_WORKER_DEVICE', 'cpu')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '')

    class Assignment:
        def get_nowait(self):
            raise Empty  # A feeder thread need not have delivered yet.

        def get(self, timeout):
            assert timeout > 0
            return 'cuda:3'

    shards._pin_worker(Assignment())
    assert shards._WORKER_DEVICE == 'cuda:3'
    assert shards.os.environ['CUDA_VISIBLE_DEVICES'] == '3'


def test_missing_assignment_never_silently_falls_back(monkeypatch):
    monkeypatch.setattr(shards, '_WORKER_DEVICE', 'cuda:2')

    class Exhausted:
        def get(self, timeout):
            raise Empty

    with pytest.raises(Empty):
        shards._pin_worker(Exhausted())
