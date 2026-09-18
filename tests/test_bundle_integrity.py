"""Bundle identity and transactional writes; ONNX serialization is explicitly simulated."""

from __future__ import annotations

import builtins
import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from etalon.boundary.infra import load
from etalon.learn import bundle


@pytest.fixture
def export_fixture(monkeypatch):
    load("molcascade")
    spec = bundle.representation()
    model = SimpleNamespace(n_features_in_=spec.width)
    surrogate = SimpleNamespace(
        model=model, names=tuple(f"f{index:05d}" for index in range(spec.width)),
        representation_json=json.dumps(spec.model_dump(mode="json"), sort_keys=True),
    )
    calls = []
    state = {"bytes": b"explicitly simulated ONNX; not an inference or accuracy test"}
    converter = ModuleType("skl2onnx")
    common = ModuleType("skl2onnx.common")
    datatypes = ModuleType("skl2onnx.common.data_types")

    class FloatTensorType:
        def __init__(self, shape):
            self.shape = shape

    def to_onnx(model, **kwargs):
        calls.append((model, kwargs))
        return SimpleNamespace(SerializeToString=lambda: state["bytes"])

    converter.to_onnx = to_onnx
    datatypes.FloatTensorType = FloatTensorType
    monkeypatch.setitem(sys.modules, "skl2onnx", converter)
    monkeypatch.setitem(sys.modules, "skl2onnx.common", common)
    monkeypatch.setitem(sys.modules, "skl2onnx.common.data_types", datatypes)
    return surrogate, spec, calls, state


def write(surrogate, path, **kwargs):
    return bundle.export(surrogate, path, model_name="test-model", endpoint_id="target.affinity", **kwargs)


def snapshot(path):
    return {entry.name: entry.read_bytes() for entry in path.iterdir()}


def test_export_digest_is_exactly_the_real_molcascade_consumer_digest(export_fixture, tmp_path):
    from molcascade.plugins.builtin.custom_model import inspect_model_bundle

    surrogate, spec, calls, state = export_fixture
    result = write(surrogate, tmp_path / "bundle")
    report = inspect_model_bundle(result.directory)
    assert report["bundle_sha256"] == result.bundle_sha256
    assert result.as_dict()["cascade_config"]["settings"]["expected_bundle_sha256"] == report["bundle_sha256"]
    assert result.model_sha256 == hashlib.sha256(state["bytes"]).hexdigest()
    assert calls[0][1]["initial_types"][0][1].shape == [None, spec.width]
    assert result.previous_directory is None


def test_consumer_inspection_does_not_import_an_inference_runtime(export_fixture, tmp_path, monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.split(".")[0] in {"onnxruntime", "torch", "tensorflow"}:
            pytest.fail("bundle inspection must not launch inference")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert write(export_fixture[0], tmp_path / "bundle").bundle_sha256


@pytest.mark.parametrize("width", [None, 3, True, "1033", 1033.0])
def test_estimator_width_must_be_known_and_exact_before_export(export_fixture, tmp_path, width):
    surrogate, _, calls, _ = export_fixture
    surrogate.model.n_features_in_ = width
    with pytest.raises(bundle.BundleError, match="width"):
        write(surrogate, tmp_path / "bundle")
    assert calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("change", ["empty", "permuted", "invented"])
def test_training_feature_names_and_order_cannot_be_relabelled(export_fixture, tmp_path, change):
    surrogate, _, calls, _ = export_fixture
    surrogate.names = (() if change == "empty" else tuple(reversed(surrogate.names))
                       if change == "permuted" else ("wrong", *surrogate.names[1:]))
    with pytest.raises(bundle.BundleError, match="feature names"):
        write(surrogate, tmp_path / "bundle")
    assert calls == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("recorded", [None, "{}", "[]", "broken JSON", 4])
def test_unrecorded_or_invalid_training_representation_is_not_guessed(export_fixture, tmp_path, recorded):
    surrogate, _, calls, _ = export_fixture
    surrogate.representation_json = recorded
    with pytest.raises(bundle.BundleError, match="training representation"):
        write(surrogate, tmp_path / "bundle")
    assert calls == [] and list(tmp_path.iterdir()) == []


def test_same_width_same_names_different_fingerprint_is_rejected(export_fixture, tmp_path):
    surrogate, spec, calls, _ = export_fixture
    changed = spec.model_dump(mode="json")
    changed["blocks"][1]["radius"] += 1
    surrogate.representation_json = json.dumps(changed)
    with pytest.raises(bundle.BundleError, match="training representation"):
        write(surrogate, tmp_path / "bundle")
    assert calls == []


def test_unfitted_model_cannot_create_a_directory(export_fixture, tmp_path):
    surrogate = export_fixture[0]
    surrogate.model = None
    with pytest.raises(bundle.BundleError, match="not been fitted"):
        write(surrogate, tmp_path / "bundle")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("payload", [b"", "not bytes", None])
def test_empty_or_invalid_serialization_never_installs_a_bundle(export_fixture, tmp_path, payload):
    export_fixture[3]["bytes"] = payload
    with pytest.raises(bundle.BundleError, match="no model bytes"):
        write(export_fixture[0], tmp_path / "bundle")
    assert list(tmp_path.iterdir()) == []


def test_default_manifest_does_not_invent_target_units_or_potency_direction(export_fixture, tmp_path):
    import yaml

    result = write(export_fixture[0], tmp_path / "bundle")
    manifest = yaml.safe_load((result.directory / bundle.MANIFEST_FILENAME).read_text())
    assert "pIC50" not in manifest["notes"] and "higher meaning more potent" not in manifest["notes"]
    assert "caller" in manifest["notes"]


@pytest.mark.parametrize("fields", [
    {"model_name": ""}, {"endpoint_id": ""}, {"model_name": "bad!name"},
    {"endpoint_id": "has whitespace"}, {"notes": "x" * 8193},
])
def test_consumer_manifest_rules_are_checked_before_creating_outputs(export_fixture, tmp_path, fields):
    options = {"model_name": "test", "endpoint_id": "target", **fields}
    with pytest.raises(bundle.BundleError, match="manifest"):
        bundle.export(export_fixture[0], tmp_path / "bundle", **options)
    assert list(tmp_path.iterdir()) == []


def test_default_export_never_overwrites_an_existing_bundle(export_fixture, tmp_path):
    target = tmp_path / "bundle"
    write(export_fixture[0], target)
    before = snapshot(target)
    with pytest.raises(bundle.BundleError, match="already exists"):
        write(export_fixture[0], target)
    assert snapshot(target) == before


def test_overwrite_archives_old_model_and_never_carries_old_calibration_forward(export_fixture, tmp_path):
    surrogate, _, _, state = export_fixture
    target = tmp_path / "bundle"
    calibration = SimpleNamespace(as_dict=lambda: {"calibration_id": "old", "q": 2})
    first = write(surrogate, target, calibration=calibration)
    before = snapshot(target)
    state["bytes"] = b"second simulated estimator"
    second = write(surrogate, target, overwrite=True)
    assert second.previous_directory is not None
    assert snapshot(second.previous_directory) == before
    assert (target / bundle.MODEL_FILENAME).read_bytes() == state["bytes"]
    assert not (target / bundle.CALIBRATION_FILENAME).exists()
    assert first.bundle_sha256 == bundle._bundle_digest(second.previous_directory)
    assert second.as_dict()["previous_bundle_dir"] == str(second.previous_directory)


def test_current_calibration_sidecar_explicitly_binds_model_bytes(export_fixture, tmp_path):
    calibration = SimpleNamespace(as_dict=lambda: {"calibration_id": "claimed-by-caller", "q": 2})
    result = write(export_fixture[0], tmp_path / "bundle", calibration=calibration)
    body = json.loads((result.directory / bundle.CALIBRATION_FILENAME).read_text())
    assert body["model_sha256"] == result.model_sha256
    assert body["endpoint_id"] == result.endpoint_id
    assert "not independently verified" in body["binding_scope"]


@pytest.mark.parametrize("kind", ["user-file", "subdirectory", "symlink"])
def test_overwrite_refuses_unrelated_files_in_a_valid_bundle(export_fixture, tmp_path, kind):
    target = tmp_path / "bundle"
    write(export_fixture[0], target)
    before = (target / bundle.MODEL_FILENAME).read_bytes()
    extra = target / "user-owned"
    if kind == "user-file":
        extra.write_text("do not delete")
    elif kind == "subdirectory":
        extra.mkdir()
    else:
        extra.symlink_to(tmp_path / "unresolved")
    with pytest.raises(bundle.BundleError, match="unrelated|unsafe"):
        write(export_fixture[0], target, overwrite=True)
    assert extra.exists() or extra.is_symlink()
    assert (target / bundle.MODEL_FILENAME).read_bytes() == before


def test_existing_unrecognized_directory_is_not_cleared(export_fixture, tmp_path):
    target = tmp_path / "user-directory"
    target.mkdir()
    with pytest.raises(bundle.BundleError, match="refused"):
        write(export_fixture[0], target, overwrite=True)
    assert target.is_dir() and list(target.iterdir()) == []


def test_symlink_output_cannot_overwrite_the_referent(export_fixture, tmp_path):
    real = tmp_path / "real"
    write(export_fixture[0], real)
    before = snapshot(real)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(bundle.BundleError, match="symlink"):
        write(export_fixture[0], alias, overwrite=True)
    assert snapshot(real) == before


@pytest.mark.parametrize("existing", [False, True])
def test_staging_write_failure_preserves_old_bundle_and_cleans_owned_temporary_files(export_fixture, tmp_path, monkeypatch, existing):
    target = tmp_path / "bundle"
    if existing:
        write(export_fixture[0], target)
    before = snapshot(target) if existing else None

    def fail(*args):
        raise OSError("simulated full disk")

    monkeypatch.setattr(bundle, "_write_yaml", fail)
    with pytest.raises(OSError, match="full disk"):
        write(export_fixture[0], target, overwrite=existing)
    assert snapshot(target) == before if existing else not target.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == (["bundle"] if existing else [])


def test_install_failure_rolls_back_original_bundle(export_fixture, tmp_path, monkeypatch):
    target = tmp_path / "bundle"
    write(export_fixture[0], target)
    before = snapshot(target)
    original = Path.rename

    def fail_staging(self, destination):
        if self.name.startswith(".etalon-bundle-"):
            raise OSError("simulated install failure")
        return original(self, destination)

    monkeypatch.setattr(Path, "rename", fail_staging)
    with pytest.raises(bundle.BundleError, match="old bundle was preserved"):
        write(export_fixture[0], target, overwrite=True)
    assert snapshot(target) == before
    assert [path.name for path in tmp_path.iterdir()] == ["bundle"]


def test_late_user_file_is_preserved_and_cannot_be_merged_into_new_bundle(export_fixture, tmp_path, monkeypatch):
    target = tmp_path / "bundle"
    write(export_fixture[0], target)
    before = snapshot(target)
    original = bundle._write_yaml

    def write_then_user_update(*args):
        original(*args)
        (target / "user-note.txt").write_text("arrived while exporter converted model")

    monkeypatch.setattr(bundle, "_write_yaml", write_then_user_update)
    with pytest.raises(bundle.BundleError, match="unrelated"):
        write(export_fixture[0], target, overwrite=True)
    assert snapshot(target) == {**before, "user-note.txt": b"arrived while exporter converted model"}


def test_nonfinite_calibration_cannot_replace_a_valid_bundle(export_fixture, tmp_path):
    target = tmp_path / "bundle"
    write(export_fixture[0], target)
    before = snapshot(target)
    calibration = SimpleNamespace(as_dict=lambda: {"q": float("nan")})
    with pytest.raises(ValueError):
        write(export_fixture[0], target, overwrite=True, calibration=calibration)
    assert snapshot(target) == before


def test_export_lock_cannot_be_bypassed_or_removed_by_a_second_exporter(export_fixture, tmp_path):
    target = tmp_path / "bundle"
    lock = tmp_path / f".etalon-export-{hashlib.sha256(str(target).encode()).hexdigest()}.lock"
    lock.write_text("original owner")
    with pytest.raises(bundle.BundleError, match="another export"):
        write(export_fixture[0], target)
    assert not target.exists() and lock.read_text() == "original owner"


def test_model_digest_tampering_is_refused_before_overwrite(export_fixture, tmp_path):
    target = tmp_path / "bundle"
    write(export_fixture[0], target)
    (target / bundle.MODEL_FILENAME).write_bytes(b"untracked replacement")
    before = snapshot(target)
    with pytest.raises(bundle.BundleError, match="refused"):
        write(export_fixture[0], target, overwrite=True)
    assert snapshot(target) == before


def test_real_featurization_and_fitted_forest_preserve_representation_through_export(export_fixture, tmp_path):
    from etalon.learn.surrogate import Surrogate, featurize

    features = featurize(["CCO", "CCN", "CCC", "c1ccccc1"])
    fitted = Surrogate(trees=2, min_samples_leaf=1).fit(features, [1.0, 2.0, 3.0, 4.0])
    assert fitted.representation_json == features.representation_json
    result = write(fitted, tmp_path / "bundle")
    assert result.n_features == features.matrix.shape[1]
    assert result.bundle_sha256 == bundle._bundle_digest(result.directory)


def test_real_equal_width_alternative_features_cannot_be_exported_as_default(export_fixture, tmp_path):
    from molcascade.chemistry.featurizers import RepresentationSpec

    from etalon.learn.surrogate import Surrogate, featurize, representation

    altered = representation().model_dump(mode="json")
    altered["blocks"][1]["radius"] += 1
    features = featurize(["CCO", "CCN", "CCC", "c1ccccc1"], RepresentationSpec.model_validate(altered))
    fitted = Surrogate(trees=2, min_samples_leaf=1).fit(features, [1.0, 2.0, 3.0, 4.0])
    assert fitted.names == export_fixture[0].names
    with pytest.raises(bundle.BundleError, match="training representation"):
        write(fitted, tmp_path / "bundle")
    assert list(tmp_path.iterdir()) == []


def test_manual_matrix_with_matching_names_is_not_promoted_to_known_representation(export_fixture, tmp_path):
    from etalon.learn.surrogate import Features, Surrogate, featurize

    features = featurize(["CCO", "CCN", "CCC", "c1ccccc1"])
    unknown = Features(features.matrix, features.names)
    fitted = Surrogate(trees=2, min_samples_leaf=1).fit(unknown, [1.0, 2.0, 3.0, 4.0])
    assert fitted.representation_json is None
    with pytest.raises(bundle.BundleError, match="training representation"):
        write(fitted, tmp_path / "bundle")
