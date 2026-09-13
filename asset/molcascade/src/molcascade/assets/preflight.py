"""Resolve every asset a run will need before the run reads a molecule.

Each adapter that depends on a vendored weight file already fails closed when
it opens one that is absent or does not verify, and the message it raises names
the exact ``molcascade assets fetch`` that would fix it.  That is correct but it
arrives too late: an adapter only opens its weights when execution reaches its
stage, so a cascade whose synthesis tier needs SCScore runs chemistry,
physicochemistry, drug-likeness and alerts over the whole library first --
hours, on the library sizes this tool is built for -- and only then discovers a
20 MB download is missing.  Worse, the discovery is one asset at a time: fetch
that one, restart, and the next tier can fail the same way.

So this module asks the same question up front, and asks it about the whole
pipeline at once.  The signal is the ``asset:`` reference itself.  Adapters do
not receive a path to a file they found by searching; they receive a string
naming an asset and a member of it, and resolving that string is what checks
presence, size and digest.  Walking the compiled configuration for those
strings therefore finds every asset the run will open, without a registry of
which plugin needs what, and without importing or executing any adapter.  A
plugin the project has never seen participates for free, as long as it takes
its weights the way every shipped adapter does.

Nothing here touches the network.  Provisioning stays an explicit human act in
:mod:`molcascade.assets.fetch`; this module only reports, and it reports
everything it found rather than the first thing that failed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from molcascade.assets.store import is_asset_reference, resolve_reference, split_reference
from molcascade.errors import AssetError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from molcascade.pipeline.compiler import CompiledStage


def _walk_strings(value: Any) -> Iterable[str]:
    """Yield every string anywhere inside a JSON-shaped value.

    Settings are free-form JSON per plugin, so an asset reference can sit at the
    top level, inside a list of model paths, or nested in a per-endpoint mapping.
    Recursing costs nothing on configuration-sized data and means a new plugin
    does not have to shape its settings a particular way to be seen.
    """

    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


def required_asset_references(
    stages: Sequence[CompiledStage],
) -> tuple[tuple[str, str], ...]:
    """Return ``(stage_id, reference)`` for every asset reference in ``stages``.

    The stage id travels with the reference because it is the part the user can
    act on: "the run needs SCScore" is a fact about the cascade, but "the
    synthesis_scscore stage needs SCScore" tells them which block to remove if
    they would rather not download it.

    Order follows execution order and duplicates are kept, so a caller can see
    that two stages share one asset.
    """

    found: list[tuple[str, str]] = []
    for stage in stages:
        for text in _walk_strings(stage.config):
            if is_asset_reference(text):
                found.append((stage.stage_id, text))
    return tuple(found)


def preflight_assets(
    stages: Sequence[CompiledStage],
    *,
    root: Path | None = None,
) -> tuple[str, ...]:
    """Verify every asset the compiled ``stages`` will open.

    Returns the distinct asset ids that were checked and are ready, so a caller
    can report what it confirmed rather than only what it rejected.

    Raises :class:`~molcascade.errors.AssetError` naming *all* unusable
    references at once.  Reporting them one at a time would turn provisioning
    into a fetch-restart-fetch loop, which is the same delay this check exists
    to remove.
    """

    ready: list[str] = []
    problems: list[str] = []
    unfetchable: list[str] = []
    for stage_id, reference in required_asset_references(stages):
        try:
            asset_id, _member = split_reference(reference)
        except AssetError as error:
            # A malformed reference is a configuration bug, not a missing
            # download, and no fetch command will help; say so with the stage
            # that carries it rather than folding it in with the rest.
            problems.append(f"  {stage_id}: {error}")
            continue
        try:
            resolve_reference(reference, root=root)
        except AssetError as error:
            problems.append(f"  {stage_id}: {error}")
            if asset_id not in unfetchable:
                unfetchable.append(asset_id)
        else:
            if asset_id not in ready:
                ready.append(asset_id)

    if problems:
        detail = "\n".join(problems)
        hint = (
            "MolCascade never downloads during a screening run, so provision "
            "these first, then start the run again."
        )
        if unfetchable:
            hint = f"run 'molcascade assets fetch {' '.join(unfetchable)}'; {hint}"
        raise AssetError(
            f"{len(problems)} stage(s) need a vendored asset that is not "
            f"ready:\n{detail}",
            code="ASSET_PREFLIGHT_FAILED",
            hint=hint,
            context={"assets": list(unfetchable)},
        )
    return tuple(ready)


__all__ = ["preflight_assets", "required_asset_references"]
