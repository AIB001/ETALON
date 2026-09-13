"""What this machine can do, and what a run would have to cite.

Five read-only tools.  None of them installs anything, downloads anything, or
writes to the filesystem except where a caller names an output path, and that
property is the point rather than a side effect: an agent deciding whether a
cascade can run here must be able to ask without changing the answer.
"""

from __future__ import annotations

from typing import Any

from molcascade.mcp._common import absolute_path, logger, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool
    def doctor(
        run_version_commands: bool = False,
        allow_copyleft: bool = False,
    ) -> str:
        """Probe every declared backend on this machine without installing anything.

        MolCascade declares 48 non-native backends -- docking engines, ADMET
        models, alert catalogues, clustering libraries, the Lilly Medchem Rules
        engine -- and each is probed the way its interface demands: a Python
        backend by import and distribution metadata, a CLI backend by locating
        its executable.  Nothing is fetched and nothing is installed.

        Call this before planning a cascade.  A criterion whose backend is
        unavailable is silently dropped when the shipped defaults are built, so a
        funnel can come out shorter than intended with no error anywhere, and the
        only way to know in advance is to ask.

        Args:
            run_version_commands: Execute each CLI backend's probe command to
                capture a version string.  Off by default because it runs
                third-party executables; the availability verdict does not need
                it, since presence is decided by locating the file.
            allow_copyleft: Include copyleft-licensed backends in the verdict
                rather than reporting them BLOCKED.  GNINA is GPL-2.0-or-later
                because it links Open Babel, and a run records that permission
                was given.  This flag does not change any licence; it records a
                decision the operator has made.

        Returns:
            JSON with one row per backend: ``id``, ``display_name``,
            ``capability``, ``tier``, ``interface``, ``license_spdx``,
            ``availability`` (AVAILABLE / DEGRADED / UNAVAILABLE / BLOCKED),
            ``version`` when one was captured, ``reason``, and the ``plugin_ref``
            that consumes it.  A null ``plugin_ref`` means the backend is
            declared but no stage plugin consumes it yet -- 17 of the 48 are in
            that state, and it is a roadmap marker rather than a fault.
        """

        from molcascade.backends.catalog import create_backend_registry
        from molcascade.backends.models import ProbePolicy

        logger.info("Probing backends (version_commands=%s)", run_version_commands)
        policy = ProbePolicy(
            allow_copyleft=allow_copyleft,
            run_version_commands=run_version_commands,
        )
        registry = create_backend_registry()
        rows = []
        for status in registry.probe_all(policy=policy):
            spec = status.spec
            rows.append(
                {
                    "id": spec.id,
                    "display_name": spec.display_name,
                    "capability": spec.capability.value,
                    "tier": spec.tier.value,
                    "interface": spec.interface.value,
                    "license_spdx": spec.license_spdx,
                    "license_class": spec.license_class.value,
                    "availability": status.availability.value,
                    "version": status.version,
                    "reason": status.reason,
                    "plugin_ref": spec.plugin_ref,
                    "platforms": list(spec.platforms),
                }
            )
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["availability"]] = counts.get(row["availability"], 0) + 1
        return ok(
            backends=rows,
            summary=counts,
            copyleft_permitted=allow_copyleft,
            version_commands_run=run_version_commands,
        )

    @mcp.tool()
    @tool
    def environment(workspace: str = "", include_gpus: bool = True) -> str:
        """Report the CPU, memory, disk and accelerators this machine actually has.

        Not a recommendation and not a plan: a measurement.  The same figures are
        recorded into a run's RUN_STARTED event so that a throughput number from
        one machine can be read against the machine it came from.

        Args:
            workspace: Absolute path whose filesystem is measured for free disk.
                Optional; omit it and no disk figure is reported.  A cascade that
                keeps docked poses writes gigabytes, so this is the figure that
                decides whether a campaign finishes.
            include_gpus: Query accelerators.  On by default.  Turn it off on a
                host where the probe is slow or where no GPU backend will be
                used; the rest of the report is unaffected.

        Returns:
            JSON with ``platform``, ``cpu`` (physical and logical counts),
            ``memory``, ``disk`` when a workspace was given, ``gpus`` as a list
            of devices with vendor and memory, ``frameworks`` for the ML stacks
            present, and ``execution_plan`` -- the worker and lane counts
            MolCascade would choose here if asked to decide for itself.
        """

        from molcascade.environment import detect_environment

        root = absolute_path(workspace, field="workspace") if workspace else None
        host = detect_environment(workspace=root, include_gpus=include_gpus)
        return ok(environment=host.model_dump(mode="json"))

    @mcp.tool()
    @tool
    def plugins() -> str:
        """List every reviewed built-in stage plugin with its contract signature.

        This is the vocabulary a cascade is written in.  Each row carries the
        plugin's ``id@version``, its kind, the contracts it consumes and
        publishes, its cardinality and its determinism -- and those last two are
        what an orchestrator needs rather than nice to have.

        ``cardinality`` says whether a stage can remove molecules (``filter``),
        must return one row per input (``one_to_one``), may fan out
        (``one_to_many``) or may fold in (``many_to_one``).  ``determinism`` says
        whether re-running it is reproducible: ``deterministic`` stages are cached
        across runs, ``seeded`` ones are cached when a seed is configured, and
        ``best_effort`` or ``non_deterministic`` ones are never cached and always
        recompute.

        Returns:
            JSON with ``plugins`` as a list of descriptors and ``kinds`` as a
            count per plugin kind.  Two kinds are declared with zero
            implementations -- ``trainer`` and ``enumerator`` -- and they are
            reported as zero rather than omitted, because the absence is a fact
            about what this installation can do.
        """

        from molcascade.plugins import PluginKind, create_builtin_registry

        registry = create_builtin_registry()
        rows = []
        by_kind: dict[str, int] = {kind.value: 0 for kind in PluginKind}
        for entry in registry.entries():
            descriptor = entry.descriptor
            by_kind[descriptor.kind.value] = by_kind.get(descriptor.kind.value, 0) + 1
            rows.append(
                {
                    "key": f"{descriptor.id}@{descriptor.version}",
                    "id": descriptor.id,
                    "version": descriptor.version,
                    "kind": descriptor.kind.value,
                    "display_name": descriptor.display_name,
                    "description": descriptor.description,
                    "inputs": list(descriptor.inputs),
                    "outputs": list(descriptor.outputs),
                    "output_ports": dict(descriptor.output_ports),
                    "cardinality": descriptor.cardinality.value,
                    "determinism": descriptor.determinism.value,
                    "api_version": descriptor.api_version,
                    "trusted": entry.trusted,
                    "origin": entry.origin,
                }
            )
        rows.sort(key=lambda row: row["key"])
        return ok(plugins=rows, kinds=by_kind, total=len(rows))

    @mcp.tool()
    @tool
    def citations(output_path: str = "") -> str:
        """Every reference a screen built from this catalogue would have to cite.

        One entry per tool, method and rule set the builder can put into a
        cascade, rendered as the text a methods section needs.  An agent that
        reports a result is obliged to say what produced it, and this is the
        list; it is derived from the catalogue rather than maintained by hand, so
        it cannot fall behind the options a run can actually select.

        Args:
            output_path: Absolute path to write the rendered Markdown to.
                Optional.  The text is returned either way; this only also puts
                it on disk.

        Returns:
            JSON with ``citations`` as the rendered document, ``uncited`` listing
            any catalogue option that carries no reference, and ``written_to``
            when a path was given.  A non-empty ``uncited`` is a gap in the
            catalogue, not in this tool.
        """

        from molcascade.cascade.citations import render_citations, uncited_options

        document = render_citations()
        written = None
        if output_path:
            destination = absolute_path(output_path, field="output_path", must_exist=False)
            destination.write_text(document, encoding="utf-8")
            written = str(destination)
        return ok(
            citations=document,
            uncited=[
                {"criterion": criterion, "option": option}
                for criterion, option in uncited_options()
            ],
            written_to=written,
        )

    @mcp.tool()
    @tool
    def assets(asset_id: str = "", deep: bool = False) -> str:
        """Report which vendored model weights and rule tables are present.

        Several backends need a payload that is not code: SCScore's published
        weights, the rd_filters alert table, ADMET model checkpoints.  MolCascade
        pins each by digest and verifies it on every run, so a stage refuses to
        start rather than scoring molecules against a table that has changed
        underneath it.

        This tool only inspects.  It does not fetch -- fetching reaches the
        network, and an agent should not acquire a multi-megabyte payload as a
        side effect of asking whether it is there.  Run ``molcascade assets
        fetch <id>`` yourself when a status comes back missing.

        Args:
            asset_id: One asset to report on.  Omit for all of them.
            deep: Re-hash every file rather than trusting recorded sizes.  Slower
                and the only check that detects a file that was modified in
                place without changing length.

        Returns:
            JSON with one row per asset: ``id``, ``kind``, ``state`` (READY /
            MISSING / CORRUPT), the files it expects with per-file status, the
            total bytes, its licence, and the citation the payload carries.
        """

        from molcascade.assets import BUILTIN_ASSET_SPECS, asset_spec, asset_status

        specs = [asset_spec(asset_id)] if asset_id else list(BUILTIN_ASSET_SPECS)
        rows = []
        for spec in specs:
            status = asset_status(spec, deep=deep)
            rows.append(
                {
                    "id": spec.id,
                    "kind": spec.kind.value,
                    "display_name": spec.display_name,
                    "state": status.state.value,
                    "version": spec.version,
                    "license_spdx": spec.license_spdx,
                    "trust": spec.trust.value,
                    "homepage": spec.homepage,
                    "used_by": list(spec.used_by),
                    "root": str(status.root),
                    "total_bytes": sum(entry.size_bytes for entry in spec.files),
                    "files": [
                        {
                            "name": entry.name,
                            "size_bytes": entry.size_bytes,
                            "sha256": entry.sha256,
                        }
                        for entry in spec.files
                    ],
                    "file_states": [
                        {"name": name, "state": getattr(state, "value", str(state))}
                        for name, state in sorted(status.files.items())
                    ]
                    if isinstance(status.files, dict)
                    else [
                        getattr(entry, "value", str(entry)) for entry in status.files
                    ],
                    "citations": [
                        dict(citation) if isinstance(citation, dict) else str(citation)
                        for citation in spec.citations
                    ],
                }
            )
        return ok(assets=rows, deep=deep)


__all__ = ["register"]
