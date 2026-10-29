"""Dependency diagnosis and asynchronous, plan-bound real screening tools."""

from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_screen_prepare_continuation(workspace: str, completed_run: str,
                                           next_config: str, output_dir: str,
                                           docking_backend: str = "") -> str:
        """CHEAP. Prepare a typed parent/conformer handoff; never launch or stop a job.

        The requested cascade keeps its chosen docking backend by default. An
        optional explicit Uni-Dock version is a caller decision, not an automatic
        optimization. Incompatible preparation is reported for the host to decide.
        """
        from etalon.screening_handoff import prepare_screen_continuation

        return ok(**prepare_screen_continuation(
            absolute_path(workspace, label="workspace"), completed_run,
            absolute_path(next_config, label="next_config"),
            absolute_path(output_dir, label="output_dir"),
            docking_backend=docking_backend or None))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_screen_export(workspace: str, run_id: str, output: str,
                            view: str = "shortlist", locations: dict | None = None,
                            rmsd_threshold: float = 2.0) -> str:
        """CHEAP. Verify and export a completed screen's final SDF/SMILES shortlist for sourcing.

        Preserves parent/source identifiers and docking sidecars. Requires a final exporter stage;
        creates a new output file. An identity SDF is not automatically an MD-ready complex pose.
        view=research instead exports committed gate/selection rows with SMILES,
        prepared receptors, top-one dock/redock SDF and a strict requested RMSD
        assessment into a new directory. Existing gates/results are unchanged.
        locations explicitly maps relocated original receptor paths. Partial runs
        can export their existing artifacts without starting or resuming science.
        """
        from etalon.boundary.screen import Screen

        if view == "research":
            from etalon.screening_delivery import export_screen_delivery
            target = absolute_path(output, label="output")
            report = export_screen_delivery(absolute_path(workspace, label="workspace"), run_id,
                target, locations=locations, rmsd_threshold=rmsd_threshold)
            return ok(path=str(target / "delivery.json"), run_status=report["run_status"],
                      stages=[{k: s[k] for k in ("stage_id", "counts", "parent_input", "parent_output",
                                                "net_parent_removal", "count_scope")} for s in report["stages"]],
                      files=len(report["files"]), scientific_calls=0, reused=report["reused"])
        if view != "shortlist":
            raise ValueError("view must be shortlist or research")

        return ok(**Screen(absolute_path(workspace, label="workspace")).export_shortlist(
            run_id, absolute_path(output, label="output")))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_doctor(prism_python: str = "") -> str:
        """CHEAP. Check installed dependencies, MCP and optional PRISM build executables.

        Reports capabilities separately. Does not install software, call an LLM API,
        or run a simulation. For a specific docking cascade also use etalon_screen_plan.
        """
        from etalon.doctor import diagnose

        interpreter = str(absolute_path(prism_python, label="prism_python")) if prism_python else None
        return ok(**diagnose(prism_python=interpreter))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_screen_plan(config_path: str, workspace: str, library_path: str = "",
                           allow_copyleft: bool = False, delivery: dict | None = None) -> str:
        """CHEAP. Compile a real MolCascade screen; return the input-bound plan_id.

        Checks required backends/assets and hashes input files, without running stages.
        Cost is not estimated/reserved. Review the supplied cascade/library before submit.
        A flat pipeline supplies its own input paths; omit library_path in that case.
        Optional delivery={} requests gate/SMILES/pose outputs after execution.
        Set require_redock=true and rmsd_threshold=2.0 there for that requested
        final protocol; incomplete redock configuration is diagnosed before submit.
        """
        from etalon.screening import plan_screen

        return ok(**plan_screen(absolute_path(config_path, label="config_path"),
                  absolute_path(library_path, label="library_path") if library_path else None,
                  absolute_path(workspace, label="workspace"), allow_copyleft=allow_copyleft, delivery=delivery))

    @mcp.tool()
    @tool(Cost.SPENDS)
    def etalon_screen_submit(config_path: str, workspace: str, run_id: str, plan_id: str,
                             library_path: str = "", workers: int = 1,
                             devices: list[str] | None = None, allow_copyleft: bool = False,
                             delivery: dict | None = None) -> str:
        """SPENDS. Submit the reviewed plan once to a detached local worker and return a job ID.

        Launches REAL screening, potentially including docking. Use only within the user's
        authorized compute scope. Repeating the same run_id/input/settings does not resubmit.
        Poll etalon_screen_status; client disconnects do not stop the worker. No automatic retry,
        MD, PMF, FEP, active-learning budget debit or cost cap is implied by this tool.
        Pass the same delivery options used for planning. Delivery failures remain
        separate from scientific outcomes and never trigger recomputation.
        """
        from etalon.screening import submit_screen

        return ok(job=submit_screen(absolute_path(config_path, label="config_path"),
                  absolute_path(library_path, label="library_path") if library_path else None,
                  absolute_path(workspace, label="workspace"), run_id=run_id,
                  expected_plan_id=plan_id, workers=workers, devices=devices if devices is not None else (),
                  allow_copyleft=allow_copyleft, delivery=delivery))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_screen_status(workspace: str, run_id: str) -> str:
        """FREE. Read the durable screening job, stage artifacts, errors and log path.

        Does not launch/retry a job. queued/running is last journaled state, not a heartbeat;
        after host/worker failure inspect the log before explicitly recovering downstream work.
        """
        from etalon.screening import screen_status

        return ok(job=screen_status(absolute_path(workspace, label="workspace"), run_id))
