# Frozen PoseBusters check bridge

`pose_check_bridge.freeze_posebusters_dock()` derives the required checks from
the actual PoseBusters dock configuration and pins the complete configuration
and Python/configuration source tree. It constructs a checker; it does not run
`bust` or observe poses.

`audit_pose_verification()` adapts the original `etalon-pose-verification/1`
format to `result_integrity.validate_verifier`. It independently requires exact
input pins, protocol/threshold, requested slots, row request order, the complete
strict Boolean check set, consistent physical validity and RMSD success, and
consistent top1/any-success aggregates. A claimed `required_checks_complete`
flag is not an input to the decision. Missing evidence produces unknown.

The future `snd1_controlled_ablation.py` freezes schema2 with this full contract
and pins both bridge and integrity implementations. Its evaluation path checks
the original evaluation request/receipt binding and audits raw observations,
including cached results. It refuses to treat a legacy schema1 run as though
it had the schema2 predeclaration; those results need a separate explicit audit.

`audit_snd1_check_contract.py` provides that read-only retrospective audit. In the
actual original scientific Python it checked all 16 original verification
files and 136 rows against the 22-check PoseBusters0.6.5 dock contract, verified
native inputs/requests/receipts and exact pose identity/order/coordinates, and
recomputed fixed-frame RMSD with the frozen original function. All checks
passed; the four original success counts remained 17/17/34/34.

That audit ran zero solver, docking, CNN or PoseBusters bust calls. RMSD was
newly recomputed, with its analysis cost recorded inside the total audit cost.
The old science snapshot remains schema1; its complete dock configuration was
not prospectively frozen. This result is a retrospective binding check, not a
new physical observation, independent confirmation or portable environment.

A separate real schema2 preparation/import validation created a new freeze and
verified its actual source pins without executing any of its 136 planned
scientific slots. Sixteen Linux tests cover complete pass/rejection, missing or
non-Boolean checks, wrong identities/RMSD/success, cached evaluation and refusal
of legacy freezes. The existing Unix `resource` dependency makes this scientific
driver Linux-specific; the retained Windows collection failure is not evidence
that the Linux tests were skipped or that counterexamples were removed.
