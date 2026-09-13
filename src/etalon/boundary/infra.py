"""Reach the two infrastructure packages, and prove which copy was reached.

ETALON runs MolCascade and PRISM as infrastructure. That makes one question load-bearing
before any other: *which* MolCascade, and *which* PRISM. Every measurement this project
records cites a commit from ``asset/MANIFEST.json``, and a citation is only worth
something if the code that ran is the code the citation names.

It is not automatically the same. The first time this was checked, ``import molcascade``
with ``asset/molcascade`` on ``sys.path`` resolved to
``/mnt/e/My_Project/MolCascade/src/molcascade`` -- the editable install in the
environment, pointing at the live working tree, which moves. The import succeeded, the
version string was right, and everything downstream would have recorded a pinned commit
beside numbers produced by whatever was uncommitted at the time. That is the exact
failure this module exists to make impossible, and it was found by printing
``__file__`` rather than by reasoning about path order.

So :func:`load` does not merely import. It imports, then compares the module's resolved
file against the asset root, and raises when they disagree. An environment that cannot
be made to load the pinned copy is reported as such, because the alternative -- loading
something else and carrying on -- is how a provenance chain acquires a break that nobody
can see afterwards.

Two deliberate non-features.

It does not re-digest the asset tree on every load. That takes seconds over 24 MB and
would turn a cheap call into a slow one; ``tools/verify_assets.py`` does it on demand and
:meth:`Infra.provenance` records the digest the manifest claims, so a campaign record
always says which tree it believed it was running and can be checked against the files
afterwards.

It does not install anything, or alter ``sys.path`` permanently in a way callers cannot
see. The insertion is explicit and reported, because a module that silently rearranges
import resolution is a module whose effects show up somewhere else as a mystery.
"""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

#: Where each package's importable root sits inside its vendored tree. MolCascade uses a
#: src layout and PRISM does not, which is the only reason this mapping exists.
_PACKAGE_ROOTS = {
    "molcascade": Path("molcascade") / "src",
    "prism": Path("prism"),
}


class InfraError(RuntimeError):
    """Raised when the code that loaded is not the code the manifest pins.

    Deliberately not a warning. A campaign that proceeds on unpinned infrastructure
    produces numbers whose provenance cannot be reconstructed later, and the cost of
    discovering that after a week of GPU time is much higher than the cost of stopping
    now.
    """


@dataclass(frozen=True, slots=True)
class Infra:
    """One infrastructure package, loaded and proven."""

    name: str
    #: The vendored tree this came from, e.g. ``asset/molcascade``.
    asset_root: Path
    #: The importable directory that was put on ``sys.path``.
    import_root: Path
    module: ModuleType
    version: str
    #: Where the module actually resolved to. Recorded rather than assumed equal to
    #: ``import_root``, because the whole point of this module is that they can differ.
    loaded_from: Path
    #: What the manifest claims about this tree, carried so a campaign round can cite it
    #: without re-reading the manifest and without re-digesting 24 MB.
    source_commit: str
    tree_sha256: str

    def provenance(self) -> dict[str, object]:
        """The record a campaign round embeds to say what it ran on."""

        return {
            "name": self.name,
            "version": self.version,
            "source_commit": self.source_commit,
            "tree_sha256": self.tree_sha256,
            "loaded_from": str(self.loaded_from),
            "pinned": True,
        }


def _manifest(asset_dir: Path) -> dict[str, dict[str, str]]:
    path = asset_dir / "MANIFEST.json"
    if not path.is_file():
        raise InfraError(
            f"no asset manifest at {path}. ETALON cannot cite a commit for code it "
            "cannot identify; run tools/vendor_assets.py --write first."
        )
    return json.loads(path.read_text(encoding="utf-8"))["assets"]


def load(name: str, *, asset_dir: Path | None = None) -> Infra:
    """Import a pinned infrastructure package and prove the copy that loaded.

    Args:
        name: ``"molcascade"`` or ``"prism"``.
        asset_dir: The vendored asset directory. Defaults to ``<repo>/asset``.

    Raises:
        InfraError: When the package resolves outside the pinned tree. This includes the
            common and silent case of an editable install in the environment shadowing
            the vendored copy, which is what actually happened the first time.
    """

    if name not in _PACKAGE_ROOTS:
        raise InfraError(f"unknown infrastructure package {name!r}; expected one of {sorted(_PACKAGE_ROOTS)}")

    asset_dir = asset_dir or Path(__file__).resolve().parents[3] / "asset"
    entry = _manifest(asset_dir).get(name)
    if entry is None:
        raise InfraError(f"the manifest at {asset_dir} pins no asset named {name!r}")

    asset_root = asset_dir / name
    import_root = (asset_dir / _PACKAGE_ROOTS[name]).resolve()
    if not (import_root / name).is_dir():
        raise InfraError(
            f"{name} is pinned in the manifest but {import_root / name} is not on disk. "
            "The asset tree is incomplete; tools/verify_assets.py will say which files."
        )

    already = sys.modules.get(name)
    if already is not None:
        # Refused rather than reused. Something earlier in the process already decided
        # which copy this name means, and honouring that decision silently is how the
        # unpinned copy gets cited as the pinned one.
        resolved = Path(getattr(already, "__file__", "") or "").resolve()
        if not _within(resolved, import_root):
            raise InfraError(
                f"{name} is already imported in this process from {resolved}, which is "
                f"outside the pinned tree at {import_root}. An editable install in the "
                "environment shadows the vendored copy, so the code that would run is "
                "not the code the manifest names. Start a fresh interpreter with "
                f"{import_root} ahead of site-packages on sys.path."
            )

    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))
    module = importlib.import_module(name)
    resolved = Path(getattr(module, "__file__", "") or "").resolve()
    if not _within(resolved, import_root):
        raise InfraError(
            f"{name} imported from {resolved}, not from the pinned tree at "
            f"{import_root}. sys.path order put another copy first -- most often an "
            "editable install. Every number produced through this import would cite "
            f"commit {entry['source_commit'][:12]} while having been computed by "
            "something else."
        )

    return Infra(
        name=name,
        asset_root=asset_root,
        import_root=import_root,
        module=module,
        version=str(getattr(module, "__version__", "unknown")),
        loaded_from=resolved,
        source_commit=entry["source_commit"],
        tree_sha256=entry["tree_sha256"],
    )


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def describe(*, asset_dir: Path | None = None) -> dict[str, object]:
    """Report what would load for each package, without raising.

    For a preflight summary: a caller that wants to tell an operator "your environment
    shadows the pinned MolCascade" needs the diagnosis as data, not as an exception.
    """

    report: dict[str, object] = {}
    for name in _PACKAGE_ROOTS:
        try:
            report[name] = load(name, asset_dir=asset_dir).provenance()
        except InfraError as error:
            report[name] = {"name": name, "pinned": False, "problem": str(error)}
    return report


__all__ = ["Infra", "InfraError", "describe", "load"]
