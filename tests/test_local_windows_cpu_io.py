"""Regression coverage for the local Windows CPU compatibility patch."""
import hashlib
import os

import pytest

from etalon.boundary.infra import load

load("molcascade")
from molcascade.io import molecules, parquet


@pytest.mark.skipif(os.name != "nt", reason="Windows path/descriptor stat regression")
def test_fresh_source_snapshots_keep_exact_bytes(tmp_path):
    # Freshly created-and-written files expose the creation/change time mismatch.
    for i in range(25):
        source = tmp_path / f"input-{i}.csv"
        content = b"id,smiles\r\nethanol,CCO\r\n"
        source.write_bytes(content)
        copy = molecules.snapshot_regular_file(source, tmp_path / f"copy-{i}.csv")
        assert copy.path.read_bytes() == content
        assert copy.sha256 == hashlib.sha256(content).hexdigest()
        bounded = molecules.read_regular_file_bounded(source, max_bytes=5)
        assert bounded.data is None
        assert bounded.sha256 == copy.sha256


def test_mutation_is_still_refused_and_partial_snapshot_removed(tmp_path, monkeypatch):
    source, destination = tmp_path / "input.csv", tmp_path / "copy.csv"
    source.write_bytes(b"id,smiles\nethanol,CCO\n")
    original = molecules._source_path_state
    calls = 0

    def change_after_read(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            with source.open("ab") as stream:
                stream.write(b"benzene,c1ccccc1\n")
        return original(path)

    monkeypatch.setattr(molecules, "_source_path_state", change_after_read)
    with pytest.raises(molecules.SourceSnapshotError, match="changed"):
        molecules.snapshot_regular_file(source, destination)
    assert not destination.exists()


def test_completed_output_fsync_preserves_bytes(tmp_path):
    path = tmp_path / "output.parquet.tmp"
    content = b"PAR1-test-payload"
    path.write_bytes(content)
    parquet._fsync_file(path)
    assert path.read_bytes() == content


def test_checkpoint_paths_preserve_distinct_cache_keys(tmp_path):
    from molcascade.runtime.cache import checkpoint_directory_name

    names = [checkpoint_directory_name('cache:sha256:' + char * 64) for char in ('a', 'b')]
    assert names[0] != names[1]
    for name in names:
        (tmp_path / name).mkdir()
    with pytest.raises(ValueError):
        checkpoint_directory_name('../invalid')
