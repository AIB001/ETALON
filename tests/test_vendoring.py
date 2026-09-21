"""The guard that stops the asset manifest from recording something untrue.

ETALON's every later claim rests on the manifest: the fault taxonomy cites measurements
taken against the vendored trees, and the determinism ADR names the commit it probed. So
the one thing the vendoring tool must not do is record a commit hash beside bytes that
commit never contained. That is not a hypothetical -- the tool copied from the working
tree and reported ``git rev-parse HEAD`` with no check between them.

These tests build throwaway repositories rather than reaching for the real sources,
because the property under test is "does it notice dirt", and the real sources being
clean is precisely what would make such a test pass for the wrong reason.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "vendor_assets.py"
_spec = importlib.util.spec_from_file_location("vendor_assets", _TOOL)
assert _spec and _spec.loader
vendor_assets = importlib.util.module_from_spec(_spec)
sys.modules["vendor_assets"] = vendor_assets
_spec.loader.exec_module(vendor_assets)


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", "-C", str(root), *args], check=True, capture_output=True
    )
    run("init", "-q")
    run("config", "user.email", "probe@example.invalid")
    run("config", "user.name", "probe")
    (root / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    run("add", "tracked.py")
    run("commit", "-qm", "first")
    return root


def test_a_clean_repository_reports_no_dirt(tmp_path: Path) -> None:
    assert vendor_assets.dirt(_repo(tmp_path / "clean")) == []


def test_a_modified_tracked_file_is_dirt(tmp_path: Path) -> None:
    """The case that would have produced a manifest naming the wrong bytes."""

    repo = _repo(tmp_path / "modified")
    (repo / "tracked.py").write_text("value = 2\n", encoding="utf-8")

    dirty = vendor_assets.dirt(repo)

    assert len(dirty) == 1
    assert "tracked.py" in dirty[0]


def test_an_untracked_file_is_not_dirt(tmp_path: Path) -> None:
    """Because the copy is driven by ``git ls-files``, so it is never copied.

    Treating it as dirt would block vendoring for a file that cannot affect the result,
    and a guard that fires on things it does not need to is a guard people disable.
    """

    repo = _repo(tmp_path / "untracked")
    (repo / "scratch.txt").write_text("notes\n", encoding="utf-8")

    assert vendor_assets.dirt(repo) == []


def test_a_staged_change_is_dirt(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "staged")
    (repo / "tracked.py").write_text("value = 3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.py"], check=True, capture_output=True)

    assert vendor_assets.dirt(repo) != []


def test_the_tree_digest_covers_paths_as_well_as_contents(tmp_path: Path) -> None:
    """A rename with identical bytes must change the digest, or a move goes unrecorded."""

    root = tmp_path / "tree"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")

    before = vendor_assets.tree_digest(root, ["pkg/a.py"])
    (root / "pkg" / "b.py").write_text("x = 1\n", encoding="utf-8")
    renamed = vendor_assets.tree_digest(root, ["pkg/b.py"])

    assert before != renamed
    # And it is order-independent, so a filesystem listing order cannot change it.
    both = vendor_assets.tree_digest(root, ["pkg/a.py", "pkg/b.py"])
    assert both == vendor_assets.tree_digest(root, ["pkg/b.py", "pkg/a.py"])


def test_the_tool_refuses_to_write_from_a_dirty_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end, with the real argument parsing, against a repository made dirty here."""

    repo = _repo(tmp_path / "source")
    (repo / "tracked.py").write_text("value = 99\n", encoding="utf-8")
    monkeypatch.setattr(vendor_assets, "SOURCES", {"probe": repo})
    monkeypatch.setattr(sys, "argv", ["vendor_assets.py", "--write"])

    assert vendor_assets.main() == 1
    # Nothing was copied: the refusal happens before any tree is removed.
    assert not (vendor_assets.ASSET / "probe").exists()


def test_targeted_refresh_preserves_other_pins_and_reference_without_their_sources(tmp_path, monkeypatch):
    import json

    source = _repo(tmp_path / "source")
    assets = tmp_path / "asset"
    assets.mkdir()
    previous = {"schema_version": 1, "assets": {"existing": {"source_commit": "keep", "tree_sha256": "keep"}},
                "reference": {"panel": {"sha256": "keep"}}}
    manifest = assets / "MANIFEST.json"
    manifest.write_text(json.dumps(previous))
    monkeypatch.setattr(vendor_assets, "ETALON", tmp_path)
    monkeypatch.setattr(vendor_assets, "ASSET", assets)
    monkeypatch.setattr(vendor_assets, "SOURCES", {"probe": source, "existing": tmp_path / "unavailable"})
    monkeypatch.setattr(sys, "argv", ["vendor_assets.py", "--write", "--asset", "probe"])
    assert vendor_assets.main() == 0
    current = json.loads(manifest.read_text())
    assert current["assets"]["existing"] == previous["assets"]["existing"]
    assert current["reference"] == previous["reference"]
    assert (assets / "probe/tracked.py").read_bytes() == (source / "tracked.py").read_bytes()


# -- the prose is part of the provenance chain ----------------------------


def test_the_asset_readme_names_the_commits_the_manifest_pins() -> None:
    """The drift that happened, pinned as a test.

    ``asset/README.md`` named ``4e19c53e0054`` and ``2e45b9656d7e`` while ``MANIFEST.json`` recorded
    ``c01a6e0b5152`` and ``f0492d964795`` -- two supersessions behind, in the document whose subject
    is that a version is part of a measurement. Nothing broke, because every tool reads the manifest.
    That is why nobody noticed: prose is the part of a provenance chain with no reader that checks
    it, so it needs one.
    """

    import json

    root = Path(__file__).resolve().parent.parent / "asset"
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    text = (root / "README.md").read_text(encoding="utf-8")

    for name, recorded in manifest["assets"].items():
        short = recorded["source_commit"][:12]
        assert short in text, f"asset/README.md does not name {name}'s pinned commit {short}"
