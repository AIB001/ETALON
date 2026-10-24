"""Local archival/relocation contracts; these fixtures do not execute science."""
import json
from pathlib import Path

import pytest

from etalon.active.schema import digest
from etalon.research.continuation import read_content, resume_research
from etalon.research.entry import resolve_entry
from etalon.research.evidence import read_evidence
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.handoff import (
    export_research_handoff, plan_research_handoff, relocate_research_handoff, verify_research_handoff,
)
from etalon.research.publication import close_publication, discover_publications, prepare_publication, resume_publication
from etalon.research.research_context import build_research_context
from test_research_graph_memory import graph_case
from test_research_publication import case as publication_case


def test_complete_handoff_keeps_counterevidence_checkpoint_pause_and_originals(graph_case, tmp_path):
    c = graph_case
    original = {p: file_identity(p) for p in tmp_path.rglob("*") if p.is_file()}
    plan = plan_research_handoff(c["entry"])
    assert original == {p: file_identity(p) for p in original}
    target = tmp_path / "copy"
    result = export_research_handoff(plan, target)
    assert result["status"] == "verified" and result["originals"] == "verified"
    assert result["authority_state"] == "paused" and not result["publication_reactivated"]
    workspace, ref, _, _ = resolve_entry(target / "entry.json")
    assert ref == c["checkpoint"]
    assert read_content(workspace, ref) == c["checkpoint_content"]
    assert read_content(workspace, c["claim"])["counter_refs"] == [c["counter"]]
    assert read_evidence(workspace, c["counter"], view="artifact")["verification"]["artifact"] == "verified"
    assert build_research_context(target / "entry.json")["checkpoint_ref"] == c["checkpoint"]
    assert result["execution_permission"] == "not_granted" and not result["automatic_retry"]
    assert original == {p: file_identity(p) for p in original}
    assert not c["store"].actions()


def test_physical_relocation_needs_no_old_source_paths(graph_case, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    plan = plan_research_handoff(graph_case["entry"])
    export_research_handoff(plan, first)
    result = relocate_research_handoff(first, second)
    assert result["status"] == "bundle_verified"
    assert result["verification_scope"] == "exported_bundle_only" and result["originals"] == "not_checked"
    assert (first / "entry.json").read_bytes() == (second / "entry.json").read_bytes()
    # Remove access to the origin, not the copied evidence. Ordinary reads work.
    graph_case["entry"].unlink()
    graph_case["workspace"].unlink()
    workspace, ref, _, _ = resolve_entry(second / "entry.json")
    assert resume_research(workspace, ref)["authority"]["state"] == "paused"
    assert verify_research_handoff(second, verify_originals=False)["originals"] == "not_checked"
    with pytest.raises(FileNotFoundError):
        verify_research_handoff(second)  # default cannot silently skip missing originals


@pytest.mark.parametrize("key,value", [("max_files", 1), ("max_bytes", 10), ("max_records", 1)])
def test_bounds_fail_without_a_partial_plan(graph_case, key, value):
    with pytest.raises(EvidenceError, match="limit"):
        plan_research_handoff(graph_case["entry"], limits={key: value})


def test_plan_and_bundle_tampering_and_existing_destination_fail_closed(graph_case, tmp_path):
    plan = plan_research_handoff(graph_case["entry"])
    changed = json.loads(json.dumps(plan))
    changed["checkpoint_ref"]["record_digest"] = "0" * 64
    with pytest.raises(EvidenceError, match="digest"):
        export_research_handoff(changed, tmp_path / "bad")
    target = tmp_path / "good"
    export_research_handoff(plan, target)
    before = file_identity(target / "entry.json")
    with pytest.raises(EvidenceError, match="already exists"):
        export_research_handoff(plan, target)
    with pytest.raises(EvidenceError, match="already exists"):
        relocate_research_handoff(target, target)
    assert file_identity(target / "entry.json") == before
    (target / plan["files"][0]["target"]).write_text("corrupt")
    with pytest.raises(EvidenceError, match="identity mismatch"):
        verify_research_handoff(target, verify_originals=False)


def test_original_drift_between_plan_and_export_is_detected(graph_case, tmp_path):
    plan = plan_research_handoff(graph_case["entry"])
    (tmp_path / "negative.txt").write_text("counterexample changed")
    with pytest.raises(EvidenceError, match="original identity changed"):
        export_research_handoff(plan, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_copied_authority_is_task_local_and_still_invalidates_guard(graph_case, tmp_path):
    target = tmp_path / "copy"
    plan = plan_research_handoff(graph_case["entry"])
    export_research_handoff(plan, target)
    authority = next(x for x in plan["files"] if x["target"].startswith("authority/"))
    (target / authority["target"]).write_text("changed copied authority")
    workspace, ref, _, _ = resolve_entry(target / "entry.json")
    resumed = resume_research(workspace, ref)
    assert resumed["authority"]["state"] == "review_required"
    assert resumed["execution_permission"] == "not_granted"
    with pytest.raises(EvidenceError, match="identity mismatch"):
        verify_research_handoff(target)
    assert (tmp_path / "STATUS.md").read_text() == "R4b paused. No automatic execution."


def test_read_context_policy_and_live_sources_are_not_laundered(graph_case, tmp_path):
    c = graph_case
    context = build_research_context(c["entry"], policy={"record_rules": [{"source_id": "science",
        "record_id": c["unrelated"]["record_id"], "allowed_purposes": []}]})["read_context"]
    with pytest.raises(EvidenceError, match="read_context"):
        plan_research_handoff(context)
    path = tmp_path / "context.json"
    path.write_text(json.dumps(context))
    with pytest.raises(EvidenceError, match="read_context"):
        plan_research_handoff(path)
    path.write_text(json.dumps({"entry": str(c["entry"])}))
    assert plan_research_handoff(path)["checkpoint_ref"] == c["checkpoint"]
    ws = json.loads(c["workspace"].read_text())
    ws["sources"][0]["identity"] = {"kind": "journal_identity", "key": "identity", "value": "live"}
    c["workspace"].write_text(json.dumps(ws))
    with pytest.raises(EvidenceError, match="frozen snapshot"):
        plan_research_handoff(c["entry"])


def test_publication_history_includes_failed_journal_and_is_never_registered(publication_case, tmp_path):
    c = publication_case
    failed = prepare_publication(**{**c["request"], "operation_id": "failed-first"})
    failed_root = Path(failed["intent_path"]).parent
    # A real failed operation can contain non-JSON producer/journal material.
    (failed_root / "failed.sqlite").write_bytes(b"failure-history-byte-fixture")
    close_publication(failed["intent_path"], disposition="withdrawn", reason="fixture failure retained",
                      expected_intent_identity=file_identity(failed["intent_path"]))
    succeeded = prepare_publication(**c["request"])
    resume_publication(succeeded["intent_path"])
    before = {p: file_identity(p) for p in failed_root.rglob("*") if p.is_file()}
    plan = plan_research_handoff(c["entry"])
    assert {s["observation"]["status"] for s in plan["publication_history"]} == {"published", "withdrawn"}
    assert any(x["target"].endswith("/failed.sqlite") for x in plan["files"])
    target = tmp_path / "copy"
    export_research_handoff(plan, target)
    entry = json.loads((target / "entry.json").read_text())
    assert "publication" not in entry and all(h["archive_only"] for h in entry["publication_history"])
    assert discover_publications(target / "entry.json")["operations"] == []
    assert before == {p: file_identity(p) for p in before}
    assert verify_research_handoff(target)["publication_reactivated"] is False


def test_explicit_unlinked_incomplete_publication_and_membership_drift(publication_case, tmp_path):
    c = publication_case
    pending = prepare_publication(**c["request"])
    # The parent entry has no index to this new attempted publication.
    plan = plan_research_handoff(c["parent"], publication_intents=[pending["intent_path"]])
    assert plan["publication_history"][0]["observation"]["needs_recovery"]
    target = tmp_path / "copy"
    export_research_handoff(plan, target)
    assert verify_research_handoff(target)["publication_history"][0]["archive_only"]
    (Path(pending["intent_path"]).parent / "late-result.bin").write_bytes(b"new result")
    with pytest.raises(EvidenceError, match="membership changed"):
        export_research_handoff(plan, tmp_path / "stale")


def test_canonical_targets_cannot_escape_even_with_recomputed_plan_digest(graph_case, tmp_path):
    plan = plan_research_handoff(graph_case["entry"])
    plan["files"][0]["target"] = "../outside"
    plan["digest"] = digest({k: v for k, v in plan.items() if k != "digest"})
    with pytest.raises(EvidenceError, match="relative path"):
        export_research_handoff(plan, tmp_path / "escape")
    assert not (tmp_path / "outside").exists()


def test_skill_snapshot_supporting_files_relocate_with_method_version(graph_case, tmp_path):
    from etalon.research.schema import MethodVersion
    from etalon.skills import persist_snapshot
    content = persist_snapshot("research-context", tmp_path / "skill")
    graph_case["journal"].record(MethodVersion("fixture", ("research",), content))
    graph_case["publish"]()
    plan = plan_research_handoff(graph_case["entry"])
    assert plan["skill_snapshots"]
    result = export_research_handoff(plan, tmp_path / "copy")
    assert result["status"] == "verified"
    assert any("/bundle/SKILL.md" in f["target"] for f in plan["files"])


def test_data_snapshot_members_keep_original_record_identity(graph_case, tmp_path):
    from etalon.data.artifacts import digest as snapshot_digest
    from etalon.research.evidence_refs import make_ref
    data = tmp_path / "data"
    data.mkdir()
    member = data / "measurement.bin"
    member.write_bytes(b"real bytes of a synthetic measurement")
    document = {"schema": "etalon-data-snapshot/1", "files": {member.name: file_identity(member)}}
    document["snapshot_id"] = snapshot_digest(document)
    source = data / "data.json"
    source.write_text(json.dumps(document))
    ws = json.loads(graph_case["workspace"].read_text())
    ws["sources"].append({"source_id": "dataset", "kind": "data_snapshot", "location": str(source),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(source)["sha256"]}})
    graph_case["workspace"].write_text(json.dumps(ws))
    plan = plan_research_handoff(graph_case["entry"])
    export_research_handoff(plan, tmp_path / "copy")
    member.unlink()
    workspace, _, _, _ = resolve_entry(tmp_path / "copy" / "entry.json")
    ref = make_ref("dataset", document["snapshot_id"], document)
    result = read_evidence(workspace, ref, view="artifact", member=member.name)
    assert result["verification"]["artifact"] == "verified"
    assert result["ref"]["record_digest"] == ref["record_digest"]


def test_existing_foreign_mapping_is_explicit_and_preserves_artifact_record(graph_case, tmp_path):
    from etalon.research import Artifact
    from etalon.research.evidence_refs import make_ref
    original = "/unavailable-server/negative.bin"
    path = tmp_path / "negative.bin"
    path.write_bytes(b"counterexample from foreign server, frozen locally")
    artifact = Artifact(original, **file_identity(path), source_kind="fixture", role="diagnostic", bindings={})
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": artifact.id, "record_type": "artifact", "record": artifact.as_dict(), "visibility": "released"}]}))
    ws = json.loads(graph_case["workspace"].read_text())
    ws["sources"].append({"source_id": "foreign", "kind": "artifact_manifest", "location": str(foreign),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(foreign)["sha256"]}})
    ws["location_map"] = [{"source_id": "foreign", "original": original, "location": path.name, **file_identity(path)}]
    graph_case["workspace"].write_text(json.dumps(ws))
    plan = plan_research_handoff(graph_case["entry"])
    export_research_handoff(plan, tmp_path / "copy")
    workspace, _, _, _ = resolve_entry(tmp_path / "copy" / "entry.json")
    ref = make_ref("foreign", artifact.id, artifact.as_dict())
    assert read_evidence(workspace, ref)["record"]["path"] == original
    assert read_evidence(workspace, ref, view="artifact")["verification"]["artifact"] == "verified"


def test_active_authority_cannot_be_frozen_into_new_execution_permission(graph_case, tmp_path):
    c = graph_case
    checkpoint = {**c["checkpoint_content"], "status_guards": [{**c["checkpoint_content"]["status_guards"][0], "state": "active"}]}
    cp = c["save"](checkpoint)
    c["publish"]()
    c["entry"].write_text(json.dumps({"workspace": "workspace.json", "checkpoint_ref": cp}))
    with pytest.raises(EvidenceError, match="paused/stopped"):
        plan_research_handoff(c["entry"])


def test_legacy_archive_repack_does_not_follow_foreign_requests(graph_case, tmp_path, monkeypatch):
    from etalon.research import handoff
    archived = tmp_path / "legacy-history"
    archived.mkdir()
    intent = archived / "intent.json"
    intent.write_text(json.dumps({"schema": "etalon-research-publication/1", "request": {
        "operation_id": "old-withdrawn", "entry": "Z:/unavailable-original/entry.json",
        "parent_entry": "Z:/unavailable-original/parent.json"}}))
    (archived / "disposition.json").write_text('{"disposition":"withdrawn"}')
    identity = file_identity(intent)
    envelope = json.loads(graph_case["entry"].read_text())
    envelope.update(publication={"intent_path": str(intent), "archive_only": True},
        publication_history=[{"operation_id": "old-withdrawn", "archived_intent": str(intent),
            "origin_intent": "Z:/unavailable-original/intent.json", "intent_identity": identity,
            "origin_status": "withdrawn", "phase": "results_recorded", "needs_recovery_at_export": False,
            "archive_only": True, "automatic_retry": False, "execution_permission": "not_granted"}],
        handoff={"history": "legacy deployment archive, external original failure tar was not declared"})
    graph_case["entry"].write_text(json.dumps(envelope))

    def forbidden(*args, **kwargs):
        pytest.fail("legacy history must not be interpreted as a live publication")
    monkeypatch.setattr(handoff, "inspect_publication", forbidden)
    plan = plan_research_handoff(graph_case["entry"])
    assert plan["history_coverage"]["legacy_archive_directories"] == 1
    history = plan["publication_history"][0]
    assert history["inventory_scope"] == "declared_legacy_archive_directory_only"
    assert history["observation"]["status"] == "withdrawn"
    assert history["observation"]["original_request_not_followed"]
    assert history["prior_archive"]["intent_identity"] == identity
    result = export_research_handoff(plan, tmp_path / "repacked")
    assert result["status"] == "verified" and not result["publication_reactivated"]
    intent.write_text("changed legacy archive")
    with pytest.raises((EvidenceError, json.JSONDecodeError)):
        plan_research_handoff(graph_case["entry"])


def test_network_destinations_are_not_local_copy_targets(graph_case):
    plan = plan_research_handoff(graph_case["entry"])
    with pytest.raises(EvidenceError, match="local paths only"):
        export_research_handoff(plan, r"\\server\share\new-handoff")


def test_entry_symlink_is_a_pinned_selector_not_an_archived_redirect(graph_case, tmp_path):
    alias = tmp_path / "CURRENT.json"
    try:
        alias.symlink_to(graph_case["entry"])
    except OSError:
        pytest.skip("platform does not permit creating symlink fixtures")
    plan = plan_research_handoff(alias)
    assert plan["entry_selector"]["resolved_path"] == str(graph_case["entry"])
    export_research_handoff(plan, tmp_path / "copy")
    alternate = tmp_path / "alternate.json"
    alternate.write_bytes(graph_case["entry"].read_bytes())
    alias.unlink()
    alias.symlink_to(alternate)
    with pytest.raises(EvidenceError, match="selector changed"):
        export_research_handoff(plan, tmp_path / "changed")


@pytest.fixture
def pinned_workset(graph_case, tmp_path, monkeypatch):
    """A published bare slug/hash whose installed current skill later changes."""
    from etalon import skills
    from etalon.research.meta_context import (
        build_task_workset, describe_resource, index_resources, make_task_profile,
        prepare_adaptation_context, publish_task_workset, review_adaptation,
    )
    from test_meta_context import part
    old, current = tmp_path / "old-bundle", tmp_path / "current-bundle"
    for root, label in ((old, "ORIGINAL"), (current, "CHANGED_CURRENT")):
        (root / "references").mkdir(parents=True)
        (root / "SKILL.md").write_text("---\nname: etalon-research-index\ndescription: fixture exact version\n---\n" + label,
                                       encoding="utf-8")
        (root / "references" / "detail.md").write_text(label + " supporting member", encoding="utf-8")
    monkeypatch.setattr(skills, "bundle_root", lambda slug: old)
    version, _ = skills.snapshot("research-index")
    pin = {"slug": "research-index", "manifest_sha256": version["manifest_sha256"]}
    profile = make_task_profile(task_id="snd1-fixture", question="Keep exact skill bytes",
        conditions={"target": "fixture"}, critical_conditions=["target"], available_stages=["input"])
    resources = [
        describe_resource(resource_id="base", kind="template", content_ref=graph_case["evidence"],
            summary="fixture template", parts=[part()]),
        describe_resource(resource_id="index", kind="skill", content_ref=pin,
            summary="pinned old skill", parts=[part("SKILL.md")]),
    ]
    index = index_resources(resources, workspace=graph_case["workspace"])
    decisions = [{"resource_id": c["resource_id"], "part_id": c["part_id"], "disposition": "reuse",
        "reason": "fixture explicit match", "reconsider_when": "new evidence"}
        for c in prepare_adaptation_context(profile, index)["candidates"]]
    review = review_adaptation(profile, index, decisions=decisions, reviewer="fixture", rationale="pin original bundle")
    workset = build_task_workset(profile, index, review, template={"resource_id": "base", "part_id": "base"})
    entry = tmp_path / "published.json"
    intent = publish_task_workset(entry, workset, parent_entry=graph_case["entry"], operation_id="pinned-workset",
                                  directory=tmp_path / "publication")
    resume_publication(intent["intent_path"])
    monkeypatch.setattr(skills, "bundle_root", lambda slug: current)
    return {"entry": entry, "workset": workset, "pin": pin, "old": old, "current": current,
            "override": {**pin, "root": str(old)}, "version": version}


def test_old_workset_skill_is_complete_offline_after_current_library_changes(pinned_workset, tmp_path):
    from etalon import skills
    from etalon.research.meta_context import read_task_workset, selected_skill_members
    c = pinned_workset
    with pytest.raises(EvidenceError, match="required skill bundle version unavailable"):
        plan_research_handoff(c["entry"])
    plan = plan_research_handoff(c["entry"], skill_bundles=[c["override"]])
    assert plan["skill_bundles"][0]["manifest"] == c["version"]
    assert plan["skill_bundles"][0]["source_basis"] == "explicit_exact_version_override"
    assert plan["workspace"]["skill_bundle_map"] == [{**c["pin"], "root": plan["skill_bundles"][0]["root"]}]
    target = tmp_path / "portable"
    export_research_handoff(plan, target)
    workspace, ref, _, _ = resolve_entry(target / "entry.json")
    active = read_task_workset(workspace, ref)
    assert active["workset"] == c["workset"]
    selected = selected_skill_members(active["workset"], workspace=workspace)
    assert skills.read_member("research-index", bundle_ref=selected[0]["bundle_ref"])["content"].endswith("ORIGINAL")
    assert (target / plan["skill_bundles"][0]["root"] / "references" / "detail.md").read_text() == "ORIGINAL supporting member"
    (c["old"] / "SKILL.md").unlink()
    assert read_task_workset(workspace, ref)["workset"]["digest"] == c["workset"]["digest"]
    assert verify_research_handoff(target, verify_originals=False)["status"] == "bundle_verified"
    with pytest.raises(FileNotFoundError):
        verify_research_handoff(target)


def test_existing_skill_map_is_carried_without_current_library_fallback(pinned_workset, tmp_path):
    c = pinned_workset
    first = tmp_path / "first"
    export_research_handoff(plan_research_handoff(c["entry"], skill_bundles=[c["override"]]), first)
    envelope = json.loads((first / "entry.json").read_text())
    republished = tmp_path / "new-pointer.json"
    republished.write_text(json.dumps({"workspace": str(first / "workspace.json"), "checkpoint_ref": envelope["checkpoint_ref"]}))
    plan = plan_research_handoff(republished)
    assert plan["skill_bundles"][0]["source_basis"] == "existing_workspace_skill_bundle_map"
    export_research_handoff(plan, tmp_path / "second")


def test_corrupt_or_wrong_skill_bundle_cannot_be_replaced_with_current(pinned_workset, tmp_path):
    from etalon.research.skill_sources import resolve_skill_bundle
    c = pinned_workset
    with pytest.raises(EvidenceError, match="required skill bundle version unavailable"):
        plan_research_handoff(c["entry"], skill_bundles=[{**c["pin"], "root": str(c["current"])}])
    target = tmp_path / "portable"
    plan = plan_research_handoff(c["entry"], skill_bundles=[c["override"]])
    export_research_handoff(plan, target)
    root = target / plan["skill_bundles"][0]["root"]
    (root / "references" / "detail.md").write_text("changed copied support")
    with pytest.raises(EvidenceError, match="(identity mismatch|unavailable or changed)"):
        verify_research_handoff(target, verify_originals=False)
    with pytest.raises(EvidenceError, match="unavailable or changed"):
        resolve_skill_bundle(c["pin"], target / "workspace.json")


def test_bundle_membership_change_after_plan_invalidates_original_pin(pinned_workset, tmp_path):
    c = pinned_workset
    plan = plan_research_handoff(c["entry"], skill_bundles=[c["override"]])
    (c["old"] / "late-member.md").write_text("new original content")
    with pytest.raises(ValueError, match="manifest digest mismatch"):
        export_research_handoff(plan, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_old_incomplete_package_is_not_claimed_as_full_workset_handoff(pinned_workset, tmp_path):
    from etalon.research.handoff import BUNDLE, _documents
    c = pinned_workset
    target = tmp_path / "portable"
    plan = plan_research_handoff(c["entry"], skill_bundles=[c["override"]])
    export_research_handoff(plan, target)
    # Recreate the old omission while keeping all scientific record bytes intact.
    plan["workspace"].pop("skill_bundle_map")
    plan.pop("skill_bundles")
    plan["digest"] = digest({k: v for k, v in plan.items() if k != "digest"})
    for name, value in _documents(plan).items():
        (target / name).write_text(json.dumps(value))
    bundle = {"schema": BUNDLE, "plan": plan}
    bundle["digest"] = digest(bundle)
    (target / "handoff.json").write_text(json.dumps(bundle))
    with pytest.raises(EvidenceError, match="missing exact workset skill bundle mapping"):
        verify_research_handoff(target, verify_originals=False)


def test_saved_method_snapshot_supplies_exact_old_workset_skill(pinned_workset, tmp_path):
    from etalon.research.evidence_refs import _local, load_workspace
    from etalon.research.schema import MethodVersion
    from etalon.skills import persist_snapshot
    c = pinned_workset
    content = persist_snapshot(c["pin"]["slug"], tmp_path / "saved-skill",
        bundle_ref={"root": str(c["old"]), "manifest_sha256": c["pin"]["manifest_sha256"]})
    method = MethodVersion("saved original", ("research",), content)
    source = tmp_path / "saved-method.json"
    source.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": method.id, "record_type": "method_version", "record": method.as_dict(), "visibility": "released"}]}))
    original_workspace, cp, _, _ = resolve_entry(c["entry"])
    ws = load_workspace(original_workspace)
    manifest = ws.manifest
    for item in manifest["sources"]:
        item["location"] = str(_local(ws.base, item["location"]))
    for item in manifest["location_map"]:
        item["location"] = str(_local(ws.base, item["location"], confined=True))
    manifest["sources"].append({"source_id": "saved-method", "kind": "artifact_manifest", "location": str(source),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(source)["sha256"]}})
    expanded_workspace, entry = tmp_path / "expanded-workspace.json", tmp_path / "expanded-entry.json"
    expanded_workspace.write_text(json.dumps(manifest))
    entry.write_text(json.dumps({"workspace": str(expanded_workspace), "checkpoint_ref": cp}))
    plan = plan_research_handoff(entry)
    assert plan["skill_bundles"][0]["source_basis"] == "saved_method_version_snapshot"
    assert plan["skill_bundles"][0]["manifest_sha256"] == c["pin"]["manifest_sha256"]
    assert export_research_handoff(plan, tmp_path / "portable-snapshot")["status"] == "verified"
