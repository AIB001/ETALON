"""The seam between ETALON and the two packages it drives.

Nothing in here edits an asset. The boundary's job is to control the environment the
assets run in, prove which copy of each it actually reached, read what they emit, and
record enough about all of it that a number can be traced to the bytes that produced it.

Three pieces, in the order a round uses them.

:mod:`etalon.boundary.infra` answers "which MolCascade, which PRISM". It does not merely
import: it imports and then checks that the module resolved inside the pinned tree, and
refuses otherwise. That check earns its place -- the first time it ran, ``import
molcascade`` with the asset directory on ``sys.path`` resolved to an editable install
pointing at a live working tree, and every number produced through it would have cited a
pinned commit while having been computed by something else.

:mod:`etalon.boundary.toolchain` controls how the assets run. It puts a ``gmx`` shim on
the path that seeds the one command PRISM calls without a seed, so a build becomes
reproducible without a line of PRISM being changed.

:mod:`etalon.boundary.screen` drives MolCascade and reads its output back verified. It is
deliberately thin: MolCascade already compiles to a revision id before it runs, commits
content-addressed artifacts, validates its contracts and resumes by verification. An
orchestration layer that reimplemented any of that would be building a worse copy beside
a working one.
"""

from etalon.boundary.infra import Infra, InfraError, describe, load
from etalon.boundary.screen import Screen, ScreenPlan, ScreenResult, StageOutcome

__all__ = [
    "Infra",
    "InfraError",
    "Screen",
    "ScreenPlan",
    "ScreenResult",
    "StageOutcome",
    "describe",
    "load",
]
