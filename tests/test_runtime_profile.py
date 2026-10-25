"""Actual child interpreters test profile binding; no scientific jobs are run."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

from etalon.boundary import runtime_profile as runtime


@pytest.fixture
def spec(tmp_path):
    package = tmp_path / "profile_fixture"
    package.mkdir()
    (package / "__init__.py").write_text("from . import implementation\n", encoding="utf-8")
    (package / "implementation.py").write_text("VALUE = 17\n", encoding="utf-8")
    return {"schema": runtime.SPEC_SCHEMA, "python": sys.executable, "cwd": str(tmp_path),
            "environment_root": str(Path(sys.executable).parent.parent), "pythonpath": [str(tmp_path)],
            "modules": [{"name": "profile_fixture", "expected_root": str(package)}],
            "checks": [{"id": "implementation", "argv": ["{python}", "-c",
                         "from profile_fixture.implementation import VALUE; assert VALUE == 17"]}],
            "capabilities": {"fixture": {"modules": ["profile_fixture"], "files": [],
                                          "checks": ["implementation"]}}}


def test_actual_loaded_implementation_and_overlay_are_recorded(spec, tmp_path):
    profile = runtime.capture(spec)
    assert profile["capabilities"]["fixture"] == {
        "available": True, "verified": True, "state": "verified", "missing": [],
        "unverified_checks": [], "real_run": "not_established"}
    loaded = profile["inventory"]["loaded_modules"]
    assert loaded["profile_fixture.implementation"]["path"] == str(tmp_path / "profile_fixture" / "implementation.py")
    assert loaded["profile_fixture.implementation"]["sha256"]
    assert profile["inventory"]["python"]["requested_executable"] == sys.executable
    assert profile["inventory"]["python"]["requested_path_used"]
    assert profile["inventory"]["dependency_layout"]["classification"] == "overlay_observed"
    assert profile["inventory"]["dependency_layout"]["self_contained_environment"] == "not_established"
    assert runtime.verify(profile, "fixture")["verified"]


def test_import_available_is_distinct_from_required_verifier_and_real_run(spec):
    spec["checks"][0]["argv"][-1] = "raise SystemExit(9)"
    profile = runtime.capture(spec)
    state = profile["capabilities"]["fixture"]
    assert state["available"] and not state["verified"] and state["real_run"] == "not_established"
    with pytest.raises(runtime.RuntimeProfileError, match="verification failed"):
        runtime.bind(profile, "fixture", ["-c", "pass"], task_id="refused")
    availability_only = runtime.capture(spec, run_checks=False)
    assert availability_only["checks"] == {}
    assert availability_only["capabilities"]["fixture"]["state"] == "available"


def test_optional_import_cannot_waive_required_check(spec):
    spec["modules"].append({"name": "etalon_missing_optional_fixture"})
    spec["checks"][0]["argv"][-1] = "import etalon_missing_optional_fixture"
    profile = runtime.capture(spec)
    assert not profile["inventory"]["modules"]["etalon_missing_optional_fixture"]["available"]
    assert profile["capabilities"]["fixture"]["available"]
    assert not profile["capabilities"]["fixture"]["verified"]
    assert runtime.verify(profile, "fixture")["checks"]["implementation"]["state"] == "failed"


def test_changed_imported_child_blocks_launch_before_check_or_entry(spec, tmp_path):
    profile = runtime.capture(spec)
    output = tmp_path / "must-not-run"
    binding = runtime.bind(profile, "fixture", ["-c", f"open({str(output)!r}, 'w').write('ran')"], task_id="fixed")
    (tmp_path / "profile_fixture" / "implementation.py").write_text("VALUE = 23\n", encoding="utf-8")
    receipt = runtime.verify(profile, "fixture")
    assert not receipt["identity_matches"] and not receipt["verified"]
    assert receipt["checks"] == {}
    with pytest.raises(runtime.RuntimeProfileError, match="launch refused"):
        runtime.launch(binding)
    assert not output.exists()


def test_changed_known_code_is_rejected_before_running_the_probe(spec, tmp_path, monkeypatch):
    profile = runtime.capture(spec)
    changed = tmp_path / "profile_fixture" / "implementation.py"
    changed.write_text("raise RuntimeError('must not import changed code')\n", encoding="utf-8")

    def forbidden_probe(_spec):
        pytest.fail("changed known files must be checked before launching Python")

    monkeypatch.setattr(runtime, "_inventory", forbidden_probe)
    receipt = runtime.verify(profile, "fixture")
    assert receipt["changed_known_files"] == [str(changed)]
    assert not receipt["verified"]


def test_existing_bytecode_is_identified_and_drift_blocks_verification(spec, tmp_path):
    import py_compile

    source = tmp_path / "profile_fixture" / "implementation.py"
    cached = Path(py_compile.compile(str(source), doraise=True))
    profile = runtime.capture(spec)
    assert profile["inventory"]["present_bytecode_files"]["profile_fixture.implementation"]["path"] == str(cached)
    cached.write_bytes(b"changed-bytecode-fixture")
    receipt = runtime.verify(profile, "fixture")
    assert str(cached) in receipt["changed_known_files"]
    assert not receipt["verified"]


def test_overlay_import_redirection_is_detected_even_when_original_files_survive(spec, tmp_path):
    first, second = tmp_path / "old-site", tmp_path / "new-site"
    first.mkdir()
    second.mkdir()
    for directory in (first, second):
        (directory / "overlay_dependency.py").write_text("VALUE = 17\n", encoding="utf-8")
    locator = tmp_path / "site-locator.txt"
    locator.write_text(str(first), encoding="utf-8")
    (tmp_path / "profile_fixture" / "__init__.py").write_text(
        f"from pathlib import Path\nimport sys\nsys.path.insert(0, Path({str(locator)!r}).read_text(encoding='utf-8'))\n"
        "import overlay_dependency\nfrom . import implementation\n", encoding="utf-8")
    profile = runtime.capture(spec)
    assert profile["inventory"]["loaded_modules"]["overlay_dependency"]["path"] == str(first / "overlay_dependency.py")
    locator.write_text(str(second), encoding="utf-8")
    assert runtime._known_drift(profile["inventory"]) == []
    receipt = runtime.verify(profile, "fixture")
    assert not receipt["identity_matches"] and not receipt["verified"]


def test_asset_loader_reuses_infra_and_pins_actual_manifest(spec, tmp_path):
    assets = tmp_path / "assets"
    package = assets / "molquarry" / "src" / "molquarry"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = 'fixture'\n", encoding="utf-8")
    manifest = assets / "MANIFEST.json"
    manifest.write_text(json.dumps({"assets": {"molquarry": {
        "source_commit": "fixture-not-a-real-commit", "tree_sha256": "fixture-not-a-verified-tree"}}}), encoding="utf-8")
    spec["pythonpath"].append(str(Path(runtime.__file__).parents[2]))
    spec["modules"] = [{"name": "molquarry", "loader": "asset", "asset_dir": str(assets),
                        "expected_root": str(package)}]
    spec["capabilities"]["fixture"]["modules"] = ["molquarry"]
    spec["checks"][0]["argv"][-1] = (
        "from pathlib import Path; from etalon.boundary.infra import load; "
        f"assert load('molquarry', asset_dir=Path({str(assets)!r})).version == 'fixture'")
    profile = runtime.capture(spec)
    module = profile["inventory"]["modules"]["molquarry"]
    assert module["infra_provenance"]["source_commit"] == "fixture-not-a-real-commit"
    assert module["asset_manifest"]["sha256"] == runtime._file(str(manifest))["sha256"]
    assert profile["capabilities"]["fixture"]["verified"]
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert not runtime.verify(profile, "fixture")["verified"]


def test_missing_exact_python_never_falls_back(spec, tmp_path):
    spec["python"] = str(tmp_path / "missing-python")
    with pytest.raises(runtime.RuntimeProfileError, match="explicit Python is unavailable"):
        runtime.capture(spec)


def test_old_binding_survives_new_default_and_argv_is_literal(spec, tmp_path):
    profile = runtime.capture(spec)
    output = tmp_path / "literal.json"
    literal = "a b; $(never-run) `also-not-run` & echo"
    argv = ["-c", f"import json,sys; open({str(output)!r}, 'w').write(json.dumps(sys.argv[1:]))", literal]
    binding = runtime.bind(profile, "fixture", argv, task_id="existing")
    # An external default points elsewhere. Existing binding contains a full copy;
    # launch reads neither this file nor the mutable originating Python dictionary.
    (tmp_path / "default.json").write_text('{"python":"missing-new-default"}', encoding="utf-8")
    profile["spec"]["python"] = "missing-new-default"
    receipt = runtime.launch(binding)
    assert receipt["returncode"] == 0
    assert receipt["scientific_success"] == "not_established"
    assert json.loads(output.read_text(encoding="utf-8")) == [literal]


def test_script_and_config_are_frozen_and_manifest_tampering_rejected(spec, tmp_path):
    profile = runtime.capture(spec)
    script, config = tmp_path / "entry.py", tmp_path / "config.json"
    script.write_text("pass\n", encoding="utf-8")
    config.write_text('{"input":"first"}', encoding="utf-8")
    binding = runtime.bind(profile, "fixture", [str(script), str(config)], task_id="entry",
                           command_files=[str(config)])
    assert set(binding["command_files"]) == {str(script), str(config)}
    config.write_text('{"input":"second"}', encoding="utf-8")
    with pytest.raises(runtime.RuntimeProfileError, match="bound command file changed"):
        runtime.launch(binding)
    changed = copy.deepcopy(binding)
    changed["argv"][0] = "replacement.py"
    with pytest.raises(runtime.RuntimeProfileError, match="binding digest"):
        runtime.launch(changed)
    changed_profile = copy.deepcopy(profile)
    changed_profile["spec"]["python"] = "replacement-python"
    with pytest.raises(runtime.RuntimeProfileError, match="manifest digest"):
        runtime.verify(changed_profile, "fixture")


def test_declared_native_and_weight_content_is_bound(spec, tmp_path):
    weight = tmp_path / "fixture.weights"
    weight.write_bytes(b"fixture-not-a-model")
    spec["files"] = [{"id": "weights", "path": str(weight), "kind": "weight"},
                     {"id": "native", "path": sys.executable, "kind": "native"}]
    spec["capabilities"]["fixture"]["files"] = ["weights", "native"]
    profile = runtime.capture(spec)
    assert profile["inventory"]["files"]["weights"]["sha256"]
    weight.write_bytes(b"different-fixture")
    assert not runtime.verify(profile, "fixture")["verified"]


def test_import_root_and_ambient_pythonpath_do_not_fall_back(spec, tmp_path, monkeypatch):
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    (ambient / "unrequested_fixture.py").write_text("VALUE = True", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(ambient))
    monkeypatch.setenv("RUNTIME_PROFILE_SECRET", "must-never-be-recorded")
    spec["modules"].append({"name": "unrequested_fixture"})
    profile = runtime.capture(spec)
    assert not profile["inventory"]["modules"]["unrequested_fixture"]["available"]
    assert "must-never-be-recorded" not in json.dumps(profile)
    spec["modules"][0]["expected_root"] = str(ambient)
    rejected = runtime.capture(spec)
    assert rejected["inventory"]["modules"]["profile_fixture"]["error"] == "loaded_outside_expected_root"
    assert not rejected["capabilities"]["fixture"]["available"]


def test_capability_check_cannot_be_empty_and_secrets_are_not_configurable(spec):
    spec["capabilities"]["fixture"]["checks"] = []
    with pytest.raises(ValueError, match="at least one required verifier"):
        runtime.capture(spec)
    spec["capabilities"]["fixture"]["checks"] = ["implementation"]
    spec["environment"] = {"API_TOKEN": "do-not-capture"}
    with pytest.raises(ValueError, match="do not store secrets"):
        runtime.capture(spec)


def test_cli_exclusive_receipt_prevents_duplicate_entry(spec, tmp_path):
    profile = runtime.capture(spec)
    output = tmp_path / "entry-output"
    binding = runtime.bind(profile, "fixture", ["-c", f"open({str(output)!r}, 'w').write('run')"], task_id="cli")
    binding_path, receipt_path = tmp_path / "binding.json", tmp_path / "receipt.json"
    binding_path.write_text(json.dumps(binding), encoding="utf-8")
    receipt_path.write_text("old receipt", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        runtime.main(["launch", "--binding", str(binding_path), "--receipt", str(receipt_path)])
    assert error.value.code == 2
    assert not output.exists()
    assert receipt_path.read_text(encoding="utf-8") == "old receipt"


@pytest.mark.skipif(os.name != "posix", reason="venv Python symlink semantics are POSIX-specific")
def test_venv_python_symlink_is_invoked_without_resolving(spec, tmp_path):
    import venv

    environment = tmp_path / "isolated-venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin" / "python"
    assert python.is_symlink()
    spec["python"] = str(python)
    spec["environment_root"] = str(environment)
    profile = runtime.capture(spec)
    identity = profile["inventory"]["python"]
    assert identity["requested_executable"] == str(python)
    assert identity["reported_executable"] == str(python)
    assert identity["prefix"] == str(environment)
    assert identity["venv_config"]["available"]
    (environment / "pyvenv.cfg").write_text((environment / "pyvenv.cfg").read_text() + "\n# changed\n")
    assert not runtime.verify(profile, "fixture")["verified"]
