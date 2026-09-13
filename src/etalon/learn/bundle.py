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
from dataclasses import dataclass
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

    def as_dict(self) -> dict[str, object]:
        return {
            "bundle_dir": str(self.directory),
            "expected_bundle_sha256": self.bundle_sha256,
            "model_name": self.model_name,
            "endpoint_id": self.endpoint_id,
            "n_features": self.n_features,
            "model_sha256": self.model_sha256,
            "calibration": None if self.calibration is None else self.calibration.as_dict(),
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
    """One digest over path-and-content pairs, so a rename changes it.

    The same construction as the asset manifest's tree digest, and for the same reason: a file
    moved within a bundle is a different bundle, and a digest over contents alone would not say
    so.
    """

    combined = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        combined.update(str(path.relative_to(directory).as_posix()).encode())
        combined.update(b"\0")
        combined.update(_digest_bytes(path.read_bytes()).encode())
        combined.update(b"\n")
    return combined.hexdigest()


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

    spec = representation()
    target = Path(directory).expanduser().resolve()
    if target.exists() and not overwrite:
        raise BundleError(
            f"{target} already exists. A bundle is identified by a digest over its whole "
            "directory, so writing into a populated one produces a digest describing a mixture "
            "of two models; pass overwrite=True to mean it."
        )
    target.mkdir(parents=True, exist_ok=True)

    onnx_model = skl2onnx.to_onnx(
        surrogate.model,
        initial_types=[("input", FloatTensorType([None, spec.width]))],
        target_opset=None,
    )
    model_path = target / MODEL_FILENAME
    model_bytes = onnx_model.SerializeToString()
    model_path.write_bytes(model_bytes)

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
            "pIC50 target, higher meaning more potent. Predictions are means only; the "
            "conformal interval needs the per-tree spread, which this graph does not carry."
        ),
    }
    _write_yaml(target / MANIFEST_FILENAME, manifest)

    if calibration is not None:
        (target / CALIBRATION_FILENAME).write_text(
            json.dumps(calibration.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    return Bundle(
        directory=target,
        model_name=model_name,
        endpoint_id=endpoint_id,
        n_features=spec.width,
        model_sha256=manifest["model"]["sha256"],  # type: ignore[index]
        bundle_sha256=_bundle_digest(target),
        calibration=calibration,
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
