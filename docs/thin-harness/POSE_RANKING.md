# Reference-free same-pool ranking pilot

`examples/validate_pose_ranking.py` evaluates the fixed `contact-relaxation-v1`
candidate implemented in `research/pose_ranking.py`. It preserves the original
GNINA empirical pool and compares empirical, CNN, and candidate orderings on those
same coordinates. The controller imports the resulting evidence into memory;
this driver does not change a deployment, policy, journal, or research entry.

The candidate considers only poses within 1 kcal/mol of the empirical best. Its
adjusted score is empirical energy + 0.10 × capped local relaxation energy drop
− 0.25 × polar-atom coverage + 0.25 × buried uncovered polar atoms + 5 × severe
heavy-atom clash pairs. Coverage means a ligand N/O/S atom lies 2.5–3.5 Å from a
receptor N/O/S atom. Burial means a receptor heavy atom is within 4 Å; severe
clashes are pairs below 1.6 Å. These are distance descriptors, not directional
hydrogen bonds or a binding-energy model. MMFF94s runs at most 200 iterations on
a copy; its nonnegative energy drop is capped at 50 kcal/mol. Nonconvergence or
unsupported parameters in an eligible pose causes an explicit empirical fallback
for that entire case. Missing values are never replaced with favorable zeros.

Recruitment is frozen before generating predictions. It uses the publicly
archived PoseBusters data and the author's PB308 identifier subset, excludes both
earlier local 24-case panels, inspected rejected IDs/series and SND1 parents,
and requires distinct scaffolds plus Morgan and protein-chain string similarity
below 0.4. The latter is an operational proximity filter, not a remote-homology
certificate. A single seed on 24 newly accepted cases gives 24 structure/series
units; seeds are never counted as independent molecules. Eligibility is checked
before predictions; execution failures remain in the denominator without swaps.

The organizer releases a crystal-derived pocket centroid and graph-regenerated
ligand only. Crystal ligand atomic coordinates are evaluator-only. Candidate
features are saved before CNN and before the exact 22-check PoseBusters verifier
or fixed-frame symmetry RMSD is run. Primary success is all physical checks AND
unrounded heavy-atom RMSD ≤ 2 Å. The frozen design states adoption and cost rules;
test results cannot revise coefficients, cases, or acceptance thresholds.

The bounded run uses 24 CPU searches and at most 24 same-pool CNN evaluations,
with no automatic retries. Native requests, receipts, logs, resource observations,
pool identities, per-pose features, rankings, checker implementation/configuration,
and evaluator outcomes remain separately inspectable. Actual audit acquisition
cost and hypothetical policy-path cost are distinct. This experiment supports
only retrospective known-pocket pose ranking; it does not measure affinity or
activity, or establish absence from model training.

Sources: [PoseBusters archive](https://zenodo.org/records/8278563),
[official implementation](https://github.com/maabuu/posebusters).

Post-review correction, 2026-10-25: the original frozen 24-case receptor for
`8EX2_Q2Q` contains one target-ligand hydrogen (`Q2Q/H161`). The candidate skips
receptor H/D, while GNINA/CNN treatment of this isolated atom was not established.
The original native inputs therefore do not support a universal claim that they
contain no crystal-ligand atoms. Primary 24-case statistics and original freezes
are preserved; the explicit 23-case sensitivity is post hoc. The current formal
driver adds a preflight that rejects any `ATOM`/`HETATM` with the explicit target
CCD residue identity, including hydrogens, and retains the unchanged source.
This guard detects exact residue identities, not arbitrary renamed remnants.
It is a later source version than the executed freeze; its test does not repair
or rerun the original science.
