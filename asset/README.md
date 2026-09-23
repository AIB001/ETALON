# Vendored assets

Three packages, copied in rather than depended on, plus one reference panel lifted out of a
place it could not be found.

> The commits below are checked against `MANIFEST.json` by `tools/verify_assets.py`. They drifted
> once -- this table named two commits the manifest had long since superseded, in the prose that
> introduces the mechanism for preventing exactly that -- so the check now exists and this table is
> no longer maintained by hand alone.

| Asset | Source commit | Files | Size | What it is |
| --- | --- | --- | --- | --- |
| `molcascade/` | `06f15d7e8274` | 168 | 4.0 MB | Ligand triage, docking and independent redock consistency. Content-addressed artifacts, enforced contracts, per-molecule gates. |
| `prism/` | `f0492d964795` | 504 | 24.7 MB | GROMACS system building, MD, FEP, MM/PBSA, PMF, REST2, and a large trajectory-analysis layer. |
| `molquarry/` | `82f4f34e54cb` | 64 | 0.5 MB | Database access, target evidence dossiers, local catalogs and compound sourcing; six discoverable search skills. |
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

The packages are not independently installed by default. ETALON puts `molcascade/src`, `prism` and `molquarry/src`
on the path explicitly, so which copy is in use is a fact about the process rather than a
fact about the environment. Verified: importing with those paths first yields
`asset/molcascade/src/molcascade/__init__.py`, `asset/prism/prism/__init__.py` and
`asset/molquarry/src/molquarry/__init__.py`, a registry
of 53 plugins, PRISM 1.2.0 and MolQuarry 0.5.0. Wheels include the same trees under
`etalon/_assets`; optional dependencies remain explicit installation extras.

## What was excluded, and why

`tests/` from all three. 103 files from MolCascade, 194 files and 19.7 MB from PRISM --
the latter dominated by full CHARMM36 force-field copies duplicated into FEP test fixtures.
MolQuarry's 16 test files are also excluded; upstream suites can be run from their source repositories.
Every other tracked file was
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
python tools/vendor_assets.py          # inspect proposed refresh; no mutation
python tools/vendor_assets.py --asset molquarry --write  # refresh only this asset
git -C . diff asset/MANIFEST.json      # read what changed before accepting it
```

A refresh changes the tree digest, which is the point: it makes the change reviewable. Read
the diff before accepting it, because an asset update is a change to every measurement taken
afterwards.

## Properties of these assets that ETALON is built around

**MolCascade enforces its contracts.** `validate_table` checks the declared schema, non-null
columns, primary-key uniqueness and declared string enums before a stage commits, so a
contract column is a field a producer cannot leave empty rather than a field it ought to
fill. Its artifacts are content-addressed, its stage cache keys hash the full plugin
descriptor, and a checkpoint whose configuration differs is refused rather than reused.

**ETALON owns PRISM process execution.** PRISM's four `build_*` tools emit a GROMACS tree and a bash
driver -- `localrun.sh`, `smd_run.sh`, `mmpbsa_run.sh` -- and the caller executes it. The
hours-to-days part of the work is not in its tool surface at all. Anything driving PRISM must
therefore own process execution, which is why ETALON has a job layer rather than a tool call.
PRISM also has simulator APIs; the statement above describes ETALON's builder/driver integration.

**MolQuarry acquisition is evidence, not a measurement ruling.** ETALON bounds HTTP requests,
seals raw responses and workflow outputs, and registers structures with MolCascade's identity
policy. Exact assays enter the active loop only through an explicit review, on historical-only
endpoints. Mutable database results are never fetched inside a cached screening stage.
