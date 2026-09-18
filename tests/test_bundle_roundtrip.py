"""Real CPU ONNX export and MolCascade inference, using synthetic training targets.

These tests verify serialization, feature identity and prediction parity, not molecular
accuracy. Missing optional export/runtime dependencies produce explicit pytest skips.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from etalon.boundary.infra import load
from etalon.learn.bundle import MANIFEST_FILENAME, MODEL_FILENAME, BundleError, export
from etalon.learn.surrogate import Surrogate, featurize


@pytest.fixture
def roundtrip(tmp_path):
    for module in ("numpy", "sklearn", "rdkit", "pyarrow", "yaml", "skl2onnx", "onnxruntime"):
        pytest.importorskip(module, reason="real CPU ONNX roundtrip needs optional model dependencies")
    pytest.importorskip("pydantic", minversion="2.10")
    load("molcascade")

    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import yaml
    from molcascade.artifacts.models import ArtifactDatasetRef
    from molcascade.contracts import PARENT_V1
    from molcascade.plugins.api import StageContext, StageInput, StageRequest
    from molcascade.plugins.builtin.custom_model import (
        CustomModelPredictorPlugin,
        inspect_model_bundle,
    )

    train_smiles = ["CCO", "CCN", "CCC", "CC(=O)O", "c1ccccc1", "c1ccncc1",
                    "CCOC", "CC(C)O", "C1CCCCC1", "O=C(N)C", "CCCl", "CO"]
    features = featurize(train_smiles)
    targets = np.asarray([5.1, 5.6, 4.2, 4.7, 6.8, 6.2, 5.4, 5.0, 6.4, 4.9, 3.8, 3.2])
    fitted = Surrogate(trees=12, min_samples_leaf=1, seed=73).fit(features, targets)
    bundle = export(fitted, tmp_path / "bundle", model_name="roundtrip fixture",
                    endpoint_id="fixture.synthetic_response")
    inspected = inspect_model_bundle(bundle.directory)
    manifest_path = bundle.directory / MANIFEST_FILENAME
    manifest = yaml.safe_load(manifest_path.read_text())

    # Invalid input in the middle verifies that later predictions keep their parent ids.
    score_smiles = ["CCO", "not a SMILES", "CCCO", "c1ccccc1", "CC(C)N"]
    parent_rows = [{"parent_id": f"parent-{index}", "identity_policy_id": "fixture/1",
                    "parent_smiles": smiles, "registration_key": f"fixture-{index}",
                    "stereo_key": None, "formula": None, "duplicate_count": 1}
                   for index, smiles in enumerate(score_smiles)]
    input_root = tmp_path / "input"
    input_root.mkdir()
    input_file = input_root / "parents.parquet"
    pq.write_table(pa.Table.from_pylist(parent_rows, schema=PARENT_V1.schema), input_file)
    reference = ArtifactDatasetRef(
        artifact_id="artifact:sha256:" + hashlib.sha256(input_file.read_bytes()).hexdigest(),
        artifact_kind="fixture.parents", port="primary", contract_id=PARENT_V1.id,
        file_paths=(input_file.name,),
    )
    request = StageRequest(
        stage_id="custom-model", inputs={"parents": StageInput(reference, input_root)},
        config=bundle.as_dict()["cascade_config"]["settings"],
    )
    context = StageContext(tmp_path / "predictions")
    return SimpleNamespace(
        fitted=fitted, features=features, bundle=bundle, inspected=inspected,
        manifest_path=manifest_path, manifest=manifest, score_smiles=score_smiles,
        parent_rows=parent_rows, request=request, context=context,
        plugin=CustomModelPredictorPlugin(),
    )


def _write_manifest(case):
    import yaml

    case.manifest_path.write_text(yaml.safe_dump(case.manifest, sort_keys=False))


def test_real_forest_roundtrips_through_public_consumer_cpu_plugin(roundtrip):
    import numpy as np
    import pyarrow.parquet as pq
    from molcascade.chemistry.featurizers import Featurizer
    from molcascade.plugins.builtin.custom_model import ModelBundleManifest

    case = roundtrip
    manifest = ModelBundleManifest.model_validate(case.manifest)
    assert manifest.representation.model_dump(mode="json") == json.loads(case.features.representation_json)
    assert case.bundle.model_sha256 == hashlib.sha256((case.bundle.directory / MODEL_FILENAME).read_bytes()).hexdigest()
    assert case.inspected["bundle_sha256"] == case.bundle.bundle_sha256
    assert case.request.config["expected_bundle_sha256"] == case.inspected["bundle_sha256"]

    score_features = featurize(case.score_smiles)
    consumer_matrix, failures = Featurizer(manifest.representation).transform(case.score_smiles, dtype="float32")
    np.testing.assert_array_equal(consumer_matrix, score_features.matrix.astype("float32"))
    assert tuple(failures) == score_features.unparsed == (1,)
    expected, spread = case.fitted.predict(score_features)
    assert np.any(spread > 0), "the source forest has uncertainty even though its ONNX mean does not"

    response = case.plugin.execute(case.request, case.context)
    predicted = [row for path in response.outputs["predictions"].file_paths
                 for row in pq.read_table(case.context.staging_root / path).to_pylist()]
    parents = [row for path in response.outputs["primary"].file_paths
               for row in pq.read_table(case.context.staging_root / path).to_pylist()]
    assert parents == case.parent_rows
    assert [row["parent_id"] for row in predicted] == ["parent-0", "parent-2", "parent-3", "parent-4"]
    # The ONNX graph accumulates float32 tree means; sklearn returns float64 means.
    np.testing.assert_allclose([row["prediction_mean"] for row in predicted], expected, rtol=1e-6, atol=1e-6)
    assert all(row["endpoint_id"] == case.bundle.endpoint_id for row in predicted)
    assert all(row["model_id"] == case.inspected["model_id"] for row in predicted)
    assert all(row[key] is None for row in predicted
               for key in ("prediction_std", "interval_lower", "interval_upper", "calibration_id"))
    assert response.metadata["input_count"] == 5 and response.metadata["prediction_count"] == 4
    assert response.metadata["unfeaturizable_count"] == 1
    assert response.metadata["non_finite_prediction_count"] == 0
    assert response.metadata["bundle_sha256"] == case.bundle.bundle_sha256
    assert response.metadata["model_file_sha256"] == case.bundle.model_sha256
    assert response.metadata["uncertainty_available"] is False
    assert response.metadata["calibration_available"] is False
    assert response.metadata["network_or_download_invoked_by_adapter"] is False


def test_wrong_expected_digest_is_refused_before_loading_onnx(roundtrip, monkeypatch):
    from molcascade.errors import PluginError
    from molcascade.plugins.builtin import custom_model

    case = roundtrip
    monkeypatch.setattr(custom_model, "_load_onnx_session", lambda *args, **kwargs: pytest.fail("loaded unpinned graph"))
    request = replace(case.request, config={**case.request.config, "expected_bundle_sha256": "0" * 64})
    with pytest.raises(PluginError) as caught:
        case.plugin.execute(request, case.context)
    assert caught.value.code == "CUSTOM_MODEL_BUNDLE_HASH_MISMATCH"
    assert not case.context.staging_root.exists()


def test_modified_onnx_bytes_are_refused_by_real_consumer(roundtrip):
    from molcascade.errors import PluginError

    case = roundtrip
    model_path = case.bundle.directory / MODEL_FILENAME
    model_path.write_bytes(model_path.read_bytes() + b"untracked change")
    with pytest.raises(PluginError) as caught:
        case.plugin.execute(case.request, case.context)
    assert caught.value.code == "CUSTOM_MODEL_FILE_HASH_MISMATCH"
    assert not case.context.staging_root.exists()


@pytest.mark.parametrize("change", ["morgan_radius", "descriptor_order"])
def test_same_width_feature_changes_cannot_reuse_pinned_bundle_identity(roundtrip, change):
    from molcascade.errors import PluginError
    from molcascade.plugins.builtin.custom_model import inspect_model_bundle

    case = roundtrip
    blocks = case.manifest["representation"]["blocks"]
    if change == "morgan_radius":
        blocks[1]["radius"] += 1
    else:
        blocks[0]["names"] = list(reversed(blocks[0]["names"]))
    _write_manifest(case)
    altered = inspect_model_bundle(case.bundle.directory)
    assert altered["n_features"] == case.inspected["n_features"]
    assert altered["bundle_sha256"] != case.bundle.bundle_sha256
    assert altered["model_id"] != case.inspected["model_id"]
    with pytest.raises(PluginError) as caught:
        case.plugin.execute(case.request, case.context)
    assert caught.value.code == "CUSTOM_MODEL_BUNDLE_HASH_MISMATCH"
    assert not case.context.staging_root.exists()


def test_declared_feature_width_must_agree_with_manifest(roundtrip):
    from molcascade.errors import PluginError
    from molcascade.plugins.builtin.custom_model import inspect_model_bundle

    case = roundtrip
    case.manifest["representation"]["blocks"][0]["names"].pop()
    _write_manifest(case)
    with pytest.raises(PluginError) as caught:
        inspect_model_bundle(case.bundle.directory)
    assert caught.value.code == "CUSTOM_MODEL_MANIFEST_INVALID"


def test_actual_onnx_input_width_is_checked_even_after_repinning_manifest(roundtrip):
    from molcascade.errors import PluginError
    from molcascade.plugins.builtin.custom_model import inspect_model_bundle

    case = roundtrip
    case.manifest["representation"]["blocks"][0]["names"].pop()
    case.manifest["model"]["n_features"] -= 1
    _write_manifest(case)
    altered = inspect_model_bundle(case.bundle.directory)
    request = replace(case.request, config={**case.request.config, "expected_bundle_sha256": altered["bundle_sha256"]})
    with pytest.raises(PluginError) as caught:
        case.plugin.execute(request, case.context)
    assert caught.value.code == "CUSTOM_MODEL_FEATURE_WIDTH_MISMATCH"
    assert not list(case.context.staging_root.rglob("*.parquet"))


def test_real_trained_representation_cannot_be_relabelled_during_export(roundtrip, tmp_path):
    case = roundtrip
    changed = json.loads(case.fitted.representation_json)
    changed["blocks"][1]["radius"] += 1
    case.fitted.representation_json = json.dumps(changed)
    target = tmp_path / "mislabelled"
    with pytest.raises(BundleError, match="training representation"):
        export(case.fitted, target, model_name="wrong representation", endpoint_id=case.bundle.endpoint_id)
    assert not target.exists()
