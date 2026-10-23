"""Read/cache race fixtures only; no scientific execution or deployment."""
import base64
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from etalon import research_hooks as hooks
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import canonical
from etalon.research import ResearchJournal
from etalon.research import continuation, evidence, evidence_refs, read_scope
from etalon.research.continuation import read_content, record_content
from etalon.research.entry import make_read_context
from etalon.research.evidence import query_evidence, read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity, load_workspace, make_ref
from etalon.research.graph_memory import build_research_graph, node_identity
from etalon.research.read_scope import CURRENT, verified_read
from etalon.research.research_context import build_research_context
from test_research_graph_memory import graph_case


def _deny(ref):
    return {"record_rules": [{"source_id": ref["source_id"], "record_id": ref["record_id"],
                               "allowed_purposes": []}]}


def _same_size_change(path):
    before = path.stat()
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def _hook(c, tmp_path, event_id, *, event="PreToolUse", policy=None):
    return hooks.handle_event(c["entry"], {"hook_event_name": event, "session_id": "fixture-session",
        "event_id": event_id, "tool_name": "mcp__etalon__etalon_research_context"},
        host="codex", state_dir=tmp_path / "receipts", policy=policy, max_bytes=65536)


def _controls(result):
    text = result["host_output"]["hookSpecificOutput"]["additionalContext"]
    payload = json.loads(text.split("\n", 1)[1])
    return payload.get("context", payload.get("current_controls"))


@pytest.mark.parametrize("narrow_first", [False, True])
def test_policy_views_remain_isolated_inside_one_read(graph_case, narrow_first):
    c = graph_case
    wide = make_read_context(c["workspace"], c["checkpoint"])
    narrow = make_read_context(c["workspace"], c["checkpoint"], policy=_deny(c["unrelated"]))
    ordered = [narrow, wide, narrow, wide] if narrow_first else [wide, narrow, wide, narrow]
    with verified_read() as scope:
        for context in ordered:
            result = read_evidence(ref=c["unrelated"], read_context=context)
            assert (result["availability"] == "available") == (context is wide)
            page = query_evidence(read_context=context)
            assert (c["unrelated"] in [item["ref"] for item in page["items"]]) == (context is wide)
        assert scope.stats()["within_read_hits"] + scope.stats()["collection_hits"] > 0
    assert CURRENT.get() is None


@pytest.mark.parametrize("changed", ["projection", "source", "entry", "workspace", "guard"])
def test_same_length_changes_after_projection_are_caught_before_return(graph_case, changed):
    c = graph_case
    paths = {"projection": Path(c["journal"].get(c["claim"]["record_id"])["path"]),
             "source": c["store"].path, "entry": c["entry"], "workspace": c["workspace"],
             "guard": Path(c["checkpoint_content"]["status_guards"][0]["location"])}
    with pytest.raises(EvidenceError):
        with verified_read():
            result = build_research_context(c["entry"], view="summary")
            assert result["continuation"]["authority_state"] == "paused"
            _same_size_change(paths[changed])
    assert CURRENT.get() is None


def test_new_wal_content_is_detected_even_when_base_database_hash_is_unchanged(graph_case):
    c = graph_case
    writer = sqlite3.connect(c["store"].path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c["publish"]()
        original = file_identity(c["store"].path)
        with pytest.raises(EvidenceError, match="WAL"):
            with verified_read():
                build_research_context(c["entry"], view="summary")
                writer.execute("INSERT INTO metadata(key,body) VALUES (?,?)", ("read-race-fixture", "{}"))
                writer.commit()
                assert file_identity(c["store"].path) == original
                assert Path(str(c["store"].path) + "-wal").stat().st_size > 0
        assert CURRENT.get() is None
    finally:
        writer.close()


def test_returned_dict_mutation_does_not_poison_subsequent_reads(graph_case):
    c = graph_case
    with verified_read():
        workspace = load_workspace(c["workspace"])
        original_source = workspace.source("science")
        workspace.source("science")["record_ids"] = []
        workspace.manifest["sources"].clear()
        assert workspace.source("science") == original_source
        original = read_evidence(workspace, c["evidence"])
        expected = copy.deepcopy(original)
        original["record"]["role"] = "sealed_holdout"
        original["record"]["bindings"]["injected"] = True
        assert read_evidence(workspace, c["evidence"]) == expected
        checkpoint = read_content(workspace, c["checkpoint"])
        checkpoint["step_refs"].clear()
        assert read_content(workspace, c["checkpoint"])["step_refs"] == c["checkpoint_content"]["step_refs"]
        graph = build_research_graph(workspace, checkpoint_ref=c["checkpoint"])
        expected_graph = copy.deepcopy(graph)
        graph["nodes"].clear()
        graph["coverage"][0]["status"] = "unavailable"
        assert build_research_graph(workspace, checkpoint_ref=c["checkpoint"]) == expected_graph


def test_exception_clears_current_scope_and_next_read_is_fresh(graph_case):
    c = graph_case
    with pytest.raises(RuntimeError, match="fixture abort"):
        with verified_read() as outer:
            query_evidence(c["workspace"])
            with verified_read() as nested:
                assert nested is outer
                raise RuntimeError("fixture abort")
    assert CURRENT.get() is None
    with verified_read() as next_read:
        assert next_read is not outer
        assert read_evidence(c["workspace"], c["evidence"])["availability"] == "available"
        assert next_read.stats()["fresh_sources"] > 0


def test_reverse_counterclaim_from_other_task_and_source_survives_cached_reads(graph_case, tmp_path):
    c = graph_case
    body = {"schema": "etalon-research-claim/1", "task_id": "independent-other-task",
            "question": "Does the old interpretation transfer?", "statement": "CROSS_TASK_COUNTER_SENTINEL",
            "epistemic_status": "interpretation", "subjects": ["fixture"], "conditions": ["other task"],
            "limitations": [], "support_refs": [], "counter_refs": [c["claim"]],
            "method_refs": [], "supersedes": []}
    store = CampaignStore(tmp_path / "other.sqlite")
    store.configure(CampaignSpec("other", 0, "wall_second", "fixture/1"),
                    [Endpoint("other", "fixture", "unmeasured", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    rid = record_content(journal, body, tmp_path / "other-content", source_id="other", source_kind="fixture")
    ref = make_ref("other", rid, journal.get(rid))
    manifest = json.loads(c["workspace"].read_text(encoding="utf-8"))
    manifest["sources"].append({"source_id": "other", "kind": "journal", "location": str(store.path),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}})
    c["workspace"].write_text(json.dumps(manifest), encoding="utf-8")
    with verified_read() as first:
        narrow = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], policy={"source_ids": ["science"]})
        assert node_identity(ref) not in {node["node_id"] for node in narrow["nodes"]}
        wide = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])
        node = next(node for node in wide["nodes"] if node["ref"] == ref)
        assert node["task_id"] == "independent-other-task" and node["scope"] == "explicit_shared_reference"
        assert any(edge["kind"] == "refutes" and edge["basis_ref"] == ref for edge in wide["edges"])
    with verified_read(first.snapshot()) as second:
        repeated = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"])
    assert second.stats()["reused_sources"] == 2
    assert any(node["ref"] == ref for node in repeated["nodes"])


def test_hook_cache_hit_rehashes_originals_without_decoding_again(graph_case, tmp_path, monkeypatch):
    c = graph_case
    first = _hook(c, tmp_path, "one", event="SessionStart")
    assert first["ok"] and first["receipt"]["read_stats"]["fresh_sources"] > 0
    hashed, decoded = [], []
    original_hash, original_decode = evidence_refs.file_identity, evidence._source_uncached
    def hashing(path):
        hashed.append(Path(path).resolve())
        return original_hash(path)
    def decoding(*args, **kwargs):
        decoded.append(True)
        return original_decode(*args, **kwargs)
    monkeypatch.setattr(evidence_refs, "file_identity", hashing)
    monkeypatch.setattr(continuation, "file_identity", hashing)
    monkeypatch.setattr(evidence, "_source_uncached", decoding)
    for event_id in ("two", "three"):
        hashed.clear()
        result = _hook(c, tmp_path, event_id)
        assert result["ok"] and result["receipt"]["read_stats"]["reused_sources"] > 0
        assert not decoded
        assert c["store"].path.resolve() in hashed
        assert Path(c["journal"].get(c["claim"]["record_id"])["path"]).resolve() in hashed


@pytest.mark.parametrize("change", ["permission", "source-version", "implementation"])
def test_changed_cache_inputs_cannot_reuse_previous_source_view(graph_case, tmp_path, monkeypatch, change):
    c = graph_case
    assert _hook(c, tmp_path, "one", event="SessionStart")["ok"]
    policy = None
    if change == "permission":
        policy = _deny(c["evidence"])
    elif change == "source-version":
        c["save"]({**c["step"], "reason": "New fixture source version, no execution"})
        c["publish"]()
    else:
        previous_identity = read_scope.implementation_identity()
        monkeypatch.setattr(read_scope, "implementation_identity", lambda: "different-" + previous_identity)
    result = _hook(c, tmp_path, "two", policy=policy)
    if change == "permission":
        assert not result["ok"]
        assert result["host_output"] == {} and result["receipt"]["injection"] is None
    else:
        assert result["ok"]
        assert result["receipt"]["read_stats"]["reused_sources"] == 0
        assert result["receipt"]["read_stats"]["fresh_sources"] > 0


@pytest.mark.parametrize("damage", [b"{broken", b'{"schema":"wrong-but-valid-json"}'])
def test_corrupt_hook_cache_falls_back_to_original_sources(graph_case, tmp_path, damage):
    first = _hook(graph_case, tmp_path, "one", event="SessionStart")
    assert first["ok"]
    cache_path = hooks._receipt_io_path(Path(first["receipt_path"]).parent / "source-read-cache")
    assert cache_path.is_file()
    cache_path.write_bytes(damage)
    result = _hook(graph_case, tmp_path, "two")
    assert result["ok"]
    assert result["receipt"]["read_stats"]["reused_sources"] == 0
    assert result["receipt"]["read_stats"]["fresh_sources"] > 0
    assert _controls(result)["continuation"]["authority_state"] == "paused"


def test_cache_write_error_preserves_normal_hook_delivery(graph_case, tmp_path, monkeypatch):
    save = hooks._save_cursor
    def fail_cache_only(path, value):
        if Path(path).name == "source-read-cache":
            raise OSError("fixture unavailable cache storage")
        return save(path, value)
    monkeypatch.setattr(hooks, "_save_cursor", fail_cache_only)
    for event_id in ("one", "two"):
        result = _hook(graph_case, tmp_path, event_id)
        assert result["ok"] and result["receipt"]["injection"] is not None
        assert result["receipt"]["read_stats"]["cache_persisted"] is False
        assert result["receipt"]["read_stats"]["fresh_sources"] > 0
        assert hooks._receipt_io_path(Path(result["receipt_path"])).is_file()
        assert _controls(result)["continuation"]["authority_state"] == "paused"


def test_guard_change_between_cached_events_cannot_return_old_active_state(graph_case, tmp_path):
    c = graph_case
    authority = Path(c["checkpoint_content"]["status_guards"][0]["location"])
    authority.write_text("Fixture authority ACTIVE; still no execution permission.", encoding="utf-8")
    checkpoint = c["save"]({**c["checkpoint_content"], "supersedes": [c["checkpoint"]],
        "status_guards": [{"location": str(authority), **file_identity(authority), "state": "active"}]})
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": str(c["workspace"]), "checkpoint_ref": checkpoint}), encoding="utf-8")
    first = _hook(c, tmp_path, "one", event="SessionStart")
    assert first["ok"] and _controls(first)["continuation"]["authority_state"] == "active_at_read"
    authority.write_text("Fixture authority PAUSED; still no execution permission.", encoding="utf-8")
    second = _hook(c, tmp_path, "two")
    assert second["ok"] and second["receipt"]["read_stats"]["reused_sources"] > 0
    assert _controls(second)["continuation"]["authority_state"] == "review_required"
    assert _controls(second)["continuation"]["review_required"]
    assert not second["receipt"]["automatic_retry"]


@pytest.mark.parametrize("event", ["SessionStart", "PreCompact"])
def test_hook_final_verification_failure_never_emits_prebuilt_context(graph_case, tmp_path, monkeypatch, event):
    original = read_scope.ReadScope.verify
    def fail_after_real_verification(scope):
        original(scope)
        raise EvidenceError("fixture failure at the return boundary")
    monkeypatch.setattr(read_scope.ReadScope, "verify", fail_after_real_verification)
    result = _hook(graph_case, tmp_path, "boundary-failure", event=event)
    assert not result["ok"]
    assert result["host_output"] == {}
    assert result["receipt"]["injection"] is None
    assert result["receipt"]["checkpoint_ack"] is None
    assert CURRENT.get() is None


def test_native_hook_processes_reuse_cache_and_reject_changed_projection(graph_case, tmp_path):
    c = graph_case
    state = tmp_path / "native-receipts"
    command = [sys.executable, "-m", "etalon.research_hooks", "--entry", str(c["entry"]),
               "--state-dir", str(state), "--host", "codex", "--max-bytes", "65536"]
    def run(event_id, kind):
        return subprocess.run(command, input=json.dumps({"hook_event_name": kind, "session_id": "native-fixture",
            "event_id": event_id, "tool_name": "mcp__etalon__etalon_research_context"}),
            capture_output=True, text=True, encoding="utf-8")
    first = run("one", "SessionStart")
    assert first.returncode == 0, first.stderr
    second = run("two", "PreToolUse")
    assert second.returncode == 0, second.stderr
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in state.rglob("*.json")]
    reused = next(receipt for receipt in receipts if receipt["event_identity"]["value"] == "two")
    assert reused["read_stats"]["reused_sources"] > 0
    _same_size_change(Path(c["journal"].get(c["claim"]["record_id"])["path"]))
    changed = run("three", "PreToolUse")
    assert changed.returncode == 1
    assert changed.stdout == ""
    assert json.loads(changed.stderr)["ok"] is False
    assert not c["store"].actions()


@pytest.fixture
def growing_checkpoint(graph_case):
    c = graph_case
    budget = 16384
    original = build_research_context(c["entry"], view="summary", max_bytes=budget)
    steps = []
    for number in range(3):
        steps.append(c["save"]({**c["step"], "reason": f"Archived fixture review {number}",
            "detail": {**c["step"]["detail"], "alternatives": [
                {"branch_id": f"alternative-{index}", "disposition": "skip",
                 "reason": "Retained full condition evidence: " + "x" * 430}
                for index in range(24)]}}))
    checkpoint = c["save"]({**c["checkpoint_content"],
        "step_refs": [c["decision"], *steps], "supersedes": [c["checkpoint"]]})
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": checkpoint}), encoding="utf-8")
    return c, original, steps, checkpoint, budget


def test_growing_checkpoint_summary_omits_tail_body_but_preserves_exact_refs_and_controls(growing_checkpoint):
    c, original, steps, checkpoint, budget = growing_checkpoint
    source_identity = file_identity(c["store"].path)
    result = build_research_context(c["entry"], view="summary", max_bytes=budget)
    assert len(canonical(result).encode("utf-8")) <= budget
    assert original["checkpoint_progress"]["step_count"] == 1
    assert result["checkpoint_progress"]["step_count"] == 4
    assert result["checkpoint_ref"] == checkpoint
    assert result["checkpoint_progress"]["tail_step_refs"] == [steps[-1]]
    refs = {row["node_id"]: row["ref"] for row in result["reference_index"]}
    assert refs[node_identity(steps[-1])] == steps[-1]
    assert result["selection"]["tail_details_omitted"] > 0
    assert result["selection"]["excluded_by_budget"] > 0 and result["truncated"]
    assert steps[-1] not in [item["ref"] for item in result["items"]]
    assert not result["selection"]["graph_truncated"]
    assert refs[node_identity(c["counter"])] == c["counter"]
    assert refs[node_identity(c["claim"])] == c["claim"]
    graph = build_research_graph(c["workspace"], checkpoint_ref=checkpoint)
    assert not graph["truncated"]
    assert result["relations"] == [edge for edge in graph["edges"] if edge["kind"] == "refutes"]
    assert any(edge["kind"] == "refutes" and edge["from"] == node_identity(c["counter"])
               and edge["to"] == node_identity(c["claim"]) for edge in result["relations"])
    assert result["counterevidence"]["selected_relationship_count"] == result["counterevidence"]["relationship_count"]
    assert result["branches"] == original["branches"]
    assert result["continuation"]["authority_state"] == "paused"
    assert result["continuation"]["pending"] == []
    assert result["publications"]["incomplete_count"] == 0
    assert result["execution_permission"] == "not_granted" and not result["automatic_retry"]
    recovered = read_evidence(ref=refs[node_identity(steps[-1])], read_context=result["read_context"],
                              view="artifact", limit=32768)
    assert recovered["verification"]["artifact"] == "verified"
    assert recovered["fragment"]["next_offset"] is None
    content = json.loads(base64.b64decode(recovered["fragment"]["content"]))
    assert content == read_content(c["workspace"], steps[-1])
    assert len(content["detail"]["alternatives"]) == 24
    delivered = hooks.handle_event(c["entry"], {"hook_event_name": "SessionStart",
        "session_id": "small-budget-fixture", "event_id": "grown-checkpoint"}, host="codex",
        state_dir=c["entry"].parent / "bounded-hook-receipts", max_bytes=budget)
    assert delivered["ok"]
    assert _controls(delivered)["selection"]["tail_details_omitted"] > 0
    assert _controls(delivered)["checkpoint_progress"]["tail_step_refs"] == [steps[-1]]
    assert _controls(delivered)["continuation"]["authority_state"] == "paused"
    assert file_identity(c["store"].path) == source_identity and not c["store"].actions()


@pytest.mark.parametrize("view", ["node", "summary"])
def test_explicit_tail_node_cannot_be_silently_omitted_when_budget_is_insufficient(growing_checkpoint, view):
    c, _, steps, _, budget = growing_checkpoint
    with pytest.raises(EvidenceError, match="selected node and control metadata exceed context bound"):
        build_research_context(c["entry"], view=view, node_id=node_identity(steps[-1]), max_bytes=budget)
