"""V independent function-boundary checks; synthetic fixtures are not scientific gains."""
from dataclasses import replace
import hashlib
import json
import math
import sqlite3
import subprocess
import sys

import pytest

from etalon.active.store import CampaignStore
from etalon.boundary.screen import Screen
from etalon.campaign.calibrate import PanelMember, calibrate
from etalon.research import Contrast, Coupling, MethodVersion, Observation, ResearchJournal
from test_function_capabilities import cpu_config
from test_function_mcp import tools
from test_research_function_methods import build_research_fixture


def inventory(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_v_legacy_query_preserves_duplicate_molecules_and_protocol_changes(tmp_path):
    """Old scientific facts must not be 'repaired' merely to answer status."""
    pool = tmp_path / "old.sqlite"
    with sqlite3.connect(pool) as db:
        db.executescript("""
        CREATE TABLE molecule(key TEXT,smiles TEXT,source TEXT,batch TEXT);
        CREATE TABLE batch(batch_id TEXT,size INTEGER,emitted_at TEXT,
          claimed_by TEXT,claimed_at TEXT,run_id TEXT,revision_id TEXT,
          outcome TEXT,recorded_at TEXT);
        INSERT INTO molecule VALUES ('a','CCO','one','a'),('b','CCO','two','b');
        INSERT INTO batch VALUES
          ('a',1,'then','w','then','ra','strict','exhausted','then'),
          ('b',1,'then','w','then','rb','relaxed','committed','then'),
          ('c',1,'now','w','now','rc','relaxed',NULL,NULL);
        """)
    before = inventory(tmp_path)
    response = json.loads(tools()["etalon_sweep_status"](
        workspace=str(tmp_path / "unused"), pool=str(pool),
        ledger=str(tmp_path / "unused.jsonl")))
    assert response["ok"]
    state = response["sweep"]
    assert state["pool"]["smiles_conflicts"] == 1
    assert state["pool"]["one_row_per_smiles"] is False
    assert state["comparable"] is False
    assert state["revisions"] == ["relaxed", "strict"]
    assert state["etalon_revisions"] == []  # absent provenance remains absent
    assert state["batches"]["by_outcome"] == {"exhausted": 1, "committed": 1}
    assert state["batches"]["outstanding"] == 1
    assert inventory(tmp_path) == before


def test_v_corrupt_run_cannot_become_missing_or_trigger_calibration_repair(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "runs").mkdir(parents=True)
    (workspace / "runs" / "broken.json").write_text("{bad", encoding="utf8")
    before = inventory(tmp_path)
    api = tools()
    for tool, arguments in (
        ("etalon_sweep_progress", {}),
        ("etalon_calibrate_gates", {"panel_json": '[{"parent_id":"a","known_active":true}]'}),
    ):
        result = json.loads(api[tool](workspace=str(workspace), run_id="broken", **arguments))
        assert result["ok"] is False
        assert result["error"]["code"] not in {"RunNotFound", "FileNotFound"}
        assert inventory(tmp_path) == before
    assert set(p.name for p in workspace.iterdir()) == {"runs"}


def test_v_actual_zero_hit_artifacts_can_be_calibrated_without_new_execution(tmp_path):
    """Actual cheap CPU algorithm, real artifact reader; no fake calibration adapter."""
    from etalon.authority import NotAuthorized, authorize_gate

    library = tmp_path / "panel.csv"
    library.write_text("id,smiles\nethanol,CCO\nbenzene,c1ccccc1\n", encoding="utf8")
    screen = Screen(tmp_path / "screen")
    plan = screen.plan(cpu_config(tmp_path / "strict.json", 100), library)
    result = screen.run(plan, run_id="panel", workers=1, devices=("cpu",))
    assert result.outcome == "exhausted" and result.status == "FAILED"
    before = inventory(screen.workspace)
    readonly = Screen.open_existing(screen.workspace)
    measured = calibrate(readonly, readonly.state("panel"), (
        PanelMember("ethanol", True), PanelMember("benzene", False)))
    # The gate's zero survival is known; a later failed tier has no population artifact.
    # The end-to-end number must remain unknown rather than fabricate measured zero.
    assert measured.recall is None
    assert measured.tiers[0].surviving == 0
    assert measured.tiers[1].unavailable
    assert measured.admissible is False
    assert "ethanol" in measured.deleted_actives
    with pytest.raises(NotAuthorized):
        authorize_gate(measured)
    assert inventory(screen.workspace) == before
    assert len(list((screen.workspace / "runs").glob("*.json"))) == 1


def test_v_failed_factorial_cell_remains_unknown_without_new_labels(tmp_path):
    store, journal, refs = build_research_fixture(tmp_path / "journal")
    original = journal.get(refs["complete_contrast"])
    measured = journal.get(original["cells"]["11"])
    failed = Observation(measured["experiment_id"], "failed", {}, {},
                         tuple(measured["evidence"]), reason="explicit fixture measurement failure")
    journal.record(failed)
    contrast = Contrast(original["task_id"], original["question"],
                        {**original["cells"], "11": failed.id}, original["readout"],
                        # The existing record is parsed only to preserve its exact coupling.
                        Coupling(**original["coupling"]),
                        original["method_id"])
    journal.record(contrast)
    before = store.export()
    readonly = ResearchJournal(CampaignStore(store.path, read_only=True))
    result = readonly.factorial(contrast.id)
    assert result["status"] == "unresolved"
    assert result["interaction"] is None and result["uncertainty"] is None
    assert result["missing_or_failed"] == {"11": "failed"}
    assert result["cells"] == {"00": 1, "01": 2, "10": 3}
    assert store.export() == before
    assert not store.actions() and not store.observations()


def test_v_invalid_parent_types_are_atomic_and_parentless_versions_still_legal(tmp_path):
    store, journal, refs = build_research_fixture(tmp_path / "lineage")
    api = tools()
    root = MethodVersion("standalone", ("domain_skill",), {"manifest_sha256": "a" * 64})
    root_id = journal.record(root)
    original_root = journal.get(root_id)
    before = store.export()
    before_balance = store.balance()
    for bad_parent in (refs["artifact"], "methodversion-" + "b" * 64):
        proposal = replace(root, name="bad-child", parents=(bad_parent,))
        response = json.loads(api["etalon_method_record"](
            database=str(store.path), record=proposal.as_dict()))
        assert response["ok"] is False
        assert store.export() == before
    child = replace(root, name="actual-child", parents=(root_id,),
                    content_ref={"manifest_sha256": "c" * 64})
    saved = json.loads(api["etalon_method_record"](
        database=str(store.path), record=child.as_dict()))
    assert saved["ok"] and saved["record"]["parents"] == [root_id]
    assert journal.get(root_id) == original_root
    independent = replace(root, name="unrelated-root")
    assert journal.record(independent)
    assert journal.get(independent.id)["parents"] == []
    assert store.balance() == before_balance


def test_v_wilson_tail_bounds_symmetry_and_rejected_unknown_confidence():
    from etalon.generate.audit import wilson

    for total in (1, 2, 10, 100000):
        for successes in {0, 1, total // 2, total}:
            prior_width = -1
            for confidence in (.5, .95, .999, math.nextafter(1.0, 0.0)):
                low, high = wilson(successes, total, confidence=confidence)
                assert 0 <= low <= successes / total <= high <= 1
                assert high - low >= prior_width - 1e-15
                mirror = wilson(total - successes, total, confidence=confidence)
                assert (low, high) == pytest.approx((1-mirror[1], 1-mirror[0]), abs=5e-15)
                prior_width = high - low
                if successes == 0:
                    assert low == 0.0
                if successes == total:
                    assert high == 1.0
    for confidence in (0, 1, -.1, 1.1, float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError, match="confidence"):
            wilson(1, 2, confidence=confidence)
    for successes, total in ((0, 0), (-1, 10), (11, 10)):
        with pytest.raises(ValueError):
            wilson(successes, total)


def test_v_productivity_standalone_cold_process_never_imports_scientific_dlls(tmp_path):
    """Separate dependency check; I/C provide actual cold stdio/native transport evidence."""
    code = """
import sys
from etalon.generate.productivity import Chunk, Productivity, allocate
assert 'numpy' not in sys.modules and 'scipy' not in sys.modules
r = allocate([Productivity('cold','fixture','same', (Chunk(100,100,0,60),)*3)])
assert 'cold' in r['retire']
assert r['loops'][0]['uniqueness']['cost_multiplier'] is None
assert 'numpy' not in sys.modules and 'scipy' not in sys.modules
"""
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                         capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stderr
    assert not list(tmp_path.iterdir())
