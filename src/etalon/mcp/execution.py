"""Dependency diagnosis and asynchronous, plan-bound real screening tools."""

from __future__ import annotations

from typing import Any

from etalon.mcp._common import Cost, absolute_path, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_screen_export(workspace: str, run_id: str, output: str) -> str:
        """CHEAP. Verify and export a completed screen's final SDF/SMILES shortlist for sourcing.

        Preserves parent/source identifiers and docking sidecars. Requires a final exporter stage;
        creates a new output file. An identity SDF is not automatically an MD-ready complex pose.
        """
        from etalon.boundary.screen import Screen

        return ok(**Screen(absolute_path(workspace, label="workspace")).export_shortlist(
            run_id, absolute_path(output, label="output")))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_doctor(prism_python: str = "") -> str:
        """CHEAP. Check installed dependencies, MCP and optional PRISM build executables.

        Reports capabilities separately. Does not install software, call an LLM API,
        or run a simulation. For a specific docking cascade also use screen_plan.
        """
        from etalon.doctor import diagnose

        interpreter = str(absolute_path(prism_python, label="prism_python")) if prism_python else None
        return ok(**diagnose(prism_python=interpreter))

    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_screen_plan(config_path: str, workspace: str, library_path: str = "",
                           allow_copyleft: bool = False) -> str:
        """CHEAP. Compile a real MolCascade screen; return the input-bound plan_id.

        Checks required backends/assets and hashes input files, without running stages.
        Cost is not estimated/reserved. Review the supplied cascade/library before submit.
        A flat pipeline supplies its own input paths; omit library_path in that case.
        """
        from etalon.screening import plan_screen

        return ok(**plan_screen(absolute_path(config_path, label="config_path"),
                  absolute_path(library_path, label="library_path") if library_path else None,
                  absolute_path(workspace, label="workspace"), allow_copyleft=allow_copyleft))

    @mcp.tool()
    @tool(Cost.SPENDS)
    def etalon_screen_submit(config_path: str, workspace: str, run_id: str, plan_id: str,
                             library_path: str = "", workers: int = 1,
                             devices: list[str] | None = None, allow_copyleft: bool = False) -> str:
        """SPENDS. Submit the reviewed plan once to a detached local worker and return a job ID.

        Launches REAL screening, potentially including docking. Use only within the user's
        authorized compute scope. Repeating the same run_id/input/settings does not resubmit.
        Poll screen_status; client disconnects do not stop the worker. No automatic retry,
        MD, PMF, FEP, active-learning budget debit or cost cap is implied by this tool.
        """
        from etalon.screening import submit_screen

        return ok(job=submit_screen(absolute_path(config_path, label="config_path"),
                  absolute_path(library_path, label="library_path") if library_path else None,
                  absolute_path(workspace, label="workspace"), run_id=run_id,
                  expected_plan_id=plan_id, workers=workers, devices=devices if devices is not None else (),
                  allow_copyleft=allow_copyleft))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_screen_status(workspace: str, run_id: str) -> str:
        """FREE. Read the durable screening job, stage artifacts, errors and log path.

        Does not launch/retry a job. queued/running is last journaled state, not a heartbeat;
        after host/worker failure inspect the log before explicitly recovering downstream work.
        """
        from etalon.screening import screen_status

        return ok(job=screen_status(absolute_path(workspace, label="workspace"), run_id))
