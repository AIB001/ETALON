"""Independent V counterexamples for full, optional capability-library seams."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from etalon import host, skills

ROOT = Path(__file__).resolve().parents[1]
MQ = {"molquarry-" + name for name in (
    "analogue-search", "assay-literature", "compound-sourcing",
    "selectivity-evidence", "structure-templates", "target-modulators")}
OLD = {"campaign", "sweep", "gate-calibration", "generation-planning", "monitoring", "install"}


def test_v_original_sources_complete_and_zero_argument_shape_remains_additive():
    report = skills.catalog()
    assert set(report) == {"schema", "skills", "catalog_sha256", "usage_method", "load_evidence"}
    rows = {row["slug"]: row for row in report["skills"]}
    assert OLD | MQ <= rows.keys()
    assert set(rows) == set(skills.WORKFLOWS)
    # This existing authoring helper is outside the shipped runtime catalog.
    originals = [path for path in (ROOT / ".claude/skills").glob("*/SKILL.md")
                 if path.parent.name != "rsi-skill-creator"]
    originals += list((ROOT / "asset/molquarry/skills").glob("*/SKILL.md"))
    assert {skills.bundle_root(s) / "SKILL.md" for s in rows} == set(originals)
    for slug in MQ:
        manifest, content = skills.snapshot(slug)
        original = ROOT / "asset/molquarry/skills" / slug
        assert set(content) == {p.relative_to(original).as_posix() for p in original.rglob("*") if p.is_file()}
        assert all(content[p] == (original / p).read_bytes() for p in content)
        assert manifest == rows[slug]
    assert not any("life-healthy-" in row["name"] for row in rows.values())


def test_v_partial_mixed_scope_and_empty_scope_do_not_resolve_other_bundles(monkeypatch):
    original = skills.bundle_root
    selected = ["molquarry-target-modulators", "research"]
    calls = []
    def selected_only(slug):
        assert slug in selected, "unrelated bundle resolved"
        calls.append(slug)
        return original(slug)
    monkeypatch.setattr(skills, "bundle_root", selected_only)
    assert skills.catalog(detail="summary", skills=[])["skills"] == []
    assert calls == []
    compact = skills.catalog(detail="summary", skills=selected)
    assert calls == selected
    for row in compact["skills"]:
        assert "files" not in row and "content" not in row
        full, content = skills.snapshot(row["slug"])
        assert row == {k: v for k, v in full.items() if k != "files"}
        reference = next(p for p in content if p.startswith("references/"))
        read = skills.read_member(row["slug"], reference)
        assert read["manifest_sha256"] == row["manifest_sha256"]
        assert hashlib.sha256(read["content"].encode()).hexdigest() == read["sha256"]
    with pytest.raises(ValueError):
        skills.catalog(skills=[selected[0], selected[0]])
    with pytest.raises(ValueError):
        skills.catalog(skills=["not-registered"])


def test_v_external_supporting_deletion_changes_full_version_preserves_original(tmp_path, monkeypatch):
    root = tmp_path / "external"
    shutil.copytree(skills.bundle_root("molquarry-target-modulators"), root)
    def no_builtin(*args, **kwargs):
        raise AssertionError("external bundle requested built-in discovery")
    monkeypatch.setattr(skills, "bundle_root", no_builtin)
    reference = {"root": str(root)}
    old, contents = skills.snapshot("unregistered-local-method", bundle_ref=reference)
    saved = skills.persist_snapshot("unregistered-local-method", tmp_path / "saved", bundle_ref=reference)
    member = next(name for name in contents if name.startswith("references/"))
    (root / member).unlink()
    new, _ = skills.snapshot("unregistered-local-method", bundle_ref=reference)
    assert new["manifest_sha256"] != old["manifest_sha256"]
    assert skills.verify_snapshot(saved) == old
    with pytest.raises(ValueError, match="digest mismatch"):
        skills.read_member("unregistered-local-method", bundle_ref={**reference, "manifest_sha256": old["manifest_sha256"]})
    with pytest.raises(KeyError):
        skills.read_member("unregistered-local-method", member, bundle_ref=reference)


@pytest.mark.parametrize("adapter", ["codex", "claude"])
def test_v_exports_keep_every_original_member_and_detect_supporting_drift(tmp_path, adapter):
    target = tmp_path / adapter
    report = host.export(target, adapter)
    base = ".agents/skills" if adapter == "codex" else ".claude/skills"
    assert {row["slug"] for row in report["skills"]} == set(skills.WORKFLOWS)
    for slug in skills.WORKFLOWS:
        manifest, contents = skills.snapshot(slug)
        for name, content in contents.items():
            assert (target / base / manifest["name"] / name).read_bytes() == content
    assert host.verify(target)["matches_current_source"]
    supporting = f"{base}/molquarry-target-modulators/references/workflow.md"
    (target / supporting).write_bytes(b"local revision\n")
    assert supporting in host.verify(target)["changed"]
    (target / supporting).unlink()
    assert supporting in host.verify(target)["missing"]
    assert not any("life-healthy-" in p for p in report["files"])


def test_v_legacy_mq_uri_resources_and_main_reader_share_complete_source():
    from etalon.mcp.server import build
    server = build()
    async def read(uri):
        response = await server.read_resource(uri)
        return list(response)[0].content
    async def check():
        listed = json.loads(await read("etalon://skills/molquarry"))["skills"]
        assert {row["name"] for row in listed} == MQ
        for row in listed:
            manifest, contents = skills.snapshot(row["name"])
            assert await read(row["uri"]) == contents["SKILL.md"].decode()
            assert await read("etalon://skills/" + row["name"]) == contents["SKILL.md"].decode()
            assert set(row["resources"]) == set(contents)
            for resource in row["references"]:
                member = resource.removeprefix(row["uri"] + "/")
                assert await read(resource) == contents[member].decode()
                assert skills.read_member(row["name"], member)["manifest_sha256"] == manifest["manifest_sha256"]
    asyncio.run(check())


def test_v_known_data_and_partial_research_calls_need_no_skill_or_method_gate(tmp_path, monkeypatch):
    from etalon.active import CampaignSpec, CampaignStore, Endpoint
    from etalon.active.adapters import MOLECULAR_REPRESENTATION
    from etalon.data.design import import_bundle, verify_import
    from etalon.research.fixed_pool import subset_rank_outcome
    def forbidden(*args, **kwargs):
        raise AssertionError("known API tried to require skill discovery")
    monkeypatch.setattr(skills, "catalog", forbidden)
    monkeypatch.setattr(skills, "snapshot", forbidden)
    store = CampaignStore(tmp_path / "isolated.sqlite")
    endpoint = Endpoint("fixture", "fixture", "engineering", "count", "fixture/1", 1, requires_handoff=True)
    store.configure(CampaignSpec(endpoint.id, 2, "wall_second", MOLECULAR_REPRESENTATION), [endpoint])
    bundle = ROOT / "tests/fixtures/prism_design_bundle_v1/bundle.json"
    first = import_bundle(store, bundle)
    events = store.events()
    assert import_bundle(store, bundle)["imported"] is False
    assert store.events() == events
    assert verify_import(store, bundle) == first
    result = subset_rank_outcome(candidate_ids=("a", "b"), vina={"a": -2., "b": -1.},
                                 vinardo={"a": -2., "b": None})
    assert result["outcome"] == "failed" and result["missing_candidates"] == ["b"]
    assert store.observations() == []
    assert store.balance()["spent"] == 0


def test_v_invalid_design_does_not_replace_existing_admissible_state(tmp_path):
    from etalon.active import CampaignSpec, CampaignStore, Endpoint
    from etalon.active.adapters import MOLECULAR_REPRESENTATION
    from etalon.data.design import design_index, import_bundle
    endpoint = Endpoint("fixture", "fixture", "engineering", "count", "fixture/1", 1, requires_handoff=True)
    store = CampaignStore(tmp_path / "isolated.sqlite")
    store.configure(CampaignSpec(endpoint.id, 2, "wall_second", MOLECULAR_REPRESENTATION), [endpoint])
    source = ROOT / "tests/fixtures/prism_design_bundle_v1"
    import_bundle(store, source / "bundle.json")
    before = (store.events(), design_index(store), store.balance(), store.observations())
    bad = tmp_path / "invalid"
    shutil.copytree(source, bad)
    bundle = json.loads((bad / "bundle.json").read_text())
    raw = bad / bundle["candidates"][0]["raw"]["path"]
    raw.write_bytes(raw.read_bytes() + b"\ncorruption\n")
    with pytest.raises(ValueError):
        import_bundle(store, bad / "bundle.json")
    assert (store.events(), design_index(store), store.balance(), store.observations()) == before
