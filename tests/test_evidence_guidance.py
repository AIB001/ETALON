"""Host resource closure and the source example against real read-only SDK calls.

Inputs below are engineering fixtures, not independent scientific evidence or
native host-adoption observations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys

import pytest

from etalon import host, skills
from etalon.research.evidence_refs import EvidenceError, make_ref


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "evidence_reuse.py"


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def published(tmp_path):
    """Two explicitly published records referencing one real, synthetic file."""
    original = tmp_path / "released.txt"
    data = b"synthetic engineering evidence; scientific benefit unknown\n"
    original.write_bytes(data)
    body = {"path": original.name, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "source_kind": "fixture", "role": "diagnostic",
            "cost": None, "statement": "a released engineering observation"}
    second = {**body, "statement": "a counterexample; not an independent repeat"}
    manifest = tmp_path / "artifacts.json"
    _write_json(manifest, {"schema": "etalon-artifact-manifest/1", "records": [
        {"record_id": "result", "record_type": "artifact", "record": body,
         "visibility": "released"},
        {"record_id": "counter", "record_type": "artifact", "record": second,
         "visibility": "released"},
    ]})
    workspace = tmp_path / "etalon-workspace.json"
    _write_json(workspace, {"schema": "etalon-evidence-workspace/1",
        "workspace_id": "guidance-fixture", "sources": [{
            "source_id": "released", "kind": "artifact_manifest", "location": manifest.name,
            "identity": {"kind": "snapshot_sha256",
                         "value": hashlib.sha256(manifest.read_bytes()).hexdigest()},
        }], "location_map": [], "status_refs": []})
    return workspace, make_ref("released", "result", body), body


def _local_links(path):
    for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text(encoding="utf-8-sig")):
        if "://" not in link and not link.startswith("#"):
            yield (path.parent / link.split("#", 1)[0]).resolve()


@pytest.mark.parametrize("consumer", ["codex", "claude"])
def test_exported_entry_and_optional_guides_have_complete_local_links(tmp_path, consumer):
    target = tmp_path / consumer
    manifest = host.export(target, consumer)
    role = target / ("AGENTS.md" if consumer == "codex" else "CLAUDE.md")
    assert role.read_bytes() == skills.ROLE_PATH.read_bytes()
    guides = list((target / "host-guides").glob("*.md"))
    assert guides
    for document in [role, target / "HOST_SETUP.md", *guides]:
        for linked in _local_links(document):
            assert linked.is_relative_to(target)
            assert linked.is_file(), (document, linked)
            assert linked.relative_to(target).as_posix() in manifest["files"]
    directory = ".agents" if consumer == "codex" else ".claude"
    research = target / directory / "skills" / "etalon-research"
    for linked in _local_links(research / "SKILL.md"):
        assert linked.is_relative_to(research) and linked.is_file()
    source_manifest, contents = skills.snapshot("research")
    assert "references/evidence-reuse.md" in contents
    assert source_manifest in manifest["skills"]
    assert (research / "references/evidence-reuse.md").read_bytes() == contents["references/evidence-reuse.md"]
    verified = host.verify(target)
    assert verified["export_matches_manifest"] and verified["matches_current_source"]


def test_example_query_is_one_page_and_independent_of_working_directory(published, tmp_path, monkeypatch):
    workspace, _, _ = published
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    other = tmp_path / "unrelated-cwd"
    other.mkdir()
    monkeypatch.chdir(other)
    inspect = runpy.run_path(str(EXAMPLE))["inspect_evidence"]
    first = inspect(workspace, limit=1)
    assert len(first["items"]) == 1 and first["next_cursor"]
    assert first["coverage"][0]["status"] == "complete"
    second = inspect(workspace, limit=1, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert {first["items"][0]["ref"]["record_id"],
            second["items"][0]["ref"]["record_id"]} == {"result", "counter"}
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    assert not list(other.iterdir())


def test_known_ref_bypasses_query_and_distinguishes_metadata_from_bytes(published, monkeypatch):
    from etalon.research import evidence

    workspace, ref, body = published
    def forbidden_query(*args, **kwargs):
        pytest.fail("known-ref read must not require a query")
    monkeypatch.setattr(evidence, "query_evidence", forbidden_query)
    inspect = runpy.run_path(str(EXAMPLE))["inspect_evidence"]
    metadata = inspect(workspace, ref=ref)
    assert metadata["record"] == body and metadata["record"]["cost"] is None
    assert metadata["verification"]["artifact"] == "not_checked"
    original = inspect(workspace, ref=ref, view="artifact", selector="bytes", limit=7)
    assert original["verification"]["artifact"] == "verified"
    assert base64.b64decode(original["fragment"]["content"]) == b"synthet"
    assert original["fragment"]["next_offset"] == 7
    assert original["ref"]["source_id"] == ref["source_id"]
    assert original["ref"]["sha256"] == body["sha256"]
    (workspace.parent / body["path"]).write_bytes(b"changed original")
    with pytest.raises(EvidenceError, match="hash|size"):
        inspect(workspace, ref=ref, view="artifact")


def test_example_cli_emits_unmodified_sdk_record_with_no_query(published, tmp_path, capsys):
    workspace, ref, body = published
    ref_file = tmp_path / "known-ref.json"
    _write_json(ref_file, ref)
    main = runpy.run_path(str(EXAMPLE))["main"]
    assert main(["--workspace", str(workspace), "--ref", str(ref_file)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["record"] == body and report["ref"] == ref
    assert report["verification"]["artifact"] == "not_checked"
    with pytest.raises(ValueError, match="known-ref"):
        main(["--workspace", str(workspace), "--ref", str(ref_file), "--filters", "{}"])
    _write_json(ref_file, None)
    with pytest.raises(SystemExit) as error:
        main(["--workspace", str(workspace), "--ref", str(ref_file)])
    assert error.value.code == 2  # A malformed direct ref must not become a query.


def test_missing_source_is_reported_without_creating_a_database(tmp_path):
    workspace = tmp_path / "etalon-workspace.json"
    _write_json(workspace, {"schema": "etalon-evidence-workspace/1",
        "workspace_id": "missing-fixture", "sources": [{"source_id": "missing",
            "kind": "journal", "location": "absent.sqlite",
            "identity": {"kind": "snapshot_sha256", "value": "a" * 64}}]})
    inspect = runpy.run_path(str(EXAMPLE))["inspect_evidence"]
    result = inspect(workspace)
    assert result["items"] == [] and result["warnings"]
    assert result["coverage"][0]["status"] == "unavailable"
    assert not (tmp_path / "absent.sqlite").exists()
    with pytest.raises(EvidenceError, match="unsupported evidence filters"):
        inspect(workspace, filters={"invented": ["ignored"]})


def _ordinary_documents(root):
    """Normal SDK writes; topic text exists only in immutable original content."""
    from etalon.active import CampaignSpec, CampaignStore, Endpoint
    from etalon.boundary.prism_v1 import file_identity
    from etalon.research import Artifact, MethodUse, MethodVersion, ResearchJournal
    from etalon.research.method_revision import build_method_revision, journal_ref, record_method_revision

    root.mkdir()
    store = CampaignStore(root / "journal.sqlite")
    store.configure(CampaignSpec("unmeasured", 1, "fixture_credit", "ordinary-record-fixture"),
                    [Endpoint("unmeasured", "fixture", "not_measured", "count", "fixture/1", 1, queryable=False)])
    journal = ResearchJournal(store)
    problem_path = root / "problem.txt"
    problem_path.write_text("Synthetic support and uncertainty; no native evaluation.", encoding="utf-8")
    problem = journal.record(Artifact(str(problem_path), **file_identity(problem_path),
                                      source_kind="fixture", role="diagnostic", bindings={}))
    target = journal.record(MethodVersion("fixture target", ("domain_skill",), {"manifest_sha256": "a" * 64}))
    producer = journal.record(MethodVersion("fixture producer", ("improvement_method",), {"manifest_sha256": "b" * 64}))
    use = journal.record(MethodUse(
        ({"kind": "request", "id": "fixture-write", "scope": str(store.path)},),
        ({"method_version_ref": producer},),
        reason="synthetic ordinary SDK record construction; no native generator ran"))
    def ref(identifier):
        return journal_ref(journal, identifier, source_id="ordinary")
    revision = build_method_revision(
        problem_refs=[ref(problem)], target_kind="S", base_method_ref=ref(target),
        producer_method_ref=ref(producer), producer_use_ref=ref(use),
        change_summary="Portability lacuna: retain the current method until measured.",
        mechanism_hypothesis="Published content may reveal the unresolved gap.",
        scope="synthetic engineering fixture; no-change; not a scientific finding")
    revision_id = record_method_revision(journal, revision, root / "revisions",
                                         source_id="ordinary", source_kind="fixture")
    claim = {"schema": "etalon-research-claim/1", "question": "What remains unknown?",
             "statement": "Counterfactual ambiguity remains after this fixture.",
             "subjects": ["synthetic engineering"], "conditions": ["normal SDK writes"],
             "epistemic_status": "interpretation", "support_refs": [ref(problem)],
             "counter_refs": [ref(revision_id)], "method_refs": [ref(target)],
             "limitations": ["not a scientific result"], "supersedes": []}
    record_claim = runpy.run_path(str(EXAMPLE))["record_claim"]
    claim_id = record_claim(journal, claim, root / "claims", source_kind="fixture")
    assert record_claim(journal, claim, root / "claims", source_kind="fixture") == claim_id
    return store, journal, {"revision": revision_id, "claim": claim_id}, problem


@pytest.mark.parametrize("kind,topic", [("revision", "Portability lacuna"), ("claim", "Counterfactual ambiguity")])
def test_normal_stored_content_is_found_by_topic_in_another_process(tmp_path, kind, topic):
    import etalon

    store, journal, records, problem = _ordinary_documents(tmp_path / "original")
    original_record = journal.get(records[kind])
    before = store.path.read_bytes()
    workspace = runpy.run_path(str(EXAMPLE))["publish_journals"](
        tmp_path / "published", {"ordinary": store.path}, workspace_id="ordinary-fixture")
    manifest = json.loads(workspace.read_text(encoding="utf-8"))
    assert set(manifest["sources"][0]) == {"source_id", "kind", "location", "identity"}
    assert topic not in workspace.read_text(encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(etalon.__file__).resolve().parents[1]), "PYTHONUTF8": "1"}
    def child(*arguments):
        result = subprocess.run([sys.executable, "-S", str(EXAMPLE), "--workspace", str(workspace), *arguments],
                                cwd=tmp_path, env=env, capture_output=True, check=True, timeout=30)
        return json.loads(result.stdout)
    # Child gets only the normal workspace and topic, not IDs or expected text.
    page = child("--query", topic)
    assert [item["ref"]["record_id"] for item in page["items"]] == [records[kind]]
    ref_file = tmp_path / "discovered-ref.json"
    _write_json(ref_file, page["items"][0]["ref"])
    record = child("--ref", str(ref_file))
    assert record["record"] == original_record
    assert record["ref"]["record_id"] == records[kind]
    lineage = child("--ref", str(ref_file), "--view", "lineage")
    assert problem in {item["record_id"] for item in lineage["items"]}
    original = child("--ref", str(ref_file), "--view", "artifact", "--limit", "32768")
    assert original["verification"]["artifact"] == "verified"
    assert original["ref"]["sha256"] == original_record["sha256"]
    assert topic in base64.b64decode(original["fragment"]["content"]).decode("utf-8")
    assert store.path.read_bytes() == before
    assert journal.get(records[kind]) == original_record


def test_publication_requires_existing_sources_and_never_overwrites(tmp_path):
    publish = runpy.run_path(str(EXAMPLE))["publish_journals"]
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        publish(tmp_path / "output", {"source": missing}, workspace_id="fixture")
    assert not missing.exists() and not (tmp_path / "output").exists()
    store, _, _, _ = _ordinary_documents(tmp_path / "original")
    workspace = publish(tmp_path / "output", {"ordinary": store.path}, workspace_id="fixture")
    before = workspace.read_bytes()
    with pytest.raises(FileExistsError):
        publish(tmp_path / "output", {"ordinary": store.path}, workspace_id="fixture")
    assert workspace.read_bytes() == before


@pytest.mark.parametrize("defect", ["missing", "corrupt"])
def test_typed_claim_content_failure_is_explicit_without_rewriting_journal(tmp_path, defect):
    store, journal, records, _ = _ordinary_documents(tmp_path / "original")
    example = runpy.run_path(str(EXAMPLE))
    workspace = example["publish_journals"](tmp_path / "published", {"ordinary": store.path},
                                            workspace_id="failure-fixture")
    body = journal.get(records["claim"])
    known_ref = make_ref("ordinary", records["claim"], body)
    before = store.path.read_bytes()
    original = Path(body["path"])
    if defect == "missing":
        original.unlink()  # Only this test's generated file, never a real original.
    else:
        original.write_bytes(b"corrupted test bytes")
    page = example["inspect_evidence"](workspace, query="Counterfactual ambiguity")
    assert not page["items"] and page["warnings"]
    assert page["coverage"][0]["status"] == "partial"
    record = example["inspect_evidence"](workspace, ref=known_ref)
    assert record["record"] == body
    assert record["projection"]["status"] == "unavailable"
    assert "content_projection_unavailable" in record["missing"]
    assert store.path.read_bytes() == before
