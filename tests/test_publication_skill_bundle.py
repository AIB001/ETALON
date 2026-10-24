"""Publication preserves archived skill versions; no current-library fallback."""
import json
from pathlib import Path

import pytest

from etalon import skills
from etalon.active import CampaignStore
from etalon.active.schema import digest
from etalon.research import ResearchJournal, MethodVersion
from etalon.research import publication
from etalon.research import improvement, opportunity_state
from etalon.research.continuation import record_content
from etalon.research.method_revision import journal_ref
from etalon.research.evidence_refs import EvidenceError, file_identity, load_workspace
from etalon.research.skill_sources import resolve_skill_bundle, validate_skill_bundle_map
from test_research_publication import case


@pytest.fixture
def bundled(case):
    workspace = case["old"] / "workspace.json"
    manifest = json.loads(workspace.read_text(encoding="utf-8"))
    manifest["skill_bundle_map"] = []
    for revision in ("old", "older"):
        bundle = case["old"] / "skills" / revision / "bundle"
        (bundle / "references").mkdir(parents=True)
        (bundle / "SKILL.md").write_text(
            "---\nname: exact-fixture\ndescription: Archived historical version\n---\nRead references/api.md.\n",
            encoding="utf-8")
        (bundle / "references" / "api.md").write_text("Exact " + revision + " semantics.\n", encoding="utf-8")
        version, _ = skills.snapshot("research-index", bundle_ref={"root": str(bundle)})
        manifest["skill_bundle_map"].append({"slug": "research-index", "manifest_sha256": version["manifest_sha256"],
                                             "root": bundle.relative_to(case["old"]).as_posix()})
    workspace.write_text(json.dumps(manifest), encoding="utf-8")
    case["skill_maps"] = manifest["skill_bundle_map"]
    case["old_identities"] = {str(p): file_identity(p) for p in case["old"].rglob("*") if p.is_file()}
    return case


def identities(root):
    return {str(p): file_identity(p) for p in root.rglob("*") if p.is_file()}


def test_publication_relocates_exact_all_versions_and_replays_idempotently(bundled, monkeypatch):
    monkeypatch.setattr(skills, "bundle_root", lambda *_: pytest.fail("no installed/current bundle fallback"))
    prepared = publication.prepare_publication(**bundled["request"])
    published = publication.resume_publication(prepared["intent_path"])
    root = Path(prepared["intent_path"]).parent
    workspace = load_workspace(root / "workspace.json")
    maps = validate_skill_bundle_map(workspace)
    assert len(maps) == 2
    for original, relocated in zip(bundled["skill_maps"], maps):
        assert relocated == {**original, "root": "skills/" + original["manifest_sha256"] + "/bundle"}
        source = bundled["old"] / original["root"]
        target = root / relocated["root"]
        expected, contents = skills.snapshot(original["slug"], bundle_ref={"root": str(source)})
        actual, copied = skills.snapshot(original["slug"], bundle_ref=resolve_skill_bundle(original, workspace))
        assert actual == expected and copied == contents
        assert target != source and target.is_relative_to(root)
    before = identities(root)
    assert publication.resume_publication(prepared["intent_path"]) == published
    assert publication.prepare_publication(**bundled["request"])["status"] == "published"
    assert identities(root) == before
    assert identities(bundled["old"]) == bundled["old_identities"]
    assert CampaignStore(root / "research.sqlite", read_only=True).actions() == []


def test_changed_parent_member_after_prepare_cannot_publish_or_fall_back(bundled, monkeypatch):
    prepared = publication.prepare_publication(**bundled["request"])
    member = bundled["old"] / bundled["skill_maps"][0]["root"] / "references" / "api.md"
    member.write_text("Unreviewed changed bytes", encoding="utf-8")
    monkeypatch.setattr(skills, "bundle_root", lambda *_: pytest.fail("changed archive cannot use current version"))
    with pytest.raises(EvidenceError, match="mapped skill bundle unavailable or changed"):
        publication.resume_publication(prepared["intent_path"])
    assert not bundled["entry"].exists()


def test_changed_published_member_is_not_idempotent_success(bundled):
    prepared = publication.prepare_publication(**bundled["request"])
    publication.resume_publication(prepared["intent_path"])
    root = Path(prepared["intent_path"]).parent
    mapping = load_workspace(root / "workspace.json").manifest["skill_bundle_map"][0]
    member = root / mapping["root"] / "references" / "api.md"
    member.write_text("Corrupted destination", encoding="utf-8")
    with pytest.raises(EvidenceError, match="mapped skill bundle unavailable or changed"):
        publication.resume_publication(prepared["intent_path"])
    assert identities(bundled["old"]) == bundled["old_identities"]


def test_successor_relocates_prior_published_bundle_without_changing_either_parent(bundled):
    prepared = publication.prepare_publication(**bundled["request"])
    publication.resume_publication(prepared["intent_path"])
    first_root = Path(prepared["intent_path"]).parent
    before = identities(first_root)
    request = {**bundled["request"], "parent_entry": None, "operation_id": "successor",
               "expected_entry_identity": file_identity(bundled["entry"])}
    second = publication.prepare_publication(**request)
    publication.resume_publication(second["intent_path"])
    second_root = Path(second["intent_path"]).parent
    workspace = load_workspace(second_root / "workspace.json")
    assert validate_skill_bundle_map(workspace) == load_workspace(first_root / "workspace.json").manifest["skill_bundle_map"]
    for mapping in workspace.manifest["skill_bundle_map"]:
        assert (second_root / mapping["root"] / "SKILL.md").is_file()
    assert identities(first_root) == before
    assert identities(bundled["old"]) == bundled["old_identities"]


def sibling_dispositions(case):
    """Create competing host judgments in an explicitly frozen fixture source."""
    journal = ResearchJournal(CampaignStore(case["old"] / "research.sqlite"))
    ref = lambda rid: journal_ref(journal, rid, source_id="prior")
    problem = case["checkpoint"]["branches"][0]["basis_refs"][0]
    base = ref(journal.record(MethodVersion("fixture-base", ("usage_method",), {"artifact_ref": problem["record_id"]})))
    context = dict(schema=improvement.CONTEXT, task_id="fixture", scope="fixture", conditions={"endpoint": "fixture/1"},
                   method_refs=[base], target_ref=base, problem_refs=[problem], evidence_refs=[], observations=[],
                   missing=[], status="ready_for_revision", supersedes=[])
    context["opportunity_key"] = digest({k: context[k] for k in ("scope", "conditions", "target_ref", "problem_refs", "evidence_refs")})
    context_ref = ref(record_content(journal, context, case["old"] / "content", source_id="prior", source_kind="fixture"))
    opportunity = improvement.register_opportunity(journal, context_ref, case["old"] / "content", source_id="prior", source_kind="fixture")["opportunity_ref"]
    results = []
    for name, outcome in (("first", "no_change"), ("second", "retain")):
        payload = opportunity_state.build_opportunity_disposition(journal, opportunity_ref=opportunity, outcome=outcome,
            reviewer="fixture-host", reason="Bounded fixture judgment " + name, support_refs=[problem],
            reconsider_when=[{"condition_id": "new", "condition": "New specific contradictory receipt"}], source_id="prior")
        path = case["old"].parent / (name + ".json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        results.append(dict(name=name, path=str(path), identity=file_identity(path), source_kind="fixture",
                            role="diagnostic", bindings={"schema": opportunity_state.DISPOSITION}))
    path = case["old"] / "workspace.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["sources"][0]["identity"]["value"] = file_identity(journal.store.path)["sha256"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    request = json.loads(json.dumps(case["request"]))
    request["results"] = results
    request["steps"][0]["content"]["output_refs"] = [{"publication_ref": "first"}, {"publication_ref": "second"}]
    return request


def test_preflight_rejects_sibling_terminal_notes_before_intent(case):
    request = sibling_dispositions(case)
    before = identities(case["old"])
    with pytest.raises(EvidenceError, match="ambiguous concurrent opportunity heads"):
        publication.prepare_publication(**request)
    assert not case["entry"].exists()
    assert publication.discover_publications(case["entry"])["operations"] == []
    assert identities(case["old"]) == before


def test_resume_rechecks_typed_result_states_even_for_older_preflight(case, monkeypatch):
    request = sibling_dispositions(case)
    # Model an already durable intent from a writer that lacked the group check.
    monkeypatch.setattr(publication, "_preflight", lambda *args: None)
    prepared = publication.prepare_publication(**request)
    with pytest.raises(EvidenceError, match="current open episode"):
        publication.resume_publication(prepared["intent_path"])
    root = Path(prepared["intent_path"]).parent
    assert not case["entry"].exists()
    assert all((root / "results" / item["identity"]["sha256"]).is_file() for item in request["results"])
    store = CampaignStore(root / "research.sqlite", read_only=True)
    recorded = [e for e in store.events() if e["kind"] == "research_recorded"]
    assert len(recorded) == 1 and store.actions() == []
