"""Synthetic contract cases; no molecular quality or transfer claims."""
import json
from pathlib import Path

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical
from etalon.research import Artifact, ResearchJournal
from etalon.research.continuation import CHECKPOINT, STEP, record_content
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.graph_memory import build_research_graph, node_identity, validate_research_graph


@pytest.fixture
def graph_case(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("graph", 0, "wall_second", "graph-fixture"),
                    [Endpoint("graph", "fixture", "unmeasured", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    def raw(name, text):
        path = tmp_path / name
        path.write_text(text)
        item = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic", bindings={})
        journal.record(item)
        return make_ref("science", item.id, journal.get(item.id))
    evidence = raw("structure.txt", "explicit fixture structure; no science claim")
    counter = raw("negative.txt", "explicit fixture negative observation")
    unrelated = raw("private.txt", "PRIVATE_SENTINEL_must_not_inject")
    def save(content):
        rid = record_content(journal, content, tmp_path / "content", source_id="science", source_kind="fixture")
        return make_ref("science", rid, journal.get(rid))
    claim = save({"schema": "etalon-research-claim/1", "question": "Which route?", "statement": "Fixture result is conditional",
        "epistemic_status": "interpretation", "subjects": ["SND1-fixture"], "conditions": ["fixture protocol"],
        "limitations": ["Not a science run"], "support_refs": [evidence], "counter_refs": [counter], "method_refs": [], "supersedes": []})
    step = {"schema": STEP, "task_id": "snd1-fixture", "question": "Which route?", "branch_id": "fixed-core",
        "operation": "decision", "reason": "Reuse the already available fixture", "input_refs": [evidence],
        "output_refs": [], "method_refs": [], "accounting_refs": [], "source_watermarks": [], "supersedes": [],
        "detail": {"choice": "reuse", "alternatives": [{"branch_id": "local-flex", "disposition": "skip", "reason": "No new evidence"}],
                   "basis_refs": [claim], "reconsider_when": "New direction-specific evidence"}}
    decision = save(step)
    authority = tmp_path / "STATUS.md"
    authority.write_text("R4b paused. No automatic execution.")
    checkpoint = {"schema": CHECKPOINT, "task_id": "snd1-fixture", "question": "Which route?", "step_refs": [decision],
        "claim_refs": [claim], "counter_refs": [counter], "method_refs": [], "pending_handles": [], "source_watermarks": [],
        "branches": [{"branch_id": "fixed-core", "state": "resolved", "basis_refs": [claim], "next_read_refs": [evidence],
                      "reconsider_when": "New constraints"}], "supersedes": [],
        "status_guards": [{"location": str(authority), **file_identity(authority), "state": "paused"}]}
    cp = save(checkpoint)
    workspace = tmp_path / "workspace.json"
    def publish():
        workspace.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "graph-fixture",
            "sources": [{"source_id": "science", "kind": "journal", "location": str(store.path),
                         "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}}],
            "status_refs": [str(authority)], "location_map": []}))
        return workspace
    publish()
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": cp}))
    return {"store": store, "journal": journal, "save": save, "publish": publish, "workspace": workspace,
            "entry": entry, "checkpoint": cp, "checkpoint_content": checkpoint, "evidence": evidence,
            "counter": counter, "claim": claim, "decision": decision, "unrelated": unrelated, "step": step}


def test_shared_evidence_cross_home_and_skip_remain_inspectable(graph_case):
    c = graph_case
    before = file_identity(c["store"].path)
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
        home_overrides={node_identity(c["counter"]): "Deep"})
    nodes = {n["node_id"]: n for n in graph["nodes"]}
    assert nodes[node_identity(c["counter"])]["home"] == "Deep"
    assert nodes[node_identity(c["claim"])]["home"] == "Judge"
    assert sum(n["ref"] == c["evidence"] for n in graph["nodes"]) == 1
    assert {"from": node_identity(c["counter"]), "to": node_identity(c["claim"]), "kind": "refutes",
            "basis_ref": c["claim"]} in graph["edges"]
    decision = nodes[node_identity(c["decision"])]["decision"]
    assert decision["alternatives"][0]["disposition"] == "skip"
    assert decision["reconsider_when"] == "New direction-specific evidence"
    assert graph["continuation"]["authority_state"] == "paused"
    assert graph["execution_permission"] == "not_granted" and not graph["automatic_retry"]
    assert file_identity(c["store"].path) == before and not c["store"].actions()


def test_scope_and_purpose_filter_before_projection(graph_case):
    c = graph_case
    policy = {"record_rules": [{"source_id": "science", "record_id": c["unrelated"]["record_id"],
        "allowed_purposes": ["evaluation"], "seen_by": ["agent-already-saw-this"]}]}
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], policy=policy)
    assert "PRIVATE_SENTINEL" not in canonical(graph)
    assert c["unrelated"]["record_id"] not in canonical(graph)
    assert not graph["policy"]["seen_is_permission"]
    # Forbidding a required dependency must fail closed, not summarize its
    # checkpoint-derived interpretation and accidentally leak the dependency.
    policy["record_rules"][0]["record_id"] = c["counter"]["record_id"]
    with pytest.raises(EvidenceError, match="checkpoint unavailable"):
        build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], policy=policy)
    with pytest.raises(EvidenceError, match="outside permitted"):
        build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], policy={"source_ids": []})


def test_tampered_artifact_and_checkpoint_rejected(graph_case):
    c = graph_case
    with pytest.raises(EvidenceError, match="identity changed"):
        build_research_graph(c["workspace"], checkpoint_ref={**c["checkpoint"], "record_digest": "0" * 64})
    path = Path(c["journal"].get(c["counter"]["record_id"])["path"])
    path.write_text("changed artifact")
    with pytest.raises(EvidenceError, match="(identity|hash|unavailable)"):
        build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])


def test_bounds_and_historical_semantics(graph_case):
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], max_nodes=1)
    assert len(graph["nodes"]) == 1 and graph["truncated"]
    assert all(e["from"] == e["to"] == graph["nodes"][0]["node_id"] for e in graph["edges"])
    snapshots = {"science": file_identity(c["store"].path)["sha256"]}
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], as_of={"source_snapshots": snapshots})
    assert graph["temporal"]["mode"] == "explicit_frozen_snapshot"
    assert not graph["temporal"]["historical_decision_replay"]
    with pytest.raises(EvidenceError, match="as_of"):
        build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], as_of="yesterday")


def test_workflow_edges_do_not_infer_execution_order(graph_case):
    from etalon.research.composition import node_step
    c = graph_case
    workflow = c["save"]({"schema": "etalon-research-workflow/1", "name": "fixture-route", "scope": "snd1-fixture",
        "nodes": [{"node_id": nid, "capability_id": None, "method_refs": [], "views": ["X"],
                   "purpose": "fixture planning", "skip_when": "evidence already exists"} for nid in ("quick", "deep")],
        "edges": [{"from": "quick", "to": "deep", "kind": "control", "condition": "only if justified"}],
        "method_refs": [], "supersedes": []})
    steps = [c["save"](node_step(task_id="snd1-fixture", question="Which route?", branch_id="candidate", node_id=nid,
        workflow_ref=workflow, state="planned" if nid == "quick" else "skipped", reason="fixture", input_refs=[c["evidence"]])) for nid in ("quick", "deep")]
    cp = c["save"]({**c["checkpoint_content"], "step_refs": steps, "supersedes": [c["checkpoint"]]})
    graph = build_research_graph(c["publish"](), checkpoint_ref=cp, home_overrides={"deep": "Deep"})
    assert any(e["kind"] == "flow" and e["from"] == node_identity(steps[0]) and e["to"] == node_identity(steps[1]) for e in graph["edges"])
    deep = next(n for n in graph["nodes"] if n["ref"] == steps[1])
    assert deep["home"] == "Deep" and deep["state"] == "skipped"
    graph["edges"][0]["to"] = "missing"
    with pytest.raises(EvidenceError, match="endpoint"):
        validate_research_graph(graph)


def test_counterevidence_from_other_published_source_and_scope_denial(graph_case):
    c = graph_case
    body = c["journal"].get(c["counter"]["record_id"])
    external = make_ref("lab-fixture", c["counter"]["record_id"], body)
    claim = c["save"]({"schema": "etalon-research-claim/1", "question": "Which route?", "statement": "External counter must remain visible",
        "epistemic_status": "interpretation", "subjects": ["fixture"], "conditions": ["fixture"], "limitations": [],
        "support_refs": [c["evidence"]], "counter_refs": [external], "method_refs": [], "supersedes": []})
    cp = c["save"]({**c["checkpoint_content"], "claim_refs": [claim], "counter_refs": [external], "supersedes": [c["checkpoint"]]})
    manifest = c["entry"].parent / "lab-manifest.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [{"record_id": external["record_id"],
        "record_type": "artifact", "record": body, "visibility": "released"}]}))
    ws = c["publish"]()
    config = json.loads(ws.read_text())
    config["sources"].append({"source_id": "lab-fixture", "kind": "artifact_manifest", "location": str(manifest),
                              "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    ws.write_text(json.dumps(config))
    graph = build_research_graph(ws, checkpoint_ref=cp, home_overrides={node_identity(external): "Deep"})
    assert node_identity(external) in graph["counter_node_ids"]
    assert next(n for n in graph["nodes"] if n["ref"] == external)["home"] == "Deep"
    with pytest.raises(EvidenceError, match="checkpoint unavailable"):
        build_research_graph(ws, checkpoint_ref=cp, policy={"source_ids": ["science"]})


def test_work_area_override_does_not_change_record_kind(graph_case):
    c = graph_case
    graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"],
        home_overrides={node_identity(c["counter"]): "Judge", node_identity(c["claim"]): "Search"})
    nodes = {n["node_id"]: n for n in graph["nodes"]}
    assert nodes[node_identity(c["counter"])]["home"] == "Judge"
    assert nodes[node_identity(c["counter"])]["kind"] == "E"
    assert nodes[node_identity(c["claim"])]["home"] == "Search"
    assert nodes[node_identity(c["claim"])]["kind"] == "J"
    assert nodes[node_identity(c["counter"])]["kind_basis"] == "legacy_record_semantics_inferred"
