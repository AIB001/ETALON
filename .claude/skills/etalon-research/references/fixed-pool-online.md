# Fixed pools, paid subsets and online visibility

Use a fixed prepared candidate pool when the question concerns a scoring
intervention or the next paid diagnostic subset. This method does not generate
poses, read labels to decide the next query, or make offline replay independent.

## Pure subset check

```python
from etalon.research.fixed_pool import subset_rank_outcome, two_chunk_catalog
result = subset_rank_outcome(candidate_ids=("a", "b"),
                            vina={"a": -2., "b": -1.},
                            vinardo={"a": -2., "b": None})
assert result["outcome"] == "failed"
catalog = two_chunk_catalog(initial_quote=1., first_quote=1., second_quote=1.,
                           loss_definition="synthetic task error")
```

The outcome compares within-subset pair order, including ties; scores from an
unpaid candidate cause rejection. Missing scores are failed, not unfavorable
numeric labels. This is a pure check, and does not itself establish that inputs
were paid. The optional two-chunk catalog declares a Vina initial prefix r0 and
two Vinardo chunks r1/r2 (six candidates in legacy workflow bindings); generalized
workflows require explicit validated bindings, not silently renamed resources.

## Visible receipts or one local composition

`rows.visible_receipt(journal, policy, *, task_id, admission_ids, probe_id=None)`
reads only exact initial resources plus the requested paid chunk. It checks
task/resource IDs, Artifact hashes and settled ledger cost. Use its returned
receipt with `transfer.next_action` or verify a saved history through
`rows.verify_visible_receipts(journal, policy, history, initial)`.

`online.execute_visible_task(policy, *, task_id, candidate_ids, acquire,
receipt_builder)` calls acquire(resource_id, reason) once per needed resource,
then receipt_builder(admissions, probe_id). It has no full row/label argument.
Callbacks own actual admission/verification. Returned resources and path cost
retain execution IDs; terminal score state can be partial/missing. It explicitly
acquires terminal resources after a stop decision when that workflow needs them.
It is not a globally pure function: callbacks may execute expensive work.

For an immediately runnable synthetic composition, run this skill's
`scripts/research_methods.py`. `visible_example()` derives actual subset outcomes
from fixture scores. `visible_example(missing=True)` gets failed from a missing
score, stops at abstain and leaves r2 unacquired. The callbacks are explicitly
synthetic and unadmitted; their .1 costs are not native timing measurements.

## Native fixed-pool execution and freezing

`pool.PoolClient(transport)` uses the explicit PRISM CLI transport; capabilities,
bind and run can invoke a producer process. `pool.validate(value, kind)` checks
pinned request/result schemas. `semantic_request(request)` removes only fields
excluded by the existing protocol identity, not scientific bindings.

`pool.execute(journal, client, request, *, root, task_id, stage, resource_id,
source_kind="new_execution", allow_dispatch=True, frozen_plan_id=None)` performs
resource reservation, native execution/adoption, hash/identity checks and ledger
settlement. `allow_dispatch=False` forbids a fresh dispatch but can still consume
and record an existing result. Preserve pending receipts, costs and failed slots;
a new request ID is not proof of an independent repetition.

`online.freeze_plan(journal, policy, manifest_path, input_freeze_path)` checks the
complete label-free test request catalog, split, candidate input hashes and
scorer/receptor/frame/grid, then appends a plan. It is not a read-only call.
`online.online_run(journal, client, policy, *, manifest_path, input_freeze_path,
root, output_dir)` appends intents, dispatches only visible resources, and writes
immutable predictions-frozen.json. It rejects a previously acquired matrix
masquerading as a new online run. Resuming the exact run follows its original
intents; it does not create new independent evidence.

`online.complete_counterfactual` takes the same arguments as online_run and is
a separate, additional acquisition after exact predictions are frozen. It may
incur cost and must fit the current authorization. Shared-panel controls stay
correlated offline comparisons; do not add their replay costs as new executions.

## Data assembly, retained baseline and support

`rows.config_objects(config)` restores GroupSplit/Catalog.
`rows.assemble_rows(journal, *, config_path, input_freeze_path, label_release_path,
oracle_path, admission_map, role, test_release=None, policy=None)` reads released
labels and executes a pinned oracle, checks paid admissions, appends Artifacts
and a taskrows record. It is not a harmless schema conversion. `verify_dataset(
journal, rows, role=..., split_sha256=...)` reads that exact dataset provenance
and settled costs. `seen.register_seen_labels(journal, path, labels, split)`
records already exposed labels; it does not restore unseen status.

`workflows.validate_bindings(binding, catalog)` checks explicit resource/scorer,
candidate-index, probe-comparison and terminal semantics. `bindings_for`,
`merged_scores`, `probe_outcome`, `terminal_scores` reuse those semantics;
`validate_receptor_binding(request, resource, task_freeze)` checks preparation
state/frame/grid. `legacy_bindings()` is only the historical six-candidate setup.

When intervention support is insufficient, `retained_baseline.freeze_retained_baseline(
journal, config_path=..., manifest_path=..., input_freeze_path=...)` freezes the
explicit cheap baseline. `run_retained_baseline(journal, client, config_path=...,
manifest_path=..., input_freeze_path=..., root=..., output_dir=...,
complete_counterfactual=False)` may execute that baseline and write predictions;
the optional counterfactual flag incurs additional work. `paid_score(journal, id)`
reads/verifies an existing settled score. These are available historical protocol
implementations, not permission to resume a paused R4b batch or evidence of a
new learned phi. In particular the retained-baseline driver is deliberately
bound to the reviewed twelve-task R4b catalog (including hif2a), its preparation
map, strategy name and fixed .001 weight; it is not a generic new-task runner.
For other tasks use the independent catalog/controller/pool APIs with explicitly
supported bindings, or report the concrete unsupported protocol. Use support
bounds to retain missing losses; never fit invented
complete worlds. Feedback is covered separately in validation-feedback.md.
