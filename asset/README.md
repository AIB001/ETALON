# Vendored assets

Two packages, copied in rather than depended on, plus one reference panel lifted out of a
place it could not be found.

> The commits below are checked against `MANIFEST.json` by `tools/verify_assets.py`. They drifted
> once -- this table named two commits the manifest had long since superseded, in the prose that
> introduces the mechanism for preventing exactly that -- so the check now exists and this table is
> no longer maintained by hand alone.

| Asset | Source commit | Files | Size | What it is |
| --- | --- | --- | --- | --- |
| `molcascade/` | `c01a6e0b5152` | 167 | 4.0 MB | Ligand triage and docking cascade. Content-addressed artifacts, enforced contracts, per-molecule gates. |
| `prism/` | `f0492d964795` | 504 | 24.7 MB | GROMACS system building, MD, FEP, MM/PBSA, PMF, REST2, and a large trajectory-analysis layer. |
| `reference/approved_drugs.py` | from `molcascade` | 1 | 8 KB | 77 approved oral drugs with an in-window / out-of-window split. |

`MANIFEST.json` carries the exact commit, subject and commit date of each source, the file
and byte counts, a digest over the whole copied tree, and what was excluded. Verify an asset
against it before trusting a number that came out of it.

## Why vendored rather than depended on

Because a version is part of a measurement. A free-energy number is a statement about a
force field, a water model, a lambda schedule and an estimator, and a screening verdict is a
statement about a threshold and a rule revision. An asset pinned by commit and verified by
tree digest means a result recorded last month can be reproduced today, and means a changed
asset is a visibly different asset rather than a silently different answer.

Neither package is installed from here by default. ETALON puts `molcascade/src` and `prism`
on the path explicitly, so which copy is in use is a fact about the process rather than a
fact about the environment. Verified: importing with those paths first yields
`asset/molcascade/src/molcascade/__init__.py` and `asset/prism/prism/__init__.py`, a registry
of 51 plugins, and PRISM 1.2.0.

## What was excluded, and why

`tests/` from both. 102 files and 1.5 MB from MolCascade, 194 files and 19.7 MB from PRISM --
the latter dominated by full CHARMM36 force-field copies duplicated into FEP test fixtures.
ETALON calls these packages; it does not run their suites. Every other tracked file was
copied, including PRISM's `prism/configs/forcefield/` (170 files), which is runtime data a
build needs rather than test material.

Nothing untracked was copied, so whatever each repository's `.gitignore` excludes is excluded
here too. For MolCascade that is most of its 196 MB working tree: run workspaces, artifact
stores, caches.

## The one file lifted deliberately

`reference/approved_drugs.py` comes out of MolCascade's excluded test tree. It is a panel of
77 approved oral drugs, and every threshold in MolCascade's shipped defaults was calibrated
against it -- the ring ceilings, the alert actions, the Rule-of-Five bound, the Lilly demerit
actions. That makes it reference data rather than a fixture, and a calibration layer that
cannot find it would re-derive a worse version of it.

It is copied rather than imported because importing it would mean depending on a test tree
that is not here.

## Refreshing an asset

```bash
python tools/vendor_assets.py          # re-copies at each source's current HEAD
git -C . diff asset/MANIFEST.json      # read what changed before accepting it
```

A refresh changes the tree digest, which is the point: it makes the change reviewable. Read
the diff before accepting it, because an asset update is a change to every measurement taken
afterwards.

## Two properties of these assets that ETALON is built around

**MolCascade enforces its contracts.** `validate_table` checks the declared schema, non-null
columns, primary-key uniqueness and declared string enums before a stage commits, so a
contract column is a field a producer cannot leave empty rather than a field it ought to
fill. Its artifacts are content-addressed, its stage cache keys hash the full plugin
descriptor, and a checkpoint whose configuration differs is refused rather than reused.

**PRISM does not run simulations.** Its four `build_*` tools emit a GROMACS tree and a bash
driver -- `localrun.sh`, `smd_run.sh`, `mmpbsa_run.sh` -- and the caller executes it. The
hours-to-days part of the work is not in its tool surface at all. Anything driving PRISM must
therefore own process execution, which is why ETALON has a job layer rather than a tool call.
