"""The two callables a real campaign injects into the supervisor.

:class:`~etalon.campaign.supervisor.Supervisor` takes generation and screening as callables so that
the loop can be exercised without a GPU. These are the ones that use the real thing, and they are
small on purpose: each compiles, runs, and returns what happened, and neither decides anything.

The screen driver carries one fact worth stating where it is used. MolCascade compiles to a revision
id *before* running, and the supervisor already knows which revision its batches were carved under.
So this driver compiles and then refuses if the compiled revision is not the expected one -- catching
a cascade file edited mid-campaign at the moment it would first produce an incomparable batch, rather
than at harvest time when 56 batches carry two revisions and nothing says which.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etalon.boundary.screen import Screen, ScreenResult


class RevisionChanged(RuntimeError):
    """The compiled cascade is not the one this campaign's batches belong to."""


@dataclass
class MolCascadeScreening:
    """Screen one batch on one device, with the compiled revision checked before it runs.

    Args:
        screen: The adapter. Its workspace is where artifacts and run records land.
        config_path: The cascade. Compiled on every call, which is what makes the revision check
            meaningful -- a cached plan would compile the file as it was at launch.
        revision_id: What the campaign expects. Refuses otherwise.
        target: MolCascade's ``TargetConfig`` mapping -- receptor, box, reference ligand. Absolute
            paths only; a relative workspace makes MolCascade derive a relative receptor path that
            its own validation then rejects.
        workers: CPU lanes per batch. Ten by default rather than the machine's width: with several
            batches and several generation loops in flight, a wider default oversubscribes the host,
            and screening is the side of that trade with spare capacity.
    """

    screen: Screen
    config_path: Path
    revision_id: str
    target: dict[str, Any] | None = None
    workers: int = 10

    def __call__(self, batch_id: str, library: Path, device: str) -> ScreenResult:
        plan = self.screen.plan(self.config_path, library, target=self.target)
        if plan.revision_id != self.revision_id:
            raise RevisionChanged(
                f"{self.config_path} now compiles to {plan.revision_id[:12]}, but this campaign's "
                f"batches were carved under {self.revision_id[:12]}. Screening {batch_id} would "
                "produce a result not comparable with the others. Start a new campaign, or restore "
                "the configuration."
            )
        return self.screen.run(
            plan,
            run_id=batch_id,
            resume=True,
            workers=self.workers,
            devices=(device,),
        )


__all__ = ["MolCascadeScreening", "RevisionChanged"]
