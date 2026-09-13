"""A learned rescoring function over poses a campaign has already paid for.

findings/0006 prices rescoring as the largest single lever available: eight engineer-hours worth the
same as ten times the compute budget. The value is not that a network beats a docking function in the
abstract -- it is that the poses already exist, so the marginal cost is inference.

findings/0008 is where the naive design was wrong. A rescorer looks like a GPU inference job and is
not: the contact-histogram model is 39,041 parameters running at 6.8 million poses per second, while
the featuriser manages 927 per second on one core. The model was never the bottleneck. Batching the
*featuriser* onto the GPU is worth 10.3x and brings 750,000 poses to about 80 seconds; the same move
on the model is worth 2.2x and saves nothing that matters. A 3D grid CNN would invert that -- measured,
a model of that size is 25x faster on the GPU -- which is an argument for a different architecture
rather than for accelerating this one.

What is not claimed is an accuracy. :meth:`Rescorer.fit` refuses below one complex per feature, so the
28 real poses available here cannot train it, and :meth:`Rescorer.as_stage` returns a stage with no
rank correlation -- which the funnel planner then refuses, correctly. This is the machinery, measured
against real poses and verified bit-identical between its two paths.
"""

from etalon.rescore.features import (
    ELEMENTS,
    SHELLS,
    Pose,
    Receptor,
    feature_names,
    featurize_pose,
    featurize_poses,
    featurize_poses_on_gpu,
    load_receptor,
    radius_for,
    read_pose,
)
from etalon.rescore.model import MIN_SAMPLES_PER_FEATURE, Rescorer, Throughput, resolve_device

__all__ = [
    "ELEMENTS",
    "MIN_SAMPLES_PER_FEATURE",
    "SHELLS",
    "Pose",
    "Receptor",
    "Rescorer",
    "Throughput",
    "feature_names",
    "featurize_pose",
    "featurize_poses",
    "featurize_poses_on_gpu",
    "load_receptor",
    "radius_for",
    "read_pose",
    "resolve_device",
]
