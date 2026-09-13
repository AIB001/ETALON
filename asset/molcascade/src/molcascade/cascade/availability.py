"""Whether this installation can actually *run* a catalogue backend option.

Registration and installation are different questions and the builder used to
conflate them.  Every built-in adapter registers itself unconditionally --
deliberately, because a plugin that vanished from ``molcascade plugins``
whenever its third-party package was absent would make a missing dependency
look like a missing feature.  The adapters then defer their heavy imports to
first use, so ``prediction.admet_ai_v2@0.1.0`` is present in the registry on a
machine with no ``torch`` and no ``admet_ai`` at all.

That is the right behaviour for the registry and the wrong answer for the
builder.  A tool offered as runnable is a promise that a screening run started
from the exported file will reach the end; a tool that is merely *registered*
breaks that promise several hours in, after the cheap tiers have already been
paid for.  So this module asks the second question separately: are the packages
the option declares in ``requires`` importable on this machine, right now?

The probe never imports anything.  ``find_spec`` walks the path finders and
stops at the module's location, which is enough to answer "is it installed"
without executing package-level code -- important here, because the packages in
question load CUDA libraries and multi-gigabyte model trees on import.

Packages are only half of it.  Several adapters need no third-party package at
all and instead read a downloaded data file -- an alert table, a weight file --
that MolCascade refuses to fetch during a run, by design.  Those options would
otherwise sail past the package check and fail on their first batch, so the
declared assets are checked here too, with the same standard: present on disk
*and* matching the digest they were pinned to.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from functools import lru_cache

from molcascade.assets import AssetState, asset_spec, asset_status
from molcascade.backends.catalog import distribution_for_module
from molcascade.cascade.catalog import BackendOption
from molcascade.plugins.registry import PluginRegistry


@lru_cache(maxsize=256)
def is_importable(module: str) -> bool:
    """Report whether ``module`` can be imported, without importing it.

    Cached for the life of the process on purpose.  A screening run must not
    change its mind halfway through about what this machine has installed:
    the plan it committed to, the provenance it records and the tools it
    actually calls all have to agree, and a pip install running in another
    terminal is not a reason for them to diverge.
    """

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
        # A namespace-package parent that cannot be imported, a module whose
        # ``__spec__`` is ``None``, a broken ``.pth`` file: all of them mean the
        # same thing to a caller asking whether the tool will work.
        return False


def missing_requirements(option: BackendOption) -> tuple[str, ...]:
    """The packages ``option`` names that are not installed here, in order."""

    return tuple(module for module in option.requires if not is_importable(module))


@lru_cache(maxsize=64)
def _asset_state(asset_id: str) -> AssetState:
    """The state of one declared asset, cached like the package probe.

    Cached for the same reason: a builder page and the plan it exports have to
    describe one consistent machine.  ``asset_status`` stats each declared file
    and compares it against the recorded digest, so this is cheap but not free,
    and every criterion in the catalogue would otherwise repeat it.
    """

    try:
        return asset_status(asset_spec(asset_id)).state
    except (KeyError, OSError):
        # An asset id no adapter declares any more, or a root that cannot be
        # read: both mean "you cannot rely on this file being there".
        return AssetState.MISSING


def missing_assets(option: BackendOption) -> tuple[str, ...]:
    """The assets ``option`` needs that are absent, incomplete or corrupt."""

    return tuple(
        asset_id
        for asset_id in option.requires_assets
        if _asset_state(asset_id) is not AssetState.READY
    )


@dataclass(frozen=True, slots=True)
class OptionAvailability:
    """Why one catalogue option can, or cannot, run on this machine."""

    runnable: bool
    missing_packages: tuple[str, ...] = ()
    missing_assets: tuple[str, ...] = ()
    reason: str | None = None


def _joined(names: tuple[str, ...]) -> str:
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _package_phrase(missing: tuple[str, ...]) -> str:
    verb = "is" if len(missing) == 1 else "are"
    return f"{_joined(missing)} {verb} not installed here"


def install_command(modules: tuple[str, ...]) -> str:
    """The pip command that supplies ``modules``, by distribution name.

    Import names and distribution names are not the same string, and for at
    least one backend the difference is actively harmful rather than merely
    untidy: ``pip install mordred`` succeeds, installs the abandoned 2018
    release, and leaves the user with something that looks like the right
    package and cannot compute a descriptor against a current RDKit.  So the
    message names what to install, not what failed to import.

    Distributions are deduplicated while preserving order, because several
    imports routinely arrive from one wheel -- ``admet_ai`` brings ``chemprop``
    and ``torch`` with it -- and listing each of them separately would suggest
    four installs where one will do.
    """

    seen: list[str] = []
    for module in modules:
        # Falling back to the import name is a guess, but it is the same guess
        # the user would make unaided, and it is right for most packages.
        distribution = distribution_for_module(module) or module
        if distribution not in seen:
            seen.append(distribution)
    return "pip install " + " ".join(seen)


def option_availability(
    option: BackendOption,
    registry: PluginRegistry,
) -> OptionAvailability:
    """Decide whether ``option`` can be selected, and say why when it cannot.

    The failures are kept distinct because they need different actions from the
    user: a research candidate is waiting on MolCascade, an unregistered adapter
    means a broken or partial installation, and a missing package or unfetched
    asset is something the user can fix in one command.
    """

    if option.plugin_ref is None:
        return OptionAvailability(
            runnable=False,
            reason=option.notes or "Reviewed research option; no adapter ships for it yet.",
        )
    if option.plugin_ref not in registry:
        return OptionAvailability(
            runnable=False,
            reason=option.notes or "Adapter is not registered in this installation.",
        )
    if option.gate_plugin is not None and option.gate_plugin not in registry:
        return OptionAvailability(
            runnable=False,
            reason="The threshold gate this tool needs is not registered here.",
        )
    if option.requires_license_optin:
        # Checked before the package probe because the answer does not depend on
        # this machine: installing the tool would not make it selectable, and a
        # message about a missing import would send the user to fix the wrong
        # thing.
        return OptionAvailability(
            runnable=False,
            reason=(
                f"This tool is {option.license_spdx}, and a run has to permit "
                "copyleft backends before it will start. Screen with "
                "'--allow-copyleft' if your project accepts those terms; the "
                "run records that it was given, so a methods section can say so."
            ),
        )
    packages = missing_requirements(option)
    if packages:
        return OptionAvailability(
            runnable=False,
            missing_packages=packages,
            reason=(
                f"The adapter is installed but {_package_phrase(packages)}. "
                f"Run '{install_command(packages)}' on the machine that will "
                "run the screen, then regenerate this builder so the tool "
                "becomes selectable."
            ),
        )
    assets = missing_assets(option)
    if assets:
        return OptionAvailability(
            runnable=False,
            missing_assets=assets,
            reason=(
                "The adapter is installed but its data files have not been "
                f"fetched. Run 'molcascade assets fetch {' '.join(assets)}', "
                "then regenerate this builder. MolCascade never downloads "
                "during a screening run, so this has to happen first."
            ),
        )
    return OptionAvailability(runnable=True)


__all__ = [
    "OptionAvailability",
    "install_command",
    "is_importable",
    "missing_assets",
    "missing_requirements",
    "option_availability",
]
