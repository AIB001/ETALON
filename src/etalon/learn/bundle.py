"""Ship a trained surrogate as a MolCascade model bundle, using its extension point.

MolCascade already has the slot. ``prediction.custom_model`` is a ``PREDICTOR`` taking
``parent/v1`` and emitting ``prediction/v1``, configured with a ``bundle_dir`` and an
``expected_bundle_sha256``. Writing a second plugin beside it would have been the obvious move
and the wrong one: the existing one already enforces the provenance ETALON cares about, and a
parallel implementation would be a second place for the featurisation to drift.

Two constraints come from that plugin rather than from here, and both are improvements.

**ONNX only.** The manifest's ``format`` is ``Literal["onnx"]`` and the loader refuses joblib and
pickle outright, because loading those runs code from the file. So a surrogate reaches a cascade
as a graph rather than as an object, which costs an export step and removes a way for a model
file to be a program.

**The representation is declared in MolCascade's vocabulary.** A bundle says
``DescriptorBlock(names=...)`` and ``MorganBlock(radius=..., n_bits=..., counts=...)``, and
MolCascade computes them. :func:`etalon.learn.surrogate.representation` returns exactly that
object, and it is the same object the training matrix was built from, so the manifest cannot
describe a featurisation the model was not trained on.

One limitation, stated rather than worked around. A random forest exported to ONNX carries the
ensemble mean and not the per-tree spread, and the conformal interval is a function of that
spread. So a bundle gives MolCascade the point prediction -- which is what
``prediction/v1.prediction_mean`` is for -- and the calibrated interval stays in ETALON, where
the calibration object lives. Filling ``interval_lower`` and ``interval_upper`` inside a cascade
would need a graph with two outputs, which skl2onnx will not produce from a forest without a
hand-written converter. The manifest therefore declares no ``uncertainty`` spec rather than
declaring one it cannot honour.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from numbers import Integral
from pathlib import Path
from typing import Any

from etalon.learn.conformal import Calibration
from etalon.learn.surrogate import Surrogate, representation

MANIFEST_FILENAME = "molcascade_model.yaml"
MODEL_FILENAME = "model.onnx"
#: Written beside the manifest. Not read by MolCascade -- it has no field for a conformal
#: quantile -- and kept so that a bundle found on disk a year later still says what its
#: predictions were calibrated against, and how badly its worst series was covered.
CALIBRATION_FILENAME = "etalon_calibration.json"


class BundleError(RuntimeError):
    """Raised when a bundle cannot be written honestly."""


@dataclass(frozen=True, slots=True)
class Bundle:
    """A written bundle, and the digest MolCascade should be configured to expect."""

    directory: Path
    model_name: str
    endpoint_id: str
    n_features: int
    model_sha256: str
    bundle_sha256: str
    calibration: Calibration | None
    previous_directory: Path | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "bundle_dir": str(self.directory),
            "expected_bundle_sha256": self.bundle_sha256,
            "model_name": self.model_name,
            "endpoint_id": self.endpoint_id,
            "n_features": self.n_features,
            "model_sha256": self.model_sha256,
            "calibration": None if self.calibration is None else self.calibration.as_dict(),
            "previous_bundle_dir": None if self.previous_directory is None else str(self.previous_directory),
            "cascade_config": {
                "backend": "prediction.custom_model@0.1.0",
                "settings": {
                    "bundle_dir": str(self.directory),
                    "expected_bundle_sha256": self.bundle_sha256,
                },
            },
            "interval_note": (
                "prediction_mean only. A forest exported to ONNX carries the ensemble mean and "
                "not the per-tree spread, and the conformal interval is a function of that "
                "spread, so interval_lower and interval_upper are not produced inside the "
                "cascade. Use etalon.learn.conformal.intervals with the live model for those."
            ),
        }


def _digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _bundle_digest(directory: Path) -> str:
    """Use the consumer's validation and digest contract, without loading an ONNX runtime."""

    from molcascade.plugins.builtin.custom_model import inspect_model_bundle

    try:
        return str(inspect_model_bundle(directory)["bundle_sha256"])
    except Exception as error:
        raise BundleError(f"MolCascade refused the model bundle: {error}") from error


def _check_representation(surrogate: Surrogate, spec: Any) -> None:
    width = getattr(surrogate.model, "n_features_in_", None)
    if isinstance(width, bool) or not isinstance(width, Integral) or width != spec.width:
        raise BundleError("fitted estimator feature width does not match the exported representation")
    if surrogate.names != tuple(f"f{index:05d}" for index in range(spec.width)):
        raise BundleError("fitted feature names or column order differ from the exported representation")
    recorded = getattr(surrogate, "representation_json", None)
    try:
        matches = isinstance(recorded, str) and json.loads(recorded) == spec.model_dump(mode="json")
    except (TypeError, ValueError):
        matches = False
    if not matches:
        raise BundleError(
            "training representation is absent or differs from the export specification; "
            "refit from provenance-bearing featurize() output before exporting"
        )


def _existing_digest(target: Path, *, overwrite: bool) -> str | None:
    if target.is_symlink():
        raise BundleError("bundle output must not be a symlink")
    if not target.exists():
        return None
    if not overwrite:
        raise BundleError(f"{target} already exists; pass overwrite=True to replace a recognized bundle")
    if not target.is_dir():
        raise BundleError("bundle output exists but is not a directory")
    owned = {MANIFEST_FILENAME, MODEL_FILENAME, CALIBRATION_FILENAME}
    if any(path.name not in owned or path.is_symlink() or not path.is_file() for path in target.iterdir()):
        raise BundleError("refusing to overwrite a directory containing unrelated or unsafe files")
    return _bundle_digest(target)


def export(
    surrogate: Surrogate,
    directory: str | Path,
    *,
    model_name: str,
    endpoint_id: str,
    calibration: Calibration | None = None,
    notes: str = "",
    overwrite: bool = False,
) -> Bundle:
    """Write a ``prediction.custom_model`` bundle for a fitted surrogate.

    Raises:
        BundleError: If the surrogate is unfitted, if the directory exists and ``overwrite`` is
            false, or if ``skl2onnx`` is not installed. The last is a refusal rather than a
            fallback: the alternative is writing a joblib file that MolCascade's loader will
            refuse anyway, an hour later and with a less useful message.
    """

    if surrogate.model is None:
        raise BundleError("the surrogate has not been fitted, so there is nothing to export")
    spec = representation()
    _check_representation(surrogate, spec)
    raw_target = Path(directory).expanduser()
    if raw_target.is_symlink():
        raise BundleError("bundle output must not be a symlink")
    target = raw_target.resolve()
    previous_digest = _existing_digest(target, overwrite=overwrite)
    try:
        import skl2onnx
        from skl2onnx.common.data_types import FloatTensorType
    except ImportError as error:
        raise BundleError(
            "skl2onnx is not installed, and MolCascade's model bundle accepts ONNX only -- its "
            "loader refuses joblib and pickle because loading those runs code from the file. "
            "Install skl2onnx in the environment that trains the surrogate; onnxruntime, which "
            "is what MolCascade needs to read the result, is a separate package and may already "
            f"be present. ({error})"
        ) from error

    onnx_model = skl2onnx.to_onnx(
        surrogate.model,
        initial_types=[("input", FloatTensorType([None, spec.width]))],
        target_opset=None,
    )
    model_bytes = onnx_model.SerializeToString()
    if not isinstance(model_bytes, bytes) or not model_bytes:
        raise BundleError("ONNX conversion produced no model bytes")

    manifest = {
        "schema_version": 1,
        "model_name": model_name,
        "endpoint_id": endpoint_id,
        "task": "regression",
        "representation": {
            "blocks": [block.model_dump(mode="json") for block in spec.blocks]
        },
        "model": {
            "file": MODEL_FILENAME,
            "sha256": _digest_bytes(model_bytes),
            "format": "onnx",
            "n_features": spec.width,
            "dtype": "float32",
        },
        "notes": notes
        or (
            "Random forest over the same RepresentationSpec that built its training matrix. "
            "Target semantics are those supplied by the caller's endpoint. Predictions are means only; the "
            "conformal interval needs the per-tree spread, which this graph does not carry."
        ),
    }
    # Validate before creating anything. The downstream inspector also checks the
    # staged bytes; neither operation imports an inference runtime or executes a graph.
    from molcascade.plugins.builtin.custom_model import ModelBundleManifest

    try:
        ModelBundleManifest.model_validate(manifest)
    except ValueError as error:
        raise BundleError(f"invalid model bundle manifest: {error}") from error
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".etalon-export-{_digest_bytes(str(target).encode())}.lock"
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(str(target))
    except FileExistsError as error:
        raise BundleError("another export owns this bundle target") from error
    previous_directory: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix=".etalon-bundle-", dir=target.parent) as temporary:
            staging = Path(temporary)
            (staging / MODEL_FILENAME).write_bytes(model_bytes)
            _write_yaml(staging / MANIFEST_FILENAME, manifest)
            if calibration is not None:
                calibration_body = {
                    **calibration.as_dict(), "model_sha256": _digest_bytes(model_bytes),
                    "endpoint_id": endpoint_id,
                    "binding_scope": "caller-supplied calibration; association with this estimator is not independently verified",
                }
                (staging / CALIBRATION_FILENAME).write_text(
                    json.dumps(calibration_body, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8",
                )
            bundle_digest = _bundle_digest(staging)
            if _existing_digest(target, overwrite=overwrite) != previous_digest:
                raise BundleError("bundle target changed during export; existing files were preserved")
            if previous_digest is not None:
                # Keep the old directory recoverable, including its calibration. Do not
                # delete or merge it. A failed install restores it when the target is free.
                previous_directory = Path(tempfile.mkdtemp(prefix=f".{target.name}.previous-", dir=target.parent))
                previous_directory.rmdir()
                target.rename(previous_directory)
            try:
                staging.rename(target)
            except OSError as error:
                if previous_directory is not None and not target.exists():
                    previous_directory.rename(target)
                    previous_directory = None
                raise BundleError("could not install staged bundle; old bundle was preserved") from error
    finally:
        lock.unlink(missing_ok=True)

    return Bundle(
        directory=target,
        model_name=model_name,
        endpoint_id=endpoint_id,
        n_features=spec.width,
        model_sha256=manifest["model"]["sha256"],  # type: ignore[index]
        bundle_sha256=bundle_digest,
        calibration=calibration,
        previous_directory=previous_directory,
    )


def _write_yaml(path: Path, document: dict[str, Any]) -> None:
    """Write the manifest, preferring PyYAML and falling back to JSON.

    JSON is a subset of YAML, so a loader that parses YAML parses this. The fallback exists
    because a missing optional dependency should not be the thing that stops a model being
    shipped, and the file it writes is valid either way.
    """

    try:
        import yaml

        path.write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    except ImportError:
        path.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8")


__all__ = [
    "CALIBRATION_FILENAME",
    "MANIFEST_FILENAME",
    "MODEL_FILENAME",
    "Bundle",
    "BundleError",
    "export",
]
