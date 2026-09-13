"""Check every Python package a run will import before the run reads a molecule.

This is the package half of :mod:`molcascade.assets.preflight`, and it exists
because the two halves behaved differently in a way no user could have
predicted.  A cascade whose synthesis tier wants SCScore weights fails
immediately, before any work happens, naming the ``molcascade assets fetch``
that fixes it.  The same cascade with a medchem tier and no medchem installed
ran chemistry, physicochemistry and drug-likeness over the whole library first
and only then raised ``MEDCHEM_BACKEND_UNAVAILABLE`` -- because
``import_medchem()`` is called inside ``execute()``, which is to say when
execution reaches the stage.  Measured on a three-molecule library that already
left a run directory and six cache entries behind; on the library sizes this
tool is built for it is hours of compute and a funnel that has to be discarded.

Nothing here imports the backend.  ``importlib.util.find_spec`` answers "would
this import succeed" without running module-level code, which matters because
some of these packages pull torch in on import and take seconds to do it.

The mapping needed no new bookkeeping.  A compiled stage names its plugin as
``id@version``; :attr:`BackendSpec.plugin_ref` is that same string; and the spec
already carries the ``module`` to probe and the ``distribution`` to install,
because :command:`molcascade doctor` has always used them.  So the chain is
stage -> plugin_ref -> spec -> module, and a cascade the project has never seen
is covered as long as its plugin is in the catalogue.  One that is not is left
alone: an unknown third-party plugin has unknown dependencies, and guessing at
them would turn a preflight into a reason a working run refuses to start.

Nothing here installs anything either.  MolCascade does not run pip; it reports
what is missing and the exact command a human can decide to run.

:func:`preflight_engine_paths` is the same idea for the software that is *not*
a package.  Where an out-of-process engine is installed is a fact about a
machine, so a portable cascade leaves it out and three things answer in turn --
the config, ``MOLCASCADE_<ENGINE>_<FIELD>``, and the layout
``envs/bootstrap.sh`` creates.  That resolution happens in config validation,
where it can supply a value but must not fail; deciding whether the answer is
really there belongs here, on the host, once, before any molecule is read.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue

from molcascade.backends.catalog import BUILTIN_BACKEND_SPECS
from molcascade.backends.models import (
    Availability,
    BackendSpec,
    BackendStatus,
    BackendTier,
    LicenseClass,
    ProbePolicy,
)
from molcascade.backends.registry import probe_backend
from molcascade.errors import ConfigError, PluginError
from molcascade.plugins.builtin._machine_paths import environment_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from molcascade.config.models import StageConfig
    from molcascade.pipeline.compiler import CompiledStage
    from molcascade.plugins.registry import PluginRegistry


def _index_by_plugin_ref(
    specs: Sequence[BackendSpec],
) -> dict[str, BackendSpec]:
    """Map ``plugin_ref`` to the one spec that decides what to install for it.

    Several backends may share a plugin_ref -- ``rdkit.filtercatalog`` and
    ``rdkit.smarts`` are both ``chemistry.rdkit_hard_gate`` -- which is fine as
    long as they agree on the module, since then the requirement is the same
    whichever one the stage's settings end up selecting.  Where they would
    disagree the answer depends on settings this module deliberately does not
    interpret, so the ref is dropped and the adapter keeps its existing
    fail-closed behaviour.  No shipped ref disagrees; the check is here so that
    adding one that does degrades quietly instead of reporting the wrong
    package.
    """

    index: dict[str, BackendSpec] = {}
    ambiguous: set[str] = set()
    for spec in specs:
        ref = spec.plugin_ref
        if ref is None:
            continue
        seen = index.get(ref)
        if seen is None:
            index[ref] = spec
        elif seen.module != spec.module:
            ambiguous.add(ref)
    for ref in ambiguous:
        del index[ref]
    return index


_BY_PLUGIN_REF: dict[str, BackendSpec] = _index_by_plugin_ref(BUILTIN_BACKEND_SPECS)


def required_backends(
    stages: Sequence[CompiledStage],
) -> tuple[tuple[str, BackendSpec], ...]:
    """Return ``(stage_id, spec)`` for every stage backed by a catalogued tool.

    The stage id travels with the spec for the same reason it does in the asset
    preflight: "the run needs medchem" is a fact about the cascade, but "the
    t4_alerts_medchem stage needs medchem" tells the user which block to drop if
    they would rather not install it.

    Order follows execution order and duplicates are kept, so a caller can see
    that two tiers share one package.
    """

    found: list[tuple[str, BackendSpec]] = []
    for stage in stages:
        spec = _BY_PLUGIN_REF.get(stage.plugin_key)
        if spec is not None:
            found.append((stage.stage_id, spec))
    return tuple(found)


def copyleft_backends(
    stages: Sequence[CompiledStage],
) -> tuple[BackendSpec, ...]:
    """The copyleft tools this pipeline will execute, deduplicated, in order.

    Answering "which ones, exactly" rather than "are there any" is the whole
    point.  A run permitted to link copyleft code has to be able to name what it
    linked -- in the audit log while it happens, and in a methods section
    afterwards -- and "the operator passed --allow-copyleft" is not that.
    """

    found: list[BackendSpec] = []
    for _stage_id, spec in required_backends(stages):
        if spec.license_class is not LicenseClass.COPYLEFT:
            continue
        if all(known.id != spec.id for known in found):
            found.append(spec)
    return tuple(found)


def _supplying_variable(spec: BackendSpec, key: str, configured: str) -> str | None:
    """Name the environment variable this path came from, if it came from one.

    An engine path may be answered once per machine by
    ``MOLCASCADE_<ENGINE>_<FIELD>`` instead of being written into every cascade,
    which means the path in a failure below can be one the operator will not find
    anywhere in the file they are reading.  Saying where it came from is the
    difference between a fixable message and a confusing one.

    Claimed only when the variable's value *is* the configured value, so there is
    nothing to get wrong: a backend whose id does not match the config model's
    engine id simply has no variable to find, and this says nothing rather than
    something untrue.
    """

    variable = environment_key(spec.id, key)
    supplied = os.environ.get(variable, "").strip()
    if not supplied:
        return None
    return variable if str(Path(supplied).expanduser()) == configured.strip() else None


def _isolated_status(
    spec: BackendSpec,
    config: Mapping[str, object],
) -> BackendStatus:
    """Check an isolated backend where it actually lives: the stage's own config.

    ``shutil.which`` is the right probe for every other CLI backend and the
    wrong one here.  An isolated backend is isolated *because* its dependency
    set conflicts with this environment's, so its ``bin`` directory is not on
    this process's ``PATH`` -- and if it were, a PATH lookup would be finding
    the very installation that must not be imported.  What decides whether the
    run can proceed is the absolute path the stage was configured with, so that
    is what gets stat'ed, and ``executable_config_key`` says which key holds it
    without this module having to know one backend's vocabulary.
    """

    key = spec.executable_config_key
    assert key is not None  # guarded by the caller
    evidence: dict[str, JsonValue] = {
        "config_key": key,
        "tier": str(spec.tier),
    }
    configured = config.get(key)
    if not isinstance(configured, str) or not configured.strip():
        return BackendStatus(
            spec=spec,
            availability=Availability.UNAVAILABLE,
            reason=f"the stage does not say where to find {spec.command[0]} ('{key}' is unset)",
            evidence=evidence,
        )
    path = Path(configured).expanduser()
    evidence["executable"] = str(path)
    variable = _supplying_variable(spec, key, str(path))
    origin = f" (from {variable})" if variable else ""
    if variable:
        evidence["environment_variable"] = variable
    if not path.is_absolute():
        return BackendStatus(
            spec=spec,
            availability=Availability.UNAVAILABLE,
            reason=(
                f"'{key}' must be an absolute path, not a command name to resolve on PATH{origin}"
            ),
            evidence=evidence,
        )
    try:
        info = path.stat()
    except OSError:
        return BackendStatus(
            spec=spec,
            availability=Availability.UNAVAILABLE,
            reason=f"nothing exists at the configured '{key}': {path}{origin}",
            evidence=evidence,
        )
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        return BackendStatus(
            spec=spec,
            availability=Availability.UNAVAILABLE,
            reason=f"the configured '{key}' is not an executable file: {path}{origin}",
            evidence=evidence,
        )
    return BackendStatus(
        spec=spec,
        availability=Availability.AVAILABLE,
        reason=f"isolated executable found at the configured path{origin}",
        evidence=evidence,
    )


def _missing_data_file(
    spec: BackendSpec,
    config: Mapping[str, object],
) -> BackendStatus | None:
    """Fail a stage now if it names a data file that is not there.

    See :attr:`BackendSpec.data_file_config_keys` for why this cannot live in
    the config validator.  Blank and absent are both left alone: whether a
    missing value is legal is the plugin's judgement, not this module's.
    """

    for key in spec.data_file_config_keys:
        configured = config.get(key)
        if not isinstance(configured, str) or not configured.strip():
            continue
        path = Path(configured.strip()).expanduser()
        if path.is_file():
            continue
        return BackendStatus(
            spec=spec,
            availability=Availability.UNAVAILABLE,
            reason=f"nothing readable at the configured '{key}': {path}",
            evidence={"config_key": key, "path": str(path)},
        )
    return None


def _install_command(missing: Sequence[BackendSpec]) -> str | None:
    """The single pip command that would satisfy every missing package."""

    extras = sorted({spec.extra for spec in missing if spec.extra})
    loose = sorted(
        {
            spec.distribution
            for spec in missing
            if spec.distribution and not spec.extra
        }
    )
    parts: list[str] = []
    if extras:
        # Quoted because a bare bracket is a glob in most shells.  One command
        # with every extra, not one per package: provisioning in a
        # install-restart-install loop is the same delay this check removes.
        parts.append(f'"molcascade[{",".join(extras)}]"')
    parts.extend(loose)
    if not parts:
        return None
    return f"pip install {' '.join(parts)}"


def preflight_backends(
    stages: Sequence[CompiledStage],
    *,
    policy: ProbePolicy | None = None,
) -> tuple[str, ...]:
    """Verify every catalogued package the compiled ``stages`` will import.

    Returns the distinct backend ids that were checked and are usable, so a
    caller can report what it confirmed rather than only what it rejected.

    Raises :class:`~molcascade.errors.PluginError` naming *all* unusable
    backends at once.  ``DEGRADED`` is not a failure: it means the module
    imports but its distribution metadata could not be read, which is a
    reporting gap, not a missing package, and refusing to run over it would
    block conda-installed and vendored builds for no benefit.
    """

    # Probing a version command means running a subprocess, which a preflight
    # has no business doing on the way into a run.  Availability of the shipped
    # backends never depends on it: every registered adapter is a Python import.
    selected_policy = policy or ProbePolicy(run_version_commands=False)

    ready: list[str] = []
    problems: list[str] = []
    missing: list[BackendSpec] = []
    blocked: list[BackendSpec] = []
    configs = {stage.stage_id: stage.config for stage in stages}
    # Backends whose only problem is a data file the stage named and nobody put
    # there.  Kept apart from the rest because the remedies do not overlap: no
    # pip install produces someone's lead set, and telling them to run one sends
    # them to fix an installation that is already correct.
    data_file_gaps: set[str] = set()
    for stage_id, spec in required_backends(stages):
        status = probe_backend(spec, selected_policy)
        if (
            spec.tier is BackendTier.ISOLATED
            and spec.executable_config_key is not None
            and status.availability is not Availability.BLOCKED
        ):
            # A licence or platform refusal stands whatever the stage says; only
            # the "is it installed" half of the verdict moves off PATH.
            status = _isolated_status(spec, configs.get(stage_id, {}))
        if spec.data_file_config_keys and status.availability in (
            Availability.AVAILABLE,
            Availability.DEGRADED,
        ):
            # Only when the backend itself is otherwise fine, so that a genuinely
            # uninstalled package keeps its own diagnosis and its pip command.
            gap = _missing_data_file(spec, configs.get(stage_id, {}))
            if gap is not None:
                status = gap
                data_file_gaps.add(spec.id)
        if status.availability in (Availability.AVAILABLE, Availability.DEGRADED):
            if spec.id not in ready:
                ready.append(spec.id)
            continue
        problems.append(f"  {stage_id} ({spec.display_name}): {status.reason}")
        if all(known.id != spec.id for known in missing):
            missing.append(spec)
            if status.availability is Availability.BLOCKED:
                blocked.append(spec)

    if problems:
        detail = "\n".join(problems)
        hint = (
            "MolCascade never installs anything during a screening run, so "
            "install these first, then start the run again. Removing the block "
            "from the cascade is the other way out."
        )
        # A licence refusal is not a missing package, and telling someone to pip
        # install their way out of it sends them to fix something that is not
        # broken.  These are separated out of both instructions below.
        refused = {spec.id for spec in blocked}
        installable = [
            spec for spec in missing if spec.id not in refused and spec.id not in data_file_gaps
        ]
        command = _install_command(installable)
        if command:
            hint = f"{command}; {hint}"
        if not installable:
            hint = "Removing the block from the cascade is the other way out."
        if data_file_gaps:
            note = (
                "Point the stage at a file that exists, or remove the block: "
                "that is a path in the cascade, not a missing installation."
            )
            # Keep the pip command when something really is uninstalled too;
            # replace the install advice outright when nothing is.
            hint = f"{note} {hint}" if installable else note
        if blocked:
            names = ", ".join(
                f"{spec.display_name} ({spec.license_spdx})" for spec in blocked
            )
            hint = (
                "Refused by this installation's licence policy, not missing from "
                f"it: {names}. Pass '--allow-copyleft' if your project accepts "
                "those terms -- the run records that it was given, so a methods "
                f"section can say so. {hint}"
            )
        isolated = [spec for spec in installable if spec.tier is BackendTier.ISOLATED]
        if isolated:
            # These are not pip installs into this environment -- installing one
            # here is the thing that would break the run -- so the instruction is
            # different in kind, not just in wording.
            names = ", ".join(spec.display_name for spec in isolated)
            hint = (
                f"{names} runs in an environment of its own because its dependencies "
                "conflict with this one's. Create that environment separately, then "
                "give the stage the absolute path to the executable inside it. "
                f"{hint}"
            )
        raise PluginError(
            f"{len(problems)} stage(s) need a backend that is not usable on "
            f"this machine:\n{detail}",
            code="BACKEND_PREFLIGHT_FAILED",
            hint=hint,
            context={"backends": [spec.id for spec in missing]},
        )
    return tuple(ready)


#: Every field a docking engine can learn from the cascade's target block, and
#: the run-time flag that supplies it.  A stage missing one of these has to be
#: told which flag fixes it; "receptor_pdbqt_path is required" is a sentence
#: about a Pydantic model, not about anything the operator can type.
TARGET_SETTING_FLAGS: Mapping[str, str] = MappingProxyType(
    {
        "receptor_path": "--receptor",
        "receptor_pdbqt_path": "--receptor-pdbqt",
        "reference_ligand_path": "--reference-ligand",
        "pocket_pdb_path": "--pocket",
        "center_x": "--box",
        "center_y": "--box",
        "center_z": "--box",
        "size_x": "--box",
        "size_y": "--box",
        "size_z": "--box",
    }
)

_SITE_FORMS = (
    "--reference-ligand LIGAND.sdf (a ligand bound in the site; its extent "
    "becomes the box), --pocket POCKET.pdb (the residues lining it), or "
    "--box cx,cy,cz,sx,sy,sz (the six numbers directly)"
)

#: What to type when a cascade carries no target.  Shared by the run-time
#: refusal below and by ``validate``, which reports the same gap without failing
#: -- one sentence, so the two can never come to describe different flags.
TARGET_RUN_TIME_HINT = (
    f"Pass '--receptor RECEPTOR.pdb' together with one binding-site definition: {_SITE_FORMS}."
)


def _installed_paths(plugin: object) -> tuple[Any, ...]:
    """What one plugin needs installed on this machine, or nothing.

    A config model that declares ``installed_paths`` is a model whose adapter
    calls out to software living somewhere else.  Asking the model rather than
    consulting a table here means an out-of-tree plugin participates for free,
    and means the places worth looking are declared next to the field they fill
    instead of in a registry that can fall behind it.
    """

    model = getattr(plugin, "config_model", None)
    declared = getattr(model, "installed_paths", None)
    if not callable(declared):
        return ()
    try:
        paths = declared()
    except Exception:  # pragma: no cover - a plugin's own bug, not a run's
        return ()
    return tuple(paths) if isinstance(paths, Sequence) else ()


def _path_gap(spec: Any, configured: object) -> str | None:
    """Say what is wrong with one installation path, or ``None`` if nothing is.

    Blank is the interesting case and it is reported first, because it is the
    one an operator arrives at by doing everything right: the builder cannot
    know where an engine lives on a machine it is not running on, so it leaves
    the box empty, and this is where that empty box is answered for.
    """

    searched = ", ".join(str(candidate) for candidate in spec.candidates)
    if not isinstance(configured, str) and isinstance(configured, Sequence):
        # A path a field may hold more than once -- one model directory per
        # ensemble member -- is checked entry by entry, and every complaint is
        # reported together for the same reason the whole preflight is: fixing
        # one path only to be told about the next is the loop this exists to
        # remove.  An empty list falls through to the blank case below, which is
        # the correct reading of "the builder could not answer this".
        entries = [str(entry) for entry in configured if str(entry).strip()]
        if entries:
            found = [gap for gap in (_path_gap(spec, entry) for entry in entries) if gap]
            return "; ".join(found) if found else None
        configured = ""
    if not isinstance(configured, str) or not configured.strip():
        looked = f" Looked in: {searched}." if searched else ""
        return (
            f"{spec.label} was not found on this machine, and the cascade does not "
            f"name it ('{spec.field}' is blank).{looked}"
        )
    path = Path(configured.strip()).expanduser()
    if not path.exists():
        return f"nothing exists at the configured '{spec.field}': {path}"
    if spec.kind == "directory":
        if not path.is_dir():
            return f"the configured '{spec.field}' is not a directory: {path}"
        absent = [item for item in spec.contents if not (path / item).exists()]
        if absent:
            return f"{path} is not a complete {spec.label}: it is missing {', '.join(absent)}"
        return None
    if not path.is_file():
        return f"the configured '{spec.field}' is not a regular file: {path}"
    if spec.kind == "executable" and not os.access(path, os.X_OK):
        return f"the configured '{spec.field}' is not executable by this user: {path}"
    return None


def preflight_engine_paths(
    stages: Sequence[CompiledStage],
    *,
    registry: PluginRegistry,
) -> tuple[str, ...]:
    """Settle where every out-of-process engine is installed, before the run starts.

    A cascade is authored in a browser and run somewhere else, so the machine
    paths in it are the one thing the authoring machine cannot fill in.  Leaving
    them blank is therefore correct, and this is where blank stops being an open
    question: by the time a stage is compiled, the path has been supplied by the
    config, by ``MOLCASCADE_<ENGINE>_<FIELD>`` or by looking where
    ``envs/bootstrap.sh`` puts things -- and if none of those answered, the run
    refuses here rather than at the docking tier some hours in.

    Every gap is reported at once, with what was searched and the command that
    provisions it.  One at a time would turn setting a machine up into an
    install-restart-install loop, which is the same delay the check removes.

    Returns the ids of the stages whose installation it confirmed.
    """

    checked: list[str] = []
    problems: list[str] = []
    remedies: list[str] = []
    gaps: list[JsonValue] = []
    for stage in stages:
        try:
            plugin = registry.entry(stage.plugin_key).plugin
        except Exception:
            # An unregistered plugin is the compiler's problem to report, and
            # guessing at its requirements here would turn a preflight into a
            # reason a working run refuses to start.
            continue
        specs = _installed_paths(plugin)
        if not specs:
            continue
        # Reached the same way as in ``_installed_paths``: the protocol a plugin
        # satisfies does not promise a config model, so asking for one directly
        # would be a type error waiting on an out-of-tree plugin to prove it.
        model = getattr(plugin, "config_model", None)
        engine_id = str(getattr(model, "engine_id", "") or "")
        stage_ok = True
        for spec in specs:
            gap = _path_gap(spec, stage.config.get(spec.field))
            if gap is None:
                continue
            stage_ok = False
            problems.append(f"  {stage.stage_id}: {gap}")
            variable = spec.variable(engine_id) if engine_id else ""
            if variable:
                problems.append(
                    f"      answer it once for this machine: export {variable}=/path/to/it"
                )
            if spec.remedy and spec.remedy not in remedies:
                remedies.append(spec.remedy)
            if spec.note:
                problems.append(f"      {spec.note}")
            gaps.append(
                {
                    "stage": stage.stage_id,
                    "field": str(spec.field),
                    "variable": variable,
                    "remedy": str(spec.remedy),
                }
            )
        if stage_ok:
            checked.append(stage.stage_id)

    if problems:
        detail = "\n".join(problems)
        hint = (
            "These are installation paths, not screening settings, so they do not "
            "belong in the cascade: export the variables above once on this machine "
            "and the same cascade.json runs here unchanged."
        )
        if remedies:
            installer = "; ".join(remedies)
            hint = f"Not installed yet? Run: {installer}. {hint}"
        raise ConfigError(
            f"{len(gaps)} installation path(s) this run needs are not resolved on "
            f"this machine:\n{detail}",
            code="ENGINE_PATH_UNRESOLVED",
            hint=hint,
            context={"gaps": gaps},
        )
    return tuple(checked)


def _docking_config_model(plugin: object) -> type[Any] | None:
    """The config model of a plugin that docks, or ``None`` for everything else.

    A plugin that declares somewhere to find a receptor is a plugin that docks
    against one.  That is the same marker lowering uses to decide where to write
    the target, so the two cannot drift into disagreeing about which stages the
    requirement applies to.
    """

    model = getattr(plugin, "config_model", None)
    fields = getattr(model, "model_fields", None)
    if not isinstance(fields, Mapping) or "receptor_path" not in fields:
        return None
    return cast("type[Any]", model)


def preflight_docking_target(
    stages: Sequence[StageConfig],
    *,
    registry: PluginRegistry,
) -> tuple[str, ...]:
    """Refuse to start a docking run that has not been told what to dock into.

    This runs on the *lowered* stage list rather than the compiled one, and
    deliberately so.  Compilation validates each stage against its plugin's
    config model, so a docking stage with no receptor already fails -- with
    ``Field required [type=missing, input_value=...]``, three tiers of Pydantic
    context, and no mention of the flag that would fix it.  Getting in front of
    that is the entire point.

    A flat pipeline that spells the receptor into its stage settings by hand
    passes untouched: the check is that the stage *has* what it needs, not that
    a cascade-level target supplied it.

    Returns the ids of the docking stages it checked, so a caller can say what
    it confirmed rather than only what it rejected.
    """

    # Imported here rather than at module scope so that checking packages stays
    # a cheap thing to do.  This module is loaded by 'doctor' and by the
    # builder's availability report, neither of which should pay for the
    # chemistry stack; the docking helpers are only needed once a cascade
    # actually contains an engine, by which point the caller holds a registry
    # and the plugin tree is loaded anyway.
    from molcascade.plugins.builtin.docking.common import (
        require_pdb_receptor,
        structure_digest,
    )

    checked: list[str] = []
    for stage in stages:
        try:
            plugin = registry.entry(stage.plugin).plugin
        except Exception:
            # An unregistered plugin is the compiler's problem to report, and
            # guessing at its requirements here would turn a preflight into a
            # reason a working run refuses to start.
            continue
        model = _docking_config_model(plugin)
        if model is None:
            continue
        checked.append(stage.id)
        missing = [
            name
            for name, field_info in model.model_fields.items()
            if name in TARGET_SETTING_FLAGS
            and field_info.is_required()
            and name not in stage.config
        ]
        if "receptor_path" in missing:
            raise ConfigError(
                f"stage {stage.id!r} docks against a protein and this run was not given one",
                code="DOCKING_TARGET_RECEPTOR_REQUIRED",
                hint=TARGET_RUN_TIME_HINT,
                context={"stage": stage.id, "plugin": stage.plugin},
            )
        if missing:
            flags = sorted({TARGET_SETTING_FLAGS[name] for name in missing})
            raise ConfigError(
                f"stage {stage.id!r} has a receptor but no binding site it can use",
                code="DOCKING_TARGET_SITE_REQUIRED",
                hint=(
                    f"This engine needs {', '.join(flags)}. A binding site is named "
                    f"exactly once, as one of: {_SITE_FORMS}."
                ),
                context={
                    "stage": stage.id,
                    "plugin": stage.plugin,
                    "missing_settings": missing,
                    "missing_flags": flags,
                },
            )

        receptor = str(stage.config["receptor_path"])
        require_pdb_receptor(
            receptor,
            engine=stage.plugin,
            hint=(
                "Convert the structure to PDB before screening. MolCascade repairs a "
                "PDB and names every change it makes, but it does not convert between "
                "formats, so this conversion is yours to check."
            ),
        )
        _, digest = structure_digest(
            receptor,
            code="DOCKING_RECEPTOR_UNREADABLE",
            hint=(
                "This is the receptor for the docking tier, read once here so a "
                "run cannot discover it is unreadable three tiers deep."
            ),
        )
        pinned = stage.config.get("receptor_sha256")
        if pinned is not None and pinned != digest:
            raise ConfigError(
                f"stage {stage.id!r} pins a receptor digest that these bytes do not match",
                code="DOCKING_RECEPTOR_DIGEST_MISMATCH",
                hint=(
                    "Every docking score records the receptor it was computed "
                    "against. Either restore the structure this cascade was written "
                    "for, or update 'target.receptor_sha256' and accept that the new "
                    "scores are not comparable with the old ones."
                ),
                context={
                    "stage": stage.id,
                    "receptor_path": receptor,
                    "expected_sha256": pinned,
                    "actual_sha256": digest,
                },
            )
    return tuple(checked)


@dataclass(frozen=True, slots=True)
class PreflightAdvisory:
    """Something worth saying at run start that is not a reason to refuse.

    Deliberately not carrying a severity.  Both advisories below describe the
    *ordinary* state of an ordinary input, and inventing a rank for two things
    that share one would only make the next reader argue about which rank a
    third belongs in.
    """

    code: str
    stage_id: str
    message: str
    detail: str
    evidence: dict[str, JsonValue]

    def as_dict(self) -> dict[str, JsonValue]:
        return {
            "code": self.code,
            "stage_id": self.stage_id,
            "message": self.message,
            "detail": self.detail,
            "evidence": dict(self.evidence),
        }


#: Bytes of a receptor scanned when counting hydrogens.  A PDB records one atom
#: per line and element in columns 77-78, so this is a line scan, not a parse --
#: bringing a structure toolkit into preflight to answer "are there any H rows"
#: would cost more than the check is worth.
_HYDROGEN_SCAN_LIMIT = 256 * 1024 * 1024


def _counts_hydrogens(path: Path) -> bool | None:
    """Whether a PDB carries any explicit hydrogen atom, or ``None`` if unknown.

    ``None`` on any read failure: the blocking checks above have already read
    and hashed this file, so a failure here is a race rather than a diagnosis,
    and an advisory has no business turning one into a verdict.
    """

    try:
        if path.stat().st_size > _HYDROGEN_SCAN_LIMIT:
            return None
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith(("ATOM  ", "HETATM")):
                    continue
                element = line[76:78].strip().upper()
                if element == "H":
                    return True
                if not element and len(line) > 12:
                    # Pre-element-column files: fall back to the atom name, whose
                    # first non-digit is the element for hydrogens named 1HB, HA
                    # and so on.
                    name = line[12:16].strip().lstrip("0123456789").upper()
                    if name.startswith("H"):
                        return True
    except OSError:
        return None
    return False


def preflight_docking_advisories(
    stages: Sequence[StageConfig],
    *,
    registry: PluginRegistry,
) -> tuple[PreflightAdvisory, ...]:
    """Say two things about a docking run that nothing else says, and never refuse.

    **A receptor with no hydrogens.**  ``cascade/receptor.py`` repairs a
    structure -- it rebuilds missing side-chain atoms, drops waters and
    heterogens, converts modified residues -- and its ``_repair`` path never
    calls ``addMissingHydrogens``.  Meanwhile ``docking/gnina.py`` states that
    GNINA wants "a protein PDB with hydrogens", and ``require_pdb_receptor``
    checks only the suffix.  So each engine applies its own protonation, by its
    own undocumented rule, and two engines in one tier need not agree.

    This is the normal state of a structure downloaded from the PDB, not a
    defect in the operator's input, and the advisory says so: it exists to make
    an invisible decision visible, not to imply a mistake.  It can never block,
    because a warning that fires on nearly every run and can stop one is a
    warning people learn to pass a flag to silence.

    **Two engines, two geometries.**  A tier that runs an engine declaring
    ``ligand_conformer/v1`` beside one that does not is running two independent
    embeddings.  KarmaDock declares ``inputs=(PARENT_V1.id,)`` and builds its own
    geometry from ``parent_smiles``, so for a molecule whose stereocentres its
    SMILES leaves undefined the two engines can be scoring different
    stereoisomers -- and a consensus whose purpose is to make disagreement
    visible would then be hiding one.  Judged by declared contract rather than by
    engine name, because the contract is what lowering binds.
    """

    from molcascade.contracts import LIGAND_CONFORMER_V1

    advisories: list[PreflightAdvisory] = []
    conformer_readers: list[str] = []
    own_geometry: list[str] = []
    for stage in stages:
        # A disabled stage is dropped at compile time.  An audit record
        # describing something that will not happen is worse than none.
        if not stage.enabled:
            continue
        try:
            entry = registry.entry(stage.plugin)
        except Exception:
            continue
        plugin = entry.plugin
        if _docking_config_model(plugin) is None:
            continue
        descriptor = getattr(plugin, "descriptor", None)
        inputs = () if descriptor is None else tuple(descriptor.inputs)
        (conformer_readers if LIGAND_CONFORMER_V1.id in inputs else own_geometry).append(
            stage.id
        )

        configured = stage.config.get("receptor_path")
        if not configured:
            continue
        carries = _counts_hydrogens(Path(str(configured)))
        if carries is False:
            advisories.append(
                PreflightAdvisory(
                    code="DOCKING_RECEPTOR_WITHOUT_HYDROGENS",
                    stage_id=stage.id,
                    message=(
                        f"{stage.id}: the receptor carries no explicit hydrogens, so "
                        f"{stage.plugin} will add them by its own rule"
                    ),
                    detail=(
                        "This is the ordinary state of a structure as deposited, not a "
                        "problem with the file. MolCascade repairs a receptor but never "
                        "assigns protonation, so each engine decides for itself and two "
                        "engines in one tier need not decide alike. Protonate the "
                        "structure yourself and pass it with '--receptor' if the "
                        "decision matters to this target."
                    ),
                    evidence={"receptor_path": str(configured), "plugin": stage.plugin},
                )
            )

    if conformer_readers and own_geometry:
        advisories.append(
            PreflightAdvisory(
                code="DOCKING_MIXED_GEOMETRY_SOURCES",
                stage_id=own_geometry[0],
                message=(
                    "this run docks the same molecules from two independent geometries: "
                    f"{', '.join(conformer_readers)} read the shared conformers, "
                    f"{', '.join(own_geometry)} build their own"
                ),
                detail=(
                    "A disagreement between them may therefore be about which conformer "
                    "or which enantiomer rather than about which scoring function. "
                    "Embedding settles a stereocentre the SMILES leaves undefined, and "
                    "two embeddings can settle it differently. Run 'molcascade stereo "
                    "<run-id>' afterwards to see which molecules that happened to."
                ),
                evidence={
                    "conformer_readers": list(conformer_readers),
                    "own_geometry": list(own_geometry),
                },
            )
        )
    return tuple(advisories)


__all__ = [
    "TARGET_RUN_TIME_HINT",
    "TARGET_SETTING_FLAGS",
    "PreflightAdvisory",
    "copyleft_backends",
    "preflight_backends",
    "preflight_docking_advisories",
    "preflight_docking_target",
    "preflight_engine_paths",
    "required_backends",
]
