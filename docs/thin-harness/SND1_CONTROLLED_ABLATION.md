# SND1 torsion-range and pocket-term ablation

`examples/snd1_controlled_ablation.py` is a retrospective task driver. It does
not install a default probe, select a production method, or create a research
database. ResearchJournal registration and adoption remain with the host.

The parent SND1 panel used two neighboring ligands, their own crystallographic
starting coordinates, and crystallographic positional anchors/contact windows.
Its fixed-core control and torsion method changed several factors together.
This diagnostic does **not** attribute their difference to freedom count.

The four cells use the same four torsion axes and moving groups, starting
coordinates, MMFF94s, soft positional anchors, fixed atoms, hard bond/angle/ring
checks, optimizer, and Cartesian-chain gradient:

| Cell | Torsion correction half widths, degrees | Pocket distance and exclusion terms |
| --- | --- | --- |
| narrow_off | 10, 10, 10, 20 | disabled |
| narrow_on | 10, 10, 10, 20 | enabled, original coefficients |
| wide_off | 30, 30, 30, 60 | disabled |
| wide_on | 30, 30, 30, 60 | enabled, original coefficients |

Directional proxies do not enter any cell's objective. Disabling pocket terms
retains the original soft positional anchors. The helper
`snd1_scale_methods.controlled_torsion_parameters` copies its input and changes
only the declared bounds and pocket terms.

The protocol selects all 17 previously observed common failures. Each receives
one nearest initialization-angle control from the same system and perturbation
family in each of two historical strata: common success and torsion rescue.
Matching allows replacement; execution deduplicates controls. The frozen panel
contains 34 unique conditions, 23 unique ligand-coordinate hashes, and 136 new
solver calls. These related conditions do not establish independent chemical
generalization.

`freeze` authenticates parent receipts and raw poses, copies the selected frozen
inputs, and binds source/native-module paths and hashes before optimization.
`run` writes a request and terminal receipt for every cell. It reuses a completed
attempt only after verifying its artifacts and refuses an attempt with an
unknown outcome. The Linux runtime requires the declared single CPU affinity
and single-thread, GPU-disabled environment. Existing workloads need a separate
identity/resource check before launch.

```text
python examples/snd1_controlled_ablation.py freeze --root NEW_ROOT --parent PARENT_P1
python examples/snd1_controlled_ablation.py run --root NEW_ROOT
```

Use the exact scientific interpreter and resource prefix recorded in the
frozen run. A helper's `PYTHONPATH` must not replace the module imports bound by
the scientific freeze. The task-specific resource configuration and exact
17-failure parent requirement are deliberate; this example is not a general
benchmark executor.

Independent evaluation uses the original `pose_verifier.py`, PoseBusters dock
checks, and symmetry-aware heavy-atom RMSD in the receptor frame, with no ligand
alignment or reference-based repair. `narrow_on` must reproduce historical T0
within 0.001 A maximum atom difference, 0.0001 kcal/mol objective difference, and
the same success flag. The prospective candidate gate requires at least three
rescued failures, no control regression, no new hard violation, and solver CPU
at most twice the matched baseline. Passing could support only a repair scoped
to this seen panel; it does not authorize global adoption or an affinity claim.

The outputs preserve individual solver and evaluator receipts, convergence,
hard checks, offending heavy-atom pairs, active bounds, motion sensitivity, and
all failures. A successful pose is a witness of reachability for that cell.
Failure of this local optimizer does not prove the allowed region infeasible.
Run CPU includes nested solver/evaluator CPU and must not be added to those
intervals. Unknown formation, testing, or transfer costs stay unknown.

Synthetic contract tests cover matching/deduplication, immutable ablation
parameters, the retained positional energy and its gradient, diagnostic atom
mobility, and the frozen gate. They do not substitute for the real scientific
run or interruption/recovery evidence.
