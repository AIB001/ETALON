"""Summarise a finished run as the funnel the user actually designed.

A flat list of fourteen succeeded stages does not answer the question a
screening user asks — *how many molecules did each tier remove, and why?*  This
module reads survivor counts back out of the artifact manifests and regroups
them under the tiers of the authored cascade.

Counts come from manifest metadata, which is bound into artifact identity, so
the report never re-hashes datasets.  A count is reported as unknown rather than
guessed when a producer does not declare one.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from typing import Any

from molcascade.artifacts import ArtifactIntegrityError, ArtifactNotFoundError
from molcascade.cascade.lower import LoweredCascade
from molcascade.cascade.models import CascadeConfig, TierMode

# The request ports lowering uses to carry the surviving population forward.
_PARENT_PORTS = ("primary", "parents")


@dataclass(frozen=True, slots=True)
class FunnelStep:
    """One executed stage, with what reached it and what left it."""

    stage_id: str
    label: str
    role: str
    criterion_id: str | None
    rows: int | None
    entering: int | None

    @property
    def removed(self) -> int | None:
        if self.rows is None or self.entering is None:
            return None
        return max(0, self.entering - self.rows)

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "label": self.label,
            "role": self.role,
            "criterion_id": self.criterion_id,
            "rows": self.rows,
            "entering": self.entering,
            "removed": self.removed,
        }


@dataclass(frozen=True, slots=True)
class FunnelTier:
    """One tier of the authored cascade and the steps it compiled to."""

    tier_id: str
    title: str
    mode: str
    minimum_passes: int | None
    steps: tuple[FunnelStep, ...]
    entering: int | None
    surviving: int | None

    @property
    def removed(self) -> int | None:
        if self.entering is None or self.surviving is None:
            return None
        return max(0, self.entering - self.surviving)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier_id": self.tier_id,
            "title": self.title,
            "mode": self.mode,
            "minimum_passes": self.minimum_passes,
            "entering": self.entering,
            "surviving": self.surviving,
            "removed": self.removed,
            "steps": [step.as_dict() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class FunnelReport:
    """The whole run, told as a funnel."""

    run_id: str
    status: str
    ingest: FunnelStep | None
    preparation: tuple[FunnelStep, ...]
    tiers: tuple[FunnelTier, ...]
    finalize: tuple[FunnelStep, ...]
    #: What the cascade asked the budget selector for.
    target_count: int | None = None
    #: How many molecules reached that selector.  The selector is a ``LIMIT``:
    #: it can only ever cut, so this is the largest shortlist that was available
    #: and the number that says whether a short shortlist is the tiers' doing.
    selected_from: int | None = None
    #: The per-scaffold cap, when the selector is the scaffold round-robin.  It
    #: bounds the shortlist at ``cap x distinct scaffolds`` independently of the
    #: target, which is the one way a run can fall short with survivors to spare.
    max_per_scaffold: int | None = None

    @property
    def ingested(self) -> int | None:
        return self.ingest.rows if self.ingest is not None else None

    @property
    def shortlisted(self) -> int | None:
        for step in reversed(self.finalize):
            if step.rows is not None:
                return step.rows
        for tier in reversed(self.tiers):
            if tier.surviving is not None:
                return tier.surviving
        return None

    @property
    def met_target(self) -> bool | None:
        """Whether the run delivered the shortlist size it was configured for."""

        if self.target_count is None or self.shortlisted is None:
            return None
        return self.shortlisted >= self.target_count

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "ingested": self.ingested,
            "shortlisted": self.shortlisted,
            "target_count": self.target_count,
            "selected_from": self.selected_from,
            "met_target": self.met_target,
            "ingest": self.ingest.as_dict() if self.ingest is not None else None,
            "preparation": [step.as_dict() for step in self.preparation],
            "tiers": [tier.as_dict() for tier in self.tiers],
            "finalize": [step.as_dict() for step in self.finalize],
        }

    def render(self) -> str:
        """Format the funnel for a terminal."""

        return "\n".join(_render_lines(self))


def _count(value: int | None) -> str:
    return "?" if value is None else f"{value:,}"


def _delta(step_or_tier: FunnelStep | FunnelTier) -> str:
    removed = step_or_tier.removed
    if removed is None:
        return ""
    if removed == 0:
        return "  (kept all)"
    return f"  (-{removed:,})"


def _mode_label(mode: str, minimum_passes: int | None) -> str:
    if mode == TierMode.SERIAL.value:
        return "serial"
    if mode == TierMode.ALL.value:
        return "parallel · all must pass"
    if mode == TierMode.ANY.value:
        return "parallel · any may pass"
    if mode == TierMode.AT_LEAST.value:
        return f"parallel · at least {minimum_passes} must pass"
    return mode


_LABEL_WIDTH = 44


def _fit(label: str, width: int = _LABEL_WIDTH) -> str:
    if len(label) <= width:
        return label.ljust(width)
    return label[: width - 1] + "…"


def _collapse_criteria(steps: tuple[FunnelStep, ...]) -> list[FunnelStep]:
    """Fold each scoring stage together with the threshold that filters on it.

    A scoring stage passes every molecule through and adds a column, so on its
    own it always reads "(kept all)".  Printed directly above its own threshold
    stage -- whose label is the same string plus the word "threshold" -- the two
    rows are indistinguishable as soon as the terminal truncates them, and the
    one that matters is the second.  What a funnel is being asked is how many
    molecules each criterion removed, and for a gated criterion that is what its
    threshold did.

    Only the rendering collapses.  ``as_dict`` keeps every executed stage,
    because a machine reader is answering a different question.
    """

    order: list[str] = []
    grouped: dict[str, list[FunnelStep]] = {}
    passthrough: list[FunnelStep] = []
    for step in steps:
        if step.criterion_id is None:
            passthrough.append(step)
            continue
        if step.criterion_id not in grouped:
            order.append(step.criterion_id)
            grouped[step.criterion_id] = []
        grouped[step.criterion_id].append(step)

    collapsed: list[FunnelStep] = []
    for criterion_id in order:
        group = grouped[criterion_id]
        producer, last = group[0], group[-1]
        collapsed.append(
            FunnelStep(
                # The stage that did the filtering is the one worth naming when
                # a reader goes looking for the decisions behind the number.
                stage_id=last.stage_id,
                label=producer.label,
                role=producer.role,
                criterion_id=criterion_id,
                rows=last.rows,
                entering=producer.entering,
            )
        )
    return collapsed + passthrough


def _render_lines(report: FunnelReport) -> list[str]:
    lines = [f"Screening funnel for run {report.run_id} ({report.status})"]
    if report.ingest is not None:
        lines.append(f"  {_fit(report.ingest.label)} {_count(report.ingest.rows):>12}")
    for step in report.preparation:
        lines.append(f"  {_fit(step.label)} {_count(step.rows):>12}{_delta(step)}")
    for index, tier in enumerate(report.tiers, start=1):
        lines.append("")
        mode = _mode_label(tier.mode, tier.minimum_passes)
        lines.append(f"  Tier {index} · {tier.title}  [{mode}]")
        criteria = _collapse_criteria(
            tuple(step for step in tier.steps if step.role != "policy")
        )
        # In a parallel tier each criterion scores the same entering population,
        # so its own count is what it would keep on its own -- not the tier's
        # survivors.  Saying so is only useful when criteria can disagree.
        annotate = tier.mode != TierMode.SERIAL.value and len(criteria) > 1
        for step in criteria:
            note = "  passing alone" if annotate else ""
            lines.append(
                f"    {_fit(step.label, _LABEL_WIDTH - 2)} {_count(step.rows):>12}"
                f"{_delta(step)}{note}"
            )
        lines.append(
            f"    {_fit('→ tier survivors', _LABEL_WIDTH - 2)} "
            f"{_count(tier.surviving):>12}{_delta(tier)}"
        )
    if report.finalize:
        lines.append("")
        for step in report.finalize:
            lines.append(f"  {_fit(step.label)} {_count(step.rows):>12}{_delta(step)}")
    lines.append("")
    kept = report.shortlisted
    started = report.ingested
    if kept is not None and started:
        share = kept / started * 100.0
        lines.append(f"  {started:,} molecules in → {kept:,} shortlisted ({share:.2f}% kept)")
        for sentence in _target_verdict(report, kept, started):
            # The counted rows are columnar and self-limiting; these are prose
            # and would otherwise run past the width the rest of the report was
            # laid out for, wrapping wherever the terminal happens to end.
            lines.extend(
                textwrap.wrap(
                    sentence,
                    width=76,
                    initial_indent="  ",
                    subsequent_indent="  ",
                )
            )
    return lines


def _target_verdict(report: FunnelReport, kept: int, started: int) -> list[str]:
    """Say whether the shortlist is the size the cascade was configured for.

    Without this the run ends on a bare count, and a count carries no verdict:
    12,000 molecules is a fine result from a 20,000-molecule library and a bad
    one from two million.  The user set a target precisely because the number
    matters to whatever comes next, so the report should answer against it.

    The two ways to fall short need different fixes and are indistinguishable
    from the shortlist alone, so they are diagnosed separately:

    * fewer molecules survived the tiers than were asked for -- feed more, or
      loosen a tier;
    * enough survived, but the per-scaffold cap bound first -- the library is
      concentrated in too few scaffolds, and raising the target will not help.
    """

    target = report.target_count
    if target is None:
        return []
    survivors = report.selected_from
    if kept >= target:
        if survivors is not None and survivors > kept:
            return [f"At the {target:,} target; the budget selected from {survivors:,} survivors."]
        return [f"At the {target:,} target."]

    short = target - kept
    cap = report.max_per_scaffold
    if survivors is not None and cap is not None and survivors >= target:
        return [
            f"Short of the {target:,} target by {short:,}: {survivors:,} molecules "
            f"survived, but no more than {cap} per scaffold may be taken, so the "
            "library does not hold enough distinct scaffolds to fill the budget.",
            "Raise max_per_scaffold on the budget step, or screen a library that "
            "spans more scaffolds.",
        ]

    lines = [
        f"Short of the {target:,} target by {short:,}: only {kept:,} molecules "
        "came through the tiers, and the budget step can cut a population but "
        "never enlarge one."
    ]
    if kept:
        # The end-to-end rate, not any single tier's: it already includes the
        # per-scaffold trim, so scaling by it answers the question actually
        # being asked -- how big an input produces the requested shortlist.
        needed = math.ceil(started * target / kept)
        lines.append(
            f"At this run's {kept / started * 100.0:.2f}% retention, reaching "
            f"{target:,} needs roughly {needed:,} molecules in."
        )
    return lines


def _rows_for(store: Any, ref: Any) -> int | None:
    """Sum declared row counts for the files behind one dataset reference."""

    if ref is None:
        return None
    try:
        manifest = store.get_manifest(ref.artifact_id)
    except (ArtifactIntegrityError, ArtifactNotFoundError, OSError):
        return None
    wanted = set(ref.file_paths)
    counts = [entry.row_count for entry in manifest.files if entry.path in wanted]
    if not counts or any(count is None for count in counts):
        return None
    return sum(count for count in counts if count is not None)


def _parent_stage(stage: Any) -> str | None:
    for port in _PARENT_PORTS:
        for binding in stage.inputs:
            if binding.request_port == port:
                return str(binding.stage)
    return None


def build_funnel(
    lowered: LoweredCascade,
    cascade: CascadeConfig,
    state: Any,
    store: Any,
) -> FunnelReport:
    """Regroup a finished :class:`RunState` under the tiers that produced it."""

    rows: dict[str, int | None] = {}
    for stage_state in state.stages:
        rows[stage_state.stage_id] = _rows_for(store, stage_state.output_ref)

    stages_by_id = {stage.id: stage for stage in lowered.pipeline.stages}
    origins = {origin.stage_id: origin for origin in lowered.origins}
    tier_specs = {tier.id: tier for tier in cascade.tiers}

    def make_step(stage_id: str) -> FunnelStep:
        origin = origins.get(stage_id)
        stage = stages_by_id.get(stage_id)
        parent = _parent_stage(stage) if stage is not None else None
        return FunnelStep(
            stage_id=stage_id,
            label=(origin.label if origin and origin.label else stage_id),
            role=(origin.role if origin else "stage"),
            criterion_id=(origin.criterion_id if origin else None),
            rows=rows.get(stage_id),
            entering=rows.get(parent) if parent else None,
        )

    ingest: FunnelStep | None = None
    preparation: list[FunnelStep] = []
    finalize: list[FunnelStep] = []
    tier_steps: dict[str, list[FunnelStep]] = {tier.id: [] for tier in cascade.tiers}
    tier_entering: dict[str, int | None] = {}

    for stage in lowered.pipeline.stages:
        origin = origins.get(stage.id)
        role = origin.role if origin else "stage"
        step = make_step(stage.id)
        if role == "ingest":
            ingest = step
        elif role == "standardize":
            preparation.append(step)
        elif role == "finalize":
            finalize.append(step)
        elif origin is not None and origin.tier_id is not None:
            bucket = tier_steps.setdefault(origin.tier_id, [])
            if not bucket:
                tier_entering[origin.tier_id] = step.entering
            bucket.append(step)

    tiers: list[FunnelTier] = []
    for tier_id, steps in tier_steps.items():
        if not steps:
            continue
        spec = tier_specs.get(tier_id)
        tiers.append(
            FunnelTier(
                tier_id=tier_id,
                title=(spec.title if spec is not None else tier_id),
                mode=(spec.mode.value if spec is not None else TierMode.SERIAL.value),
                minimum_passes=(spec.minimum_passes if spec is not None else None),
                steps=tuple(steps),
                entering=tier_entering.get(tier_id),
                surviving=steps[-1].rows,
            )
        )

    # The budget step is the one that was handed the target, which is how
    # lowering identifies it too -- by the setting rather than by a hardcoded
    # stage id, so a different selector plugged in later is still found.
    budget_entering: int | None = None
    max_per_scaffold: int | None = None
    for stage in lowered.pipeline.stages:
        settings = stage.config
        if "target_count" not in settings:
            continue
        parent = _parent_stage(stage)
        budget_entering = rows.get(parent) if parent else None
        cap = settings.get("max_per_scaffold")
        max_per_scaffold = cap if isinstance(cap, int) else None

    return FunnelReport(
        run_id=state.run_id,
        status=getattr(state.status, "value", str(state.status)),
        ingest=ingest,
        preparation=tuple(preparation),
        tiers=tuple(tiers),
        finalize=tuple(finalize),
        target_count=cascade.finalize.target_count,
        selected_from=budget_entering,
        max_per_scaffold=max_per_scaffold,
    )


__all__ = [
    "FunnelReport",
    "FunnelStep",
    "FunnelTier",
    "build_funnel",
]
