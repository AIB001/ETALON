"""Drive MolCascade as the campaign's cheap stage, and read its results back verified.

MolCascade is already shaped for an agent and this adapter mostly refuses to get in the
way. Its cascade compiles to a revision id before anything runs, its stages commit
content-addressed artifacts whose digests can be re-checked, its contracts are validated
on commit rather than on faith, and a run resumes by verifying committed stages instead
of recomputing them. An orchestration layer that reimplemented any of that would be
building a worse copy beside a working one.

So there are only four operations here, and the reason each exists is a property the
campaign loop needs rather than a convenience.

:meth:`Screen.plan` compiles without running. The campaign records the ``revision_id``
before spending anything, because that id is what makes two rounds comparable: a round
whose revision differs from the last one screened a different funnel, and a feedback loop
that cannot tell those apart will attribute a change in enrichment to its own update when
the cause was an edited config.

:meth:`Screen.run` executes and returns per-stage status. Failures come back as data,
not as exceptions, because a round with one failed tier is a result the campaign has to
record and reason about rather than a crash.

:meth:`Screen.read` resolves a committed artifact and **verifies its digests before
reading**. This is the one place the adapter is deliberately slower than it needs to be.
The campaign's whole premise is that an expensive measurement may only update the screen
if the agent can say what it measured; reading the screen's own output without checking
it would put an unverified table at the start of that chain.

:meth:`Screen.handoff` returns the ``md_system_input/v1`` rows. That contract exists
precisely so the facts a simulation stack would otherwise assume -- where the coordinates
came from, whether there are hydrogens, which receptor, which protonation state -- are
columns a producer had to fill in. It is the seam between the cheap stage and the
expensive one, and it is where a campaign can refuse a molecule for free.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from etalon.boundary.infra import Infra, load

#: A run in which no molecule survived the gates, recognised as a family rather than as a list.
#:
#: MolCascade reports this by raising from whichever stage first finds nothing left to work on, and
#: which stage that is depends on the cascade: the evidence gate when a policy tier empties, a
#: feature stage when the gate above it emptied, pose strain when docking admitted nobody. Measured
#: over 56 batches of one campaign, three different codes appeared --
#: ``EVIDENCE_GATE_EMPTY_PARENT_INPUT``, ``FEATURE_EMPTY_INPUT`` and
#: ``POSE_STRAIN_EMPTY_PARENT_INPUT`` -- and a caller enumerating the ones it had already seen
#: misfiled two batches as failures before the pattern was generalised. Enumerating members of this
#: family has now been wrong twice, so the family is matched.
#:
#: The distinction this draws is not cosmetic. A failed run is a defect to fix; an exhausted run is
#: a measurement -- every docking score it computed is committed in the artifact store, and the
#: campaign's next decision depends on reading them rather than on retrying anything.
_EXHAUSTION = re.compile(r"\A[A-Z0-9_]*EMPTY_(PARENT_)?INPUT\Z")


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _visible_devices() -> tuple[str, ...]:
    """Lanes to use when a caller asked for parallelism and named no devices.

    A visible CUDA device when there is one, and the library's own default otherwise rather than an
    invention of this adapter's.
    """

    try:
        import torch

        count = int(torch.cuda.device_count())
    except Exception:
        count = 0
    return tuple(f"cuda:{index}" for index in range(count)) or ("cpu",)


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """One compiled stage, after the run. Failure is data here, not an exception."""

    stage_id: str
    plugin: str
    status: str
    attempts: int
    artifact_id: str | None
    error: dict[str, Any] | None

    @property
    def committed(self) -> bool:
        return self.artifact_id is not None and self.error is None


@dataclass(frozen=True, slots=True)
class ScreenPlan:
    """What a round intends to screen, fixed before anything is spent."""

    revision_id: str
    stage_count: int
    tiers: tuple[dict[str, Any], ...]
    advisories: tuple[Any, ...]
    assets: tuple[Any, ...]
    backends: tuple[Any, ...]
    configuration_kind: str = "cascade"
    #: The objects MolCascade built. Live Python, not part of any record, and kept so
    #: the plan a caller inspected is the plan that runs -- the same reason
    #: MolCascade's own MCP layer keeps them.
    _internal: dict[str, Any] = field(repr=False, default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "revision_id": self.revision_id,
            "stage_count": self.stage_count,
            "tiers": [dict(tier) for tier in self.tiers],
            "advisory_count": len(self.advisories),
            "asset_count": len(self.assets),
            "backend_count": len(self.backends),
            "configuration_kind": self.configuration_kind,
        }


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """What a round's screen produced."""

    run_id: str
    revision_id: str
    status: str
    stages: tuple[StageOutcome, ...]

    @property
    def failed(self) -> tuple[StageOutcome, ...]:
        return tuple(stage for stage in self.stages if stage.error is not None)

    @property
    def committed(self) -> tuple[StageOutcome, ...]:
        """Every stage that produced an artifact, latest last."""

        return tuple(stage for stage in self.stages if stage.committed)

    @property
    def exhaustion(self) -> StageOutcome | None:
        """The stage that found nothing left, when that is why the run stopped.

        ``None`` for a run that finished, and for one that failed for any other reason.
        """

        for stage in self.failed:
            code = str((stage.error or {}).get("code", ""))
            if _EXHAUSTION.match(code):
                return stage
        return None

    @property
    def exhausted(self) -> bool:
        """Whether every molecule was gated out. Not a failure; see :data:`_EXHAUSTION`."""

        return self.exhaustion is not None

    @property
    def outcome(self) -> str:
        """``"committed"``, ``"exhausted"`` or ``"failed"`` -- the three-way answer a caller needs.

        Callers want one question answered: do I read results, record a zero, or fix something. A
        boolean cannot carry that, and ``status`` alone cannot either, because MolCascade reports an
        exhausted run as FAILED with a non-zero exit -- correctly, since a stage did raise. The
        classification belongs here so that no caller has to know which codes mean which.
        """

        if self.exhausted:
            return "exhausted"
        return "failed" if self.failed else "committed"

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "revision_id": self.revision_id,
            "status": self.status,
            "outcome": self.outcome,
            "exhausted_at": None if self.exhaustion is None else self.exhaustion.stage_id,
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "plugin": stage.plugin,
                    "status": stage.status,
                    "attempts": stage.attempts,
                    "artifact_id": stage.artifact_id,
                    "error": stage.error,
                }
                for stage in self.stages
            ],
            "failed_stages": [stage.stage_id for stage in self.failed],
            "committed_stages": len(self.committed),
        }


class Screen:
    """MolCascade, as the campaign's cheap stage."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        infra: Infra | None = None,
        allow_copyleft: bool = False,
    ) -> None:
        self.infra = infra or load("molcascade")
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.allow_copyleft = allow_copyleft

    # -- plan ---------------------------------------------------------------

    def plan(
        self,
        config_path: str | Path,
        library: str | Path | None = None,
        *,
        target: dict[str, Any] | None = None,
    ) -> ScreenPlan:
        """Compile the cascade and fix a revision id. Nothing is executed.

        The revision id is the campaign's handle on "which funnel was this". Two rounds
        with the same revision screened the same criteria in the same order; two rounds
        with different revisions did not, and a change in enrichment between them cannot
        be attributed to a learned update without saying so.
        """

        from molcascade.assets import preflight_assets
        from molcascade.backends.models import ProbePolicy
        from molcascade.backends.preflight import (
            preflight_backends,
            preflight_docking_advisories,
        )
        from molcascade.cascade import load_screening_config
        from molcascade.cascade.lower import lower_cascade
        from molcascade.cascade.models import TargetConfig
        from molcascade.pipeline import PipelineCompiler
        from molcascade.plugins import create_builtin_registry

        screening = load_screening_config(str(config_path))
        registry = create_builtin_registry()
        cascade = screening.cascade
        if cascade is not None:
            lowered = lower_cascade(
                cascade,
                registry=registry,
                library_path=str(library) if library is not None else None,
                target=TargetConfig.model_validate(target) if target else None,
            )
            pipeline = lowered.pipeline
        else:
            if library is not None or target is not None:
                raise ValueError("flat pipelines bind their own inputs/target; omit library and target overrides")
            assert screening.pipeline is not None
            pipeline = screening.pipeline
        compiled = PipelineCompiler(registry).compile(pipeline)

        return ScreenPlan(
            revision_id=compiled.revision.revision_id,
            stage_count=len(compiled.stages),
            tiers=tuple(
                {
                    "id": tier.id,
                    "title": tier.title,
                    "mode": tier.mode.value,
                    "criteria": [criterion.id for criterion in tier.criteria],
                }
                for tier in (cascade.tiers if cascade is not None else ())
                if tier.enabled
            ),
            advisories=tuple(
                preflight_docking_advisories(pipeline.stages, registry=registry)
            ),
            assets=tuple(preflight_assets(compiled.stages)),
            backends=tuple(
                preflight_backends(
                    compiled.stages,
                    policy=ProbePolicy(
                        allow_copyleft=self.allow_copyleft, run_version_commands=False
                    ),
                )
            ),
            configuration_kind=screening.kind,
            _internal={"registry": registry, "pipeline": pipeline, "compiled": compiled},
        )

    # -- compose ------------------------------------------------------------

    def with_handoff(
        self,
        config_path: str | Path,
        output_path: str | Path,
        *,
        prefer: str = "docked_pose",
        protonation_state_id: str | None = None,
        evidence_from: Mapping[str, str] | None = None,
        tier_id: str = "z_md_handoff",
    ) -> Path:
        """Append the MD handoff tier to a cascade, and write the composed config.

        MolCascade ships the handoff as a selectable criterion and deliberately leaves
        it out of the starter cascade, so that adding it cannot change what an existing
        funnel accepts. Composing it is therefore the campaign's job, and doing it here
        rather than by editing the operator's file keeps the authored config the thing
        the operator authored.

        The tier goes last. Everything above it decides which molecules survive; this
        one states what is being handed over, and a stage that filters on the grounds of
        simulability belongs after the science rather than inside it.

        Args:
            prefer: ``docked_pose`` when the round has a receptor, so the record carries
                the geometry the docking number is about. ``embedded_conformer`` for a
                round with no receptor, where a pose does not exist and demanding one
                would refuse the whole population.
            protonation_state_id: What decided the protonation state, when something
                did. Left alone the gate records ``INHERITED_FROM_STANDARDIZER``, which
                is honest and is what the preflight layer refuses; passing a real value
                here is the remedy, not a way of silencing it.
            evidence_from: Explicit contract-to-stage bindings when the cascade has
                multiple pose or conformer producers. For example,
                ``{"docking_score/v1": "docking_score"}`` preserves the original
                scored pose after a redock consistency gate. The compiler checks
                that each named producer supplies the requested contract.
        """

        import yaml
        from molcascade.cascade.catalog import CRITERIA_BY_ID
        from molcascade.cascade.defaults import build_criterion
        from molcascade.cascade.models import CascadeConfig
        from molcascade.plugins import create_builtin_registry

        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "tiers" not in raw:
            raise ValueError(f"{config_path} does not look like a cascade config")

        spec = CRITERIA_BY_ID["md_system_handoff"]
        # The mode follows the preference, because the two differ in what they are
        # allowed to require: the pose mode declares a docking score as an input and a
        # cascade without docking is refused before it starts, while the conformer mode
        # does not. Picking the recommended option regardless would refuse every
        # receptor-free round for a dependency it does not have.
        wanted = (
            "molcascade_handoff_conformer"
            if prefer == "embedded_conformer"
            else "molcascade_handoff"
        )
        option = next(
            (item for item in spec.executable_options if item.id == wanted),
            spec.default_option,
        )
        if option is None:
            raise RuntimeError(
                "the md_system_handoff criterion has no runnable option in this "
                "installation, so a handoff cannot be composed"
            )
        settings: dict[str, Any] = {"prefer": prefer}
        if protonation_state_id is not None:
            settings["protonation_state_id"] = protonation_state_id

        criterion = build_criterion(
            spec,
            option,
            settings=settings,
            registry=create_builtin_registry(),
        )
        if evidence_from is not None:
            from molcascade.cascade.models import CriterionConfig

            criterion = CriterionConfig.model_validate({
                **criterion.model_dump(mode="json"), "evidence_from": dict(evidence_from),
            })
        tier = {
            "id": tier_id,
            "title": "Handoff to simulation",
            "mode": "serial",
            "enabled": True,
            "criteria": [criterion.model_dump(mode="json")],
            "note": (
                "Added by ETALON. States what a force field is about to receive so the "
                "campaign can refuse a molecule before spending GPU time on it."
            ),
        }
        if any(existing.get("id") == tier_id for existing in raw["tiers"]):
            raw["tiers"] = [t for t in raw["tiers"] if t.get("id") != tier_id]
        raw["tiers"] = [*raw["tiers"], tier]

        # Re-validated through MolCascade's own model rather than trusted: a config this
        # layer assembled must be held to the same bounds as one a person wrote.
        CascadeConfig.model_validate(raw)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return target

    # -- run ----------------------------------------------------------------

    def run(
        self,
        plan: ScreenPlan,
        *,
        run_id: str | None = None,
        resume: bool = False,
        workers: int | None = None,
        devices: Sequence[str] | None = None,
    ) -> ScreenResult:
        """Execute the compiled plan. A failed stage comes back as data.

        Args:
            workers: Parallel lanes.
            devices: Lane devices, e.g. ``("cuda:0",)``.

        Supplying ``workers`` alone is how a GPU-only engine ends up on a CPU lane.
        ``StageResources`` defaults ``devices`` to ``("cpu",)``, which is the right default for a
        library and the wrong one here: the tiers this adapter drives include engines with no CPU path
        at all, and a campaign asking for four workers did not ask for four CPU lanes. Measured, that
        combination fails with "Uni-Dock requires a CUDA device and was assigned lane 'cpu'" -- after
        the conformer tier has already run, so the cost of the mistake is the tier above it.

        So when a caller asks for parallelism and names no devices, the visible CUDA lanes are used,
        falling back to the library's own default when there are none.
        """

        from molcascade.parallel.models import StageResources
        from molcascade.runtime import LocalRunner

        resources = None
        if workers is not None or devices is not None:
            lanes = tuple(devices) if devices else _visible_devices()
            resources = StageResources(workers=workers or len(lanes), devices=lanes)

        runner = LocalRunner(
            self.workspace,
            plugins=plan._internal["registry"],
            resources=resources,
        )
        result = runner.run(plan._internal["pipeline"], run_id=run_id, resume=resume)
        state = runner.load_run(result.run_id)
        return ScreenResult(
            run_id=result.run_id,
            revision_id=plan.revision_id,
            status=str(state.status),
            stages=tuple(
                StageOutcome(
                    stage_id=stage.stage_id,
                    plugin=stage.plugin_key,
                    status=stage.status.value,
                    attempts=stage.attempts,
                    artifact_id=(None if stage.output_ref is None else stage.output_ref.artifact_id),
                    error=(None if stage.error is None else stage.error.model_dump(mode="json")),
                )
                for stage in state.stages
            ),
        )

    # -- read ---------------------------------------------------------------

    def state(self, run_id: str) -> ScreenResult | None:
        """A run's durable state, without running anything. ``None`` when there is no such run.

        Works on a run still executing, in another process, or left by a crash -- MolCascade writes
        run state as each stage commits, so this is the authoritative answer to "what happened to
        that batch" and the only one that survives the process table.

        That property is why recovery is built on it. A supervisor deciding whether a batch was
        abandoned by looking for a live process is reading something that is neither durable nor
        specific: measured, ``pgrep`` on a run id matched the supervisor's own shell, and a worker
        whose shell had died left a child still committing stages for another hour. The run record
        was right in both cases.
        """

        from molcascade.plugins import create_builtin_registry
        from molcascade.runtime import LocalRunner

        runner = LocalRunner(self.workspace, plugins=create_builtin_registry())
        try:
            state = runner.load_run(run_id)
        except Exception:  # noqa: BLE001 -- RUN_NOT_FOUND and an unreadable state are both "no answer"
            return None
        return ScreenResult(
            run_id=run_id,
            revision_id=str(state.revision_id),
            status=str(state.status),
            stages=tuple(
                StageOutcome(
                    stage_id=stage.stage_id,
                    plugin=stage.plugin_key,
                    status=stage.status.value,
                    attempts=stage.attempts,
                    artifact_id=(None if stage.output_ref is None else stage.output_ref.artifact_id),
                    error=(None if stage.error is None else stage.error.model_dump(mode="json")),
                )
                for stage in state.stages
            ),
        )

    def progress(self, run_id: str) -> dict[str, Any]:
        """Which stage a run is on, out of how many.

        The answer to a question that looks answerable from resource usage and is not. Both docking
        engines in a default cascade allocate about 25 GB and release it, so a batch at stage 6 of 43
        and one at stage 29 present identically on the GPU -- and a campaign log recording "nearly
        finished" for a batch at stage 27 was wrong twice in one night from exactly that read.
        """

        result = self.state(run_id)
        if result is None:
            return {"run_id": run_id, "exists": False}
        running = next((stage.stage_id for stage in result.stages if stage.status == "RUNNING"), None)
        # How far the run got, which is not the same as how many stages succeeded. A stage that
        # failed was still reached -- an exhausted run that stopped at pose strain is at stage 34 of
        # 43, not 33 -- and a run with a stage in flight is *at* that stage. Counting successes and
        # adding one reported stage 44 of 43 for a completed run, which is the kind of off-by-one a
        # progress readout must not have: it is read while deciding whether to intervene.
        reached = sum(1 for stage in result.stages if stage.status in ("SUCCEEDED", "FAILED"))
        return {
            "run_id": run_id,
            "exists": True,
            "status": result.status,
            "outcome": result.outcome,
            "stage": reached + (1 if running else 0),
            "stages": len(result.stages),
            "current": running,
            "terminal": result.status in ("SUCCEEDED", "FAILED"),
        }

    def recall(self, run_id: str, *, panel_size: int | None = None) -> dict[str, Any]:
        """Per-tier retention for a run whose library was a panel of known molecules.

        Delegates to MolCascade's ``measure_recall``, which recovers the tier structure from the
        revision's own pipeline metadata and so cannot be pointed at the wrong configuration. This
        adapter adds nothing to the measurement; :mod:`etalon.campaign.calibrate` is where the
        judgement about it lives.

        The separation is the point. MolCascade can say which molecules a tier lost. Only the
        campaign knows which of them were known to bind, and therefore only the campaign can say
        whether the configuration is fit to apply to a million molecules.
        """

        from molcascade.plugins import create_builtin_registry
        from molcascade.recall import measure_recall
        from molcascade.runtime import LocalRunner

        report = measure_recall(
            LocalRunner(self.workspace, plugins=create_builtin_registry()),
            run_id,
            panel_size=panel_size,
        )
        as_tier = lambda tier: {  # noqa: E731 -- one shape, used twice, named where it is used
            "tier_id": tier.tier_id,
            "title": tier.title,
            "mode": tier.mode,
            "entering": tier.entering,
            "surviving": tier.surviving,
            "lost": list(tier.lost),
            "unavailable": tier.unavailable,
        }
        return {
            "run_id": report.run_id,
            "status": report.status,
            "panel_size": report.panel_size,
            "registered": report.registered,
            "tiers": [as_tier(tier) for tier in report.tiers],
            "finalize": [as_tier(tier) for tier in report.finalize],
            "unaccounted": list(report.unaccounted),
            "notes": list(report.notes),
            "infrastructure": self.infra.provenance(),
        }

    def export_shortlist(self, run_id: str, output: str | Path) -> dict[str, Any]:
        """Materialize a completed screen's verified final export for MolQuarry or another consumer.

        Preserves MolCascade parent/source ids and any docking evidence sidecar. A generic
        SDF export remains an identity file, not an MD-ready pose or an affinity measurement.
        Existing outputs are never overwritten.
        """
        from molcascade.handoff import materialize_shortlist
        from molcascade.plugins import create_builtin_registry
        from molcascade.runtime import LocalRunner

        exported = materialize_shortlist(LocalRunner(self.workspace, plugins=create_builtin_registry()),
                                          run_id, output)
        return {"path": str(exported.path.resolve()), "sha256": exported.sha256,
                "row_count": exported.row_count, "record_format": exported.record_format,
                "source_artifact_id": exported.source_artifact_id,
                "export_spec_id": exported.export_spec_id, "identified_count": exported.identified_count,
                "docking_sidecar": str(exported.docking.path.resolve()) if exported.docking else None,
                "infrastructure": self.infra.provenance()}

    def read(self, artifact_id: str, *, contract_id: str | None = None) -> list[dict[str, Any]]:
        """Read a committed artifact's rows, after verifying its digests.

        Verification is not optional here, and it is the slowest thing this adapter does.
        A campaign that lets an expensive measurement update its cheap screen has a chain
        of evidence running from a parquet file to a threshold; starting that chain with
        an unchecked read would make every later check decorative.

        Args:
            artifact_id: As returned by a committed stage.
            contract_id: Restrict to one output port's contract, e.g.
                ``"md_system_input/v1"``. Omitted, every port is read and the rows
                carry a ``_port`` key so a caller can tell them apart.
        """

        import pyarrow.parquet as pq
        from molcascade.artifacts.store import LocalArtifactStore

        store = LocalArtifactStore(self.workspace)
        # verify=True re-hashes the bundle. That is the point.
        directory = store.artifact_directory(artifact_id, verify=True)
        manifest = store.get_manifest(artifact_id)

        rows: list[dict[str, Any]] = []
        for output in manifest.outputs:
            if contract_id is not None and output.contract_id != contract_id:
                continue
            for relative in output.file_paths:
                table = pq.read_table(directory / str(relative))
                for row in table.to_pylist():
                    rows.append({**row, "_port": output.port, "_contract": output.contract_id})
        available = sorted({output.contract_id for output in manifest.outputs})
        if contract_id is not None and contract_id not in available:
            raise KeyError(
                f"artifact {artifact_id[:12]} has no port carrying {contract_id!r}; "
                f"it carries {available}"
            )
        # A present but empty population is a scientific result: every candidate
        # was rejected. It must not look like an absent output contract, which
        # consumers may legitimately skip when inspecting multi-output stages.
        return rows

    def artifact_carrying(self, result: ScreenResult, contract_id: str) -> str | None:
        """The last committed artifact with a port on this contract.

        Found by contract rather than by plugin name, and the difference is not style.
        Matching names looked fine and was wrong: the handoff producer's key is
        ``handoff.md_system_input@0.1.0`` and its gate's is
        ``handoff.md_system_input_gate@0.1.0``, so a suffix match for the producer
        returned the gate -- whose ports carry decisions, not the record. Asking for the
        contract cannot make that mistake, and it is also the project's own thesis: the
        contract is the interface between two stages, and a plugin's name is not.

        Manifests are read without verification here, because this is a search; the read
        that follows verifies the one artifact it actually uses.
        """

        found = self.artifacts_carrying(result, contract_id)
        return found[-1] if found else None

    def artifacts_carrying(self, result: ScreenResult, contract_id: str) -> tuple[str, ...]:
        """Every committed artifact with a port on this contract, in stage order.

        :meth:`artifact_carrying` answers "where is the handoff", which has one producer. This
        answers "where are the docking scores", which does not: a cascade running two engines
        commits two artifacts on ``docking_score/v1``, and a caller comparing engines that took only
        the last one would silently compare one engine with itself.

        Committed stages only -- which is what makes this readable on a run that gated everything
        out. Measured: a batch whose pose-strain stage raised on an empty input had already committed
        7,545 docking scores from two engines, and those scores are the entire product of that batch.
        """

        from molcascade.artifacts.store import LocalArtifactStore

        store = LocalArtifactStore(self.workspace)
        carrying: list[str] = []
        for stage in result.committed:
            assert stage.artifact_id is not None
            manifest = store.get_manifest(stage.artifact_id)
            if any(output.contract_id == contract_id for output in manifest.outputs):
                carrying.append(stage.artifact_id)
        return tuple(carrying)

    def handoff(self, result: ScreenResult) -> list[dict[str, Any]]:
        """The ``md_system_input/v1`` rows this run produced, or an empty list.

        Empty is a legitimate answer and the caller must handle it rather than treat it
        as an error: a cascade with no handoff stage configured has produced no claim
        about anyone's coordinates, which is exactly what the population-level fault
        ``F_HANDOFF_ABSENT`` is for. Returning an empty list rather than raising is what
        lets that fault be reported as a finding instead of a crash.
        """

        artifact = self.artifact_carrying(result, "md_system_input/v1")
        if artifact is None:
            return []
        return self.read(artifact, contract_id="md_system_input/v1")

    def metrics(
        self,
        result: ScreenResult,
        *,
        metric_id: str | None = None,
    ) -> dict[str, float]:
        """The screen's own number per molecule, for the calibration to work against.

        Looked for in two places and in this order. ``docking_score/v1`` first, because a
        score against the receptor the campaign is simulating is the number a binding free
        energy is most directly comparable with. Then ``derived_metric/v1``, which is where
        everything else lands -- a predicted affinity, a demerit total, a ligand efficiency.

        Returns an empty mapping when neither exists, which is the honest answer for a
        ligand-only round: there is no screen number, so nothing can be calibrated and the
        campaign has to say so rather than invent one.

        A ``derived_metric/v1`` value is never used unless ``metric_id`` names it, even when
        the run carries exactly one. Being the only candidate is not an argument for being
        the right one.

        Args:
            metric_id: Restrict ``derived_metric/v1`` to one metric. Without it, and with
                several metrics present, this refuses rather than picking: which of a
                demerit total and a predicted affinity a campaign is calibrating is a
                decision, and silently taking the first to appear would make it invisibly.
        """

        docking = self.artifact_carrying(result, "docking_score/v1")
        if docking is not None and metric_id is None:
            rows = self.read(docking, contract_id="docking_score/v1")
            # Rank 0 only: a lower-ranked pose is a different hypothesis about the same
            # molecule, and averaging hypotheses is not a score.
            return {
                str(row["parent_id"]): float(row["score"])
                for row in rows
                if row.get("score") is not None and int(row.get("pose_rank") or 0) == 0
            }

        # Every artifact that carries the contract, not the last one. Taking the last was
        # the first implementation and it silently chose a metric: a cascade commits a
        # derived metric per criterion -- a synthesizability score, a Lilly demerit total, a
        # hERG probability -- so "the last stage to commit" selected whichever tier happened
        # to run last. On a real three-molecule round that produced cheap values from a
        # liability model, which a binding free energy has no business being calibrated
        # against, and nothing in the record would have said so.
        available: dict[str, dict[str, float]] = {}
        for stage in result.committed:
            assert stage.artifact_id is not None
            try:
                rows = self.read(stage.artifact_id, contract_id="derived_metric/v1")
            except KeyError:
                continue
            for row in rows:
                if row.get("value") is None or str(row.get("status") or "OK") != "OK":
                    continue
                name = str(row["metric_id"])
                available.setdefault(name, {})[str(row["parent_id"])] = float(row["value"])

        if not available:
            return {}
        if metric_id is None:
            # Named or nothing, even when there is only one. A docking score is an affinity
            # proxy measured against the receptor the campaign is simulating, so using it
            # needs no permission. A derived metric is whatever a criterion computed, and
            # this funnel's only one is lilly_demerit_total -- a medicinal-chemistry
            # liability count. Returning it because it was the sole candidate is how a
            # campaign ends up calibrating a binding free energy against a structural alert
            # score, with nothing in the record saying so. Being the only option available
            # is not an argument that it is the right one.
            raise ValueError(
                "this run has no docking score, and its derived metrics are "
                f"{', '.join(sorted(available))}. Name one with metric_id if it really is "
                "comparable with what the simulations return. None of them is comparable "
                "by default: a demerit total and a predicted affinity are both derived "
                "metrics, and only one of them belongs on the other axis from a binding "
                "free energy."
            )
        if metric_id not in available:
            raise KeyError(
                f"no metric {metric_id!r} in this run; it carries "
                f"{', '.join(sorted(available))}"
            )
        return dict(available[metric_id])

    def durability(self) -> dict[str, object]:
        """Whether this workspace will still exist tomorrow, and why it might not.

        Worth a method because it cost a measured loss. A calibration run -- 231 molecules docked, the
        enrichment curve behind ``findings/0010`` -- had its workspace under ``/tmp``, and the directory
        was gone when the session restarted. The findings survived because they had been committed; the
        artifacts did not. A campaign whose expensive tiers take GPU-days cannot put its content-
        addressed store somewhere the operating system clears, and nothing in the adapter said so.

        Reported rather than refused: a smoke test legitimately wants a temporary directory, and a
        guard that refuses one would be worked around rather than read. The campaign loop puts this in
        its ledger line so a round that lost its artifacts says why.
        """

        import tempfile

        volatile = (Path(tempfile.gettempdir()).resolve(), Path("/tmp"), Path("/var/tmp"), Path("/dev/shm"))
        under = next(
            (str(root) for root in volatile if _within(self.workspace, root)),
            "",
        )
        return {
            "workspace": str(self.workspace),
            "durable": not under,
            "volatile_root": under or None,
            "why_it_matters": (
                f"This workspace is under {under}, which the operating system may clear at any time. "
                "Artifacts from a GPU-day tier would be lost with it; a calibration run's were, and "
                "only the committed findings survived. Move it somewhere durable before spending."
                if under
                else "Not under a temporary directory."
            ),
        }

    def provenance(self) -> dict[str, object]:
        return self.infra.provenance()


__all__ = ["Screen", "ScreenPlan", "ScreenResult", "StageOutcome"]
