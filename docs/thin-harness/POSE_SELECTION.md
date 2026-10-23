# Input-conditioned pose selection

`research.pose_selection` adds an optional rule over conditioning ligand identity.
It does not choose scientific endpoints or execute tools. `input_features` accepts
one ligand SDF, verifies its byte identity, and computes eight graph descriptors;
no reference pose, CNN score, RMSD or post-run verifier result is an argument.

`fit_rule` fits at most one threshold on explicitly seen paired tasks. Both seeds
of a case remain together in `grouped_diagnostics`. Physical validity is a fixed
floor; ties favor fewer CNN calls. The number of candidate thresholds and feature
ranges remain in the policy. Selection outside the fitted feature's range falls
back to empirical ranking. Development fit and cross-validation are diagnostics,
not independent validation or evidence of a chemical mechanism.

The opt-in example `research_pose_selection.py` inherits the existing pose pilot's
native request/receipt ledger and checkpoint/publication implementation. It uses
the existing MethodVersion, MethodUse, research-step, revision context, opportunity,
patch, assessment, evaluation and experience interfaces. There is no fixed Probe.

The example compares three strategies on a newly frozen panel:

- Fixed empirical: CPU search followed by empirical ordering.
- Fixed CNN: the same search followed by GPU rescoring of unchanged poses.
- Conditional workflow: choose whether to rescore using input features, before
  any new scientific execution or outcome. The current example rule is a candidate,
  not a general default.

All alternatives are computed for this bounded audit and charged once. Each
workflow-use node references the actual native nodes and receipts it consumes.
The selected path's attributed cost is distinct from the full audit cost; neither
is an independent measurement of end-to-end production speed. The verifier and
quality thresholds remain identical across strategies. Unknown alternatives in
ordinary selective use must remain unknown.

`prepare_pose_selection.py` organizes 24 project-unseen cases from the retained
author archive, excluding old case IDs, scaffolds, Morgan Tanimoto >= 0.5 and chain
sequence-string similarity >= 0.5. The last check is a heuristic, not certified
protein-family independence; model pretraining overlap remains unverified. Gold
atomic coordinates remain evaluator-only; the organizer explicitly releases the
known-pocket centroid. All old validation examples become seen development data.

`select_pose_branch` now resolves explicit, identity-bound `supersedes` links in
retrieved experience. It retains historical records. Missing ancestry, changed
scope, cycles, conflicting active conclusions or incomplete retrieval prevent
automatic candidate selection. A previous CNN adoption does not automatically
adopt a new conditional workflow: the latter requires its own normal-use evidence.

Rejecting a patch (`retain`) only retains its named comparator in that revision
record. It does not certify that comparator as the best scientific branch. When
another measured static strategy is better, record a separate scoped decision
with the experience and comparison as inputs. Preserve both conclusions; do not
silently relabel the failed patch as an adopted workflow or rewrite its history.

Checkpoint/entry recovery reuses saved executions; ambiguous reservations require
inspection. An independent audit must verify conditioning inputs against the
frozen manifest, choices before execution, same pose pools, cost lineage, actual
use, and unchanged receipts after repeated continuation. Native host hooks are a
separate integration and remain deferred until reviewed. R4b is unaffected.
