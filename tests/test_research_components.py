"""Actual local component readers; inputs are labelled engineering fixtures."""
import json
from pathlib import Path
import shutil

import pytest

from etalon.boundary.quarry import DataBudget
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.data.service import run_data
from etalon.research.components import capabilities, inspect_component, record_component_step
from etalon.research.continuation import read_content, resume_research
from etalon.research.evidence_refs import EvidenceError, file_identity
from test_research_continuation import case, checkpoint, publish, save

FIXTURE = Path(__file__).parent / "fixtures/prism_design_bundle_v1"


def record(case, capability_id, location, **kwargs):
    j, _, _, root = case
    return record_component_step(j, capability_id=capability_id, location=location,
        directory=root / "content", source_id="research", source_kind="fixture",
        task_id="case", question="Can verified component evidence be reused?", branch_id="existing",
        node_id="read", reason="Existing fixture outputs suffice", **kwargs)


def files(root):
    return {str(p): file_identity(p) for p in root.rglob("*") if p.is_file()}


def test_capabilities_keep_ownership_and_unsupported_scope_explicit():
    catalog = capabilities()
    rows = {c["id"]: c for c in catalog["capabilities"]}
    assert len(rows["molquarry.data"]["skills"]) == 6
    assert rows["prism.general_fep_lab"]["integration"] == "not_connected"
    assert rows["prism.bridge_v1"]["memory_reader"] is None
    assert rows["prism.legacy_mmpbsa"]["integration"] == "existing_asset_executor"
    with pytest.raises(EvidenceError, match="no read adapter"):
        inspect_component("prism.general_fep_lab", "not-a-real-path")


def test_quarry_actual_catalog_read_is_sealed_and_replay_does_not_requery(case):
    j, _, _, root = case
    path = root / "fixture.csv"
    path.write_text("id,smiles\nfixture-ethanol,CCO\nfixture-benzene,c1ccccc1\n")
    result = run_data({"kind": "import_catalog", "source": "chembl", "path": str(path),
                      "options": {"source_version": "SYNTHETIC-ENGINEERING"}, "search": {"limit": 10}},
                     root / "quarry", run_id="fixture", budget=DataBudget(max_requests=0))
    snapshot = Path(result["snapshot"])
    before = files(root / "quarry")
    node = record(case, "molquarry.data", snapshot)
    events = j.store.events()
    assert record(case, "molquarry.data", snapshot) == node
    assert j.store.events() == events and files(root / "quarry") == before
    assert result["usage"]["requests"] == 0
    cp = save(case, checkpoint(case, node["step_ref"]))
    context = resume_research(publish(case), cp)
    assert not context["issues"] and len(node["artifact_refs"]) > 1
    (snapshot / "result.json").write_text("{}")
    events = j.store.events()
    with pytest.raises(ValueError, match="changed"):
        record(case, "molquarry.data", snapshot)
    assert j.store.events() == events


def test_real_cpu_screen_receipts_read_without_dispatch_or_label_admission(case, monkeypatch):
    j, _, _, root = case
    library = root / "fixture.csv"
    library.write_text("id,smiles\nfixture-ethanol,CCO\nfixture-benzene,c1ccccc1\n")
    config = root / "screen.json"
    config.write_text(json.dumps(compose("cpu-fixture", [{"id": "properties", "title": "CPU properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}])))
    screen = Screen(root / "screen")
    result = screen.run(screen.plan(config, library), run_id="fixture")
    assert result.terminal and result.outcome == "committed"
    before = files(root / "screen")
    def forbidden(*args, **kwargs):
        raise AssertionError("a memory read must not run the screen")
    monkeypatch.setattr(Screen, "run", forbidden)
    node = record(case, "molcascade.screen", root / "screen", run_id="fixture")
    events = j.store.events()
    assert record(case, "molcascade.screen", root / "screen", run_id="fixture") == node
    assert j.store.events() == events and files(root / "screen") == before
    cp = save(case, checkpoint(case, node["step_ref"]))
    ws = publish(case)
    assert not resume_research(ws, cp)["issues"]
    receipt = j.get(node["receipt_ref"]["record_id"])
    payload = json.loads(Path(receipt["path"]).read_text("utf-8"))
    assert payload["native"]["result"]["run_id"] == "fixture"
    assert payload["scientific_admission"] == "not_performed"
    assert not j.store.actions() and not j.store.observations()
    # Native mutable status may advance; the exact historical bytes must survive.
    status_path = root / "screen/runs/fixture.json"
    frozen_status = next(j.get(r["record_id"]) for r in node["artifact_refs"]
                         if j.get(r["record_id"])["bindings"].get("original_path") == str(status_path))
    assert frozen_status["path"] != str(status_path)
    assert Path(frozen_status["path"]).read_bytes() == status_path.read_bytes()
    status_path.write_text("fixture changed producer state")
    assert not resume_research(ws, cp)["issues"]  # Historical memory, not a live heartbeat.
    with pytest.raises(EvidenceError, match="no such screen"):
        record(case, "molcascade.screen", root / "screen", run_id="absent")


def test_prism_bundle_preserves_failed_slots_and_exact_state_pose_identity(case):
    j, _, _, root = case
    bundle = root / "fixture-bundle"
    shutil.copytree(FIXTURE, bundle)
    before = files(bundle)
    inspected = inspect_component("prism.design", bundle / "bundle.json")
    assert len(inspected["native"]["candidates"]) == 4
    assert any(c["raw_status"] != "DECODED" for c in inspected["native"]["candidates"])
    assert {i["kind"] for i in inspected["identities"]} >= {"candidate", "molecule", "chemical_state", "pose"}
    node = record(case, "prism.design", bundle / "bundle.json")
    events = j.store.events()
    assert record(case, "prism.design", bundle / "bundle.json") == node
    assert j.store.events() == events and files(bundle) == before
    cp = save(case, checkpoint(case, node["step_ref"]))
    ws = publish(case)
    assert not resume_research(ws, cp)["issues"]
    assert read_content(ws, node["step_ref"])["detail"]["state"] == "reused"
    raw = json.loads((bundle / "bundle.json").read_text())
    raw["candidates"][0]["identity"]["pose_id"] = "pose-" + "0" * 64
    (bundle / "bundle.json").write_text(json.dumps(raw))
    events = j.store.events()
    with pytest.raises(ValueError, match="identity derivation"):
        record(case, "prism.design", bundle / "bundle.json")
    assert j.store.events() == events and not j.store.actions()
