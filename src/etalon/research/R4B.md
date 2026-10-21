# R4b consumer: retained baseline and bounded intervention diagnostics

This is an additive continuation of R4. It reuses `CampaignStore`,
`ResearchJournal`, fixed-pool scoring, immutable artifacts and the original assay
oracle. It does not introduce another learner, score service or scientific ledger.
The old nine groups are now seen. New CA2/AR/MMP12 groups are independent only
within this project's prepared-input evaluation; their upstream aligned poses
and chosen receptor are not a blind structure-discovery input.

The coordinator's frozen protocol chooses retained B because only one seen
training family has an eligible reviewed His intervention and no calibration
family supplies that contrast. `retained_baseline.py` therefore creates no
empirical worlds and records `phi_learning_not_established=true`. Fixed B is an
explicit executable retention decision, not a newly learned P selector.

## Actual preparation boundary

`preparation.py` pins PRISM's `prism-receptor-preparation-{request,result}/1`
contracts. Grid fields are `center_A`, `size_A`, `spacing_A`. The consumer reserves
in the existing ledger before the actual owner-supplied preparer command; it
verifies original request, receipt, artifacts and imported source pins before
admission. PREPARED, NOT_APPLICABLE and FAILED all retain their measured costs.
An unresolved reservation cannot redispatch. A terminal receipt arriving later
settles the original execution; repeated admission does not charge or append
again. Already-produced receipts without a prior reservation are rejected.

`examples/preparation_round4b.py` accepts `--database --request --result --output`.
For a new execution also supply `--reservation 120 --command-json ARGV_FILE`.
The argv file contains the actual PRISM preparation command without a shell.
Omit these two flags to admit an existing reserved/settled receipt read-only.
PRISM remains the execution owner. This interface does not rewrite score fees.

Preparation is physically performed during catalogue compilation. A policy's
attributed preparation fee is a share of that measured resource, paid once per
path; choosing B does not physically undo preparation of P already performed.
Full collection cost, online scoring cost, attributed path cost and separately
measured development overhead must remain distinguishable. Online delayed
preparation is still an unimplemented boundary.

## Retained B execution

Use `examples/round4b_retained_config.template.json` as a shape example only;
replace placeholder IDs with same-H preparation admissions and freeze a new
coordinator-reviewed config. It binds the structural freeze, protocol and split,
all twelve p1 resources, and p0 for HIF2A plus the three new test groups. Original
preparation receipt protocol must match both config and split. No old admission
body or Artifact role is edited.

`examples/baseline_round4b.py` provides three commands with common arguments
`--database --config --manifest --input-freeze --root --output-dir`:

1. `freeze`: verify prepared state, receptor input bytes, candidate partitions,
   Vina scorer, grid and label-free request metadata; register the conservative
   policy and pool plan. It performs no scoring.
2. `run`: with `--producer-source --python --request-root`, acquire only the
   three test B/r0 scores through the existing pool adapter and freeze all
   predictions in the same H. p0 failure remains a paid failure/abstention.
3. `complete-counterfactual`: only after the coordinator checks the prediction
   freeze, collect feasible P/r1,r2 and the authorized seen HIF2A comparison.
   It requires the original prediction/H binding and leaves predictions intact.

A manifest uses the existing request-list shape: each entry has task, role,
resource, path, file sha256 and semantic request_sha256. r0 scores all six B
candidates; r1/r2 score the first/last three P candidates. No P request is created
for an unavailable preparation. Optional seen B reuse must match the three
actual prepared input hashes; otherwise the protocol's new HIF2A B execution
is used. New test scores cannot be imported as already-acquired online data.

NOT_APPLICABLE/FAILED P retains its original status and paid p1 receipt. Raw P
scores, scientific loss and measured scoring cost remain null; unexecuted scoring
is `branch_skipped`, not a zero-cost scientific observation. A fixed P-attempt
comparator can use actual B as its effective fallback, with that origin explicit.
It never copies B scores into a successful raw P observation.

## Independent evaluation and feedback

`examples/feedback_round4b.py evaluate` requires original policy, predictions,
diagnostics, released labels, independent release and coordinator oracle script.
It checks the release before reading labels and recomputes score/fee associations
from actual same-H receipts. The scientific loss implementation remains the
coordinator's existing `experimental_order.evaluate`.

The main comparison is equal-family mean loss + .001 times attributed actual
seconds, from the same paid B starting point. Missing scientific values retain
[0,1] bounds and all groups stay in the denominator. The coordinator fixed cold-
start P to include both p0 and p1 preparation fees because its parent certificate
depends on p0. Omitting B scoring there is counterfactual attribution only.
The reports distinguish raw P, effective fixed P-attempt fallback, and retained B.
Copy-best, learned, myopic and nonmyopic new P selectors are not identifiable from
this catalogue's single applicable training family; they are not fabricated
performance rows or claimed defeated baselines.

After coordinator review, `attach-feedback` binds the evaluation and independent
audit to H. An optional `--seen-gain-audit` retains the coordinator's old-nine-group
query/gain audit as now-seen feedback, not new independent validation or fit data.
Promotion remains none, complete RSI false, and the next diagnostic must address
scientifically justified catalogue coverage, receptor geometry and cross-family
query-to-improvement instability. Archive the same H after feedback with the
existing `examples/archive_joint_feedback.py`.

## Reusable increments retained for a future identifiable catalogue

- `support.py`: explicit visible-prefix versus full-joint support, unknown loss
  intervals, paid cheap fallback, missing score preservation. No smoothing creates
  scientific worlds or counterfactual losses.
- Optional `support_guard` in the existing learner/controller fixes new planning
  and common evaluation cost weight at .001. Existing policies retain their old
  behavior. Complete-panel fitting still rejects incomplete scientific rows.
- `workflows.py` and optional bindings generalize score resources/receptor states
  while retaining the existing pool schema. The current generic score adapter
  is not a claim of online dynamic preparation support.
- `seen.py`: explicit coordinator later-round release with old independent
  Artifact parents, exact old task labels and new split binding. Original
  independent records remain immutable and barred from direct diagnostic input.
- `transfer_checks.py`: common-cost/cold-start comparisons and equal-group
  outcome-to-improvement diagnostics with group omission. Seen omission results
  are development checks, not independent validation or causal identification.

Tests use explicitly synthetic fixtures for protocol and accounting invariants.
Actual preparation, online scoring, independent evaluation and feedback require
separate Linux receipt evidence; test success is not scientific validation.


## Certification-only identity repair accounting

`preparation_cost_attribution(journal, admission_id)` verifies the original paid
receipt and follows only `recipe.operation=certification_only` with explicit
`reused_execution_id` and matching `reused_preparation_receipt` refs in request
inputs/result artifacts. It rejects changed task/state/resource/protocol/frame,
native bytes, parent references and cycles. Each distinct execution is counted
once. New certification admissions pin `cost_attribution` with total
`wall_second`, individual `execution_costs`, and `parent_state_charged=false`.
Original native admission bodies and each receipt's own `cost` remain unchanged.

The retained policy, predictions, diagnostics and evaluation use this verified
attribution, including original native preparation and new certification cost.
The scientific B parent is excluded from the same-resource lineage: matched B
start already includes p0; cold P separately includes p0+p1. Global collection
retains failed identity attempts and prior preparation failures. Repair creates
new policy/plan/attempt files; it never rewrites the failed prediction freeze.


The optional feedback arguments `--support-replay` and
`--support-replay-driver` retain a read-only old-evidence support audit and its
exact public driver. `examples/replay_support_round4b.py` verifies a delivered
read-only H archive, the original model/rows/predictions, real score artifacts
and original fees. It passes only actual online query history to the historical
controller; complete post-freeze outcomes enter a separate offline report.
Feedback checks the original model and independent row role in the same H. This
now-seen model-support challenge is distinct from the query-to-net-gain audit;
neither is a newly fitted model or a new independent test. The three quote-only
fallback scenarios do not create measured receipts or erase original costs.
