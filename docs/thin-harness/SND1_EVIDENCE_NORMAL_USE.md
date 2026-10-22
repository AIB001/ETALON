# SND1: evidence-conditioned normal refinement

The example connects typed scientific inputs to an optional local-relaxation
node. It uses existing `snd1_pose_workflow` identity, template and evaluation
functions, PRISM geometry/chemistry helpers, RDKit MMFF and SciPy optimization.
It does not alter PRISM or MolCascade interfaces, start docking/MD, or allocate
compute resources. Existing resource receipts account for actual executions.

## Producer and consumer

`snd1_evidence_workflow.build_input` consumes the previous input bundle, a
normalized activity extraction, source paper/table, and frozen protocol. It emits
`etalon-scientific-input/1`; the shared compiler validates it. The SND1 adapter
accepts five parameter groups: `pocket`, `contacts`, `mobility`, `binding_support`,
and `optimizer`. Other domain adapters declare their own targets.

The local method starts from existing fixed-core outputs. Six phenyl-ring atoms
remain fixed; mapped atoms outside that ring have finite positional restraints.
Protein exclusion and three grounded contact distances enter the Cartesian
objective and gradient. These are preparation heuristics, not binding energies.
The source paper's neighboring-crystal contact is not imported into a single-chain
model. Heavy-atom distances do not certify hydrogen-bond direction or protonation.

The existing fixed-core result is a reused comparator. It does not retroactively
consume new pocket forces; the record explicitly distinguishes its historical
generation from new post hoc assessment. The local method's receipt attributes
every actual parameter group. Prior outputs and costs stay immutable.

## Public example

`research_snd1_evidence.py init|compare|followup --root RUN --prior PRIOR_RUN
--authority PAUSE_FILE` uses the existing ResearchJournal, claims, MethodVersion,
MethodUse, MethodRevision, MethodEvaluation, research-step and checkpoint APIs.
RUN must contain `PROTOCOL.md`, `activity.json`, `sources/paper.xml`, and
`sources/supplementary-tables.xls`. PRIOR_RUN supplies the exact batch5 inputs,
completed poses and original PDBs. Run from a frozen source/package combination;
do not update code underneath a completed execution identity.

`init` records separated source/observation/hypothesis/binding context and a frozen
evaluation plan. `compare` executes only the new local node; the comparator is
reused. `followup` retrieves scoped experience, rechecks domain applicability,
records a decision, and runs only the selected uncompleted branch. A repeated
completed command returns the recorded disposition without science dispatch.

`snd1_evidence_evaluation` separately evaluates receptor-frame whole-ligand and
non-anchor RMSD. It independently fits receptor coordinates with RDKit and
cross-checks the existing evaluator; it never aligns a generated ligand to the
answer. Keep SSD and RMSD distinct in the low-level alignment API.

## Measured case, 2026-10-22

First case: C26-A6 in the C26-A2 frame, twelve correlated seed slots. Fixed-core
non-anchor median RMSD 0.26338 A; local candidate 0.49660 A. Whole-heavy median
RMSD 0.28006 -> 0.45764 A. Both have zero severe clashes, but all twelve local
slots reached the 500-iteration cap and failed the convergence requirement.
No adopted structural benefit: retain fixed-core, preserve the candidate.

The reverse C26-A2 branch retrieved this negative experience, retained the exact
existing fixed-core output and skipped the twelve optional local slots. This
demonstrates a result-driven workflow choice, not a learned policy or a measured
counterfactual speedup. Two already-seen analogs do not establish broad transfer.
The optimized objective decreased while external pose fidelity worsened; objective
improvement must not be substituted for scientific success.

Generation functions in the delivered example match the executed snapshot by
AST; the corrected independent evaluator is versioned separately. No failed
initialization or reading attempt, scientific result, or historical source is
deleted or rewritten. The original R4b pause remains authoritative.

See [Scientific input contract](SCIENTIFIC_INPUT.md) for visibility, provenance,
units, bounded summaries and extension rules.
