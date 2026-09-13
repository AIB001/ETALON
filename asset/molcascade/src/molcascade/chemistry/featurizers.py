"""Deterministic molecular representations that MolCascade computes itself.

A user-trained model arrives here as two separable things: a *representation*
(how a molecule becomes a vector) and an *estimator* (how that vector becomes a
number).  MolCascade owns the first and only ever loads the second.

That split is forced by how model export actually works.  A scikit-learn
pipeline whose first step is an RDKit featuriser does not survive conversion to
ONNX: ``skl2onnx`` can only convert transformers it has a shape calculator for,
so a custom descriptor block is either dropped or refuses to convert at all.
The workable pattern is to export the estimator alone and recompute the
features on the inference side -- which means the feature code becomes part of
the model's identity and has to be pinned, ordered, and reproducible.  Hence
this module: every block is declared explicitly, the column order is the
declared order, and the whole specification hashes into the ``model_id`` that
travels with each prediction.

Nothing here is specific to ONNX or to any one backend; the same
representations are what any future locally-run predictor should be fed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Annotated, Any, Literal

import numpy as np
import rdkit
from pydantic import Field, TypeAdapter, field_validator
from rdkit import Chem, rdBase
from rdkit.Chem import Descriptors, rdFingerprintGenerator, rdMolDescriptors

from molcascade.config.canonical import canonical_sha256
from molcascade.config.models import StrictFrozenModel
from molcascade.errors import PluginError

# Bumped whenever the numbers this module produces could change for an
# unchanged specification.  It is part of every feature identity, so an old
# prediction can never be silently attributed to new feature code.
FEATURIZER_IMPLEMENTATION_VERSION = 1

_MACCS_WIDTH = 167
_DESCRIPTORS: dict[str, Any] = dict(Descriptors._descList)
#: Every RDKit descriptor name available in this installation, sorted for
#: display.  The set is version-dependent, which is exactly why the RDKit
#: version is folded into the feature identity below.
DESCRIPTOR_NAMES: tuple[str, ...] = tuple(sorted(_DESCRIPTORS))


def _array_to_tuple(value: Any) -> Any:
    """YAML and JSON have arrays; the models here are immutable.

    Strict validation deliberately refuses to coerce scalars, so this one
    structural conversion is made explicit rather than relaxing the whole
    model, matching how ``PipelineConfig`` accepts its stage array.
    """

    return tuple(value) if isinstance(value, list) else value


class _Block(StrictFrozenModel):
    """One contiguous run of columns in the feature vector."""

    @property
    def width(self) -> int:  # pragma: no cover - overridden by every subclass
        raise NotImplementedError

    def build(self) -> Any:  # pragma: no cover - overridden by every subclass
        raise NotImplementedError


class _FingerprintBlock(_Block):
    n_bits: int = Field(default=2048, ge=32, le=65_536)
    counts: bool = False

    @property
    def width(self) -> int:
        return self.n_bits


class MorganBlock(_FingerprintBlock):
    """ECFP/FCFP circular fingerprint."""

    kind: Literal["morgan"] = "morgan"
    radius: int = Field(default=2, ge=1, le=8)
    use_chirality: bool = False
    # ``use_features`` switches ECFP invariants for FCFP pharmacophoric ones.
    use_features: bool = False

    def build(self) -> Any:
        invariants = (
            rdFingerprintGenerator.GetMorganFeatureAtomInvGen()
            if self.use_features
            else None
        )
        return rdFingerprintGenerator.GetMorganGenerator(
            radius=self.radius,
            fpSize=self.n_bits,
            includeChirality=self.use_chirality,
            atomInvariantsGenerator=invariants,
        )


class AtomPairBlock(_FingerprintBlock):
    kind: Literal["atom_pair"] = "atom_pair"
    min_distance: int = Field(default=1, ge=1, le=30)
    max_distance: int = Field(default=30, ge=1, le=30)
    use_chirality: bool = False

    @field_validator("max_distance")
    @classmethod
    def _ordered(cls, value: int, info: Any) -> int:
        minimum = info.data.get("min_distance")
        if minimum is not None and value < minimum:
            raise ValueError("max_distance must be no less than min_distance")
        return value

    def build(self) -> Any:
        return rdFingerprintGenerator.GetAtomPairGenerator(
            minDistance=self.min_distance,
            maxDistance=self.max_distance,
            includeChirality=self.use_chirality,
            fpSize=self.n_bits,
        )


class TopologicalTorsionBlock(_FingerprintBlock):
    kind: Literal["topological_torsion"] = "topological_torsion"
    use_chirality: bool = False

    def build(self) -> Any:
        return rdFingerprintGenerator.GetTopologicalTorsionGenerator(
            includeChirality=self.use_chirality,
            fpSize=self.n_bits,
        )


class RDKitPathBlock(_FingerprintBlock):
    kind: Literal["rdkit_path"] = "rdkit_path"
    min_path: int = Field(default=1, ge=1, le=30)
    max_path: int = Field(default=7, ge=1, le=30)

    @field_validator("max_path")
    @classmethod
    def _ordered(cls, value: int, info: Any) -> int:
        minimum = info.data.get("min_path")
        if minimum is not None and value < minimum:
            raise ValueError("max_path must be no less than min_path")
        return value

    def build(self) -> Any:
        return rdFingerprintGenerator.GetRDKitFPGenerator(
            minPath=self.min_path,
            maxPath=self.max_path,
            fpSize=self.n_bits,
        )


class MACCSBlock(_Block):
    """The 167-bit MACCS key set; it has no tunable width."""

    kind: Literal["maccs"] = "maccs"

    @property
    def width(self) -> int:
        return _MACCS_WIDTH

    def build(self) -> Any:
        return None


class DescriptorBlock(_Block):
    """An explicitly ordered list of RDKit descriptors.

    The order is the training order.  It is written out in full rather than
    referring to "all RDKit descriptors", because that set grows between RDKit
    releases and a silently widened vector would be fed to a model expecting
    the old width -- or worse, the old width with shifted meanings.
    """

    kind: Literal["descriptors"] = "descriptors"
    names: tuple[str, ...] = Field(min_length=1, max_length=4096)

    _as_tuple = field_validator("names", mode="before")(_array_to_tuple)

    @field_validator("names")
    @classmethod
    def _known_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        for name in value:
            if name in seen:
                raise ValueError(f"descriptor {name!r} is listed more than once")
            seen.add(name)
            if name not in _DESCRIPTORS:
                raise ValueError(
                    f"descriptor {name!r} is not provided by RDKit "
                    f"{rdkit.__version__} in this installation"
                )
        return value

    @property
    def width(self) -> int:
        return len(self.names)

    def build(self) -> Any:
        return tuple(_DESCRIPTORS[name] for name in self.names)


FeatureBlock = Annotated[
    MorganBlock
    | AtomPairBlock
    | TopologicalTorsionBlock
    | RDKitPathBlock
    | MACCSBlock
    | DescriptorBlock,
    Field(discriminator="kind"),
]

_BLOCK_ADAPTER: TypeAdapter[Any] = TypeAdapter(FeatureBlock)


class RepresentationSpec(StrictFrozenModel):
    """The complete recipe for turning a SMILES string into a feature vector."""

    blocks: tuple[FeatureBlock, ...] = Field(min_length=1, max_length=16)

    _as_tuple = field_validator("blocks", mode="before")(_array_to_tuple)

    @property
    def width(self) -> int:
        return sum(block.width for block in self.blocks)


class Featurizer:
    """A built, reusable representation. Not thread-safe; build one per worker."""

    def __init__(self, spec: RepresentationSpec) -> None:
        self.spec = spec
        self.width = spec.width
        self._blocks = tuple((block, block.build()) for block in spec.blocks)
        self.identity = canonical_sha256(
            {
                "implementation_version": FEATURIZER_IMPLEMENTATION_VERSION,
                # ``prediction/v1`` requires model_id to bind the software that
                # produced it, and descriptor definitions do change between
                # RDKit releases.  Folding the version in means an upgrade
                # produces a visibly different model identity instead of
                # quietly different numbers under the old one.
                "rdkit_version": rdkit.__version__,
                "blocks": [block.model_dump(mode="json") for block in spec.blocks],
                "width": self.width,
            }
        )

    def _row(self, mol: Any) -> list[float] | None:
        values: list[float] = []
        for block, built in self._blocks:
            if isinstance(block, DescriptorBlock):
                for function in built:
                    try:
                        value = float(function(mol))
                    except (ValueError, TypeError, OverflowError, ZeroDivisionError):
                        return None
                    if not math.isfinite(value):
                        return None
                    values.append(value)
            elif isinstance(block, MACCSBlock):
                keys = rdMolDescriptors.GetMACCSKeysFingerprint(mol)
                values.extend(float(bit) for bit in keys)
            elif block.counts:
                values.extend(built.GetCountFingerprintAsNumPy(mol).astype(np.float64))
            else:
                values.extend(built.GetFingerprintAsNumPy(mol).astype(np.float64))
        return values

    def transform(
        self,
        smiles: Sequence[str],
        *,
        dtype: str = "float32",
    ) -> tuple[np.ndarray, list[int]]:
        """Featurise ``smiles``, returning the matrix and the rows that failed.

        A molecule RDKit cannot parse, or one whose descriptors are not finite,
        produces no row at all rather than a row of zeros.  A zero vector is a
        perfectly plausible input that a model will happily score, so imputing
        one would turn "we could not featurise this" into a confident number.
        Dropping it instead leaves the molecule without evidence, and every
        MolCascade threshold gate rejects missing evidence.
        """

        kept: list[list[float]] = []
        failed: list[int] = []
        with rdBase.BlockLogs():
            for index, entry in enumerate(smiles):
                mol = Chem.MolFromSmiles(entry) if entry else None
                row = self._row(mol) if mol is not None else None
                if row is None:
                    failed.append(index)
                    continue
                kept.append(row)
        matrix = np.asarray(kept, dtype=dtype).reshape(len(kept), self.width)
        return matrix, failed


def parse_representation(value: Any) -> RepresentationSpec:
    """Validate a representation mapping, raising a plugin-shaped error."""

    try:
        return RepresentationSpec.model_validate(value)
    except Exception as error:
        raise PluginError(
            f"invalid molecular representation: {error}",
            code="REPRESENTATION_INVALID",
            hint=(
                "Each block needs a 'kind' of morgan, atom_pair, "
                "topological_torsion, rdkit_path, maccs or descriptors."
            ),
        ) from error


__all__ = [
    "DESCRIPTOR_NAMES",
    "FEATURIZER_IMPLEMENTATION_VERSION",
    "_BLOCK_ADAPTER",
    "AtomPairBlock",
    "DescriptorBlock",
    "FeatureBlock",
    "Featurizer",
    "MACCSBlock",
    "MorganBlock",
    "RDKitPathBlock",
    "RepresentationSpec",
    "TopologicalTorsionBlock",
    "parse_representation",
]
