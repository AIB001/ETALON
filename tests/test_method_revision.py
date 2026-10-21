"""Revision relationships, immutable publication and replay; no science/model runs."""
import copy
import json

import pytest

from etalon import host, skills
from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.boundary.prism_v1 import file_identity
from etalon.research import Artifact, MethodVersion, ResearchJournal
from etalon.research.method_revision import build_method_revision, journal_ref, record_method_revision


@pytest.fixture
def case(tmp_path):
    store = CampaignStore(tmp_path / "campaign.sqlite")
    store.configure(CampaignSpec("x", 1, "fixture", "revision-test"),
                    [Endpoint("x", "fixture", "unmeasured", "count", "fixture/1", 1, queryable=False)])
    journal = ResearchJournal(store)
    p = tmp_path / "problem.json"
    p.write_text('{"problem":"ambiguous target lineage"}', encoding="utf-8")
    problem = journal.record(Artifact(str(p), **file_identity(p), source_kind="fixture",
                                      role="diagnostic", bindings={}))
    def version(name, role, parents=()):
        root = tmp_path / name
        root.mkdir()
        (root / "SKILL.md").write_text("---\nname: local-method\ndescription: Test method\n---\n" + name,
                                       encoding="utf-8")
        content = skills.persist_snapshot("local-method", tmp_path / (name + "-saved"),
                                          bundle_ref={"root": str(root)})
        return journal.record(MethodVersion(name, (role,), content, parents=parents,
                                            verification_basis="content_verified"))
    base = version("base", "domain_skill")
    generator = version("generator", "improvement_method")
    candidate = version("candidate", "domain_skill", (base,))
    use = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": "dev", "scope": str(store.path)},),
        selected_methods=({"method_version_ref": generator, "scope": "lineage only"},), reason="Fixture")
    ref = lambda identifier: journal_ref(journal, identifier, source_id="fixture")
    payload = build_method_revision(problem_refs=[ref(problem)], target_kind="S",
        base_method_ref=ref(base), producer_method_ref=ref(generator), producer_use_ref=ref(use),
        candidate_version_refs=[ref(candidate)], change_summary="Clarify parentage",
        mechanism_hypothesis="Separating producer preserves target lineage", scope="fixture")
    return journal, payload, tmp_path / "published", ref, version


def save(case, payload=None):
    journal, original, directory, _, _ = case
    return record_method_revision(journal, original if payload is None else payload, directory,
                                   source_id="fixture", source_kind="fixture")


def event_count(journal):
    with journal.store.connection() as db:
        return db.execute("SELECT count(*) FROM events").fetchone()[0]


def test_idempotent_content_no_change_and_corruption(case):
    journal, payload, directory, _, _ = case
    identifier = save(case)
    count = event_count(journal)
    assert save(case) == identifier
    assert event_count(journal) == count
    original = journal.get(identifier)
    no_change = {**payload, "candidate_version_refs": [], "change_summary": "Insufficient evidence; retain base"}
    other = save(case, no_change)
    assert other != identifier
    path = directory / (next(p for p in directory.iterdir() if str(p) == original["path"]).name)
    path.write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts"):
        save(case)
    assert journal.get(identifier) == original


def test_rejects_wrong_digest_parent_and_producer_attribution_before_write(case):
    journal, payload, directory, ref, version = case
    bad = copy.deepcopy(payload)
    bad["base_method_ref"]["record_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        save(case, bad)
    generator_id = payload["producer_method_ref"]["record_id"]
    wrong = version("wrong-parent", "domain_skill", (generator_id,))
    with pytest.raises(ValueError, match="descend"):
        save(case, {**payload, "candidate_version_refs": [ref(wrong)]})
    mixed = version("mixed-parent", "domain_skill", (payload["base_method_ref"]["record_id"], generator_id))
    with pytest.raises(ValueError, match="producer is not"):
        save(case, {**payload, "candidate_version_refs": [ref(mixed)]})
    skipped = host.record_method_use(journal,
        subject_refs=({"kind": "request", "id": "skip", "scope": str(journal.store.path)},),
        selected_methods=(), reason="Read but not applied")
    with pytest.raises(ValueError, match="attribute"):
        save(case, {**payload, "producer_use_ref": ref(skipped)})
    assert not directory.exists()


def test_external_evidence_keeps_source_and_unresolved_state(case):
    journal, payload, _, _, _ = case
    external = {**payload["problem_refs"][0], "source_id": "another-source"}
    result = save(case, {**payload, "problem_refs": [external]})
    record = journal.get(result)
    assert record["bindings"]["reference_verification"].endswith("external_unresolved")
    from pathlib import Path
    assert json.loads(Path(record["path"]).read_text(encoding="utf-8"))["problem_refs"] == [external]
    with pytest.raises(ValueError, match="local snapshots"):
        save(case, {**payload, "base_method_ref": {**payload["base_method_ref"], "source_id": "another-source"}})


def test_db_failure_retains_complete_orphan_for_retry(case, monkeypatch):
    journal, _, directory, _, _ = case
    original_record = journal.record
    def fail(item):
        raise RuntimeError("injected registration failure")
    monkeypatch.setattr(journal, "record", fail)
    with pytest.raises(RuntimeError, match="registration"):
        save(case)
    files = list(directory.glob("*.json"))
    assert len(files) == 1
    before = files[0].read_bytes()
    monkeypatch.setattr(journal, "record", original_record)
    result = save(case)
    assert files[0].read_bytes() == before
    assert journal.get(result)["sha256"] == file_identity(files[0])["sha256"]
