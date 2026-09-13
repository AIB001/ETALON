"""Getting the product out of a finished run.

Three tools, and they differ in what they are for rather than in how much they
return.

:func:`export_shortlist` produces the deliverable: the molecules that survived,
verified against the digests the run recorded, written as one file for whatever
comes next.  It is the only tool here that re-hashes what it reads, because it
publishes a product and a product has to be what the run committed rather than
what happens to be on disk now.

:func:`trace_run` produces the working material: one CSV per stage, so every
column a stage published can be read with ordinary tools, plus docked poses as
SDF where a run produced them.  Use it when you want to look at the evidence
rather than the verdict.

:func:`generate_report` produces the document: a single self-contained HTML file
that carries aggregate counts, per-stage provenance and the reasons molecules
left, and **no molecule rows at all**.  That last property is deliberate and
structural rather than a setting -- the page is built with entity ids suppressed
at the source, so a report can be emailed without deciding, per recipient,
whether the library is shareable.
"""

from __future__ import annotations

from typing import Any

from molcascade.mcp._common import absolute_path, logger, ok, open_runner, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool
    def export_shortlist(
        run_id: str,
        workspace: str,
        output_path: str = "",
        overwrite: bool = False,
    ) -> str:
        """Materialise the verified shortlist a run produced.

        Every file behind the shortlist artifact is re-hashed against the
        manifest before a byte is written out, so an export either matches what
        the run committed or fails.  The result carries its own sha256 so a
        downstream tool can record which bytes it consumed.

        A caveat that matters if you are handing this to a simulation stack: the
        default shortlist is a SMILES table.  SMILES carries no coordinates, no
        hydrogens and no chosen protonation state, and a docked pose is in the
        sidecar rather than in the shortlist.  ``docking`` in the result says
        whether a sidecar exists.  Do not feed a coordinate-free record to a
        force-field build and expect the geometry to mean anything.

        Args:
            run_id: The run to export.
            workspace: Absolute path to its workspace.
            output_path: Absolute path for the file.  Omit it and the export
                lands at the run's default location inside the workspace.
            overwrite: Replace an existing file at ``output_path``.  Off by
                default: an export is a product, and silently replacing one is
                how two different shortlists end up with the same name in
                somebody's notes.

        Returns:
            JSON with ``path``, ``record_format``, ``row_count``,
            ``identified_count`` (how many carry a library identifier -- fewer
            than ``row_count`` means the library was read without an id column),
            ``sha256``, ``source_artifact_id``, ``export_spec_id``, and
            ``docking`` describing the pose sidecar when one exists.
        """

        runner = open_runner(workspace)
        destination = (
            absolute_path(output_path, field="output_path", must_exist=False)
            if output_path
            else None
        )
        from molcascade.handoff import materialize_shortlist

        logger.info("Exporting shortlist for %s", run_id)
        result = materialize_shortlist(runner, run_id, destination, overwrite=overwrite)
        docking = result.docking
        return ok(
            run_id=run_id,
            path=str(result.path),
            record_format=result.record_format,
            row_count=result.row_count,
            identified_count=result.identified_count,
            sha256=result.sha256,
            source_artifact_id=result.source_artifact_id,
            export_spec_id=result.export_spec_id,
            docking=(
                None
                if docking is None
                else {
                    "path": str(getattr(docking, "path", "")),
                    "row_count": getattr(docking, "row_count", None),
                    "sha256": getattr(docking, "sha256", None),
                }
            ),
        )

    @mcp.tool()
    @tool
    def trace_run_stages(
        run_id: str,
        workspace: str,
        output_dir: str = "",
        overwrite: bool = False,
    ) -> str:
        """Write one CSV per stage, plus docked poses, into a readable directory.

        This is the tool to reach for when a number is surprising.  Each stage's
        own output ports are joined onto its molecule table, so the CSV for a
        docking stage carries the score beside the molecule, the CSV for an
        applicability stage carries the nearest reference and its similarity, and
        the CSV for a graded gate carries the metric it published.  Those columns
        exist nowhere else in readable form -- the shortlist has none of them.

        Args:
            run_id: The run to trace.
            workspace: Absolute path to its workspace.
            output_dir: Absolute path for the directory.  Omit for the default
                inside the workspace.
            overwrite: Replace an existing directory's contents.

        Returns:
            JSON with ``output_dir`` and one row per stage carrying the stage id,
            the CSV path, its row count, and the pose SDF where one was written.
        """

        runner = open_runner(workspace)
        destination = (
            absolute_path(output_dir, field="output_dir", must_exist=False)
            if output_dir
            else None
        )
        from molcascade.trace import trace_run

        logger.info("Tracing %s", run_id)
        traced = trace_run(runner, run_id, destination, overwrite=overwrite)
        return ok(
            run_id=traced.run_id,
            status=traced.status,
            output_dir=str(traced.path),
            index_path=str(traced.index_path),
            shortlist_sdf=None if not traced.shortlist_sdf else str(traced.shortlist_sdf),
            pose_count=traced.pose_count,
            skipped=list(traced.skipped),
            notes=list(traced.notes),
            stages=[
                {
                    "stage_id": stage.stage_id,
                    "order": stage.order,
                    "slot": stage.slot,
                    "plugin": stage.plugin,
                    "status": stage.status,
                    "tier_id": stage.tier_id,
                    "tier_title": stage.tier_title,
                    "csv_name": stage.csv_name,
                    "sdf_name": stage.sdf_name,
                    "row_count": stage.row_count,
                    "entering": stage.entering,
                    # The columns a stage's own evidence ports contributed. These
                    # exist in no other readable output, which is the reason to
                    # trace rather than export.
                    "evidence_columns": list(stage.evidence_columns),
                }
                for stage in traced.stages
            ],
        )

    @mcp.tool()
    @tool
    def generate_report(
        run_id: str,
        workspace: str,
        output_path: str,
        overwrite: bool = True,
    ) -> str:
        """Write a self-contained HTML report for one run.

        One file, no network, no molecule rows.  It carries the per-stage audit
        with timings and artifact ids, the aggregate funnel, and the reason codes
        every gate recorded -- grouped by stage, with the caveat the page states
        itself: those are decision *rows* rather than molecules, because one
        molecule can fail two rules in one stage and is counted twice.

        The page also embeds the run's own pipeline as editable JSON, so a reader
        can adjust a threshold and download a configuration for the next run.  It
        cannot execute anything and cannot undo a hard rejection; it is a document
        that happens to know what it describes.

        Args:
            run_id: The run to report on.
            workspace: Absolute path to its workspace.
            output_path: Absolute path ending in ``.html`` or ``.htm``.
            overwrite: Replace an existing file.  On by default here, unlike the
                shortlist export: a report is a rendering of immutable state, so
                regenerating one cannot lose anything.

        Returns:
            JSON with ``written_to`` and ``bytes``.
        """

        runner = open_runner(workspace)
        destination = absolute_path(output_path, field="output_path", must_exist=False)
        from molcascade.ui.report import generate_run_report

        written = generate_run_report(runner, run_id, destination, overwrite=overwrite)
        return ok(
            run_id=run_id,
            written_to=str(written),
            bytes=written.stat().st_size,
            molecule_rows_embedded=0,
        )


__all__ = ["register"]
