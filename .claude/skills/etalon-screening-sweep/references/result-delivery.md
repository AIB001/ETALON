# Preserve the population and deliver the actual poses

For new executions, put `delivery={}` in the existing plan/submit request. This
binds output production to the job and returns `result.delivery.path` on completion.
The CLI equivalents are `screen plan/submit --deliver`; `--require-redock` also
checks the requested strict gate and retained-pose configuration. The host chooses
the scientific operations; these flags do not silently insert docking or change
selection thresholds. A failed export is recoverable without repeating science.

Use the existing `etalon_screen_export(..., view="research")` to materialize
committed decisions with SMILES, selection decisions, prepared receptor PDBs and
top-one dock/redock SDFs. SDK: `etalon.screening_delivery.export_screen_delivery`.
For moved originals pass an explicit `locations` prefix map. This only reads
results; it never launches a missing calculation. A partial run stays partial.
The compact receipt points to compressed CSV tables and coordinate files.

Each gate should answer: which population was evaluated, how many entered/left,
what was rejected or unknown, and where its SMILES/reason rows live. Sibling
criteria can assess the same population: do not sum their rejections as cumulative
attrition. Reused registration decisions can describe an older, larger population;
label them as inherited evidence, not newly rejected molecules. Raw source records,
standardized parents and poses have different denominators.
`parent-origins.csv.gz` retains the occurrence-to-parent mapping and ingested
metadata. Inherited decisions remain references to originals rather than another
full copy at every tier.
Later registered-parent tiers follow explicit source artifact references for this
metadata, including across workspaces. Keep those original stores available or
give a relocation map. Missing origins are reported as incomplete source coverage;
a valid subset of poses does not establish complete lineage.

Keep quality rejection separate from budget selection. A target count and a
scaffold cap are host choices, not scientific evidence that discarded parents are
inferior. Record selection order, seed, per-partition cap and all selection rows.
`select.native_scaffold_round_robin@0.2.0` keeps the same selected population as
0.1.0 and separates scaffold-cap exclusions from total-budget exclusions. A
Murcko group is not a generator model, generation run or conditioning family.
Do not replace an interrupted native selector with a script that saves only the
winners. If a historical export omitted loser ranks, retain that gap instead of
inventing which constraint excluded each molecule.

Generated libraries often need within-family exploration. Preserve model, run,
conditioning/parent family and observed scaffold as separate grouping fields.
Choose exploration breadth and within-family sampling for the question; applying
commercial-library scaffold round-robin globally can erase useful analog series.
The generation handoff preserves every model/run occurrence, including duplicate
chemical identities, and can annotate scaffolds with `include_scaffolds=True`.
After deduplication retain the many-to-one occurrence map. A failed scaffold
annotation is unknown group membership, not a molecular quality rejection.

For a requested strict `RMSD < 2 Å` acceptance rule, distinguish two meanings:

- Crystal-pose recovery: compare the same ligand/state to its experimental
  reference in a common receptor frame. Report top-one and best-of-N separately;
  never substitute best-of-N for the delivered top-one pose's result.
- Prediction consistency: compare independent docking searches in the same
  receptor frame. In MolCascade use `dock_redock_rmsd`, `maximum=2.0`,
  `maximum_exclusive=true`, `on_unscorable=reject` for a new protocol when this
  condition is requested. This is not crystal accuracy or binding evidence.

Heavy-atom mapping must respect chemistry and symmetry. Do not independently
superpose ligands to make pocket displacement disappear. An unseen compound has
no crystal accuracy label; state unknown/not applicable instead of manufacturing
a reference. RMSD alone does not replace geometry, clash and chemical-state checks.

The delivery view defaults to a separate `<2 Å` assessment of saved redock
metrics; it does not retroactively change historical gates or survivors. Preserve
the old threshold in the receipt. Decide a new protocol or follow-up based on the
new assessment. Unknown is separate from measured rejection.

Deliver the prepared protein actually used, original docking SDF, independent
redocking SDF, parent/source mapping, scores and RMSD status. For rigid docking
the protein coordinates are shared; do not invent distinct protein conformations.
Separate files for distinct receptor structures. The ordinary shortlist SDF may
only represent molecular identity; never substitute it for saved 3D poses.
If only top-one redock poses were retained, say so; all missing poses remain a
delivery gap, not permission to silently recompute them.
