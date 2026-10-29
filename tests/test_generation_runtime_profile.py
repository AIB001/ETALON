"""Actual local PRISM import/config inspection; the tiny weight is a labelled fixture."""
import json
import sys
from pathlib import Path

import pytest

from etalon.generate.runtime import inspect_runtime, generation_command
from etalon.runtime.files import file_identity


def test_profile_binds_actual_source_and_changed_config_requires_new_profile(tmp_path):
    source = Path(__file__).parents[1] / "asset" / "prism"
    weight = tmp_path / "fixture.pt"
    weight.write_bytes(b"not a scientific model weight; inspection fixture only")
    config = tmp_path / "generation.json"
    config.write_text(json.dumps({"models": {"targetdiff": {"enabled": True, "backend": "conda",
        "environment": "fixture-not-executed", "command": ["python", "fixture.py"],
        "artifacts": {"checkpoint": {"path": str(weight), "sha256": file_identity(weight)["sha256"]}}}}}))
    profile = inspect_runtime(sys.executable, config, "targetdiff", prism_source=source)
    assert Path(profile["observed"]["prism_source"]).resolve() == (source / "prism").resolve()
    assert profile["readiness"] == "inspected_not_executed"
    call = generation_command(profile, protein="fixture.pdb", pocket="pocket.pdb", output=tmp_path / "out",
        num_samples=1, seed=17, device="cpu", pocket_kind="structure", add_hydrogens=False)
    assert call["argv"][:3] == [str(Path(sys.executable).absolute()), "-m", "prism.cli"]
    assert call["argv"][call["argv"].index("--device") + 1] == "cpu"
    assert call["argv"][call["argv"].index("--pocket-kind") + 1] == "structure"
    assert "--no-hydrogens" in call["argv"]
    config.write_text(config.read_text() + "\n")
    with pytest.raises(ValueError, match="profile changed"):
        generation_command(profile, protein="fixture.pdb", pocket="pocket.pdb", output=tmp_path / "out", num_samples=1, seed=17)


def test_missing_weights_do_not_become_executable_readiness(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"models": {"targetdiff": {"enabled": True, "backend": "conda",
        "artifacts": {"checkpoint": {"path": str(tmp_path / "missing.pt")}}}}}))
    profile = inspect_runtime(sys.executable, config, "targetdiff", prism_source=Path(__file__).parents[1] / "asset" / "prism")
    assert profile["observed"]["missing_weights"]
    with pytest.raises(ValueError, match="missing model weights"):
        generation_command(profile, protein="fixture.pdb", pocket="pocket.pdb", output=tmp_path / "out", num_samples=1, seed=17)
