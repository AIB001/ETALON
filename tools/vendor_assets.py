#!/usr/bin/env python3
"""Copy infrastructure packages into ETALON/asset as pinned, verifiable trees.

Three things about this script are deliberate, and two of them were learned by it going
wrong.

**It refuses to vendor from a dirty source tree.** The manifest records a commit hash
beside a tree digest, and a reader is entitled to assume the first describes the second.
Copying from a working tree with uncommitted changes breaks that: the bytes would be
whatever was on disk and the recorded provenance would name a commit that never
contained them. A provenance record that can be wrong is worse than none, because the
whole of ETALON rests on this manifest being true. So a dirty source is an error, not a
warning.

**It does nothing without ``--write``.** The previous version had no argument parsing at
all, so every invocation -- including ``--help`` -- deleted the asset tree and rebuilt it
from whatever the sources currently held. That was found by typing ``--help``. The
default is now a comparison against the manifest already on disk, which answers the
question the script is usually run to answer.

**The excluded prefixes are recorded in the manifest.** Not so the exclusion can be
audited in the abstract, but because one file is lifted back out of the excluded tree on
purpose, and a reader who sees ``tests/`` excluded and ``approved_drugs.py`` present
needs the record to explain it rather than look like a mistake.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ETALON = Path(__file__).resolve().parent.parent
ASSET = ETALON / "asset"
SOURCES = {
    "molcascade": Path("/mnt/e/My_Project/MolCascade"),
    "prism": Path("/mnt/e/My_Project/PRISM/PRISM_Alpha/PRISM-main"),
    "molquarry": Path("/mnt/e/My_Project/MolQuarry"),
}
#: Excluded from the asset copy, with the reason recorded rather than implied.
EXCLUDE_PREFIXES = ("tests/",)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dirt(repo: Path) -> list[str]:
    """Uncommitted changes to tracked files, which make a recorded commit a lie.

    Untracked files are not dirt for this purpose: the copy is driven by ``git
    ls-files``, so an untracked file is never copied and cannot misrepresent anything.
    A modified or staged tracked file is, because it would be.
    """

    return [line for line in git(repo, "status", "--porcelain", "-uno").splitlines() if line]


def tree_digest(root: Path, relatives: list[str]) -> str:
    """One digest over path-and-content pairs, so the asset verifies as a unit."""

    digest = hashlib.sha256()
    for relative in sorted(relatives):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(sha256_file(root / relative).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def vendor_one(name: str, src: Path, *, write: bool) -> dict[str, object]:
    dest = ASSET / name
    tracked = [line for line in git(src, "ls-files").splitlines() if line]
    kept = [f for f in tracked if not f.startswith(EXCLUDE_PREFIXES)]
    skipped = [f for f in tracked if f.startswith(EXCLUDE_PREFIXES)]

    if write:
        if dest.exists():
            shutil.rmtree(dest)
        copied, total_bytes = [], 0
        for relative in kept:
            source = src / relative
            if not source.is_file():  # a submodule, or a link to nothing
                continue
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            total_bytes += target.stat().st_size
            copied.append(relative)
    else:
        # Digest what is already vendored, so the default run answers "does the asset
        # still match its manifest" without touching anything.
        copied = [
            str(path.relative_to(dest).as_posix()) for path in sorted(dest.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        ]
        total_bytes = sum((dest / relative).stat().st_size for relative in copied)

    return {
        "source_path": str(src),
        "source_commit": git(src, "rev-parse", "HEAD").strip(),
        "source_subject": git(src, "log", "-1", "--pretty=%s").strip(),
        "source_committed_at": git(src, "log", "-1", "--pretty=%cI").strip(),
        "tracked_files": len(tracked),
        "copied_files": len(copied),
        "copied_bytes": total_bytes,
        "tree_sha256": tree_digest(dest, copied),
        "excluded_prefixes": list(EXCLUDE_PREFIXES),
        "excluded_files": len(skipped),
    }


def lift_reference(*, write: bool) -> dict[str, object]:
    """The approved-oral-drug panel, lifted deliberately out of the excluded test tree.

    It is reference data a calibration layer needs, not a test, and upstream keeps it
    where a fixture belongs rather than where reference data is findable.
    """

    ref = ASSET / "reference"
    panel = ref / "approved_drugs.py"
    if write:
        ref.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCES["molcascade"] / "tests/fixtures/approved_drugs.py", panel)
    return {
        "approved_drugs.py": {
            "lifted_from": "molcascade:tests/fixtures/approved_drugs.py",
            "sha256": sha256_file(panel),
            "why": (
                "A panel of 77 approved oral drugs with an in-window/out-of-window split. "
                "Every threshold in MolCascade's shipped defaults was calibrated against "
                "it, so it is the reference ETALON measures its own gates on. It lives in "
                "the excluded test tree upstream, which is where a test fixture belongs "
                "and not where reference data is findable."
            ),
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually re-vendor: delete asset/<name> and copy from the source repos. "
        "Without this the script only digests what is already vendored and compares.",
    )
    parser.add_argument(
        "--asset", action="append", choices=sorted(SOURCES),
        help="Refresh only the named asset; preserves every other pin and the reference panel.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Vendor from a source tree with uncommitted changes to tracked files. The "
        "recorded commit will not describe the copied bytes, so the manifest becomes "
        "untrue; this exists for a deliberate experiment, not for convenience.",
    )
    arguments = parser.parse_args()
    selected = {name: SOURCES[name] for name in dict.fromkeys(arguments.asset or SOURCES)}

    if arguments.write and not arguments.allow_dirty:
        dirty = {name: dirt(src) for name, src in selected.items()}
        offending = {name: lines for name, lines in dirty.items() if lines}
        if offending:
            for name, lines in offending.items():
                print(f"{name}: {len(lines)} uncommitted change(s) to tracked files")
                for line in lines[:10]:
                    print(f"    {line}")
            print(
                "\nRefusing to vendor. The manifest records a commit beside a tree digest "
                "and a reader may assume the first describes the second; copying from a "
                "dirty tree would make that false. Commit the sources, or pass "
                "--allow-dirty and accept an untrue manifest."
            )
            return 1

    target = ASSET / "MANIFEST.json"
    manifest: dict[str, object] = (
        json.loads(target.read_text(encoding="utf-8"))
        if arguments.asset and target.is_file() else {"schema_version": 1, "assets": {}}
    )
    assets: dict[str, object] = manifest["assets"]  # type: ignore[assignment]
    for name, src in selected.items():
        assets[name] = vendor_one(name, src, write=arguments.write)
        entry = assets[name]
        assert isinstance(entry, dict)
        print(
            f"{name:<12} {entry['copied_files']:>4} files  "
            f"{int(entry['copied_bytes']) / 1048576:>6.1f} MB  "
            f"commit {str(entry['source_commit'])[:12]}  "
            f"tree {str(entry['tree_sha256'])[:12]}"
        )
    if "molcascade" in selected:
        manifest["reference"] = lift_reference(write=arguments.write)

    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if arguments.write:
        target.write_text(rendered, encoding="utf-8")
        print(f"\nwritten: {target.relative_to(ETALON)}")
        return 0

    if not target.is_file():
        print("\nNo manifest on disk to compare against. Run with --write to create one.")
        return 1
    if target.read_text(encoding="utf-8") == rendered:
        print("\nThe vendored assets still match the manifest that pinned them.")
        return 0
    print(
        "\nWhat is vendored no longer digests to the recorded manifest. Either the asset "
        "tree was edited in place, or the sources moved; tools/verify_assets.py reports "
        "which files. Re-vendor with --write only after deciding that is what you want."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
