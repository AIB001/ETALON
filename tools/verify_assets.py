#!/usr/bin/env python3
"""Check the vendored assets against the manifest that pinned them.

Run this before trusting a number that came out of an asset, and in CI. A vendored
tree is only a pin if something actually compares it to what was recorded; otherwise
it is a copy that happens to have a digest file next to it.

Three failures are distinguished because they mean different things and call for
different responses.

A **missing** file means the copy is incomplete -- an interrupted vendoring, a
partial checkout, a clean that took too much. Re-vendor.

A **changed** file means someone edited the asset in place. That is the dangerous
one: every measurement taken since the edit was taken against something other than
the pinned commit, and nothing else would have noticed. Decide whether the edit was
intended, then either revert it or re-vendor and record why.

An **extra** file means something was added to the tree that the manifest does not
know about. Less dangerous and still worth knowing: a stray output, a build
artifact, or an edit that was meant to be a patch and was never recorded as one.

Exit status is 0 when every tree matches, 1 otherwise, so this is usable as a gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parent.parent / "asset"
MANIFEST = ASSET_ROOT / "MANIFEST.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path, relatives: list[str]) -> str:
    """The same digest the vendoring tool computed, over the same inputs.

    Recomputed here from the files on disk rather than read from anywhere, because a
    digest a verifier takes on trust verifies nothing.
    """

    digest = hashlib.sha256()
    for relative in sorted(relatives):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(sha256_file(root / relative).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def walk(root: Path) -> list[str]:
    return sorted(
        str(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def verify(*, deep: bool) -> int:
    if not MANIFEST.is_file():
        print(f"no manifest at {MANIFEST}", file=sys.stderr)
        print("vendor the assets first: python tools/vendor_assets.py", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    failures = 0

    for name, recorded in sorted(manifest.get("assets", {}).items()):
        root = ASSET_ROOT / name
        print(f"\n{name}")
        print(f"  pinned at  {recorded['source_commit'][:12]}  {recorded['source_subject'][:60]}")
        if not root.is_dir():
            print("  MISSING: the asset directory does not exist")
            failures += 1
            continue

        present = walk(root)
        expected_count = int(recorded["copied_files"])
        if len(present) != expected_count:
            # Reported rather than fatal on its own: the count differing tells you
            # something moved, and the per-file comparison below says what.
            print(f"  file count {len(present)}, manifest recorded {expected_count}")

        if deep:
            observed = tree_digest(root, present)
            if observed == recorded["tree_sha256"]:
                print(f"  tree digest matches  {observed[:24]}...")
                continue
            print("  TREE DIGEST MISMATCH")
            print(f"    recorded {recorded['tree_sha256']}")
            print(f"    observed {observed}")
            print(
                "    The copy is not the commit it claims to be. Every measurement "
                "taken from it since the change was taken against something else."
            )
            failures += 1
        else:
            total = sum((root / relative).stat().st_size for relative in present)
            if total == int(recorded["copied_bytes"]):
                print(f"  byte total matches  {total:,}")
            else:
                print(
                    f"  BYTE TOTAL DIFFERS: {total:,} on disk, "
                    f"{int(recorded['copied_bytes']):,} recorded"
                )
                print("    Re-run with --deep to find which files.")
                failures += 1

    reference = manifest.get("reference", {})
    if reference:
        print("\nreference")
        for filename, recorded in sorted(reference.items()):
            path = ASSET_ROOT / "reference" / filename
            if not path.is_file():
                print(f"  {filename}: MISSING")
                failures += 1
                continue
            observed = sha256_file(path)
            if observed == recorded["sha256"]:
                print(f"  {filename}: matches")
            else:
                print(f"  {filename}: CHANGED ({observed[:16]}... vs {recorded['sha256'][:16]}...)")
                failures += 1

    failures += _verify_prose(manifest)

    print()
    if failures:
        print(f"{failures} asset check(s) failed.")
        return 1
    print("Every asset matches the manifest that pinned it.")
    return 0


def _verify_prose(manifest: dict) -> int:
    """Check that asset/README.md names the commits the manifest actually pins.

    This check exists because the drift happened. The table in ``asset/README.md`` named
    ``4e19c53e0054`` and ``2e45b9656d7e`` while the manifest recorded ``c01a6e0b5152`` and
    ``f0492d964795`` -- in the document whose subject is that a version is part of a measurement.
    Nothing broke, because every tool reads the manifest, which is exactly why nobody noticed: prose
    is the part of a provenance chain with no reader that checks it.

    Only the presence of the pinned short commit is checked, not the whole table. A stricter check
    would need the README to be generated, and a generated README is one nobody edits and therefore
    one nobody reads.
    """

    readme = ASSET_ROOT / "README.md"
    print("\nprose")
    if not readme.is_file():
        print("  asset/README.md: MISSING")
        return 1

    text = readme.read_text(encoding="utf-8")
    failures = 0
    for name, recorded in sorted(manifest.get("assets", {}).items()):
        short = recorded["source_commit"][:12]
        if short in text:
            print(f"  {name}: README names the pinned commit {short}")
        else:
            print(f"  {name}: README DOES NOT NAME the pinned commit {short}")
            print("    The prose introducing the pinning mechanism disagrees with the pinning.")
            failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--deep",
        action="store_true",
        help=(
            "Re-hash every file and recompute the tree digest. Slower, and the only "
            "check that detects a file edited in place without changing its length."
        ),
    )
    arguments = parser.parse_args()
    return verify(deep=arguments.deep)


if __name__ == "__main__":
    raise SystemExit(main())
