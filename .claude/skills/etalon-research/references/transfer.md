# Group-separated transfer and calibration

Use when complete paired task outcomes, scientific losses and resource costs
are available and the question is which finite policy to retain or inspect.
These are existing empirical finite methods, not a new optimizer. Skip fitting
when a supplied policy or one pure diagnostic already answers the question.

## Inputs and calls

From `etalon.research.transfer`:

```python
fit_policy(rows, *, split, catalog, budget, max_depth, cost_weight,
           static_order=(), calibration_prefix=(), calibration_selection=None,
           support_guard=None, workflow_bindings=None, evaluation_cost_weight=None)
calibrate_policy(policy, rows)  # returns (new_policy, report)
next_action(policy, history, *, mode="phi", static_order=None, initial_receipt=None)
evaluate_policies(policy, rows, *, role, static_order=None, release=None)
probability_assessment(policy, rows, *, role="calibration", visible_prefix=None, release=None)
```

`GroupSplit(tasks, grouping_evidence, protocol_sha256)` freezes task roles
train/calibration/test, family and group. At least two training groups are needed;
poses, seeds or pair counts are not independent tasks. `Catalog` declares probes,
resource quotes/unit, terminal resource identities, loss definition, abstention
and initial decision/resources. Use an actual frozen protocol digest in measured work.

Each row has `task_id`, `sample_unit="task"`, `independence_group`,
`source_kind="measured"` or `"synthetic_fixture"`, `outcomes`, `losses`,
`resource_costs`, `loss_definition`, `cost_unit`, `evidence`, and per-cell
`outcome_evidence/loss_evidence/cost_evidence`. `checked_rows(rows, split, role,
catalog)` requires every task of exactly that role and complete paired cells.
Unknown costs/losses are not imputable; an explicit failed outcome category is
different from a missing scientific loss. Source kinds cannot be mixed.

`fit_policy` consumes training rows only. `calibrate_policy` consumes the frozen
calibration role and a grid declared before fitting, retains the training model
and creates a new policy digest; repeated calibration is rejected. It changes
mixture probabilities/cost selection within existing worlds, not unseen support.
`next_action` sees only ordered receipts and the paid initial prefix, never the
complete row's unobserved query outcomes or terminal losses. Receipt fields are
policy_sha256, probe_id/outcome, newly charged resource_costs and evidence.
Real callers verify receipts with `rows.verify_visible_receipts` first; the pure
controller cannot establish artifact admission or actual payment from a string ID.

The returned query/stop is advice. Host execution is separate. A stop decision
can still require paid terminal resources; inspect `resources_acquired`.
`unsupported_history`/abstain ends the current suggestion unless an explicit
declared support fallback applies. Do not repeatedly call until a desired branch.

`evaluate_policies` replays phi0/phi/copy_best/static/myopic/nonmyopic on complete
observed panels and reports scientific loss and measured resource costs separately.
It does not execute those policies independently. Test evaluation requires an
independent release binding policy_sha256, split_sha256 and evidence. Neither
a supplied release dictionary nor synthetic test rows demonstrate real isolation.
Calibration comparison after calibration selection is development feedback,
not an unbiased estimate of generalization. `probability_assessment` reports
group predictive scores and unsupported joint mass; Brier improvement is not
mechanism calibration or proof of utility.

## Run an isolated example

Run `python <this-skill>/scripts/research_methods.py`, or load just one function:

```python
import runpy
# Set skill_script to the actual exported script path.
example = runpy.run_path(str(skill_script))
result = example["transfer_example"]()
assert result["executed"] is False
assert result["independent_gain"] is None
```

The script constructs all eight synthetic rows, group declarations and catalog;
no journal, producer or scientific dataset is opened. Its functions can be used
separately. Source checkout convenience: `python examples/research_methods.py`.
Installed/exported use: execute the bundled script with the installed interpreter.

## Persistence and adjacent diagnostics

`save_policy(policy, Path)` writes a policy; `load_policy(Path)` validates its
content identity; `unpack(policy)` restores dataclasses and verifies digest.
`record_policy(journal, policy)` appends provenance after evidence checks. Pure
fit/calibrate/next/evaluate do not append records or charge a ledger; Python CPU
cost still exists and is not measured by those functions.

For additional finite comparisons, `transfer_checks.cold_start_comparators(policy,
row)` and `outcome_improvement_diagnostic(rows, catalog, cost_weight=.001)` expose
existing descriptive controls. `support.loss_interval(None, [0, 1])` preserves
unknown loss as an interval; `bounded_panel(row, decisions=..., loss_bounds=...)`
retains incomplete panels but does not make them learner eligible.
`support_report(model, observed, bounds)` checks joint visible-prefix support;
marginal support is insufficient. `validate_guard`/`fallback_action` implement an
explicit catalog baseline guard, not permission to invent external-world mass.
