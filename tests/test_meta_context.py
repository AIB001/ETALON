"""Small contract counterexamples; fixtures do not establish scientific benefit."""
import copy
import json
from pathlib import Path

import pytest

from etalon import host, skills
from etalon.active.schema import canonical
from etalon.research.continuation import read_content
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.meta_context import (
    build_task_workset, describe_resource, index_resources, make_task_profile,
    prepare_adaptation_context, publish_task_workset, read_task_workset,
    review_adaptation, selected_skill_members, validate_workset,
)
from etalon.research.publication import resume_publication
from etalon.research.research_context import build_research_context
from etalon import research_hooks as hooks
from test_research_publication import case, assert_old_unchanged


def part(name="base", conditions=None, *, requires=(), irrelevant=("target", "objective"), dependencies=(), stage="input"):
    return dict(part_id=name, conditions=conditions or {}, requires=list(requires), irrelevant=list(irrelevant),
                available_at=stage, dependencies=list(dependencies))


def profile(target="SND1", objective="pose", task_id="fixture"):
    return make_task_profile(task_id=task_id, question="Choose applicable resources", conditions={"target": target, "objective": objective},
        critical_conditions=["target", "objective"], available_stages=["input"])


def setup(case):
    entry = json.loads(case["parent"].read_text())
    ws = entry["workspace"]
    step = read_content(ws, case["checkpoint"]["step_refs"][0])
    ref = step["input_refs"][0]
    base = describe_resource(resource_id="base", kind="template", content_ref=ref,
        summary="Explicit fixture template", parts=[part()])
    method = describe_resource(resource_id="geometry", kind="method", content_ref=ref, summary="fixture partial method",
        parts=[part("parser"), part("fixed-core", {"target": "SND1", "objective": "pose"}, irrelevant=(),
                                  dependencies=[{"resource_id": "geometry", "part_id": "parser"}])])
    return ws, [base, method]


def review(p, idx, chosen, *, why="Fixture explicit adaptation"):
    decisions = [dict(resource_id=c["resource_id"], part_id=c["part_id"],
        disposition="reuse" if (c["resource_id"], c["part_id"]) in chosen else "exclude",
        reason="Fixture condition decision", reconsider_when="New conditions or evidence")
        for c in prepare_adaptation_context(p, idx)["candidates"]]
    return review_adaptation(p, idx, decisions=decisions, reviewer="fixture-review/1", rationale=why)


def workset(case, *, target="SND1", chosen=None, mode="selected", parent=None):
    ws, resources = setup(case)
    p, idx = profile(target), index_resources(resources, workspace=ws)
    chosen = chosen if chosen is not None else {("base", "base"), ("geometry", "parser"), ("geometry", "fixed-core")}
    rv = review(p, idx, chosen)
    return build_task_workset(p, idx, rv, template={"resource_id": "base", "part_id": "base"},
        mode=mode, fallback_reason="No applicable specialization" if mode == "template" else None, parent_workset_ref=parent)


def test_missing_conflicting_and_irrelevant_conditions_are_distinct(case):
    ws, resources = setup(case)
    idx = index_resources(resources, workspace=ws)
    def status(p):
        return {c["part_id"]: c for c in prepare_adaptation_context(p, idx)["candidates"]}
    assert status(profile(None))["fixed-core"]["applicability"] == "unknown"
    assert status(profile("other"))["fixed-core"]["applicability"] == "conflict"
    assert status(profile("other"))["parser"]["checks"]["target"] == "not_applicable"
    incomplete = describe_resource(resource_id="old", kind="experience", content_ref=resources[0]["content_ref"],
        summary="Legacy missing scope", parts=[part("adopt", {}, irrelevant=())])
    old_index = index_resources([*resources, incomplete], workspace=ws)
    assert prepare_adaptation_context(profile(), old_index)["candidates"][-1]["applicability"] == "unknown"
    with pytest.raises(EvidenceError, match="unknown/conflicting"):
        review(profile("other"), idx, {("base", "base"), ("geometry", "fixed-core")})


def test_partial_inheritance_and_template_fallback(case):
    partial = workset(case, target="other", chosen={("base", "base"), ("geometry", "parser")})
    assert {p["part_id"] for p in partial["enabled"]} == {"base", "parser"}
    assert any(d["part_id"] == "fixed-core" and d["disposition"] == "exclude" for d in partial["review"]["decisions"])
    fallback = workset(case, target="other", chosen={("base", "base")}, mode="template")
    assert fallback["mode"] == "template" and fallback["fallback_reason"]
    with pytest.raises(EvidenceError, match="dependency"):
        workset(case, chosen={("base", "base"), ("geometry", "fixed-core")})
    with pytest.raises(EvidenceError, match="only template"):
        workset(case, mode="template")


def test_hidden_descriptor_filtered_before_review_and_versions_verified(case):
    ws, resources = setup(case)
    hidden = describe_resource(resource_id="answer", kind="evidence", content_ref=resources[0]["content_ref"],
        summary="PRIVATE_ANSWER_SENTINEL", parts=[part()], visibility="evaluation_only")
    idx = index_resources([*resources, hidden], workspace=ws)
    assert "PRIVATE_ANSWER_SENTINEL" not in canonical(idx)
    assert idx["coverage"]["hidden_count"] == 1
    assert len(prepare_adaptation_context(profile(), idx)["candidates"]) == 3
    Path(ws).with_name("evidence.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(EvidenceError):
        index_resources(resources, workspace=ws)


def test_draft_inactive_publication_atomic_replay_and_partial_replacement(case, tmp_path):
    ws = workset(case)
    before = build_research_context(case["parent"], view="summary")
    assert before["task_workset"]["status"] == "legacy_unscoped"
    intent = publish_task_workset(case["entry"], ws, parent_entry=case["parent"], operation_id="select",
        directory=tmp_path / "meta")
    assert not case["entry"].exists()
    resume_publication(intent["intent_path"])
    active_entry = json.loads(case["entry"].read_text())
    active = read_task_workset(active_entry["workspace"], active_entry["checkpoint_ref"])
    assert active["workset"] == ws
    assert publish_task_workset(case["entry"], ws, parent_entry=case["parent"], operation_id="select", directory=tmp_path / "meta")["status"] == "published"
    context = build_research_context(case["entry"], view="summary")
    assert context["task_workset"]["digest"] == ws["digest"]
    assert not context["automatic_retry"]
    nxt = workset(case, target="other", chosen={("base", "base"), ("geometry", "parser")}, parent=active["ref"])
    intent2 = publish_task_workset(case["entry"], nxt, operation_id="switch", directory=tmp_path / "switch",
        expected_entry_identity=file_identity(case["entry"]))
    resume_publication(intent2["intent_path"])
    assert build_research_context(case["entry"], view="summary")["task_workset"]["digest"] == nxt["digest"]
    assert_old_unchanged(case)


def test_cross_task_and_mutated_review_cannot_activate(case, tmp_path):
    ws = workset(case)
    bad = copy.deepcopy(ws)
    bad["enabled"] = []
    with pytest.raises(EvidenceError, match="digest"):
        validate_workset(bad)
    p = profile(task_id="another-task")
    rv = review(p, ws["index"], {("base", "base")})
    other = build_task_workset(p, ws["index"], rv, template=ws["template"], mode="template", fallback_reason="No transfer")
    with pytest.raises(EvidenceError, match="own initial checkpoint"):
        publish_task_workset(case["entry"], other, parent_entry=case["parent"], operation_id="wrong", directory=tmp_path / "wrong")


def test_hook_workset_transition_resets_baseline_and_same_version_can_delta(tmp_path, monkeypatch):
    ctx = {"task_id": "fixture", "source_watermarks": [], "source_issues": [],
        "publications": {"status": "known", "operations": []}, "padding": "x" * 4000,
        "task_workset": {"status": "active", "digest": "first", "enabled": [{"resource_id": "old", "part_id": "core"}]}}
    monkeypatch.setattr(hooks, "_build_context", lambda *a, **k: copy.deepcopy(ctx))
    def run(eid, name="PreToolUse"):
        return hooks.handle_event({}, {"session_id": "same", "event_id": eid, "hook_event_name": name,
            "tool_name": "mcp__etalon__etalon_research_context"}, host="codex", state_dir=tmp_path)
    first = run("one", "SessionStart")
    ctx["task_workset"] = {"status": "active", "digest": "next", "enabled": []}
    second = run("two")
    delivery = second["receipt"]["injection"]["delivery"]
    assert delivery["mode"] == "baseline" and delivery["reason"] == "task_or_workset_changed"
    assert delivery["workset_transition"]["deactivated"] == [{"resource_id": "old", "part_id": "core"}]
    assert run("two")["duplicate"]
    ctx["new_result"] = "archived result"
    third = run("three")
    assert third["receipt"]["injection"]["delivery"]["mode"] == "delta"
    assert not third["receipt"]["rsi_triggered"]


def test_export_omits_unselected_skill_members_and_preserves_dependencies(case, tmp_path):
    ws, resources = setup(case)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text("---\nname: fixture-skill\ndescription: Test partial bundle\n---\nUse helper.py.\n", encoding="utf-8")
    (bundle / "helper.py").write_text("# selected helper", encoding="utf-8")
    (bundle / "snd1-only.json").write_text('{"private_setting": 1}', encoding="utf-8")
    manifest, _ = skills.snapshot("fixture-skill", bundle_ref={"root": str(bundle)})
    skill = describe_resource(resource_id="skill", kind="skill", content_ref={"slug": "fixture-skill", "manifest_sha256": manifest["manifest_sha256"],
        "bundle_ref": {"root": str(bundle), "manifest_sha256": manifest["manifest_sha256"]}}, summary="fixture",
        parts=[part("SKILL.md", dependencies=[{"resource_id": "skill", "part_id": "helper.py"}]), part("helper.py"),
               part("snd1-only.json", {"target": "SND1", "objective": "pose"}, irrelevant=())])
    idx = index_resources([resources[0], skill], workspace=ws)
    p = profile("other")
    rv = review(p, idx, {("base", "base"), ("skill", "SKILL.md"), ("skill", "helper.py")})
    value = build_task_workset(p, idx, rv, template={"resource_id": "base", "part_id": "base"})
    assert selected_skill_members(value)[0]["members"] == ["SKILL.md", "helper.py"]
    host.export(tmp_path / "export", task_workset=value)
    exported = tmp_path / "export/.agents/skills/fixture-skill"
    assert (exported / "helper.py").exists() and not (exported / "snd1-only.json").exists()
    assert host.verify(tmp_path / "export")["matches_current_source"]
    assert not (tmp_path / "export/.agents/skills/etalon-campaign").exists()
