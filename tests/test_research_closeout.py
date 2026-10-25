"""Synthetic archival fixtures: no molecular computation or scientific claims."""
import importlib.util
import json
from pathlib import Path

import pytest

from etalon.research.continuation import read_content, resume_research
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.publication import resume_publication

spec = importlib.util.spec_from_file_location("research_closeout_example", Path(__file__).parents[1] / "examples/research_closeout.py")
closeout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(closeout)


@pytest.fixture
def packet_case(tmp_path):
    config = {"task_id": "fixture-closeout", "source_id": "fixture", "question": "Archive fixture outcomes",
        "as_of": "2026-01-01T00:00:00Z", "limitations": ["Synthetic archival test, not scientific evidence"],
        "history_entries": [], "branches": [
            {"branch_id": "md", "state": "waiting", "reason": "Await explicitly selected producer output",
             "reconsider_when": "New verified producer output"},
            {"branch_id": "R4b", "state": "stopped", "reason": "User paused",
             "reconsider_when": "Explicit user resumption"}]}
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    bootstrap = closeout.bootstrap(tmp_path / "harness", config_path)
    files = []
    for category in ("protocol", "result", "receipt", "counterevidence"):
        path = tmp_path / (category + ".json")
        path.write_text(json.dumps({"fixture": True, "category": category}), encoding="utf-8")
        files.append({"name": category, "path": str(path), **file_identity(path), "category": category})
    packet = {"schema": "etalon-closeout-packet/1", "task_id": "fixture-closeout", "branch_id": "md",
        "as_of": "2026-01-01T01:00:00Z", "scope": "Fixture only", "node_state": "reused",
        "capability_id": None, "reason": "Archive fixture completed output",
        "reconsider_when": "Changed conditions", "branch_state": "resolved", "choice": "reuse", "files": files}
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    return bootstrap, packet_path, config_path


def test_new_progress_resolves_only_selected_branch_and_is_idempotent(packet_case):
    initial, packet_path, config_path = packet_case
    entry_path = Path(initial["entry"])
    old = json.loads(entry_path.read_text())
    cp_before = read_content(old["workspace"], old["checkpoint_ref"])
    prepared = closeout.prepare_packet(entry_path, packet_path)
    published = resume_publication(prepared["intent_path"])
    updated = json.loads(entry_path.read_text())
    cp = read_content(updated["workspace"], updated["checkpoint_ref"])
    assert cp["branches"][0]["state"] == "resolved"
    assert cp["branches"][1] == cp_before["branches"][1]
    assert set(map(json.dumps, cp_before["counter_refs"])) <= set(map(json.dumps, cp["counter_refs"]))
    assert cp["pending_handles"] == []  # A producer receipt is not a fabricated runtime handle.
    assert cp["step_refs"][:2] == cp_before["step_refs"]
    assert cp["supersedes"] == [old["checkpoint_ref"]]
    identities = {str(p): file_identity(p) for p in entry_path.parent.rglob("*") if p.is_file()}
    assert closeout.prepare_packet(entry_path, packet_path)["status"] == "published"
    assert resume_publication(prepared["intent_path"]) == published
    assert closeout.bootstrap(entry_path.parent, config_path) == initial
    assert identities == {str(p): file_identity(p) for p in entry_path.parent.rglob("*") if p.is_file()}
    resumed = resume_research(updated["workspace"], updated["checkpoint_ref"])
    assert resumed["execution_permission"] == "not_granted" and not resumed["automatic_retry"]
    assert not resumed["issues"]


@pytest.mark.parametrize("problem", ["changed_original", "no_counterevidence", "foreign_task", "unknown_branch"])
def test_unsafe_closeout_does_not_advance_entry(packet_case, problem):
    initial, path, _ = packet_case
    entry = Path(initial["entry"])
    before = file_identity(entry)
    packet = json.loads(path.read_text())
    if problem == "changed_original": Path(packet["files"][0]["path"]).write_text("changed")
    elif problem == "no_counterevidence": packet["files"] = packet["files"][:-1]
    elif problem == "foreign_task": packet["task_id"] = "different"
    elif problem == "unknown_branch": packet["branch_id"] = "different"
    path.write_text(json.dumps(packet), encoding="utf-8")
    with pytest.raises(EvidenceError):
        closeout.prepare_packet(entry, path)
    assert file_identity(entry) == before


@pytest.mark.parametrize("change_config", [False, True])
def test_bootstrap_interruption_binds_configuration_before_snapshot(packet_case, monkeypatch, tmp_path, change_config):
    _, _, config_path = packet_case
    root = tmp_path / "interrupted-bootstrap"
    save = closeout._save

    def interrupted(path, value, **kwargs):
        if Path(path).name == "workspace.json":
            raise SystemExit(73)
        return save(path, value, **kwargs)

    monkeypatch.setattr(closeout, "_save", interrupted)
    with pytest.raises(SystemExit):
        closeout.bootstrap(root, config_path)
    assert (root / "snapshot.sqlite").exists()
    assert not (root / "bootstrap-receipt.json").exists()
    snapshot_identity = file_identity(root / "snapshot.sqlite")
    monkeypatch.setattr(closeout, "_save", save)
    if change_config:
        config = json.loads(config_path.read_text())
        config["branches"][0]["reason"] = "Different scope after crash"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(EvidenceError, match="scope changed during interrupted"):
            closeout.bootstrap(root, config_path)
        assert not (root / "entry.json").exists()
    else:
        restored = closeout.bootstrap(root, config_path)
        entry = json.loads(Path(restored["entry"]).read_text())
        assert read_content(entry["workspace"], entry["checkpoint_ref"])["task_id"] == "fixture-closeout"
        assert closeout.bootstrap(root, config_path) == restored
    assert file_identity(root / "snapshot.sqlite") == snapshot_identity
