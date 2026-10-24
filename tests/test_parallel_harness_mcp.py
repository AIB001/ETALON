"""Cross-module synthetic acceptance; no native-host/scientific benefit claim."""
import json

from etalon.mcp import evidence, methods
from etalon.research.evidence_refs import file_identity, make_ref
from etalon.research.handoff import relocate_research_handoff
from etalon.research.entry import resolve_entry
from etalon import research_hooks
from test_mcp import Collector
from test_research_graph_memory import graph_case
from test_research_continuation import case, checkpoint, save, publish
from test_resource_usage import chain


def tools():
    collector = Collector()
    evidence.register(collector)
    methods.register(collector)
    return collector.tools


def test_entry_export_read_counterevidence_and_compaction_are_compatible(graph_case, tmp_path):
    t, c = tools(), graph_case
    baseline = file_identity(c["store"].path)
    out = json.loads(t["etalon_research_handoff"](str(c["entry"]), str(tmp_path / "export")))
    assert out["ok"] and out["originals"] == "verified"
    target = tmp_path / "relocated"
    relocated = relocate_research_handoff(tmp_path / "export", target)
    assert relocated["checkpoint_ref"] == c["checkpoint"]
    verified = json.loads(t["etalon_research_handoff_verify"](str(target), verify_originals=False))
    assert verified["status"] == "bundle_verified" and verified["originals"] == "not_checked"
    context = json.loads(t["etalon_research_context"](str(target / "entry.json"), view="summary"))
    assert context["ok"] and context["checkpoint_ref"] == c["checkpoint"]
    negative = json.loads(t["etalon_evidence_read"](ref=c["counter"],
        read_context=context["read_context"], view="artifact"))
    assert negative["ok"] and negative["verification"]["artifact"] == "verified"

    def event(event_id, kind="PreToolUse", session="A"):
        return research_hooks.handle_event(target / "entry.json", {
            "hook_event_name": kind, "session_id": session, "event_id": event_id,
            "tool_name": "mcp__etalon__etalon_evidence_read", "tool_use_id": event_id, "tool_input": {}},
            host="codex", state_dir=tmp_path / "hook-receipts")

    def payload(result):
        return json.loads(result["host_output"]["hookSpecificOutput"]["additionalContext"].split("\n", 1)[1])

    assert payload(event("first"))["delivery"]["mode"] == "baseline"
    assert payload(event("next"))["delivery"]["mode"] == "unchanged"
    event("compact", "PreCompact")
    restored = payload(event("resume"))
    assert restored["delivery"]["mode"] == "baseline"
    assert payload(event("new-chat", session="B"))["delivery"]["mode"] == "baseline"
    assert file_identity(c["store"].path) == baseline and not c["store"].actions()


def test_usage_chain_survives_handoff_without_backfilling_consumption(case, tmp_path):
    t = tools()
    payload = chain(case)
    j, _, authority, _ = case
    result = json.loads(t["etalon_resource_usage_record"](str(j.store.path), payload,
        str(tmp_path / "usage"), "research", "fixture"))
    assert result["ok"]
    usage_ref = make_ref("research", result["record_id"], result["record"])
    cp = checkpoint(case, payload["selection_ref"], step_refs=[payload["node_ref"],
        payload["evaluation_ref"], payload["selection_ref"]])
    authority.write_text("Paused fixture; no scientific execution.")
    cp["status_guards"] = [{"location": str(authority), **file_identity(authority), "state": "paused"}]
    cp_ref = save(case, cp)
    ws = publish(case)
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps({"workspace": str(ws), "checkpoint_ref": cp_ref}), encoding="utf-8")
    original = file_identity(j.store.path)
    exported = json.loads(t["etalon_research_handoff"](str(entry), str(tmp_path / "bundle")))
    assert exported["ok"]
    joined = json.loads(t["etalon_resource_usage_read"](usage_ref, entry=str(tmp_path / "bundle/entry.json")))
    assert joined["ok"] and joined["chain_complete"]
    assert joined["causal_benefit"] == "not_established"
    assert all(v["status"] == "unknown" for v in joined["observations"].values())
    assert file_identity(j.store.path) == original and not j.store.actions()


def test_mcp_refuses_relative_paths_and_empty_usage_before_writes(tmp_path):
    t = tools()
    assert not json.loads(t["etalon_research_handoff"]("relative.json", str(tmp_path / "never")))["ok"]
    assert not (tmp_path / "never").exists()
    assert not json.loads(t["etalon_resource_usage_record"](str(tmp_path / "never.sqlite"), {},
        str(tmp_path / "usage"), "fixture", "fixture"))["ok"]
    assert not (tmp_path / "never.sqlite").exists()
