"""Paging optimizations on synthetic fixtures, not molecular performance claims."""
from pathlib import Path

import pytest

from etalon.research import evidence, read_scope
from etalon.research.evidence import StaleCursorError
from etalon.research.evidence_refs import EvidenceError
from etalon.research.graph_memory import build_research_graph
from test_frozen_check_deferral import _hash_calls
from test_research_graph_memory import graph_case
from test_verified_read_scope import _same_size_change


@pytest.fixture(autouse=True)
def empty_page_cache(monkeypatch):
    monkeypatch.setattr(read_scope, "_PAGE_SOURCES", None)


def page(c, cursor=None, **kwargs):
    return build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
        scope={"branch_id": "fixed-core", "ordinary_hops": 1}, max_bytes=4096,
        cursor=cursor, **kwargs)


def test_warm_page_reuses_decoding_but_rechecks_originals_and_matches_cold_page(graph_case, monkeypatch):
    c = graph_case
    first = page(c)
    calls = _hash_calls(monkeypatch)
    decodes = []
    decode = evidence._source_uncached
    def measured(*args):
        decodes.append(True)
        return decode(*args)
    monkeypatch.setattr(evidence, "_source_uncached", measured)
    warm = page(c, first["next_cursor"])
    assert not decodes
    checked = set(calls)
    for original in (c["store"].path, Path(c["journal"].get(c["claim"]["record_id"])["path"]),
                     Path(c["journal"].get(c["counter"]["record_id"])["path"])):
        assert str(original.absolute()) in checked
    read_scope._PAGE_SOURCES = None
    calls.clear()
    cold = page(c, first["next_cursor"])
    assert decodes and cold == warm
    assert checked == set(calls)


@pytest.mark.parametrize("damaged", ["source", "claim", "counter"])
def test_same_size_same_mtime_damage_between_pages_is_not_hidden(graph_case, damaged):
    c = graph_case
    first = page(c)
    path = c["store"].path if damaged == "source" else Path(c["journal"].get(c[damaged]["record_id"])["path"])
    _same_size_change(path)
    with pytest.raises(EvidenceError, match="hash|unavailable|identity"):
        page(c, first["next_cursor"])


def test_new_reverse_counter_changes_projection_instead_of_reusing_old_closure(graph_case):
    c = graph_case
    first = page(c)
    counter = c["save"]({"schema": "etalon-research-claim/1", "question": "New fixture counter?",
        "statement": "new negative fixture", "epistemic_status": "interpretation",
        "subjects": ["fixture"], "conditions": [], "limitations": ["synthetic"],
        "support_refs": [], "counter_refs": [c["claim"]], "method_refs": [], "supersedes": []})
    c["publish"]()
    with pytest.raises(StaleCursorError):
        page(c, first["next_cursor"])
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
        scope={"branch_id": "fixed-core", "ordinary_hops": 1})
    assert any(n["ref"] == counter for n in graph["nodes"])
    assert any(r["basis_ref"] == counter for r in graph["scope_receipt"]["counter_relations"])


def test_permission_changes_are_recomputed_and_never_borrow_wide_page(graph_case):
    c = graph_case
    first = page(c)
    policy = {"record_rules": [{"source_id": "science", "record_id": c["counter"]["record_id"],
        "allowed_purposes": []}]}
    with pytest.raises(EvidenceError, match="checkpoint unavailable"):
        page(c, first["next_cursor"], policy=policy)


@pytest.mark.parametrize("invalidate", ["budget", "implementation", "eviction"])
def test_optional_cache_miss_is_ordinary_read_without_lost_counter_or_cursor(graph_case, monkeypatch, invalidate):
    c = graph_case
    first = page(c)
    expected = page(c, first["next_cursor"])
    if invalidate == "budget":
        monkeypatch.setattr(read_scope, "MAX_CACHE_BYTES", 0)
    elif invalidate == "implementation":
        # Obsolete cache implementation, with current cursor/code unchanged.
        read_scope._PAGE_SOURCES = {**read_scope._PAGE_SOURCES, "implementation": "old"}
    else:
        read_scope._PAGE_SOURCES = None
    decodes = []
    decode = evidence._source_uncached
    def measured(*args):
        decodes.append(True)
        return decode(*args)
    monkeypatch.setattr(evidence, "_source_uncached", measured)
    assert page(c, first["next_cursor"]) == expected and decodes
    assert read_scope.CURRENT.get() is None and read_scope.VERIFY_LOCATIONS.get() is None


def test_final_location_check_is_fresh_even_after_validation_path_reuse(tmp_path, monkeypatch):
    from etalon.research import evidence_refs
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.write_text("same bytes")
    replacement.write_text("same bytes")
    target = original
    resolve = Path.resolve
    def retarget(path, *args, **kwargs):
        return target if path == tmp_path / "alias" else resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", retarget)
    def during_final_validation(_):
        nonlocal target
        assert read_scope.CURRENT.get() is None
        assert read_scope.VERIFY_LOCATIONS.get() is not None
        assert evidence_refs._local(tmp_path, "alias") == original
        target = replacement
    monkeypatch.setattr(evidence_refs, "load_workspace", during_final_validation)
    with pytest.raises(EvidenceError, match="location changed"):
        with read_scope.verified_read() as scope:
            assert evidence_refs._local(tmp_path, "alias") == original
            scope.workspaces["synthetic_validation_callback"] = None
    assert read_scope.VERIFY_LOCATIONS.get() is None


def test_paths_refused_by_read_budget_do_not_acquire_unchecked_validation_cache(tmp_path, monkeypatch):
    from etalon.research import evidence_refs
    original, replacement = tmp_path / "original", tmp_path / "replacement"
    original.write_text("same bytes")
    replacement.write_text("same bytes")
    target = original
    resolve = Path.resolve
    def retarget(path, *args, **kwargs):
        return target if path == tmp_path / "alias" else resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", retarget)
    def during_final_validation(_):
        nonlocal target
        assert evidence_refs._local(tmp_path, "alias") == original
        target = replacement
        # This path has no final comparison obligation in the original scope:
        # the bounded read declined it. Its validation must remain uncached.
        assert evidence_refs._local(tmp_path, "alias") == replacement
    monkeypatch.setattr(evidence_refs, "load_workspace", during_final_validation)
    with read_scope.verified_read() as scope:
        scope.bytes = read_scope.MAX_CACHE_BYTES
        evidence_refs._local(tmp_path, "alias")
        assert not scope.locations
        scope.workspaces["synthetic_validation_callback"] = None
