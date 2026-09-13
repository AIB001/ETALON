"""Starting a screen, and asking how one went.

Two of these three tools can run for a long time, and that fact shapes how they
are written rather than being a caveat attached to them.

**They are ``async`` and do their work in a worker thread.**  Under stdio
transport a synchronous tool function is invoked inline on the event loop, so a
call that takes an hour takes the whole server with it: no second tool call, no
ping, no cancellation, no progress.  ``await asyncio.to_thread(...)`` puts the
runner on a thread and leaves the loop free, so the session stays alive while a
cascade executes and the client can still be talked to.  This is not a
refinement; a screen of a real library is minutes to hours and the naive version
does not work.

**A timed-out call is safe to repeat, and that is a property of the runner rather
than of this adapter.**  MolCascade's artifacts are content-addressed and
immutable, stage checkpoints are keyed by cache key rather than by run id, and
``resume=True`` re-verifies every committed checkpoint's bytes and lineage before
continuing.  So when a client gives up on a call that is still running, the
correct recovery is to call again with ``resume=True`` and the same ``run_id``:
completed stages are re-used after verification and only the unfinished work
re-executes.  An agent can retry here without thinking, which is unusual and
worth relying on.

What is deliberately *not* exposed: nothing here fetches an asset, installs a
package, or reaches the network.  A cascade that names an unprovisioned asset
fails on the way in with the command to run, rather than acquiring a hundred
megabytes because an agent asked a question.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from molcascade.mcp._common import (
    StdoutToStderr,
    absolute_path,
    dumps,
    logger,
    ok,
    open_runner,
    tool,
)


def _plan(
    *,
    config_path: Path,
    library: Path,
    workspace: Path,
    molecule_format: str,
    smiles_column: str | None,
    id_column: str | None,
    target: dict[str, Any] | None,
    allow_copyleft: bool,
) -> dict[str, Any]:
    """Everything a run decides before it starts, in the order the CLI decides it.

    Kept as one function so that ``dry_run`` and a real execution cannot diverge:
    the plan a caller inspects is the plan that runs.
    """

    from molcascade.assets import preflight_assets
    from molcascade.backends.models import ProbePolicy
    from molcascade.backends.preflight import (
        preflight_backends,
        preflight_docking_advisories,
    )
    from molcascade.cascade import LibraryConfig, load_screening_config
    from molcascade.cascade.lower import lower_cascade
    from molcascade.cascade.models import TargetConfig
    from molcascade.pipeline import PipelineCompiler
    from molcascade.plugins import create_builtin_registry

    screening = load_screening_config(config_path)
    registry = create_builtin_registry()

    overrides: dict[str, Any] = {}
    if smiles_column:
        overrides["smiles_column"] = smiles_column
    if id_column:
        overrides["id_column"] = id_column
    if molecule_format and molecule_format != "auto":
        overrides["format"] = molecule_format

    target_config = TargetConfig.model_validate(target) if target else None

    if screening.cascade is not None:
        cascade = screening.cascade
        if overrides:
            # Re-validated rather than copied in, so a nonsense column name is
            # refused by the same bounds the authored config was held to.
            merged = LibraryConfig.model_validate(
                {**cascade.library.model_dump(mode="python"), **overrides}
            )
            cascade = cascade.model_copy(update={"library": merged})
        lowered = lower_cascade(
            cascade,
            registry=registry,
            library_path=str(library),
            target=target_config,
        )
        pipeline = lowered.pipeline
        tier_plan = [
            {
                "id": tier.id,
                "title": tier.title,
                "mode": tier.mode.value,
                "criteria": [criterion.id for criterion in tier.criteria],
            }
            for tier in cascade.tiers
            if tier.enabled
        ]
    else:
        raise _unsupported_pipeline()

    advisories = preflight_docking_advisories(pipeline.stages, registry=registry)
    compiled = PipelineCompiler(registry).compile(pipeline)
    assets = preflight_assets(compiled.stages)
    backends = preflight_backends(
        compiled.stages,
        policy=ProbePolicy(allow_copyleft=allow_copyleft, run_version_commands=False),
    )
    return {
        "registry": registry,
        "pipeline": pipeline,
        "compiled": compiled,
        "revision_id": compiled.revision.revision_id,
        "tiers": tier_plan,
        "stage_count": len(compiled.stages),
        "advisories": [
            {
                "code": entry.code,
                "stage_id": entry.stage_id,
                "message": entry.message,
                "detail": entry.detail,
            }
            for entry in advisories
        ],
        "assets_provisioned": [str(entry) for entry in assets],
        "backends_required": [str(entry) for entry in backends],
        "target": None if target_config is None else target_config.model_dump(mode="json"),
    }


def _unsupported_pipeline() -> Exception:
    from molcascade.errors import MolCascadeError

    return MolCascadeError(
        "this tool screens a schema-2 cascade; the given file is a schema-1 pipeline",
        code="MCP_PIPELINE_NOT_SUPPORTED",
        hint=(
            "A flat pipeline binds its own library in its source stage, so there is "
            "nothing for --library to override unambiguously. Run it with "
            "'molcascade run <config>' instead, or convert it to a cascade."
        ),
    )


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool
    def plan_screen(
        config_path: str,
        library_path: str,
        workspace: str,
        molecule_format: str = "auto",
        smiles_column: str = "",
        id_column: str = "",
        target: dict[str, Any] | None = None,
        allow_copyleft: bool = False,
    ) -> str:
        """Decide everything a screen would decide, and stop before executing it.

        Every check a run performs on its way in, performed here with nothing
        started: the cascade is lowered against the library, the pipeline
        compiled, the revision id assigned, the assets and backends the stages
        require resolved, and the docking advisories collected.

        Call this first.  It is seconds rather than hours, it reports the funnel
        that would actually be built -- which can be shorter than the one
        authored, because a criterion whose backend is unavailable is dropped --
        and it yields the ``revision_id`` that identifies the configuration, so a
        later run can be matched to the plan that produced it.

        Args:
            config_path: Absolute path to a schema-2 cascade (JSON or YAML).
            library_path: Absolute path to the molecule library.
            workspace: Absolute path to the run workspace.  Not written to by
                this tool; reported so the plan names the same place the run will.
            molecule_format: ``auto``, ``delimited``, ``xlsx``, ``sdf``,
                ``parquet`` or ``mol2-directory``.  ``auto`` decides from the
                suffix.
            smiles_column: Column holding SMILES, for a delimited or Excel
                library.  Overrides whatever the cascade names.
            id_column: Column holding the molecule identifier.  Supply it
                whenever you will want ``measure_recall``, ``explain_molecule`` or
                a named shortlist -- without it the run records no name and those
                readers have nothing to key on.
            target: Docking target, as an object matching ``TargetConfig``:
                ``name`` and ``receptor_path`` are required; ``box`` takes
                ``center_x/y/z`` and ``size_x/y/z``; ``reference_ligand_path``,
                ``pocket_path``, ``receptor_pdbqt_path`` and ``preparation``
                (``enabled``, ``keep_waters``, ``keep_heterogens``) are optional.
                Omit it for a ligand-only cascade.  A docking cascade without a
                target fails here rather than at the docking tier.
            allow_copyleft: Permit copyleft backends such as GNINA.  The verdict
                is recorded in the run.

        Returns:
            JSON with ``revision_id``, ``stage_count``, ``tiers`` as the planned
            funnel, ``advisories`` from the docking preflight, the assets and
            backends required, and the resolved ``target``.
        """

        plan = _plan(
            config_path=absolute_path(config_path, field="config_path"),
            library=absolute_path(library_path, field="library_path"),
            workspace=absolute_path(workspace, field="workspace", must_exist=False),
            molecule_format=molecule_format,
            smiles_column=smiles_column or None,
            id_column=id_column or None,
            target=target,
            allow_copyleft=allow_copyleft,
        )
        # The registry, pipeline and compiled objects are live Python the plan
        # carries for the executor; only the serialisable half is reported.
        internal = ("registry", "pipeline", "compiled")
        return ok(**{key: value for key, value in plan.items() if key not in internal})

    @mcp.tool()
    async def screen(
        config_path: str,
        library_path: str,
        workspace: str,
        run_id: str = "",
        molecule_format: str = "auto",
        smiles_column: str = "",
        id_column: str = "",
        target: dict[str, Any] | None = None,
        allow_copyleft: bool = False,
        resume: bool = False,
        force: bool = False,
        workers: int = 0,
    ) -> str:
        """Execute a cascade over a library, committing every stage as it goes.

        This is the expensive tool.  A 5,000-molecule 2D cascade is under a
        minute; a million-molecule library with docking is hours to days.  The
        work happens on a worker thread so the server stays responsive, but the
        call itself does not return until the run finishes or fails.

        **If your client times out, call again with the same ``run_id`` and
        ``resume=true``.**  That is safe and cheap: committed stages are verified
        by digest and re-used, and only unfinished work re-executes.  It is safe
        because artifacts are immutable and checkpoints are keyed by cache key
        rather than by run id -- the interrupted run and the run resuming it share
        the same committed work.

        Each stage publishes typed artifacts under the workspace and appends to an
        append-only audit log.  Nothing is overwritten; a second run with a
        changed configuration gets a new revision id and its own artifacts, and
        the old ones remain readable.

        Args:
            config_path: Absolute path to a schema-2 cascade.
            library_path: Absolute path to the molecule library.
            workspace: Absolute path to the workspace.  Created if absent.  All
                artifacts, run state, checkpoints, the cache and the audit log
                live beneath it, and MolCascade owns it absolutely -- it refuses
                symlinked children, so do not point it at a mounted scratch path.
            run_id: Identifier for this run.  Generated if omitted, but supply
                one: it is how every other tool here finds the run, and it is
                what ``resume`` needs.
            molecule_format: See ``plan_screen``.
            smiles_column: See ``plan_screen``.
            id_column: See ``plan_screen``.  Supply it unless you are certain no
                reader will need molecule names.
            target: See ``plan_screen``.
            allow_copyleft: See ``plan_screen``.
            resume: Continue an interrupted run of the same ``run_id``, verifying
                each committed checkpoint before re-using it.  Cannot be combined
                with ``force``.
            force: Ignore the cache and recompute every stage.  Cannot be
                combined with ``resume``.
            workers: Shard-level parallelism.  ``0`` lets MolCascade decide from
                the machine, which is normally right -- the shard boundaries are a
                function of the input rather than of the worker count, so the
                committed bytes are identical either way.

        Returns:
            JSON with ``run_id``, ``revision_id``, ``status``, and per-stage
            rows carrying the stage id, plugin, status, attempts and the artifact
            id it published.  On failure the error carries the MolCascade code and
            a ``retryable`` flag; a retryable failure is one where calling again
            with ``resume=true`` is the right response.
        """

        from molcascade.errors import MolCascadeError

        try:
            config = absolute_path(config_path, field="config_path")
            library = absolute_path(library_path, field="library_path")
            root = absolute_path(workspace, field="workspace", must_exist=False)
            if resume and force:
                raise MolCascadeError(
                    "resume and force cannot both be requested",
                    code="MCP_RESUME_AND_FORCE",
                    hint="resume re-uses verified work; force discards it. Choose one.",
                )

            def execute() -> dict[str, Any]:
                from molcascade.environment import detect_environment
                from molcascade.runtime import LocalRunner

                with StdoutToStderr():
                    plan = _plan(
                        config_path=config,
                        library=library,
                        workspace=root,
                        molecule_format=molecule_format,
                        smiles_column=smiles_column or None,
                        id_column=id_column or None,
                        target=target,
                        allow_copyleft=allow_copyleft,
                    )
                    resources = None
                    if workers > 0:
                        from molcascade.parallel import StageResources

                        resources = StageResources(workers=workers)
                    runner = LocalRunner(
                        root,
                        plugins=plan["registry"],
                        resources=resources,
                        environment=detect_environment(workspace=root),
                    )
                    result = runner.run(
                        plan["pipeline"],
                        run_id=run_id or None,
                        resume=resume,
                        force=force,
                    )
                    state = runner.load_run(result.run_id)
                    return {
                        "run_id": result.run_id,
                        "revision_id": plan["revision_id"],
                        "status": str(state.status),
                        "stage_count": plan["stage_count"],
                        "stages": [
                            {
                                "stage_id": stage.stage_id,
                                "plugin": stage.plugin_key,
                                "status": stage.status.value,
                                "attempts": stage.attempts,
                                "artifact_id": (
                                    None
                                    if stage.output_ref is None
                                    else stage.output_ref.artifact_id
                                ),
                                "error": (
                                    None
                                    if stage.error is None
                                    else stage.error.model_dump(mode="json")
                                ),
                            }
                            for stage in state.stages
                        ],
                        "advisories": plan["advisories"],
                    }

            logger.info("Screening %s with %s", library, config)
            payload = await asyncio.to_thread(execute)
            return ok(**payload)
        except MolCascadeError as error:
            logger.error("screen failed: [%s] %s", error.code, error)
            return dumps({"ok": False, "error": error.as_dict()})
        except Exception as error:
            logger.exception("screen raised")
            return dumps(
                {
                    "ok": False,
                    "error": {
                        "category": "UNEXPECTED",
                        "code": type(error).__name__,
                        "message": str(error),
                        "hint": (
                            "If the run had already started, call screen again with the "
                            "same run_id and resume=true: committed stages are verified "
                            "and re-used rather than recomputed."
                        ),
                        "retryable": True,
                        "context": {"tool": "screen"},
                    },
                }
            )

    @mcp.tool()
    @tool
    def run_status(run_id: str, workspace: str, include_events: bool = False) -> str:
        """Report the durable state of one run, and optionally its audit log.

        Run state survives process death: it is written to the workspace as each
        stage completes, so this answers correctly for a run that is finished, one
        that failed, and one that is still executing in another process.

        Args:
            run_id: The run to inspect.
            workspace: Absolute path to the workspace holding it.
            include_events: Also return the append-only audit log.  Each event
                carries a type (RUN_STARTED, STAGE_COMPLETED,
                STAGE_CACHE_BYPASSED, and so on), the run and revision ids, and a
                details mapping.  This is the record of what the run decided and
                why, and it is the thing to read when a result is surprising.

        Returns:
            JSON with ``status``, timestamps, the per-stage rows including each
            stage's cache key and artifact id, and ``events`` when asked for.
        """

        runner = open_runner(workspace)
        state = runner.load_run(run_id)
        payload: dict[str, Any] = {
            "run_id": state.run_id,
            "revision_id": state.revision_id,
            "status": str(state.status),
            "started_at": state.started_at,
            "finished_at": state.finished_at,
            "updated_at": state.updated_at,
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "plugin": stage.plugin_key,
                    "status": stage.status.value,
                    "attempts": stage.attempts,
                    "cache_key": stage.cache_key,
                    "artifact_id": (
                        None if stage.output_ref is None else stage.output_ref.artifact_id
                    ),
                    "started_at": stage.started_at,
                    "finished_at": stage.finished_at,
                    "error": (
                        None if stage.error is None else stage.error.model_dump(mode="json")
                    ),
                }
                for stage in state.stages
            ],
        }
        if include_events:
            # The log is a bounded control-plane JSONL under the workspace, read
            # through AuditLog so a truncated final line is the model's problem
            # rather than this adapter's.
            from molcascade.runtime import AuditLog

            log_path = runner.events_root / f"{state.run_id}.jsonl"
            if log_path.is_symlink() or not log_path.is_file():
                payload["events_unavailable"] = (
                    f"no audit log on disk for this run at {log_path}"
                )
            else:
                log = AuditLog(
                    log_path, run_id=state.run_id, revision_id=state.revision_id
                )
                payload["events"] = [
                    event.model_dump(mode="json") for event in log.read()
                ]
        return ok(**payload)


__all__ = ["register"]
