"""A learned rescoring function, on the GPU, and the refusal that keeps it honest.

``findings/0006`` prices rescoring as the largest single lever a campaign has: eight engineer-hours
worth the same as ten times the compute budget. The value is not that a neural network is better than a
docking function in the abstract -- it is that a campaign already *has* the poses, so a rescorer reads
geometry it has already paid for, and the marginal cost is inference.

That makes throughput the quantity that matters, and the reason this runs on a GPU. 750,000 poses is
the scale the operator described; at a millisecond each that is thirteen minutes of featurisation and
the model must not be the slow part. Measured throughput is in ``findings/0008`` and is what turns
``rescore_ml``'s cost in the stage catalogue from an estimate into a number.

One refusal, and it is the thing that stops this module being a way to overfit with extra steps.

**It will not fit a model with fewer complexes than features.** The featuriser produces 600 columns. A
model fitted on 28 complexes can reproduce any labels exactly and has learned the panel; on a
scaffold-grouped split it will predict the mean. So ``fit`` refuses below one sample per feature and
names the two real remedies: drop the columns that are identically zero, which on measured poses takes
600 down to under a hundred, or get more complexes. It does not offer regularisation as a remedy,
because a ridge penalty on 28 points is a way of making the overfitting less visible rather than less
present.

What this module deliberately does not do is claim an accuracy. The published figures behind the knob
-- SCORCH2 surpassing Glide in more than half of cases, GNINA's CNN outperforming Vina on ten targets
-- were obtained on training sets of thousands of complexes with measured affinities. This code is the
machinery for that, tested against real poses, and the accuracy belongs to whoever trains it on enough
data. Saying so is the difference between shipping infrastructure and shipping a claim.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from etalon.rescore.features import feature_names

#: Minimum complexes per feature before :meth:`Rescorer.fit` will run. One is already generous: a
#: model with as many parameters as observations interpolates, and the usual rule of thumb for a
#: non-linear model is an order of magnitude more.
MIN_SAMPLES_PER_FEATURE = 1.0


def resolve_device(requested: str = "auto") -> str:
    """Which device to use, and say so rather than silently falling back.

    A rescorer that quietly runs on the CPU is a rescorer whose throughput figure is wrong by two
    orders of magnitude, and a campaign sizing a tier from it will under-budget by that much.
    """

    import torch

    if requested != "auto":
        return requested
    return "cuda" if torch.cuda.is_available() else "cpu"


@dataclass(frozen=True, slots=True)
class Throughput:
    """Measured inference rate, and what it means for a tier's price."""

    device: str
    poses: int
    seconds: float
    batch_size: int

    @property
    def per_second(self) -> float:
        return self.poses / self.seconds if self.seconds else float("inf")

    @property
    def gpu_hours_per_molecule(self) -> float:
        """The figure a stage catalogue wants, in the units it uses."""

        return self.seconds / self.poses / 3600.0 if self.poses else 0.0

    def hours_for(self, molecules: int) -> float:
        return self.gpu_hours_per_molecule * molecules

    def as_dict(self) -> dict[str, object]:
        return {
            "device": self.device,
            "poses": self.poses,
            "seconds": round(self.seconds, 4),
            "poses_per_second": round(self.per_second, 1),
            "batch_size": self.batch_size,
            "gpu_hours_per_molecule": self.gpu_hours_per_molecule,
            "hours_for_750k": round(self.hours_for(750_000), 3),
        }


@dataclass
class Rescorer:
    """A small network over contact features, fitted and run on whatever device is available.

    Small on purpose. The features are a contact histogram, which is a summary rather than a
    representation, and depth buys little over it: two hidden layers is where published grid-free
    rescorers sit. Capacity spent beyond that is capacity spent memorising a training set whose size is
    the binding constraint.
    """

    hidden: tuple[int, ...] = (256, 64)
    dropout: float = 0.1
    epochs: int = 200
    learning_rate: float = 1e-3
    seed: int = 0
    device: str = "auto"
    #: Columns kept after dropping those identically zero in training. Recorded because a model applied
    #: to a different column set predicts confidently and wrongly.
    kept_columns: tuple[int, ...] = ()
    _network: Any = field(default=None, repr=False)
    _mean: Any = field(default=None, repr=False)
    _scale: Any = field(default=None, repr=False)
    _resolved_device: str = field(default="", repr=False)

    # -- fitting ------------------------------------------------------------

    def fit(self, features: Any, target: Sequence[float], *, drop_empty_columns: bool = True) -> Rescorer:
        """Fit on contact features, refusing when there are fewer complexes than columns.

        Args:
            drop_empty_columns: Remove columns identically zero across the training set. On measured
                poses this takes 600 columns to under a hundred, which is the difference between a
                refusal and a model.
        """

        import numpy as np
        import torch
        from torch import nn

        matrix = np.asarray(features, dtype=np.float32)
        labels = np.asarray(list(target), dtype=np.float32)
        if matrix.shape[0] != labels.shape[0]:
            raise ValueError(
                f"{matrix.shape[0]} complexes and {labels.shape[0]} labels; a silent truncation here "
                "would train on mismatched pairs"
            )

        kept = (
            tuple(int(index) for index in np.nonzero(matrix.any(axis=0))[0])
            if drop_empty_columns
            else tuple(range(matrix.shape[1]))
        )
        if not kept:
            raise ValueError(
                "every feature column is identically zero. The usual cause is a receptor that parsed "
                "to no atoms or a pocket radius that excluded it; check load_receptor's output before "
                "looking at the model."
            )
        matrix = matrix[:, list(kept)]

        samples, columns = matrix.shape
        if samples < MIN_SAMPLES_PER_FEATURE * columns:
            raise ValueError(
                f"{samples} complexes against {columns} features is not enough to fit anything: a "
                f"model with this many parameters reproduces any labels exactly and predicts the mean "
                f"on a scaffold-grouped split. Either dock more complexes -- {int(columns)} is the "
                "floor and an order of magnitude more is the usual rule -- or reduce the feature set. "
                "Regularisation is not offered as a remedy here because a penalty on this many points "
                "makes the overfitting less visible rather than less present."
            )

        torch.manual_seed(self.seed)
        self._resolved_device = resolve_device(self.device)
        self._mean = matrix.mean(axis=0)
        # Standardised because the outer distance shell dominates the raw counts by two orders of
        # magnitude; without it the network spends its first layer learning the scale.
        self._scale = np.where(matrix.std(axis=0) > 1e-6, matrix.std(axis=0), 1.0)
        scaled = (matrix - self._mean) / self._scale

        layers: list[Any] = []
        width = columns
        for size in self.hidden:
            layers += [nn.Linear(width, size), nn.ReLU(), nn.Dropout(self.dropout)]
            width = size
        layers.append(nn.Linear(width, 1))
        network = nn.Sequential(*layers).to(self._resolved_device)

        inputs = torch.as_tensor(scaled, device=self._resolved_device)
        outputs = torch.as_tensor(labels, device=self._resolved_device).unsqueeze(1)
        optimiser = torch.optim.Adam(network.parameters(), lr=self.learning_rate)
        loss_function = nn.MSELoss()
        network.train()
        for _ in range(self.epochs):
            optimiser.zero_grad()
            loss_function(network(inputs), outputs).backward()
            optimiser.step()
        network.eval()

        self._network = network
        self.kept_columns = kept
        return self

    # -- inference ----------------------------------------------------------

    def predict(self, features: Any, *, batch_size: int = 8192) -> Any:
        """Score poses. Batched, because 750,000 feature rows do not fit on a card at once."""

        import numpy as np
        import torch

        if self._network is None:
            raise RuntimeError("the rescorer has not been fitted")
        matrix = np.asarray(features, dtype=np.float32)
        if matrix.shape[1] == len(feature_names()):
            matrix = matrix[:, list(self.kept_columns)]
        elif matrix.shape[1] != len(self.kept_columns):
            raise ValueError(
                f"expected {len(feature_names())} raw columns or {len(self.kept_columns)} kept ones; "
                f"got {matrix.shape[1]}. Refused rather than reshaped: a model applied to a different "
                "column set predicts confidently and wrongly, and nothing about the output looks odd."
            )
        scaled = (matrix - self._mean) / self._scale

        out: list[Any] = []
        with torch.no_grad():
            for start in range(0, len(scaled), batch_size):
                chunk = torch.as_tensor(scaled[start : start + batch_size], device=self._resolved_device)
                out.append(self._network(chunk).squeeze(1).cpu().numpy())
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)

    def measure_throughput(
        self, features: Any, *, repeats: int = 3, batch_size: int = 8192
    ) -> Throughput:
        """Time inference, after a warm-up, so the number is the steady state.

        The first call on a CUDA device includes kernel compilation and allocator setup, which on a
        small batch can be most of the elapsed time. A throughput figure that includes it
        under-reports by an order of magnitude and a tier sized from it over-budgets by the same.
        """

        import numpy as np
        import torch

        matrix = np.asarray(features, dtype=np.float32)
        self.predict(matrix[:1], batch_size=batch_size)
        if self._resolved_device.startswith("cuda"):
            torch.cuda.synchronize()

        start = time.perf_counter()
        for _ in range(repeats):
            self.predict(matrix, batch_size=batch_size)
        if self._resolved_device.startswith("cuda"):
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - start

        return Throughput(
            device=self._resolved_device,
            poses=len(matrix) * repeats,
            seconds=elapsed,
            batch_size=batch_size,
        )

    def as_stage(self, throughput: Throughput, *, spearman: float | None = None) -> Any:
        """This rescorer as a priced stage, for the funnel planner.

        ``spearman`` is ``None`` until somebody measures it on a panel with known affinities, and the
        planner refuses a ranking stage with no measured correlation -- which is the correct behaviour
        and the reason this method does not invent one. Pass the number once it exists.
        """

        from etalon.economics.stage import Answers, Evidence, Stage

        return Stage(
            id="rescore_learned",
            label=f"Learned rescoring of existing poses ({throughput.device})",
            answers=Answers.RANKING,
            gpu_hours=throughput.gpu_hours_per_molecule,
            spearman=spearman,
            run_to_run_kcal_mol=None,
            evidence=Evidence.MEASURED_HERE,
            source=(
                f"Inference throughput measured on this machine: {throughput.per_second:,.0f} poses "
                f"per second on {throughput.device} at batch size {throughput.batch_size}, which is "
                f"{throughput.hours_for(750_000):.3f} GPU-hours for 750,000 poses. The rank "
                + (
                    f"correlation of {spearman:.3f} was supplied by the caller."
                    if spearman is not None
                    else "correlation has not been measured, so the planner will refuse this stage "
                    "until it is -- which is correct: a ranking tier with no measured correlation "
                    "cannot be placed in a funnel."
                )
            ),
            systematic_caveat=(
                "It reads the poses docking produced, so it cannot rank a pose the sampler never "
                "found, and its errors are correlated with docking's. A funnel model treating the two "
                "tiers as independent is at its most optimistic here."
            ),
        )


__all__ = ["MIN_SAMPLES_PER_FEATURE", "Rescorer", "Throughput", "resolve_device"]
