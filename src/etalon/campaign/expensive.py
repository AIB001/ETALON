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

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from etalon.boundary.simulate import (
    STAGE_PRODUCTS,
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
            must be able to stop. Completed stage products are retained on timeout,
            but an unclean termination is not an admissible affinity measurement.
        mmpbsa_subdir: Where to look for a finished gmx_MMPBSA result, relative to the
            build. Absent is the normal case and is reported, not raised.
        production_ns: Optional explicit production duration, forwarded to the build and
            bound into its manifest. Required-product ``stages`` do not stop the driver
            before production; leaving this unset preserves PRISM's default duration.
    """

    simulate: Simulate
    receptor: Path
    stages: tuple[str, ...] = ("em", "nvt", "npt")
    timeout_per_molecule: int = 86_400
    mmpbsa_subdir: str = "GMX_PROLIG_MMPBSA"
    reuse: bool = True
    production_ns: float | None = None

    def __post_init__(self) -> None:
        if self.production_ns is not None and (
            isinstance(self.production_ns, bool) or not isinstance(self.production_ns, (int, float))
            or not math.isfinite(self.production_ns) or self.production_ns <= 0
        ):
            raise ValueError("production_ns must be finite and positive, or None")
        if (not self.stages or len(set(self.stages)) != len(self.stages)
                or set(self.stages) - {name for name, _, _ in STAGE_PRODUCTS}):
            raise ValueError("stages must be nonempty, unique known driver stages")
        if (isinstance(self.timeout_per_molecule, bool)
                or not isinstance(self.timeout_per_molecule, (int, float))
                or not math.isfinite(self.timeout_per_molecule)
                or self.timeout_per_molecule <= 0):
            raise ValueError("timeout_per_molecule must be finite and positive")
        subdir = Path(self.mmpbsa_subdir)
        if (not self.mmpbsa_subdir.strip() or subdir.is_absolute()
                or ".." in subdir.parts or "\\" in self.mmpbsa_subdir or "\0" in self.mmpbsa_subdir):
            raise ValueError("mmpbsa_subdir must be a relative path inside the build")

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
        identifiers = [str(row.get("parent_id") or "") for row in rows]
        if not all(identifiers) or len(set(identifiers)) != len(identifiers):
            raise NotAuthorized("handoff parent_id values must be nonempty and unique before spending")
        for row in rows:
            require(row, grants, receptor_path=self.receptor)

        ligand_dir = self.simulate.workspace / "ligands"
        written = materialize_ligands(rows, ligand_dir)

        out: list[Measurement] = []
        for row, material in zip(rows, written, strict=True):
            # A long preceding simulation may expire the next token or the receptor
            # may change. The batch's first check cannot authorize every later spend.
            require(row, grants, receptor_path=self.receptor)
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
        run_id = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        try:
            duration = {"production_ns": self.production_ns} if self.production_ns is not None else {}
            build = self.simulate.build(
                self.receptor, ligand, run_id=run_id, reuse=self.reuse, **duration
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
                provenance={"run_id": build.run_id, "output_dir": str(build.output_dir),
                            "execution": dict(build.execution)},
            )

        drive = self.simulate.drive(
            build, stages=self.stages, timeout=self.timeout_per_molecule
        )
        manifest = {"build": {"built": True}, "drive": drive.as_dict()}
        observations = postflight.check_run(manifest, build_log=log, requested=self.stages)

        energy_dir = (build.output_dir / self.mmpbsa_subdir).resolve()
        energy_file = (energy_dir / "FINAL_RESULTS_MMPBSA.dat").resolve()
        energy = (
            read_binding_energy(energy_dir)
            if energy_dir.is_relative_to(build.output_dir.resolve())
            and energy_file.is_relative_to(build.output_dir.resolve()) else None
        )
        provenance: dict[str, object] = {
            "run_id": build.run_id,
            "output_dir": str(build.output_dir),
            "stages_finished": list(drive.finished),
            "driver_exit_code": drive.exit_code,
            "timed_out": drive.timed_out,
            "arguments": dict(build.arguments),
            "execution": dict(drive.execution),
        }
        if energy is not None:
            provenance["binding_energy"] = energy.as_dict()
            if not drive.succeeded:
                provenance["binding_energy_withheld_reason"] = (
                    "driver did not terminate successfully; retained output is not an admitted label"
                )
        else:
            provenance["no_binding_energy"] = (
                f"no FINAL_RESULTS_MMPBSA.dat under {self.mmpbsa_subdir}. Equilibration is "
                "not a binding free energy: this round built and equilibrated the system "
                "and nothing has estimated an affinity from it yet."
            )
        return Measurement(
            parent_id=identifier,
            cheap_value=cheap,
            expensive_value=None if energy is None or not drive.succeeded else energy.total_kcal_mol,
            observations=observations,
            provenance=provenance,
        )


__all__ = ["PrismStage"]
