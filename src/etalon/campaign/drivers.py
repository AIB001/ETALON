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

from dataclasses import dataclass, replace
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
    #: The library the campaign's ``revision_id`` was computed against -- normally the calibration
    #: panel's. Required, because a compiled revision covers the library as well as the funnel.
    #:
    #: MolCascade will not compile a cascade with no library bound, and binding a different one
    #: yields a different revision: measured, five batches of one campaign compiled to five
    #: revisions from one unchanged config file. So comparing each batch's own revision against the
    #: campaign's cannot ever succeed -- the check refused every batch, which is how a campaign
    #: generated 470,651 molecules and screened none of them. Compiling the *same* reference library
    #: every time isolates the question the check is actually asking: is the funnel still the one
    #: the gate certified?
    reference_library: Path | None = None

    def __call__(self, batch_id: str, library: Path, device: str) -> ScreenResult:
        identity = self.screen.plan(
            self.config_path, self.reference_library or library, target=self.target
        )
        if identity.revision_id != self.revision_id:
            raise RevisionChanged(
                f"{self.config_path} now compiles to {identity.revision_id[:12]}, but this "
                f"campaign's batches were carved under {self.revision_id[:12]}. Screening "
                f"{batch_id} would produce a result not comparable with the others. Start a new "
                "campaign, or restore the configuration."
            )
        plan = (
            identity
            if self.reference_library is None
            else self.screen.plan(self.config_path, library, target=self.target)
        )
        # Resume only a batch that has a run record. MolCascade raises ``run does not exist`` when
        # asked to resume one that never started, so an unconditional ``resume=True`` fails every
        # batch on its first attempt -- which is every batch of a fresh campaign. Measured on ALK2:
        # 23 batches carved, five claimed, none screened, and the supervisor swallowed the error in
        # its worker thread, so the campaign generated 470,651 molecules over fourteen hours into a
        # pool nothing ever read. The condition is what makes the second attempt reuse committed
        # stages, which is what resume was for.
        resume = self.screen.state(batch_id) is not None
        result = self.screen.run(
            plan,
            run_id=batch_id,
            resume=resume,
            workers=self.workers,
            devices=(device,),
        )
        if self.reference_library is None or result.revision_id == self.revision_id:
            return result
        # Report the funnel the batch went through, not the funnel-and-this-library digest. The
        # sweep asks two questions of a recorded revision -- does it match what the batch was carved
        # under, and do all recorded batches share one -- and both are about the funnel. Under the
        # per-library digest the first refuses every batch and the second makes ``comparable`` false
        # for any campaign with more than one batch, which is every campaign. The per-library digest
        # is not lost: it stays in the run record, where provenance belongs.
        return replace(result, revision_id=self.revision_id)


@dataclass
class MolCascadeProcessScreening:
    """Screen one batch in its own process, so concurrent batches do not share one GIL.

    :class:`MolCascadeScreening` runs the cascade in the caller's process and the supervisor runs
    each screen on a thread. ``_Call`` justifies the thread by saying "the work is already in a
    subprocess -- PRISM and MolCascade are external programs". That is true of PRISM, which is
    driven as ``prism generate``, and **false of MolCascade**, which the ``Screen`` adapter imports
    and runs in-process.

    Measured on ALK2: five batches concurrent, all five threads inside RDKit's Python API in the
    ``standardize`` stage, **105% CPU total on a 96-core machine**, and the artifact store growing
    by zero bytes in a minute. A cascade's cheap tiers are a Python loop over molecules, so
    concurrency there buys nothing at all; only the docking engines, which are separate binaries,
    overlap. Five batches therefore cost five times one batch's wall clock through every tier above
    the docking one.

    This driver hands each batch to the ``molcascade`` CLI instead, which is how the first SND1
    campaign ran them -- by hand, from bash, one process per batch -- and why that campaign never
    saw this.

    Args:
        screen: Used to read the run record when the subprocess finishes, and for nothing else.
        target_args: The CLI flags naming the receptor and the binding site, e.g.
            ``("--receptor", "...pdb", "--reference-ligand", "...mol2")``. Exactly one site
            definition; MolCascade refuses two that could disagree.
        reference_library: The library the campaign's ``revision_id`` was computed against. The
            identity check compiles it with ``--dry-run``, which is the same code path the run
            takes rather than a reimplementation of it.
        executable: argv prefix for the CLI. A list so a wrapper that activates an environment can
            be put in front of it.
        env: Extra environment for the child. ``CUDA_VISIBLE_DEVICES`` is set from ``device``.
    """

    screen: Screen
    config_path: Path
    workspace: Path
    revision_id: str
    target_args: tuple[str, ...]
    reference_library: Path
    executable: tuple[str, ...] = ("molcascade",)
    workers: int = 10
    env: dict[str, str] | None = None
    timeout: float | None = None

    def _argv(self, library: Path, run_id: str, *, dry_run: bool) -> list[str]:
        argv = [
            *self.executable,
            "screen",
            "--config",
            str(self.config_path),
            "--library",
            str(library),
            "--workspace",
            str(self.workspace),
            "--run-id",
            run_id,
            *self.target_args,
        ]
        if dry_run:
            argv += ["--dry-run", "--json"]
        else:
            argv += ["--workers", str(self.workers)]
            # Only resume a run that exists: MolCascade raises RUN_NOT_FOUND otherwise, which is
            # every batch's first attempt. The in-process driver had the same fault and it is the
            # same one line -- worth saying twice, because "resume is always safe" is true of the
            # committed-stage cache and false of the run record it is keyed on.
            if self.screen.state(run_id) is not None:
                argv += ["--resume"]
        return argv

    def __call__(self, batch_id: str, library: Path, device: str) -> ScreenResult:
        import json
        import os
        import subprocess

        environment = dict(os.environ)
        environment.update(self.env or {})
        # One visible card per batch. MolCascade's own --device names a lane within what it can see,
        # and two batches naming cuda:0 of different physical cards is the confusion this avoids.
        if device.startswith("cuda:"):
            environment["CUDA_VISIBLE_DEVICES"] = device.split(":", 1)[1]

        probe = subprocess.run(  # noqa: S603 -- argv built from validated campaign configuration
            self._argv(self.reference_library, f"{batch_id}__identity", dry_run=True),
            capture_output=True,
            text=True,
            env=environment,
            timeout=self.timeout,
        )
        if probe.returncode != 0:
            raise RevisionChanged(
                f"the cascade would not compile for {batch_id}: {probe.stderr.strip()[-400:]}"
            )
        compiled = str(json.loads(probe.stdout).get("revision_id", ""))
        if compiled != self.revision_id:
            raise RevisionChanged(
                f"{self.config_path} now compiles to {compiled[:12]}, but this campaign's batches "
                f"were carved under {self.revision_id[:12]}. Screening {batch_id} would produce a "
                "result not comparable with the others. Start a new campaign, or restore the "
                "configuration."
            )

        done = subprocess.run(  # noqa: S603 -- same argv, with the batch's own library
            self._argv(library, batch_id, dry_run=False),
            capture_output=True,
            text=True,
            env=environment,
            timeout=self.timeout,
        )
        result = self.screen.state(batch_id)
        if result is None:
            raise RuntimeError(
                f"{batch_id}: molcascade exited {done.returncode} and left no run record. "
                f"stderr: {done.stderr.strip()[-600:]}"
            )
        # The run record is the authority on the outcome, not the exit code: a batch whose every
        # molecule was gated out exits non-zero and is a measurement, not a failure.
        return replace(result, revision_id=self.revision_id)


__all__ = ["MolCascadeProcessScreening", "MolCascadeScreening", "RevisionChanged"]
