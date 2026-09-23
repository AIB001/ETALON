"""A handoff with multiple geometry producers must preserve the selected evidence."""

from __future__ import annotations

import json

import pytest
import yaml

from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose


def test_handoff_requires_and_preserves_explicit_geometry_binding(tmp_path):
    config = compose("two-conformers", [{"id": "prepare", "title": "Two preparations",
        "mode": "serial", "criteria": [
            component("first", "docking.rdkit_conformers@0.1.0"),
            component("second", "docking.rdkit_conformers@0.1.0"),
        ]}])
    source = tmp_path / "source.json"
    source.write_text(json.dumps(config))
    library = tmp_path / "library.csv"
    library.write_text("id,smiles\na,CCO\n")
    screen = Screen(tmp_path / "work")
    ambiguous = screen.with_handoff(source, tmp_path / "ambiguous.yaml", prefer="embedded_conformer")
    from molcascade.errors import ConfigError

    with pytest.raises(ConfigError, match="earlier stages"):
        screen.plan(ambiguous, library)
    selected = screen.with_handoff(source, tmp_path / "selected.yaml", prefer="embedded_conformer",
        evidence_from={"ligand_conformer/v1": "first"})
    authored = yaml.safe_load(selected.read_text())
    assert authored["tiers"][-1]["criteria"][0]["evidence_from"] == {"ligand_conformer/v1": "first"}
    assert json.loads(source.read_text()) == config
    plan = screen.plan(selected, library)
    handoff = next(stage for stage in plan._internal["compiled"].stages
                   if "handoff" in stage.plugin_key)
    assert any(binding.source_stage_id == "first" for binding in handoff.input_bindings)


def test_handoff_rejects_malformed_evidence_binding_before_writing(tmp_path):
    config = compose("minimal", [{"id": "prepare", "title": "Prepare", "mode": "serial",
        "criteria": [component("first", "docking.rdkit_conformers@0.1.0")]}])
    source = tmp_path / "source.json"
    source.write_text(json.dumps(config))
    destination = tmp_path / "invalid.yaml"
    with pytest.raises(ValueError, match="contract reference"):
        Screen(tmp_path / "work").with_handoff(source, destination,
            evidence_from={"not-a-contract": "first"})
    assert not destination.exists()
