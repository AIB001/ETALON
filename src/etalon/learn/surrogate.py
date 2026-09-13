"""A cheap model of an expensive number, and the features it is allowed to use.

The campaign's expensive stage measures tens of molecules. A surrogate trained on tens of
molecules is an overfitting machine, so almost every decision here is about making that fact
visible rather than about making the model stronger.

Three choices, each with its reason.

**pIC50, not nM.** Binding affinity is log-linear in free energy, so a model fitted on
nanomolar concentrations spends its capacity on the difference between 10 µM and 60 µM and
almost none on the difference between 1 nM and 10 nM. On the real panel here the range is 0.26
nM to 64 µM -- five and a half orders of magnitude -- and on that scale the arithmetic mean is
a number about the weakest compounds.

**A random forest, for its spread as much as its predictions.** An ensemble's disagreement is
a usable heuristic for where the model is out of its depth, and the conformal layer needs
exactly such a heuristic to make interval widths adaptive rather than constant. Gradient
boosting predicts a little better here and gives nothing to calibrate against.

**Descriptors and a counts fingerprint, both, and no 3D.** The descriptors carry size,
lipophilicity and polarity, which is most of what a docking score responds to; the fingerprint
carries substructure, which is what distinguishes members of one series. 3D descriptors are
left out because they depend on a conformer, and a model whose features depend on an embedding
inherits that embedding's arbitrariness -- which this project has a fault code for.

**And the features are computed by MolCascade, not here.** The first version of this module had
its own RDKit calls, which worked and was wrong for a reason that only showed up two rounds
later. MolCascade's ``prediction.custom_model`` plugin accepts a model bundle whose manifest
declares its representation as a tuple of feature blocks -- ``DescriptorBlock`` with a list of
names, ``MorganBlock`` with a radius and a bit count -- and computes them with
``molcascade.chemistry.featurizers.Featurizer``. A surrogate trained on features this module
computed, shipped with a manifest declaring blocks MolCascade computes, would be a model whose
training and inference featurisations are two implementations that agree today. They would
drift on an RDKit upgrade, silently, and the predictions would stay plausible. So the spec is
declared in MolCascade's vocabulary and evaluated by MolCascade's code, and the bundle's
manifest holds the same spec object that trained the model.

Nothing here reports a performance number. Scoring belongs to
:mod:`etalon.learn.calibrate`, which holds the scaffold-grouped split and the standard error,
and separating them is deliberate: a model that could score itself would be asked to.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

#: RDKit descriptors, named rather than taken wholesale. The full list is ~210 columns and at
#: n in the low hundreds that is more features than molecules -- a model fitted on it is fitting
#: the panel. These nine are the ones a medicinal chemist would name for potency in a series.
DESCRIPTORS = (
    "MolWt",
    "MolLogP",
    "TPSA",
    "NumHDonors",
    "NumHAcceptors",
    "NumRotatableBonds",
    "RingCount",
    "FractionCSP3",
    "HeavyAtomCount",
)

#: Morgan radius and length. 2 is the usual ECFP4 equivalent; 1024 bits folded, as counts
#: rather than presence, because a series often differs by how many of a group it carries.
MORGAN_RADIUS = 2
MORGAN_BITS = 1024


def pic50(nanomolar: float) -> float:
    """Convert a nanomolar affinity to pIC50, which is the scale free energy is linear in."""

    if not nanomolar > 0:
        raise ValueError(f"an affinity must be positive; got {nanomolar}")
    return 9.0 - math.log10(nanomolar)


@dataclass(frozen=True, slots=True)
class Features:
    """A feature matrix and the names of its columns, kept together.

    Together because they come apart easily and silently: a model trained on one column order
    and applied to another produces predictions that look entirely reasonable.
    """

    matrix: Any
    names: tuple[str, ...]
    #: Input indices that produced no row. The matrix is shorter than the input by exactly
    #: this many, which is MolCascade's behaviour and its reasoning rather than mine: a zero
    #: vector is a perfectly plausible input that a model will happily score, so imputing one
    #: turns "this could not be featurised" into a confident number. Dropping instead leaves
    #: the molecule without evidence, which every threshold gate already refuses.
    unparsed: tuple[int, ...] = ()

    def __len__(self) -> int:
        return int(self.matrix.shape[0])

    def align(self, values: Sequence[Any]) -> list[Any]:
        """Subset a per-input sequence to the rows that survived featurisation.

        Needed because the matrix is shorter than the input. Pairing a full target list against
        a short matrix is the silent failure this exists to prevent: the lengths differ by two
        and every molecule after the first failure is trained against the wrong answer.
        """

        if len(values) == len(self):
            return list(values)
        expected = len(self) + len(self.unparsed)
        if len(values) != expected:
            raise ValueError(
                f"{len(values)} values for {len(self)} featurised rows out of {expected} "
                "inputs; this sequence describes neither, so aligning it would pair molecules "
                "with other molecules' answers"
            )
        dropped = set(self.unparsed)
        return [value for index, value in enumerate(values) if index not in dropped]


def representation() -> Any:
    """The feature specification, in MolCascade's own vocabulary.

    Returned as a ``RepresentationSpec`` rather than described in prose because this object goes
    two places: into the featuriser that builds the training matrix, and verbatim into the model
    bundle's manifest. One object, so the two cannot disagree.
    """

    from molcascade.chemistry.featurizers import (
        DescriptorBlock,
        MorganBlock,
        RepresentationSpec,
    )

    return RepresentationSpec(
        blocks=(
            DescriptorBlock(names=DESCRIPTORS),
            MorganBlock(radius=MORGAN_RADIUS, n_bits=MORGAN_BITS, counts=True),
        )
    )


def featurize(smiles: Sequence[str], spec: Any = None) -> Features:
    """Build the feature matrix with MolCascade's featuriser.

    A molecule RDKit cannot parse keeps a row of zeros and its index is recorded in
    :attr:`Features.unparsed`, rather than being dropped: a caller that asked for 231
    predictions and received 229 has to be told which two are missing, and a silently shorter
    matrix pairs every later molecule with the wrong target.
    """

    import numpy as np
    from molcascade.chemistry.featurizers import Featurizer

    chosen = spec if spec is not None else representation()
    matrix, failed = Featurizer(chosen).transform(list(smiles), dtype="float64")
    names = tuple(f"f{index:05d}" for index in range(chosen.width))
    return Features(
        matrix=np.asarray(matrix, dtype=float), names=names, unparsed=tuple(failed)
    )


@dataclass
class Surrogate:
    """A random forest over :func:`featurize`'s columns, with its ensemble spread exposed.

    Deliberately not a neural network. At two hundred molecules a network's capacity is spent
    before its inductive bias helps, it needs a validation split this panel cannot spare, and
    its uncertainty has to be bolted on afterwards anyway. The forest's disagreement is free,
    is monotone in the thing it should be monotone in, and is what the conformal layer
    calibrates. If the panel reaches thousands, this is the class to replace -- the interface
    is two methods and everything above it is indifferent.
    """

    trees: int = 400
    min_samples_leaf: int = 2
    seed: int = 0
    #: Set by :meth:`fit`.
    model: Any = field(default=None, repr=False)
    names: tuple[str, ...] = ()

    def fit(self, features: Features, target: Sequence[float]) -> Surrogate:
        from sklearn.ensemble import RandomForestRegressor

        if len(target) != len(features):
            raise ValueError(
                f"{len(features)} molecules and {len(target)} targets; a silent truncation "
                "here would train on mismatched pairs and score well on nothing"
            )
        self.model = RandomForestRegressor(
            n_estimators=self.trees,
            min_samples_leaf=self.min_samples_leaf,
            random_state=self.seed,
            n_jobs=-1,
        ).fit(features.matrix, list(target))
        self.names = features.names
        return self

    def predict(self, features: Features) -> tuple[Any, Any]:
        """Mean and ensemble spread, per molecule.

        The spread is the standard deviation across trees. It is not an error bar and is not
        presented as one anywhere: it is the heuristic the conformal layer turns into an
        interval with a stated coverage, which is a different object with a different warranty.
        """

        import numpy as np

        if self.model is None:
            raise RuntimeError("the surrogate has not been fitted")
        if features.names != self.names:
            raise ValueError(
                "the feature columns differ from the ones this model was fitted on. Refused "
                "rather than reordered: a model applied to permuted columns predicts "
                "confidently and wrongly, and nothing about the output looks unusual."
            )
        per_tree = np.stack(
            [tree.predict(features.matrix) for tree in self.model.estimators_], axis=0
        )
        return per_tree.mean(axis=0), per_tree.std(axis=0)


__all__ = [
    "DESCRIPTORS",
    "MORGAN_BITS",
    "MORGAN_RADIUS",
    "Features",
    "Surrogate",
    "featurize",
    "pic50",
    "representation",
]
