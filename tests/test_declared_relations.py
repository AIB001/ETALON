"""Synthetic cross-view/index contracts; no scientific correctness claims."""
import copy
import json
from pathlib import Path

import pytest

from etalon.active.schema import canonical
from etalon.research import Artifact
from etalon.research.continuation_card import _inventory
from etalon.research.continuation_pages import _closure_inventory
from etalon.research.declared_relations import relation_index
from etalon.research.evidence import _collect
from etalon.research.evidence_refs import EvidenceError, file_identity, load_workspace, make_ref
from etalon.research.graph_memory import build_research_graph, node_identity
from etalon.research.read_scope import verified_read
from test_research_graph_memory import graph_case


def nested_counter(case, *, target=None):
    """Publish a nested raw Artifact declaration from a separate source."""
    c = case
    path = c["entry"].parent / "external-counter.txt"
    path.write_text("fixture nested declaration; not a scientific finding")
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic",
                        bindings={"review": {"counter_refs": [target or c["decision"]]}})
    body = artifact.as_dict()
    ref = make_ref("other", artifact.id, body)
    manifest = path.with_suffix(".json")
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": artifact.id, "record_type": "artifact", "record": body, "visibility": "released"}]}))
    ws = json.loads(c["workspace"].read_text())
    ws["sources"].append({"source_id": "other", "kind": "artifact_manifest", "location": str(manifest),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    c["workspace"].write_text(json.dumps(ws))
    return ref


def test_nested_counter_has_same_direction_in_graph_card_and_closure(graph_case):
    c = graph_case
    external = nested_counter(c)
    expected = {"kind": "refutes", "from_ref": c["decision"], "to_ref": external, "basis_ref": external}
    with verified_read() as scope:
        broad = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], max_nodes=128, max_bytes=131072)
        scoped = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
            scope={"root_refs": [c["decision"]], "ordinary_hops": 0}, max_nodes=128, max_bytes=131072)
        card = _inventory(c["workspace"], c["checkpoint"], policy=None, purpose="research",
                          publications={"operations": []}, handoff={})
        closure = _closure_inventory(c["workspace"], c["checkpoint"], policy=None, purpose="research", handoff={})
        assert expected in scoped["scope_receipt"]["counter_relations"]
        assert expected in card["relations"]
        assert expected in closure["rows"]
        assert any(e["kind"] == "refutes" and e["from"] == node_identity(c["decision"])
                   and e["to"] == node_identity(external) for e in broad["edges"])
        assert scope.stats()["relation_index_builds"] == 1
        assert scope.stats()["relation_index_hits"] >= 3


@pytest.mark.parametrize("field,value", [("record_digest", "0" * 64), ("sha256", "0" * 64), ("bytes", 123)])
def test_nested_qualified_identity_cannot_disappear_before_incidence(graph_case, field, value):
    c = graph_case
    row = c["journal"].get(c["decision"]["record_id"])
    target = {**c["decision"], "sha256": row["sha256"], "bytes": row["bytes"], field: value}
    nested_counter(c, target=target)
    with pytest.raises(EvidenceError, match="identity|unavailable"):
        build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])


def test_relation_cache_is_disposable_copied_and_policy_isolated(graph_case):
    c = graph_case
    from etalon.research.graph_memory import restrict_workspace
    ws = load_workspace(c["workspace"])
    with verified_read() as scope:
        collection = _collect(ws)
        first = relation_index(ws, collection)
        expected = copy.deepcopy(first)
        first["relations"].clear()
        assert relation_index(ws, collection) == expected
        restricted, _ = restrict_workspace(ws, policy={"record_ids": {"science": [c["unrelated"]["record_id"]]}})
        narrow = relation_index(restricted, _collect(restricted))
        assert not narrow["relations"]
        assert scope.stats()["relation_index_builds"] == 2
        assert scope.stats()["relation_index_hits"] == 1


def test_cache_does_not_hide_original_mutation_before_outer_return(graph_case):
    c = graph_case
    with pytest.raises(EvidenceError, match="identity|hash|changed|unavailable"):
        with verified_read():
            ws = load_workspace(c["workspace"])
            collection = _collect(ws)
            relation_index(ws, collection)
            path = Path(c["journal"].get(c["claim"]["record_id"])["path"])
            original = path.read_bytes()
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            relation_index(ws, collection)


def test_nested_counter_original_still_verified_by_card(graph_case):
    c = graph_case
    external = nested_counter(c)
    path = c["entry"].parent / "external-counter.txt"
    path.write_text("tampered")
    with pytest.raises(EvidenceError, match="identity|hash|changed|unavailable"):
        with verified_read():
            _inventory(c["workspace"], c["checkpoint"], policy=None, purpose="research",
                       publications={"operations": []}, handoff={})
