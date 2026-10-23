"""Adversarial local API fixtures; no scientific execution or deployment evidence."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from etalon import host, research_hooks as hooks, skills
from etalon.active import CampaignStore
from etalon.active.schema import canonical
from etalon.data.artifacts import seal
from etalon.research import Artifact
from etalon.research.entry import make_read_context
from etalon.research.evidence import query_evidence, read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.meta_context import (
    WORKSET, build_task_workset, describe_resource, index_resources,
    publish_task_workset, read_task_workset,
)
from etalon.research.publication import STAGES, inspect_publication, prepare_publication, resume_publication
from etalon.research.research_context import build_research_context
from test_meta_context import part, profile, review, setup, workset
from test_research_graph_memory import graph_case
from test_research_publication import assert_old_unchanged, case


def _publish(case, value, directory, operation="select", *, initial=True):
    prepared = publish_task_workset(
        case["entry"], value, operation_id=operation, directory=directory,
        parent_entry=case["parent"] if initial else None,
        expected_entry_identity=None if initial else file_identity(case["entry"]),
    )
    resume_publication(prepared["intent_path"])
    entry = json.loads(case["entry"].read_text(encoding="utf-8"))
    active = read_task_workset(entry["workspace"], entry["checkpoint_ref"])
    return entry, active


def _skill_resource(root, *, resource_id="skill", slug="fixture-skill", name="fixture-skill", text="First version"):
    root.mkdir()
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Explicit test fixture\n---\n{text}\n",
        encoding="utf-8",
    )
    manifest, _ = skills.snapshot(slug, bundle_ref={"root": str(root)})
    return describe_resource(
        resource_id=resource_id, kind="skill", summary="External fixture skill",
        content_ref={"slug": slug, "manifest_sha256": manifest["manifest_sha256"],
                     "bundle_ref": {"root": str(root), "manifest_sha256": manifest["manifest_sha256"]}},
        parts=[part("SKILL.md")],
    )


def _with_skills(case, resources, *, parent=None):
    workspace, existing = setup(case)
    index = index_resources([existing[0], *resources], workspace=workspace)
    task = profile()
    selected = {("base", "base"), *((r["resource_id"], "SKILL.md") for r in resources)}
    return build_task_workset(
        task, index, review(task, index, selected),
        template={"resource_id": "base", "part_id": "base"}, parent_workset_ref=parent,
    )


@pytest.mark.parametrize("view", ["record", "summary", "artifact", "lineage"])
def test_published_workset_cannot_bypass_original_record_denial(case, tmp_path, view):
    value = workset(case)
    entry, active = _publish(case, value, tmp_path / "publication")
    original = value["index"]["resources"][0]["content_ref"]
    assert read_evidence(entry["workspace"], active["ref"], view="artifact")["availability"] == "available"
    policy = {"record_rules": [{"source_id": original["source_id"],
                                "record_id": original["record_id"], "allowed_purposes": []}]}
    context = make_read_context(entry["workspace"], entry["checkpoint_ref"], policy=policy)
    denied = read_evidence(ref=active["ref"], read_context=context, view=view)
    assert denied["availability"] == "unavailable"
    assert "record" not in denied and "fragment" not in denied
    assert active["ref"] not in [row["ref"] for row in query_evidence(read_context=context)["items"]]
    assert_old_unchanged(case)


@pytest.mark.parametrize("relation", ["support_refs", "counter_refs"])
def test_resource_evidence_relations_verify_original_bytes(graph_case, relation):
    c = graph_case
    resource = describe_resource(
        resource_id="conditional-method", kind="method", content_ref=c["evidence"],
        summary="Scope depends on a separate fixture artifact", parts=[part()],
        **{relation: [c["counter"]]},
    )
    assert index_resources([resource], workspace=c["workspace"])["coverage"]["selected_count"] == 1
    path = Path(c["journal"].get(c["counter"]["record_id"])["path"])
    old = path.read_bytes()
    path.write_bytes(bytes([old[0] ^ 1]) + old[1:])  # Same size, different original bytes.
    with pytest.raises(EvidenceError):
        index_resources([resource], workspace=c["workspace"])


@pytest.mark.parametrize("relation", ["content_ref", "support_refs", "counter_refs"])
def test_data_snapshot_member_bytes_are_verified(case, tmp_path, relation):
    root = tmp_path / "snapshot"
    root.mkdir()
    member = root / "result.txt"
    member.write_text("fixture result bytes", encoding="utf-8")
    seal(root, kind="fixture", result={}, infrastructure={})
    source = {"source_id": "data", "kind": "data_snapshot", "location": str(root / "snapshot.json"),
              "identity": {"kind": "snapshot_sha256", "value": file_identity(root / "snapshot.json")["sha256"]}}
    workspace, resources = setup(case)
    manifest = json.loads(Path(workspace).read_text(encoding="utf-8"))
    manifest["sources"].append(source)
    published = tmp_path / "with-snapshot.json"
    published.write_text(json.dumps(manifest), encoding="utf-8")
    record = query_evidence(published, filters={"source_ids": ["data"]})["items"][0]["ref"]
    member_ref = read_evidence(published, record, view="artifact", member="result.txt")["ref"]
    fields = {"content_ref": resources[0]["content_ref"], relation: member_ref if relation == "content_ref" else [member_ref]}
    resource = describe_resource(resource_id="snapshot-method", kind="method", summary="Pinned snapshot member",
                                 parts=[part()], **fields)
    assert index_resources([resource], workspace=published)["coverage"]["selected_count"] == 1
    member.write_bytes(b"X" + member.read_bytes()[1:])
    with pytest.raises(EvidenceError):
        index_resources([resource], workspace=published)


@pytest.mark.parametrize("other_task", [False, True], ids=["ordinary-step", "another-task-step"])
def test_activation_rejects_superseding_nonpredecessor(graph_case, tmp_path, other_task):
    c = graph_case
    base = describe_resource(resource_id="base", kind="template", content_ref=c["evidence"],
                             summary="Fixture base", parts=[part()],
                             support_refs=[c["counter"]], counter_refs=[c["unrelated"]])
    task = profile(task_id=c["checkpoint_content"]["task_id"])
    index = index_resources([base], workspace=c["workspace"])
    value = build_task_workset(task, index, review(task, index, {("base", "base")}),
                               template={"resource_id": "base", "part_id": "base"})
    path = tmp_path / "workset.json"
    path.write_text(canonical(value), encoding="utf-8")
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic",
                        bindings={"schema": WORKSET, "meta_activation": WORKSET,
                                  "task_id": task["task_id"], "evidence_refs": sorted(
                                      [c["evidence"], c["counter"], c["unrelated"]], key=canonical)})
    c["journal"].record(artifact)
    ref = make_ref("science", artifact.id, artifact.as_dict())
    unrelated = c["save"]({**c["step"], "task_id": "another-task" if other_task else task["task_id"],
                            "reason": "An unrelated ordinary decision, not a workset predecessor"})
    activation = c["save"]({**c["step"], "output_refs": [ref], "supersedes": [unrelated]})
    checkpoint = c["save"]({**c["checkpoint_content"], "step_refs": [c["decision"], activation]})
    with pytest.raises(EvidenceError, match="(non-predecessor|another task|parent|supersede)"):
        read_task_workset(c["publish"](), checkpoint)


@pytest.mark.parametrize("omitted", [None, "content", "support", "counter", "parent", "bindings-field"],
                         ids=["complete", "missing-content", "missing-support", "missing-counter",
                              "missing-parent", "missing-bindings-field"])
def test_generic_publication_cannot_activate_incomplete_evidence_bindings(graph_case, tmp_path, omitted):
    c = graph_case
    base = describe_resource(resource_id="base", kind="template", content_ref=c["evidence"],
                             summary="Depends on distinct support and counterevidence", parts=[part()],
                             support_refs=[c["counter"]], counter_refs=[c["unrelated"]])
    task = profile(task_id=c["checkpoint_content"]["task_id"])
    index = index_resources([base], workspace=c["workspace"])
    reviewed = review(task, index, {("base", "base")})
    root = build_task_workset(task, index, reviewed, template={"resource_id": "base", "part_id": "base"})
    prepared = publish_task_workset(c["entry"], root, operation_id="root", directory=tmp_path / "root",
                                    expected_entry_identity=file_identity(c["entry"]))
    resume_publication(prepared["intent_path"])
    entry = json.loads(c["entry"].read_text(encoding="utf-8"))
    previous = read_task_workset(entry["workspace"], entry["checkpoint_ref"])
    value = build_task_workset(task, index, reviewed, template=root["template"],
                               parent_workset_ref=previous["ref"])
    path = tmp_path / "successor-workset.json"
    path.write_text(canonical(value), encoding="utf-8")
    dependencies = {"content": c["evidence"], "support": c["counter"],
                    "counter": c["unrelated"], "parent": previous["ref"]}
    bindings = {"schema": WORKSET, "meta_activation": WORKSET, "task_id": task["task_id"],
                "evidence_refs": sorted((ref for name, ref in dependencies.items() if name != omitted), key=canonical)}
    if omitted == "bindings-field":
        del bindings["evidence_refs"]
    activation = {**c["step"], "reason": "Generic archival API fixture with a claimed activation",
                  "input_refs": [previous["activation_ref"]], "supersedes": [previous["activation_ref"]],
                  "output_refs": [{"publication_ref": "workset"}],
                  "detail": {**c["step"]["detail"], "basis_refs": [previous["activation_ref"]]}}
    # Ordinary archival publication accepts opaque Artifacts. Its success must
    # not make an incomplete activation envelope authoritative for task context.
    prepared = prepare_publication(c["entry"], directory=tmp_path / "successor", operation_id="successor",
        expected_entry_identity=file_identity(c["entry"]),
        results=[{"name": "workset", "path": str(path), "identity": file_identity(path),
                  "source_kind": "fixture", "role": "diagnostic", "bindings": bindings}],
        steps=[{"name": "activation", "content": activation}])
    assert resume_publication(prepared["intent_path"])["status"] == "published"
    entry = json.loads(c["entry"].read_text(encoding="utf-8"))
    if omitted is None:
        assert read_task_workset(entry["workspace"], entry["checkpoint_ref"])["workset"] == value
    else:
        with pytest.raises(EvidenceError, match="complete evidence dependencies"):
            read_task_workset(entry["workspace"], entry["checkpoint_ref"])


def test_same_named_resource_version_replacement_resets_hook_baseline(case, tmp_path):
    first_skill = _skill_resource(tmp_path / "v1", text="First exact instruction version")
    first = _with_skills(case, [first_skill])
    _, active = _publish(case, first, tmp_path / "first")

    def event(event_id, kind):
        return hooks.handle_event(case["entry"],
            {"session_id": "same-task", "event_id": event_id, "hook_event_name": kind,
             "tool_name": "mcp__etalon__etalon_research_context"},
            host="codex", state_dir=tmp_path / "receipts", max_bytes=65536)

    initial = event("start", "SessionStart")
    assert initial["ok"]
    second_skill = _skill_resource(tmp_path / "v2", text="A distinct reviewed instruction version")
    second = _with_skills(case, [second_skill], parent=active["ref"])
    assert first["enabled"] == second["enabled"]
    _publish(case, second, tmp_path / "second", operation="replace", initial=False)
    changed = event("replace", "PreToolUse")
    assert changed["ok"]
    delivery = changed["receipt"]["injection"]["delivery"]
    assert delivery["mode"] == "baseline"
    assert delivery["reason"] == "task_or_workset_changed"
    transition = delivery["workset_transition"]
    assert transition["deactivated"] == [{"resource_id": "skill", "part_id": "SKILL.md",
                                           "resource_digest": first_skill["digest"]}]
    assert transition["activated"] == [{"resource_id": "skill", "part_id": "SKILL.md",
                                         "resource_digest": second_skill["digest"]}]
    assert not changed["receipt"]["rsi_triggered"]
    assert_old_unchanged(case)


@pytest.mark.parametrize("name", ["../escape", "nested/escape", "..\\escape", "C:escape"])
def test_external_skill_frontmatter_cannot_escape_export_directory(tmp_path, name):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Deliberately unsafe test fixture\n---\nFixture.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        skills.snapshot("safe-logical-slug", bundle_ref={"root": str(bundle)})
    assert not (tmp_path / "escape").exists()


def test_different_skill_slugs_cannot_overwrite_same_actual_host_name(case, tmp_path):
    one = _skill_resource(tmp_path / "one", resource_id="one", slug="one", name="collision", text="One")
    two = _skill_resource(tmp_path / "two", resource_id="two", slug="two", name="collision", text="Two")
    value = _with_skills(case, [one, two])
    target = tmp_path / "export"
    with pytest.raises(ValueError, match="collid"):
        host.export(target, task_workset=value)
    assert not target.exists()


@pytest.mark.parametrize("extra", ["fixture-skill/unreviewed.py", "unselected-skill/SKILL.md"])
def test_host_verify_detects_extra_skill_files(case, tmp_path, extra):
    skill = _skill_resource(tmp_path / "bundle")
    target = tmp_path / "export"
    host.export(target, task_workset=_with_skills(case, [skill]))
    assert host.verify(target)["export_matches_manifest"]
    added = target / ".agents" / "skills" / extra
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text("Unreviewed test instruction", encoding="utf-8")
    checked = host.verify(target)
    assert not checked["export_matches_manifest"]
    assert added.relative_to(target).as_posix() in checked["unexpected"]


def test_changed_task_conditions_disable_previous_enabled_parts(case, tmp_path):
    value = workset(case)
    _publish(case, value, tmp_path / "publication")
    original = build_research_context(case["entry"], view="summary")
    changed = build_research_context(case["entry"], view="summary", conditions={"target": "another-target"})
    assert original["task_workset"]["status"] == "active"
    assert original["task_workset"]["enabled"]
    current = changed["task_workset"]
    assert current["status"] == "review_required"
    assert current["changed_conditions"] == ["target"]
    assert current["enabled"] == [] and current["enabled_versions"] == []
    assert current["digest"] == original["task_workset"]["digest"]
    assert changed["execution_permission"] == "not_granted" and not changed["automatic_retry"]
    assert_old_unchanged(case)


def test_cli_publication_replays_same_operation_and_rejects_changed_plan(case, tmp_path):
    value = workset(case)
    plan = {"profile": {key: val for key, val in value["profile"].items() if key not in {"schema", "digest"}},
            "resources": value["index"]["resources"], "decisions": value["review"]["decisions"],
            "reviewer": value["review"]["reviewer"], "rationale": value["review"]["rationale"],
            "template": value["template"], "mode": value["mode"]}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "examples" / "research_meta_workset.py"),
               "--entry", str(case["parent"]), "--plan", str(plan_path),
               "--directory", str(tmp_path / "cli-publication"), "--operation-id", "cli-fixture"]

    def run():
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8")

    original_entry = file_identity(case["parent"])
    first = run()
    assert first.returncode == 0, first.stderr
    published = json.loads(first.stdout)
    assert published["status"] == "published"
    pinned_entry = file_identity(case["parent"])
    assert pinned_entry != original_entry
    entry = json.loads(case["parent"].read_text(encoding="utf-8"))
    assert read_task_workset(entry["workspace"], entry["checkpoint_ref"])["workset"] == value
    store = CampaignStore(Path(published["intent_path"]).parent / "research.sqlite", read_only=True)
    events = store.events()
    assert not store.actions()

    replay = run()
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["intent_path"] == published["intent_path"]
    assert file_identity(case["parent"]) == pinned_entry
    assert store.events() == events and not store.actions()

    plan["rationale"] = "A different review cannot reuse the accepted operation identity"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    conflicting = run()
    assert conflicting.returncode != 0
    assert "different plan" in conflicting.stderr
    assert file_identity(case["parent"]) == pinned_entry
    assert store.events() == events and not store.actions()
    # This test intentionally advances the old entry; all its preexisting
    # journal, evidence, checkpoint and authority bytes remain unchanged.
    assert all(file_identity(Path(path)) == identity for path, identity in case["old_identities"].items()
               if Path(path) != case["parent"])


@pytest.mark.parametrize("phase", STAGES)
def test_workset_publication_recovers_after_process_death(case, tmp_path, phase):
    value = workset(case)
    prepared = publish_task_workset(case["entry"], value, parent_entry=case["parent"],
                                    operation_id="crash-fixture", directory=tmp_path / "publication")
    script = """
import os, sys
from etalon.research import publication
original = publication._stage
def crash(root, phase, payload):
    result = original(root, phase, payload)
    if phase == sys.argv[2]:
        os._exit(73)
    return result
publication._stage = crash
publication.resume_publication(sys.argv[1])
"""
    process = subprocess.run([sys.executable, "-c", script, prepared["intent_path"], phase],
                             capture_output=True, text=True)
    assert process.returncode == 73, process.stderr
    assert inspect_publication(prepared["intent_path"])["phase"] == phase
    if STAGES.index(phase) < STAGES.index("entry_replaced"):
        assert not case["entry"].exists()
        prior = json.loads(case["parent"].read_text(encoding="utf-8"))
        assert read_task_workset(prior["workspace"], prior["checkpoint_ref"])["status"] == "legacy_unscoped"
    # Durable publication owns the saved bytes; recovery needs no producer rerun.
    (tmp_path / "publication" / (value["digest"] + ".json")).unlink()
    assert resume_publication(prepared["intent_path"])["status"] == "published"
    entry = json.loads(case["entry"].read_text(encoding="utf-8"))
    active = read_task_workset(entry["workspace"], entry["checkpoint_ref"])
    assert active["workset"] == value
    store = CampaignStore(Path(prepared["intent_path"]).parent / "research.sqlite", read_only=True)
    events = store.events()
    assert len([event for event in events if event["kind"] == "research_recorded"]) == 3
    assert not store.actions()
    resume_publication(prepared["intent_path"])
    assert store.events() == events
    assert_old_unchanged(case)
