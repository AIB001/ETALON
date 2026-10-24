"""Exact archived skill relocation; no workset mutation or automatic adoption."""
import copy
import json
from pathlib import Path

import pytest

from etalon import skills
from etalon.research.evidence_refs import EvidenceError, file_identity, load_workspace
from etalon.research.meta_context import (
    build_task_workset, describe_resource, index_resources, make_task_profile,
    prepare_adaptation_context, review_adaptation, selected_skill_members, verify_resource,
)
from etalon.research.skill_sources import resolve_skill_bundle, validate_skill_bundle_map


@pytest.fixture
def archived(tmp_path):
    root = tmp_path / "skills" / "original" / "bundle"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("---\nname: archived-fixture\ndescription: Pinned fixture\n---\nRead references/api.md.\n", encoding="utf-8")
    (root / "references").mkdir()
    (root / "references/api.md").write_text("Original semantics.\n", encoding="utf-8")
    version, _ = skills.snapshot("research-index", bundle_ref={"root": str(root)})
    content_ref = {"slug": "research-index", "manifest_sha256": version["manifest_sha256"]}
    manifest = {"schema": "etalon-evidence-workspace/1", "workspace_id": "archive-fixture", "sources": [],
        "location_map": [], "skill_bundle_map": [{**content_ref, "root": "skills/original/bundle"}]}
    path = tmp_path / "workspace.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    resource = describe_resource(resource_id="skill", kind="skill", content_ref=content_ref,
        summary="Archived exact version", parts=[dict(part_id=p, conditions={}, requires=[],
            irrelevant=["target"], available_at="archive", dependencies=[]) for p in ("SKILL.md", "references/api.md")])
    return path, root, manifest, resource


def test_old_exact_bundle_reads_without_current_lookup_or_descriptor_changes(archived, monkeypatch):
    path, root, manifest, resource = archived
    original = copy.deepcopy(resource)
    before = {p: file_identity(p) for p in [path, root / "SKILL.md", root / "references/api.md"]}
    monkeypatch.setattr(skills, "bundle_root", lambda *_: pytest.fail("mapped version must not use current library"))
    for ws in (path, load_workspace(path)):
        mapped = resolve_skill_bundle(resource["content_ref"], ws)
        assert mapped == {"root": str(root), "manifest_sha256": resource["content_ref"]["manifest_sha256"]}
        verify_resource(resource, ws)
        assert skills.read_member("research-index", "references/api.md", bundle_ref=mapped)["content"] == (root / "references/api.md").read_bytes().decode("utf-8")
    assert validate_skill_bundle_map(manifest, base=path.parent) == manifest["skill_bundle_map"]
    assert resource == original and {p: file_identity(p) for p in before} == before


@pytest.mark.parametrize("edit,match", [
    (lambda m: m.update(skill_bundle_map=None), "at most 64"),
    (lambda m: m.update(skill_bundle_map=m["skill_bundle_map"] * 65), "at most 64"),
    (lambda m: m["skill_bundle_map"].append(m["skill_bundle_map"][0].copy()), "duplicate"),
    (lambda m: m["skill_bundle_map"][0].update(extra=True), "requires"),
    (lambda m: m["skill_bundle_map"][0].update(slug="../../escape"), "slug"),
    (lambda m: m["skill_bundle_map"][0].update(manifest_sha256="bad"), "SHA256"),
    (lambda m: m["skill_bundle_map"][0].update(root="../outside"), "relative"),
    (lambda m: m["skill_bundle_map"][0].update(root="/absolute"), "relative"),
    (lambda m: m["skill_bundle_map"][0].update(root="C:/foreign"), "relative"),
    (lambda m: m["skill_bundle_map"][0].update(root="skills\\original\\bundle"), "relative"),
    (lambda m: m["skill_bundle_map"][0].update(root="skills//original/bundle"), "relative"),
    (lambda m: m["skill_bundle_map"][0].update(root="missing"), "unavailable"),
    (lambda m: m["skill_bundle_map"][0].update(manifest_sha256="f" * 64), "changed"),
])
def test_bad_mapping_never_falls_back(archived, edit, match, monkeypatch):
    path, _, manifest, resource = archived
    edit(manifest)
    monkeypatch.setattr(skills, "bundle_root", lambda *_: pytest.fail("broken mappings must not fall back"))
    with pytest.raises(EvidenceError, match=match):
        validate_skill_bundle_map(manifest, base=path.parent)


@pytest.mark.parametrize("change", ["changed", "missing", "extra"])
def test_full_inventory_is_rechecked_between_reads(archived, change):
    path, root, _, resource = archived
    resolve_skill_bundle(resource["content_ref"], path)
    member = root / "references/api.md"
    if change == "changed":
        member.write_text("Different semantics", encoding="utf-8")
    elif change == "missing":
        member.unlink()
    else:
        (root / "unreviewed.md").write_text("Unexpected", encoding="utf-8")
    with pytest.raises(EvidenceError, match="changed"):
        resolve_skill_bundle(resource["content_ref"], path)


def test_redirected_mapping_rejected(archived, monkeypatch):
    path, root, manifest, _ = archived
    old = skills.redirected
    monkeypatch.setattr(skills, "redirected", lambda p: p == root or old(p))
    with pytest.raises(EvidenceError, match="symlinks"):
        validate_skill_bundle_map(manifest, base=path.parent)


def test_wrong_version_map_and_conflicting_explicit_version_rejected(archived):
    path, root, manifest, resource = archived
    ref = {**resource["content_ref"], "manifest_sha256": "a" * 64}
    with pytest.raises(EvidenceError, match="requested skill version"):
        resolve_skill_bundle(ref, path)
    ref = {**resource["content_ref"], "bundle_ref": {"root": str(root), "manifest_sha256": "b" * 64}}
    with pytest.raises(EvidenceError, match="conflicts"):
        resolve_skill_bundle(ref, path)


def test_legacy_without_map_retains_original_explicit_locator(archived):
    path, root, manifest, resource = archived
    manifest.pop("skill_bundle_map")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert resolve_skill_bundle(resource["content_ref"], path) is None
    explicit = {"root": str(root), "manifest_sha256": resource["content_ref"]["manifest_sha256"]}
    assert resolve_skill_bundle({**resource["content_ref"], "bundle_ref": explicit}, path) == explicit
    assert resolve_skill_bundle({**resource["content_ref"], "bundle_ref": explicit}) == explicit
    with pytest.raises(EvidenceError, match="absolute base"):
        validate_skill_bundle_map(manifest)


def test_selected_members_expose_resolved_locator_without_revising_workset(archived, tmp_path, monkeypatch):
    path, root, _, resource = archived
    # Use an ordinary fixture Artifact as a compatible template, not a new
    # scientific source. The existing test workspace supplies its valid refs.
    from etalon.active import CampaignStore, CampaignSpec, Endpoint
    from etalon.research import ResearchJournal, Artifact
    from etalon.research.evidence_refs import make_ref
    store = CampaignStore(tmp_path / "fixture.sqlite")
    store.configure(CampaignSpec("audit", 0, "wall_second", "fixture"),
                    [Endpoint("audit", "fixture", "none", "count", "fixture", 0, queryable=False)])
    journal = ResearchJournal(store)
    template_file = tmp_path / "template.txt"
    template_file.write_text("Archive only", encoding="utf-8")
    ar = Artifact(str(template_file), **file_identity(template_file), source_kind="fixture", role="diagnostic", bindings={})
    journal.record(ar)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["sources"] = [dict(source_id="fixture", kind="journal", location=str(store.path),
        identity={"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]})]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    template = describe_resource(resource_id="template", kind="template",
        content_ref=make_ref("fixture", ar.id, journal.get(ar.id)), summary="Archive template",
        parts=[dict(part_id="base", conditions={}, requires=[], irrelevant=["target"], available_at="archive", dependencies=[])])
    p = make_task_profile(task_id="fixture", question="Use archived evidence", conditions={"target": None},
        critical_conditions=["target"], available_stages=["archive"])
    index = index_resources([template, resource], workspace=path)
    review = review_adaptation(p, index, decisions=[dict(resource_id=c["resource_id"], part_id=c["part_id"],
        disposition="reuse" if c["part_id"] != "references/api.md" else "reference", reason="Explicit selection",
        reconsider_when="Scope changes") for c in prepare_adaptation_context(p, index)["candidates"]],
        reviewer="fixture", rationale="Read-only archive")
    value = build_task_workset(p, index, review, template={"resource_id": "template", "part_id": "base"})
    before = copy.deepcopy(value)
    selected = selected_skill_members(value, workspace=path)
    assert selected[0]["bundle_ref"]["root"] == str(root)
    assert selected[0]["members"] == ["SKILL.md"]
    assert value == before and not store.actions()

    # A real checkpoint summary exposes this locator to existing skill_read;
    # it does not merely validate the archive then point callers at current.
    from etalon.research.components import record_json
    from etalon.research.continuation import record_content
    from etalon.research.meta_context import WORKSET, _dependencies, workset_summary
    def local_ref(rid):
        return make_ref("fixture", rid, journal.get(rid))
    wr = local_ref(record_json(journal, value, tmp_path / "content", source_kind="fixture",
        bindings=dict(schema=WORKSET, meta_activation=WORKSET, task_id="fixture", evidence_refs=_dependencies(value))))
    activation = dict(schema="etalon-research-step/1", task_id="fixture", question="Archive lookup", branch_id="read",
        operation="decision", reason="Keep this pinned old version", input_refs=[], output_refs=[wr], method_refs=[],
        accounting_refs=[], source_watermarks=[], supersedes=[], detail=dict(choice="reuse", alternatives=[],
            basis_refs=[make_ref("fixture", ar.id, journal.get(ar.id))], reconsider_when="Task conditions change"))
    sr = local_ref(record_content(journal, activation, tmp_path / "content", source_id="fixture", source_kind="fixture"))
    cp = dict(schema="etalon-research-checkpoint/1", task_id="fixture", question="Archive lookup", step_refs=[sr],
        claim_refs=[], counter_refs=[], method_refs=[], branches=[dict(branch_id="read", state="resolved", basis_refs=[wr],
        next_read_refs=[wr], reconsider_when="Changed conditions")], pending_handles=[], source_watermarks=[],
        supersedes=[], status_guards=[])
    cr = local_ref(record_content(journal, cp, tmp_path / "content", source_id="fixture", source_kind="fixture"))
    manifest["sources"][0]["identity"]["value"] = file_identity(store.path)["sha256"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    summary = workset_summary(path, cr)
    assert summary["status"] == "active"
    locator = summary["selected_skill_sources"][0]
    assert locator["bundle_ref"]["root"] == str(root)
    assert skills.read_member(locator["slug"], bundle_ref=locator["bundle_ref"])["manifest_sha256"] == resource["content_ref"]["manifest_sha256"]
    changed = workset_summary(path, cr, conditions={"target": "new-target"})
    assert changed["status"] == "review_required" and changed["selected_skill_sources"] == []
    assert value == before and not store.actions()
    from etalon import host
    entry = tmp_path / "entry.json"
    entry.write_text(json.dumps({"workspace": str(path), "checkpoint_ref": cr}), encoding="utf-8")
    monkeypatch.setattr(skills, "bundle_root", lambda *_: pytest.fail("export must not fall back to current library"))
    target = tmp_path / "export"
    host.export(target, task_workset=value, research_entry=entry)
    exported = target / ".agents/skills/archived-fixture"
    assert (exported / "SKILL.md").read_bytes() == (root / "SKILL.md").read_bytes()
    assert not (exported / "references/api.md").exists()
    assert host.verify(target)["matches_current_source"]
    (root / "references/api.md").write_text("Foreign version", encoding="utf-8")
    with pytest.raises(EvidenceError, match="changed"):
        workset_summary(path, cr)
