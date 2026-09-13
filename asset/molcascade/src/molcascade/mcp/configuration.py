"""Reading a configuration, and reading what it would do — without running it.

MolCascade accepts two configuration shapes and an agent has to know which it is
holding.  A **cascade** (``schema_version: 2``) is tier-first: it names tiers,
each tier names criteria, each criterion names a backend and its thresholds, and
a tier declares whether every criterion must pass or any one of them will do.  A
**pipeline** (``schema_version: 1``) is the flat stage list a cascade is lowered
into, with explicit port-to-port bindings.  The cascade is what people author and
what the HTML builder emits; the pipeline is what actually executes.  Both are
accepted everywhere a configuration is taken, and :func:`validate_config` reports
which one it found.

Validation here is the same work a run does on its way in, with nothing stopped
and nothing written.  That makes it the cheap way to answer three questions an
agent would otherwise only learn by starting a campaign: does this configuration
parse and compile, does this machine hold the backends and weights it names, and
what funnel would it actually build -- because a criterion whose backend is
missing is dropped silently, so the funnel that runs can be shorter than the one
that was authored.
"""

from __future__ import annotations

from typing import Any

from molcascade.mcp._common import absolute_path, logger, ok, tool


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool
    def validate_config(config_path: str) -> str:
        """Strictly parse and compile a cascade or pipeline, and report the funnel.

        Strict means strict: the configuration models forbid unknown fields, so a
        misspelled threshold is an error naming the field rather than a default
        silently applied.  That is the behaviour an agent most needs from this
        tool -- a config it generated with a plausible-looking but wrong key will
        be refused here instead of running for an hour and producing a number
        under settings nobody chose.

        Compiling resolves every stage to a plugin, checks that each input port
        is bound to an upstream port publishing the contract it requires, and
        assigns the revision id the run would record.  Two things are
        deliberately tolerated: a cascade on disk carries no library and, for a
        docking cascade, no target, because both arrive on the command line at
        screen time.  They are reported as placeholders rather than as errors.

        Args:
            config_path: Absolute path to a JSON or YAML cascade or pipeline.

        Returns:
            JSON with ``schema`` (``cascade`` or ``pipeline``), ``name``,
            ``revision_id`` -- the digest that identifies this exact
            configuration and that a run records, so two runs with the same
            revision id screened the same funnel -- ``enabled_stages``, the
            ``tiers`` with each tier's mode and the criteria that survived
            availability, ``placeholders`` for the library and target, and
            ``environment_notes`` carrying anything this machine cannot satisfy:
            missing engine executables, unprovisioned assets, uninstalled
            packages.  Those notes are informational: a configuration authored on
            a laptop to run on a GPU host is valid even where the laptop cannot
            run it.
        """

        from molcascade.assets import preflight_assets
        from molcascade.backends.preflight import preflight_engine_paths
        from molcascade.cascade import load_screening_config
        from molcascade.cascade.lower import lower_cascade
        from molcascade.errors import AssetError, ConfigError
        from molcascade.pipeline import PipelineCompiler
        from molcascade.plugins import create_builtin_registry

        path = absolute_path(config_path, field="config_path")
        logger.info("Validating %s", path)
        screening = load_screening_config(path)
        registry = create_builtin_registry()

        tiers: list[dict[str, Any]] = []
        placeholders = {"library": False, "target": False}
        if screening.cascade is not None:
            schema = "cascade"
            lowered = lower_cascade(
                screening.cascade,
                registry=registry,
                allow_missing_library=True,
                allow_missing_target=True,
            )
            config = lowered.pipeline
            placeholders = {
                "library": lowered.library_is_placeholder,
                "target": lowered.target_is_placeholder,
            }
            for tier in screening.cascade.tiers:
                tiers.append(
                    {
                        "id": tier.id,
                        "title": tier.title,
                        "mode": tier.mode.value,
                        "enabled": tier.enabled,
                        "note": tier.note,
                        "criteria": [
                            {
                                "id": criterion.id,
                                "criterion": criterion.criterion,
                                "label": criterion.label,
                                "backend": criterion.backend,
                                "enabled": criterion.enabled,
                                "settings": dict(criterion.settings or {}),
                            }
                            for criterion in tier.criteria
                        ],
                    }
                )
        else:
            schema = "pipeline"
            config = screening.pipeline

        compiled = PipelineCompiler(registry).compile(config)

        notes: dict[str, Any] = {}
        try:
            engines = preflight_engine_paths(compiled.stages, registry=registry)
            notes["engines"] = [str(entry) for entry in engines]
        except ConfigError as error:
            notes["engines_unresolved"] = {
                "message": str(error),
                "hint": error.hint,
            }
        try:
            provisioned = preflight_assets(compiled.stages)
            notes["assets"] = [str(entry) for entry in provisioned]
        except AssetError as error:
            notes["assets_unprovisioned"] = {
                "message": str(error),
                "hint": error.hint,
            }

        return ok(
            config_path=str(path),
            schema=schema,
            name=config.name,
            revision_id=compiled.revision.revision_id,
            enabled_stages=len(compiled.stages),
            stages=[
                {
                    "stage_id": stage.stage_id,
                    "plugin": f"{stage.descriptor.id}@{stage.descriptor.version}",
                    "kind": stage.descriptor.kind.value,
                    "cardinality": stage.descriptor.cardinality.value,
                    "determinism": stage.descriptor.determinism.value,
                }
                for stage in compiled.stages
            ],
            tiers=tiers,
            placeholders=placeholders,
            environment_notes=notes,
        )

    @mcp.tool()
    @tool
    def inspect_bundle(bundle_path: str) -> str:
        """Read a model bundle and report the digest that pins it in a config.

        Two bundle kinds exist and they are told apart by which manifest is
        present rather than by a flag: an ONNX graph that MolCascade featurizes
        for (``molcascade_model.yaml``, which declares the representation so the
        adapter can recompute it and check the width against the graph), and a
        Chemprop checkpoint that featurizes for itself.

        This reads and hashes.  It never loads the model, so it is safe to point
        at a bundle before deciding whether to trust it -- which matters because
        a Chemprop checkpoint is a Torch checkpoint and deserialising one
        executes code from the file.  ONNX is loaded as a graph by the runtime
        and needs no such acknowledgement; pickle and joblib are refused outright
        for the same reason.

        Args:
            bundle_path: Absolute path to the bundle directory.

        Returns:
            JSON with the manifest contents, the per-file digests, and the
            ``model_id`` to pin in a cascade.  Pinning it is what makes a swapped
            checkpoint a different measurement rather than a silent one.
        """

        from molcascade.plugins.builtin.chemprop_model import (
            MANIFEST_FILENAME as CHEMPROP_MANIFEST,
        )
        from molcascade.plugins.builtin.chemprop_model import inspect_chemprop_bundle
        from molcascade.plugins.builtin.custom_model import inspect_model_bundle

        directory = absolute_path(bundle_path, field="bundle_path")
        if (directory / CHEMPROP_MANIFEST).is_file():
            kind = "chemprop_checkpoint"
            report = inspect_chemprop_bundle(directory)
        else:
            kind = "onnx_bundle"
            report = inspect_model_bundle(directory)
        payload = (
            report.model_dump(mode="json")
            if hasattr(report, "model_dump")
            else dict(report)
            if isinstance(report, dict)
            else {"report": str(report)}
        )
        return ok(bundle_path=str(directory), bundle_kind=kind, report=payload)

    @mcp.tool()
    @tool
    def generate_builder(output_path: str, pipeline_editor: bool = False) -> str:
        """Write the offline HTML configuration builder to a file.

        The builder is a single self-contained page with no network access: it
        cannot publish artifacts, execute commands, or reach a server.  It embeds
        the same catalogue this server reports and the same shipped defaults a
        run would use, so a configuration downloaded from it is byte-identical to
        the one the Python API constructs -- the default cascade it carries is
        generated from ``default_cascade()`` rather than transcribed, and round
        trips through the strict models unchanged.

        Use it when a person should choose the thresholds.  An agent can author a
        configuration directly and validate it with ``validate_config``; the
        builder exists for the case where the decision is the operator's and they
        want to see the catalogue, the citations and the measured throughput for
        each option while making it.

        Args:
            output_path: Absolute path ending in ``.html``.  Its parent directory
                must already exist.
            pipeline_editor: Emit the flat schema-1 stage graph editor instead of
                the tier-first cascade builder.  Rarely what you want: the
                cascade builder is the current surface, and the pipeline editor
                operates a level below it on explicit port bindings.

        Returns:
            JSON with ``written_to`` and ``bytes``.
        """

        from molcascade.ui.builder import generate_config_builder
        from molcascade.ui.cascade_builder import generate_cascade_builder

        destination = absolute_path(output_path, field="output_path", must_exist=False)
        if destination.suffix.casefold() not in {".html", ".htm"}:
            from molcascade.errors import MolCascadeError

            raise MolCascadeError(
                "output_path must end in .html or .htm",
                code="MCP_OUTPUT_SUFFIX_INVALID",
                context={"path": str(destination)},
            )
        if pipeline_editor:
            written = generate_config_builder(destination)
        else:
            written = generate_cascade_builder(destination)
        return ok(
            written_to=str(written),
            bytes=written.stat().st_size,
            surface="pipeline_editor" if pipeline_editor else "cascade_builder",
        )


__all__ = ["register"]
