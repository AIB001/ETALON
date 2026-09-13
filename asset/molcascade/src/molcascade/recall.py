"""Measure what a funnel keeps, using molecules that are known to be worth keeping.

Every hard reject in a cascade is a claim: *molecules like this are not worth
looking at*.  The cheapest way to find out whether a claim like that is true is
to point it at molecules that are known to be worth looking at and count how
many it deletes.  ``tests/fixtures/approved_drugs.py`` says exactly this and has
said it since the defaults were calibrated -- but only per tier, in a test, on
one panel, at the moment somebody was writing a threshold.  There has never been
a way to ask it of a whole funnel, or of a project's own actives.

That gap matters more than its size suggests, because in a cascade **recall
multiplies**.  Twenty-two independent gates that each keep 98% of what they see
keep 0.98^22 = 64% between them, and no single tier looks wrong at any point.
A per-tier number cannot show that; only the product can.

So this reads a finished run whose library *was* a panel of known molecules, and
reports what each tier kept, which molecules each tier lost by name, and the
product.  Four rules about the numbers, each of which is a way this measurement
can lie:

**An unreadable tier makes the whole product unknown, not optimistic.**  If a
stage published nothing its retention is ``None`` and the end-to-end figure is
``None`` as well.  The tempting alternative -- treat survivors as equal to
arrivals and carry on -- turns a run that died into a run that kept everything,
and the headline number would be *higher* the more broken the run was.

**A run that did not finish gets no headline at all.**  A partial run's tiers
are individually honest and their product is meaningless, so the report says
which stage stopped instead.

**What runs after the last tier counts too.**  The shortlist selector is not a
tier and nobody authored it as a screening criterion, but it stands between the
funnel and the file somebody opens, and it caps each Murcko scaffold.  A panel
of measured molecules is a congeneric series -- that is what a project's own
actives look like -- so on a real panel the cap can remove more molecules than
every gate combined while each tier truthfully reports having kept everything.
Measuring only the tiers there is not a small approximation of the delivered
number; it is a different and reliably higher one.

**No names is an error, not a result of zero.**  A panel whose identifiers never
reached the run -- because the library was read without an id column -- would
otherwise be reported as a funnel that lost everything, which is both wrong and
exactly the shape of a real catastrophic finding.

Nothing here runs inside the pipeline, changes any contract, or writes anything
back to a configuration.  It measures and it prints; deciding what to do about
a number is the operator's, and a tool that retunes thresholds from its own
measurement would destroy the reproducibility that makes the measurement worth
having.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow.dataset as ds

from molcascade.artifacts import (
    ArtifactDatasetRef,
    ArtifactIntegrityError,
    ArtifactManifest,
    ArtifactNotFoundError,
)
from molcascade.contracts import PARENT_V1
from molcascade.errors import PipelineError
from molcascade.io.parquet import iter_parquet_batches
from molcascade.runtime import LocalRunner, RunState

#: The largest registered population this will measure.  A recall panel is tens
#: to thousands of molecules; pointing this at a production run would hold every
#: parent id in memory to answer a question about a panel that is not there.
#: Refused with an explanation rather than attempted.
_MAX_PANEL_PARENTS = 100_000

_BATCH_SIZE = 65_536


@dataclass(frozen=True, slots=True)
class TierRecall:
    """What one tier of the funnel did to the panel."""

    tier_id: str
    title: str
    mode: str
    entering: int | None
    surviving: int | None
    #: Panel names this tier removed, sorted. Empty when it removed nothing --
    #: or when the tier could not be read, which ``unavailable`` distinguishes.
    lost: tuple[str, ...]
    unavailable: str | None = None

    @property
    def retention(self) -> float | None:
        """Fraction kept, or ``None`` when that cannot be known.

        ``None`` rather than 1.0 for an unreadable tier: the alternative makes a
        broken run look like a perfect one, and makes it look better the more of
        it is broken.
        """

        if self.unavailable is not None:
            return None
        if self.entering is None or self.surviving is None or not self.entering:
            return None
        return self.surviving / self.entering

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier_id": self.tier_id,
            "title": self.title,
            "mode": self.mode,
            "entering": self.entering,
            "surviving": self.surviving,
            "retention": self.retention,
            "lost": list(self.lost),
            "unavailable": self.unavailable,
        }


@dataclass(frozen=True, slots=True)
class RecallReport:
    """End-to-end retention of a known panel through one run's funnel."""

    run_id: str
    status: str
    panel_size: int
    registered: int
    tiers: tuple[TierRecall, ...]
    #: Stages that ran *after* the last tier and still removed molecules -- the
    #: shortlist selector above all.  They are not tiers and are not reported as
    #: such, but they are between the funnel and the file somebody opens, so
    #: leaving them out makes this measure something other than end to end.
    #: The shipped selector caps each Murcko scaffold at 25, and a panel of
    #: measured molecules is by definition a congeneric series, so this is the
    #: ordinary case for a recall panel rather than a corner of it.
    finalize: tuple[TierRecall, ...] = ()
    #: Names that registered but that no tier is accountable for, because the
    #: run stopped before the funnel finished.
    unaccounted: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.status == "SUCCEEDED"

    @property
    def end_to_end(self) -> float | None:
        """The product of every tier's retention, or ``None`` if any is unknown.

        Unknown propagates on purpose. A funnel is a conjunction, so a product
        missing one factor is not an approximation of the answer -- it is a
        different question with a reliably higher answer.
        """

        if not self.complete or not self.tiers:
            return None
        product = 1.0
        for tier in self.tiers + self.finalize:
            retention = tier.retention
            if retention is None:
                return None
            product *= retention
        return product

    @property
    def survivors(self) -> int | None:
        """Molecules still present when the run ended -- after the selector.

        This is what ``molcascade export`` writes out, which is the number a
        reader will compare against, so it is the number reported.
        """

        stages = self.tiers + self.finalize
        if not stages:
            return self.registered
        return stages[-1].surviving

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "complete": self.complete,
            "panel_size": self.panel_size,
            "registered": self.registered,
            "survivors": self.survivors,
            "end_to_end_recall": self.end_to_end,
            "tiers": [tier.as_dict() for tier in self.tiers],
            "finalize": [stage.as_dict() for stage in self.finalize],
            "unaccounted": list(self.unaccounted),
            "notes": list(self.notes),
        }

    def render(self) -> str:
        return "\n".join(_render_lines(self))


def measure_recall(
    runner: LocalRunner,
    run_id: str,
    *,
    panel_size: int | None = None,
) -> RecallReport:
    """Read a finished run whose library was a panel, and report what it kept.

    Takes no configuration: the tier structure is recovered from the revision's
    own pipeline metadata, which ``lower_cascade`` records precisely so that a
    finished run can be regrouped into the funnel someone authored. That also
    means this cannot be pointed at the wrong config by accident.
    """

    state = runner.load_run(run_id)
    revision = runner._load_revision(state.revision_id)  # Internal first-party boundary.
    tiers = _recorded_tiers(revision)
    if not tiers:
        raise PipelineError(
            f"run {run_id} did not come from a cascade, so it has no tiers to measure",
            code="RECALL_NOT_A_CASCADE",
            hint=(
                "Recall is measured per tier. A flat pipeline has stages but no tier "
                "structure; 'molcascade decisions' reports per stage instead."
            ),
            context={"run_id": run_id},
        )

    manifests = {
        stage.stage_id: _manifest_for(runner, state, stage.stage_id) for stage in state.stages
    }
    registered = _registered_parents(runner, state, manifests)
    names = _panel_names(runner, state, registered)
    notes: list[str] = []
    if registered and not names:
        raise PipelineError(
            f"run {run_id} registered {len(registered)} molecules but recorded no "
            "identifier for any of them",
            code="RECALL_NAMES_UNAVAILABLE",
            hint=(
                "A recall panel has to be readable by name, so the library must be "
                "read with an id column -- '--id-column' on the command line, or "
                "'candidate_id_column' in the source stage."
            ),
            context={"run_id": run_id, "registered": len(registered)},
        )
    unnamed = len(registered) - len(names)
    if unnamed > 0:
        notes.append(
            f"{unnamed} registered molecule(s) carry no name. Two panel rows that "
            "standardize to one structure register once, and only one of the two "
            "names survives the join."
        )

    order = [stage.stage_id for stage in state.stages]
    position = {stage_id: index for index, stage_id in enumerate(order)}
    rows: list[TierRecall] = []
    tier_exits: list[str] = []
    entering_set = set(registered)
    for tier in tiers:
        exit_stage = _tier_exit(tier, position)
        if exit_stage is None:
            rows.append(
                TierRecall(
                    tier_id=tier["id"],
                    title=tier["title"],
                    mode=tier["mode"],
                    entering=len(entering_set),
                    surviving=None,
                    lost=(),
                    unavailable="tier published no stage in this run",
                )
            )
            continue
        tier_exits.append(exit_stage)
        survivors, failure = _survivors(
            runner, manifests.get(exit_stage), entering_set
        )
        if failure is not None:
            rows.append(
                TierRecall(
                    tier_id=tier["id"],
                    title=tier["title"],
                    mode=tier["mode"],
                    entering=len(entering_set),
                    surviving=None,
                    lost=(),
                    unavailable=failure,
                )
            )
            # The population below an unreadable tier is unknown, so every tier
            # after it is unknown too rather than measured against a guess.
            entering_set = set()
            # render() promises "see the notes below" whenever the end-to-end
            # figure is withheld, and a tier failing to read is one of the two
            # ways that happens.  Without this the JSON reports complete=true
            # with an empty notes list and a null recall, which reads to a
            # consumer as "nothing to report" rather than "this did not read".
            notes.append(
                f"Tier {tier['id']} could not be read ({failure}), so no end-to-end "
                "figure is reported and every tier below it is measured against an "
                "unknown population."
            )
            continue
        lost = tuple(sorted(names[p] for p in entering_set - survivors if p in names))
        rows.append(
            TierRecall(
                tier_id=tier["id"],
                title=tier["title"],
                mode=tier["mode"],
                entering=len(entering_set),
                surviving=len(survivors),
                lost=lost,
            )
        )
        entering_set = survivors

    finalize_rows: list[TierRecall] = []
    last_tier = max(
        (position[stage_id] for stage_id in tier_exits if stage_id in position),
        default=-1,
    )
    for stage in state.stages:
        if position.get(stage.stage_id, -1) <= last_tier:
            continue
        remaining, failure = _survivors(runner, manifests.get(stage.stage_id), entering_set)
        if failure is not None or remaining == entering_set:
            # A stage that published no population of its own, or kept every
            # molecule, is not attrition and does not earn a row.
            continue
        finalize_rows.append(
            TierRecall(
                tier_id=stage.stage_id,
                title=stage.stage_id,
                mode="finalize",
                entering=len(entering_set),
                surviving=len(remaining),
                lost=tuple(sorted(names[p] for p in entering_set - remaining if p in names)),
            )
        )
        entering_set = remaining
    if finalize_rows:
        notes.append(
            "Molecules were removed after the last tier, by "
            + ", ".join(row.tier_id for row in finalize_rows)
            + ". The shipped shortlist selector caps each Murcko scaffold, so a "
            "congeneric panel loses molecules here that no tier rejected."
        )

    unaccounted: tuple[str, ...] = ()
    if state.status != "SUCCEEDED":
        notes.append(
            f"This run ended {state.status}, so the tiers below describe only how far "
            "it got. No end-to-end figure is reported for a funnel that did not run."
        )
        unaccounted = tuple(sorted(names[p] for p in entering_set if p in names))
    return RecallReport(
        run_id=run_id,
        status=str(state.status),
        panel_size=len(registered) if panel_size is None else panel_size,
        registered=len(registered),
        tiers=tuple(rows),
        finalize=tuple(finalize_rows),
        unaccounted=unaccounted,
        notes=tuple(notes),
    )


def _recorded_tiers(revision: Any) -> list[dict[str, Any]]:
    """Tier structure as ``lower_cascade`` recorded it in the pipeline metadata.

    Read from the revision rather than from a cascade file so this cannot be
    pointed at a configuration that is not the one the run executed -- the most
    plausible way a recall measurement could describe the wrong funnel.
    """

    metadata = getattr(revision.config, "metadata", None) or {}
    tiers = metadata.get("tiers")
    if not isinstance(tiers, list):
        return []
    recorded: list[dict[str, Any]] = []
    for entry in tiers:
        if not isinstance(entry, dict) or "id" not in entry:
            continue
        recorded.append(
            {
                "id": str(entry["id"]),
                "title": str(entry.get("title") or entry["id"]),
                "mode": str(entry.get("mode") or "serial"),
                "criteria": [str(c) for c in entry.get("criteria") or ()],
            }
        )
    return recorded


def _tier_exit(tier: dict[str, Any], position: dict[str, int]) -> str | None:
    """The last stage this tier compiled to, which is the tier's filter.

    Candidate ids are derived from the same rule lowering uses -- a criterion
    stage, its ``__gate``, and the tier's ``__policy`` join -- and the one that
    actually ran latest is the exit. Derived rather than assumed, so a tier whose
    criteria are all disabled reports as unavailable instead of silently
    measuring the tier above it.
    """

    candidates = [tier["id"] + "__policy"]
    for criterion in tier["criteria"]:
        candidates.append(criterion)
        candidates.append(criterion + "__gate")
    present = [stage_id for stage_id in candidates if stage_id in position]
    if not present:
        return None
    return max(present, key=lambda stage_id: position[stage_id])


def _manifest_for(
    runner: LocalRunner, state: RunState, stage_id: str
) -> ArtifactManifest | None:
    for stage in state.stages:
        if stage.stage_id != stage_id:
            continue
        if stage.output_ref is None:
            return None
        try:
            return runner.store.get_manifest(stage.output_ref.artifact_id)
        except (ArtifactIntegrityError, ArtifactNotFoundError, OSError):
            return None
    return None


def _parent_ref(manifest: ArtifactManifest | None) -> ArtifactDatasetRef | None:
    if manifest is None:
        return None
    for output in manifest.outputs:
        if output.contract_id == PARENT_V1.id:
            return manifest.dataset_ref(output.port)
    return None


def _dataset_paths(runner: LocalRunner, ref: ArtifactDatasetRef) -> tuple[Path, ...]:
    root = runner.store.resolve_dataset(ref, verify=False)
    return tuple(
        root.joinpath(*PurePosixPath(relative).parts) for relative in ref.file_paths
    )


def _registered_parents(
    runner: LocalRunner,
    state: RunState,
    manifests: dict[str, ArtifactManifest | None],
) -> set[str]:
    """Every parent the run registered, from the first stage that published any."""

    for stage in state.stages:
        ref = _parent_ref(manifests.get(stage.stage_id))
        if ref is None:
            continue
        found: set[str] = set()
        for path in _dataset_paths(runner, ref):
            if not path.exists():
                continue
            for batch in iter_parquet_batches(
                path, columns=["parent_id"], batch_size=_BATCH_SIZE
            ):
                found.update(str(value) for value in batch.column("parent_id").to_pylist())
                if len(found) > _MAX_PANEL_PARENTS:
                    raise PipelineError(
                        f"run {state.run_id} registered more than "
                        f"{_MAX_PANEL_PARENTS:,} molecules",
                        code="RECALL_POPULATION_TOO_LARGE",
                        hint=(
                            "Recall is measured against a panel of known molecules, "
                            "not against a screening library. Screen the panel as its "
                            "own run and measure that."
                        ),
                        context={"run_id": state.run_id},
                    )
        return found
    return set()


def _panel_names(
    runner: LocalRunner, state: RunState, registered: set[str]
) -> dict[str, str]:
    """``parent_id`` to the identifier its library row carried."""

    if not registered:
        return {}
    from molcascade.handoff import _identifier_index  # Internal first-party boundary.

    try:
        return _identifier_index(runner, state, registered)
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError):
        return {}


def _survivors(
    runner: LocalRunner,
    manifest: ArtifactManifest | None,
    wanted: set[str],
) -> tuple[set[str], str | None]:
    """Which of ``wanted`` this stage still holds, or why that is unknown."""

    ref = _parent_ref(manifest)
    if ref is None:
        return set(), "stage published no parent population"
    if not wanted:
        return set(), None
    found: set[str] = set()
    expression = ds.field("parent_id").isin(sorted(wanted))
    try:
        for path in _dataset_paths(runner, ref):
            if not path.exists():
                continue
            for batch in iter_parquet_batches(
                path,
                columns=["parent_id"],
                filter_expression=expression,
                batch_size=_BATCH_SIZE,
            ):
                found.update(str(value) for value in batch.column("parent_id").to_pylist())
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError, ValueError) as error:
        return set(), type(error).__name__
    return found, None


def _percent(value: float | None) -> str:
    return "?" if value is None else f"{value * 100:.1f}%"


def _render_lines(report: RecallReport) -> list[str]:
    lines = [
        f"Recall of {report.registered} known molecule(s) through run "
        f"{report.run_id} ({report.status})"
    ]
    if report.end_to_end is not None:
        lines.append(
            f"  end to end: {_percent(report.end_to_end)} "
            f"({report.survivors} of {report.registered} kept)"
        )
    else:
        lines.append("  end to end: not available — see the notes below")
    lines.append("")
    lines.append(f"  {'tier':<26}{'in':>6}{'out':>6}{'kept':>9}   lost")

    def _row(entry: TierRecall) -> str:
        if entry.unavailable is not None:
            return f"  {entry.tier_id:<26}{'':>6}{'':>6}{'?':>9}   {entry.unavailable}"
        lost = ", ".join(entry.lost[:6])
        if len(entry.lost) > 6:
            lost = f"{lost}, … (+{len(entry.lost) - 6})"
        return (
            f"  {entry.tier_id:<26}{entry.entering:>6}{entry.surviving:>6}"
            f"{_percent(entry.retention):>9}   {lost}"
        )

    for tier in report.tiers:
        lines.append(_row(tier))
    if report.finalize:
        # Separated rather than appended to the tier block: these are not tiers,
        # nobody authored them as screening criteria, and a reader who sees them
        # in the same list will report them as gates that rejected molecules.
        lines.append("")
        lines.append("  after the last tier — not screening criteria:")
        for entry in report.finalize:
            lines.append(_row(entry))
    if report.unaccounted:
        lines.append("")
        lines.append(
            f"  {len(report.unaccounted)} molecule(s) were still in the funnel when the "
            "run stopped."
        )
    for note in report.notes:
        lines.append("")
        lines.append(f"  note: {note}")
    return lines


__all__ = ["RecallReport", "TierRecall", "measure_recall"]
