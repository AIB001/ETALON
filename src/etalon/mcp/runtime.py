"""Typed workflow execution, persistent executors and bounded evidence inspection."""

from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, threaded_tool, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_workflow_capabilities() -> str:
        """FREE. List registered workflow operations and their required/optional argument names.

        Workflows use explicit dependencies and {$ref: node.output} references. The controller
        selects ready nodes; fixed service validators establish completion from real artifacts.
        """
        from etalon.runtime.operations import describe

        return ok(operations=describe(), workflow_schema="etalon-workflow/1",
                  controller_modes=["ordered", "advisor", "external"])

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_executor_prepare(kind: str, configuration: dict[str, Any],
                                 endpoint: dict[str, Any]) -> str:
        """CHEAP. Freeze a molcascade or prism executor and its explicit endpoint without dispatch.

        MolCascade needs cascade/readout; PRISM needs absolute receptor_path/python and explicit
        production_ns. Returns a prepared executor to use for campaign creation and registration.
        """
        from etalon.runtime.executors import prepare_executor

        return ok(executor=prepare_executor(kind, configuration, endpoint))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_executor_register(database: str, prepared: dict[str, Any], rationale: str) -> str:
        """CHEAP. Register a prepared immutable executor against an exactly matching campaign endpoint.

        Revalidates scientific inputs; repeats are idempotent. Creates no campaign or experiment.
        """
        from etalon.runtime.executors import register_executor

        return ok(**register_executor(absolute_path(database, label="database"), prepared, rationale))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_executor_list(database: str) -> str:
        """FREE. Read registered executor records by endpoint ID from an existing campaign."""
        from etalon.runtime.executors import registered_executors

        return ok(executors=registered_executors(absolute_path(database, label="database")))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_screen_configure(workspace: str, configuration: dict[str, Any]) -> str:
        """CHEAP. Validate and save an immutable caller-specified MolCascade configuration.

        Accepts kind=cascade or schema=etalon-cascade-design/1 with name and explicit tiers.
        Returns configuration and config_path; no scientific hierarchy or threshold is invented.
        """
        from etalon.runtime.artifacts import configure

        return ok(**configure(absolute_path(workspace, label="workspace"), configuration))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_workflow_plan(spec: dict[str, Any]) -> str:
        """CHEAP. Validate a typed workflow graph, resources and immutable input hashes without execution.

        Returns normalized spec and plan_id. Resource quotes are explicit; only supported usage
        receipts establish actual consumption. Existing active journals remain mutable.
        """
        from etalon.runtime.service import plan

        return ok(**plan(spec))

    @mcp.tool()
    @threaded_tool(Cost.SPENDS)
    def etalon_workflow_submit(spec: dict[str, Any], workspace: str, job_id: str, plan_id: str) -> str:
        """SPENDS. Submit the reviewed plan to a durable local worker within its declared scope.

        May run real data acquisition, screening, active learning or registered PRISM execution.
        Same job ID and plan return the existing job. External mode waits for explicit decisions.
        Use workflow_status/observe/artifact; client disconnection does not stop the worker.
        """
        from etalon.runtime.service import submit

        return ok(job=submit(spec, absolute_path(workspace, label="workspace"), job_id=job_id,
                             expected_plan_id=plan_id))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_workflow_status(workspace: str, job_id: str) -> str:
        """FREE. Inspect durable node states, resource usage, worker identity and reconciliation needs."""
        from etalon.runtime.service import status

        return ok(job=status(absolute_path(workspace, label="workspace"), job_id))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_workflow_observe(workspace: str, job_id: str) -> str:
        """FREE. Read the identified controller observation with ready nodes and verified result summaries.

        External controllers submit exactly observation_id/node_id/reason through workflow_advance.
        A model's finish request cannot replace verification of every required node.
        """
        from etalon.runtime.service import observe

        return ok(**observe(absolute_path(workspace, label="workspace"), job_id))

    @mcp.tool()
    @threaded_tool(Cost.SPENDS)
    def etalon_workflow_advance(workspace: str, job_id: str, proposal: dict[str, Any]) -> str:
        """SPENDS. Accept one external model decision and dispatch its ready node through the worker.

        Proposal contains only observation_id, node_id and reason. Rejects stale state, invented
        nodes and unverified finish claims. pause requests a stop and never establishes success.
        """
        from etalon.runtime.service import advance

        return ok(job=advance(absolute_path(workspace, label="workspace"), job_id, proposal))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_workflow_cancel(workspace: str, job_id: str, reason: str) -> str:
        """CHEAP. Request cancellation with a reason; retain uncertain results and resource reservations.

        Cancellation does not imply all scientific processes have stopped. Inspect status and
        reconcile their actual outcomes before resuming or releasing uncertain expenditure.
        """
        from etalon.runtime.service import cancel

        return ok(job=cancel(absolute_path(workspace, label="workspace"), job_id, reason=reason))

    @mcp.tool()
    @threaded_tool(Cost.SPENDS)
    def etalon_workflow_reconcile(workspace: str, job_id: str, reason: str,
                                   resume: bool = False,
                                   settlements: dict[str, Any] | None = None) -> str:
        """SPENDS when resume=true. Reconcile saved receipts only after the worker and children stop.

        Defaults to inspection/reconciliation without new dispatch. Unknown outcomes keep their
        reservations; explicit failure settlements need documented costs and an existing evidence
        file in the step workspace. Never invent a scientific value to resolve an interruption.
        """
        from etalon.runtime.service import reconcile

        return ok(job=reconcile(absolute_path(workspace, label="workspace"), job_id, reason=reason,
                                resume=resume, settlements=settlements))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_workflow_artifact(workspace: str, job_id: str, node_id: str, kind: str = "result",
                                  member: str = "", artifact_id: str = "", contract_id: str = "",
                                  offset: int = 0, limit: int = 100) -> str:
        """CHEAP. Read a bounded page of a node result, sealed snapshot member or recorded screen artifact.

        kind=result supports a JSON Pointer in member; kind=snapshot lists sealed members or reads
        one relative member; kind=screen requires its recorded artifact_id and optional contract_id.
        No arbitrary host path is read. Use next_offset for pagination; output stays below 1 MiB.
        """
        from etalon.runtime.service import read_artifact

        return ok(**read_artifact(absolute_path(workspace, label="workspace"), job_id, node_id,
                  kind=kind, member=member, artifact_id=artifact_id, contract_id=contract_id,
                  offset=offset, limit=limit))

    @mcp.tool()
    @threaded_tool(Cost.CHEAP)
    def etalon_active_execution_plan(database: str, max_rounds: int = 1,
                                      min_new_admitted: int = 1, max_seconds: float = 3600) -> str:
        """CHEAP. Plan a single active.run workflow using registered executors and the remaining budget.

        Does not execute. Explicit max_rounds/min_new_admitted fix the task's limits and success
        requirement. The active policy selects candidates from the journal at dispatch time.
        """
        from etalon.runtime.api import active_execution_plan

        return ok(**active_execution_plan(absolute_path(database, label="database"),
                  max_rounds=max_rounds, min_new_admitted=min_new_admitted, max_seconds=max_seconds))

    @mcp.tool()
    @threaded_tool(Cost.SPENDS)
    def etalon_active_submit(database: str, workspace: str, job_id: str, plan_id: str,
                              max_rounds: int = 1, min_new_admitted: int = 1,
                              max_seconds: float = 3600) -> str:
        """SPENDS. Dispatch the reviewed active execution plan through the persistent workflow worker.

        Requires registered executors. Reuses the existing active policy, authorization, QC and
        budget journal; success requires new admitted observations. Inspect/cancel/reconcile with
        the workflow tools. Identical submissions reuse the existing job even after completion.
        """
        from etalon.runtime.api import active_submit

        return ok(job=active_submit(absolute_path(database, label="database"),
                  absolute_path(workspace, label="workspace"), job_id=job_id,
                  expected_plan_id=plan_id, max_rounds=max_rounds,
                  min_new_admitted=min_new_admitted, max_seconds=max_seconds))
