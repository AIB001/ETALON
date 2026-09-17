"""A real expensive stage: build, drive, read, and report what is not there.

The loop takes its expensive stage as a callable so that the orchestration can be
exercised without a GPU. This is the callable that uses PRISM, and almost all of it is
about the cases where there is no number.

That emphasis is the right one. A campaign of thirty molecules will have a handful whose
ligand would not parameterise, one whose equilibration blew up, and -- the ordinary case --
a majority still running when the wall clock ran out. A stage that returns numbers for the
survivors and nothing for the rest has silently turned a mixed result into a clean one,
and the screen then learns from a population selected by whatever happened to finish.

So every molecule handed over comes back as a :class:`Measurement`, with or without a
value, carrying the postflight observations that say why. The admissibility layer already
knows what to do with that: no expensive value withholds the measurement, with the reason
in the record.

One thing this stage deliberately does not do is decide that a missing result is
acceptable. It reports; the campaign's waivers decide.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etalon.boundary.simulate import (
    Simulate,
    SimulationError,
    materialize_ligands,
    read_binding_energy,
)
from etalon.faults import postflight
from etalon.faults.attribution import Observation
from etalon.learn.admissible import Measurement


@dataclass(frozen=True, slots=True)
class PrismStage:
    """Build and drive one system per handoff record, then read what exists.

    Args:
        simulate: The configured adapter. Its environment has already been probed, so a
            missing AmberTools is reported before the first molecule rather than during it.
        receptor: The protein every record is simulated against. One receptor per round,
            because a campaign comparing ligands must hold it fixed -- and because the
            handoff records carry a ``receptor_id`` that can then be checked against this
            file rather than trusted.
        stages: Which driver stages are required for a result to count.
        timeout_per_molecule: Wall clock for one drive. The default is deliberately finite:
            PRISM's production default is 500 ns, so a campaign that wanted equilibration
            must be able to stop, and a timeout with em/nvt/npt on disk is a success.
        mmpbsa_subdir: Where to look for a finished gmx_MMPBSA result, relative to the
            build. Absent is the normal case and is reported, not raised.
    """

    simulate: Simulate
    receptor: Path
    stages: tuple[str, ...] = ("em", "nvt", "npt")
    timeout_per_molecule: int = 86_400
    mmpbsa_subdir: str = "GMX_PROLIG_MMPBSA"
    reuse: bool = True

    def __call__(
        self,
        rows: Sequence[Mapping[str, Any]],
        cheap: Mapping[str, float] | None = None,
        grants: Mapping[str, Any] | None = None,
    ) -> list[Measurement]:
        """Simulate each row, after checking that each row may be simulated.

        ``grants`` comes from :func:`etalon.authority.authorize`, which runs the preflight and
        issues nothing for a record that blocks. It is required, and ``None`` raises rather than
        defaulting to permission: a gate whose default is "allowed" protects whoever remembers it,
        which is the population that did not need it.

        The check is per row and happens before anything is written, because the failure it catches
        is a row edited between the ruling and the build -- the same thing
        :func:`materialize_ligands` catches one layer down when a written file disagrees with the
        record it came from, and for the same reason.
        """

        from etalon.authority.grant import NotAuthorized, require

        cheap = cheap or {}
        if grants is None:
            raise NotAuthorized(
                "PrismStage was called with no spend authorizations. Build them with "
                "etalon.authority.authorize(rows, receptor_path=..., waivers=...), which runs the "
                "preflight and mints a token only for records that survive it, then pass the "
                "result. Campaign.round does this for you. There is deliberately no default: a "
                "stage that spent when nobody said it could would be ADR 0006's bug with a "
                "different name."
            )
        for row in rows:
            require(row, grants)

        ligand_dir = self.simulate.workspace / "ligands"
        written = materialize_ligands(rows, ligand_dir)

        out: list[Measurement] = []
        for row, material in zip(rows, written, strict=True):
            identifier = str(row.get("parent_id") or "")
            if not material.usable:
                # The seam refused it, which is a finding about the record rather than
                # about the simulation. Reported as an unreadable structure because that is
                # what it is: nobody can build from this.
                out.append(
                    Measurement(
                        parent_id=identifier,
                        cheap_value=cheap.get(identifier),
                        expensive_value=None,
                        observations=(
                            Observation(
                                "F_STRUCTURE_UNREADABLE",
                                fired=True,
                                detail=material.refused,
                            ),
                        ),
                    )
                )
                continue
            out.append(self._one(identifier, material.path, cheap.get(identifier)))
        return out

    def _one(self, identifier: str, ligand: Path | None, cheap: float | None) -> Measurement:
        assert ligand is not None
        run_id = f"{identifier.rsplit(':', 1)[-1][:16]}"
        try:
            build = self.simulate.build(
                self.receptor, ligand, run_id=run_id, reuse=self.reuse
            )
        except SimulationError as error:
            # A refusal before any compute: the environment was not ready, or the directory
            # could not be reused and could not be replaced. Either way there is no build
            # to read, and saying so is the whole value of having refused early.
            return Measurement(
                parent_id=identifier,
                cheap_value=cheap,
                expensive_value=None,
                observations=(
                    Observation("F_BUILD_INCOMPLETE", fired=True, detail=str(error)),
                ),
            )

        log = ""
        if build.stdout_path is not None and build.stdout_path.is_file():
            log = build.stdout_path.read_text(encoding="utf-8", errors="replace")

        if not build.built:
            return Measurement(
                parent_id=identifier,
                cheap_value=cheap,
                expensive_value=None,
                observations=postflight.check_build(
                    {"built": False, "detail": build.detail}, log
                ),
                provenance={"run_id": build.run_id, "output_dir": str(build.output_dir)},
            )

        drive = self.simulate.drive(
            build, stages=self.stages, timeout=self.timeout_per_molecule
        )
        manifest = {"build": {"built": True}, "drive": drive.as_dict()}
        observations = postflight.check_run(manifest, build_log=log, requested=self.stages)

        energy = read_binding_energy(build.output_dir / self.mmpbsa_subdir)
        provenance: dict[str, object] = {
            "run_id": build.run_id,
            "output_dir": str(build.output_dir),
            "stages_finished": list(drive.finished),
            "driver_exit_code": drive.exit_code,
            "timed_out": drive.timed_out,
            "arguments": dict(build.arguments),
        }
        if energy is not None:
            provenance["binding_energy"] = energy.as_dict()
        else:
            provenance["no_binding_energy"] = (
                f"no FINAL_RESULTS_MMPBSA.dat under {self.mmpbsa_subdir}. Equilibration is "
                "not a binding free energy: this round built and equilibrated the system "
                "and nothing has estimated an affinity from it yet."
            )
        return Measurement(
            parent_id=identifier,
            cheap_value=cheap,
            expensive_value=None if energy is None else energy.total_kcal_mol,
            observations=observations,
            provenance=provenance,
        )


__all__ = ["PrismStage"]
