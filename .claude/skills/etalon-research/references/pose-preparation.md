# Pose diagnostics and preparation evidence

Use read-only diagnostics when existing admitted poses or preparation receipts
answer the question. Molecular graph/state, pose/slot, receptor state, coordinate
frame, preparation and execution identity are separate. A graph edit is not a
3D pose; certification is not a new native preparation or independent replicate.

## Diagnose existing poses without generating any

```python
from etalon.research.pose_selection import selection_diagnostic
result = selection_diagnostic([
    {"id": "synthetic-a", "slot": 0, "admitted": True,
     "score": -2., "mode_rmsd_A": {"reference-mode": 3.}},
    {"id": "synthetic-b", "slot": 1, "admitted": True,
     "score": -1., "mode_rmsd_A": {"reference-mode": .5}},
], score_key="score", minimize=True, tie_tolerance=1e-6, loss_cap=6.)
assert result["selected_pose_id"] == "synthetic-a"
assert result["single_pose_objectives"]["nearest_mode"]["same_loss_regret_A"] == 2.5
```

This pure call uses supplied rows and does not verify provenance itself. Real
inputs must already have admitted measurements and released reference-mode
RMSDs. Keep the attempted-slot denominator and unscored admitted IDs. Score ties
use the declared tolerance and minimum global slot. No admitted/scored poses
returns no_admitted_pose/no_scored_pose; do not claim a pose was selected.
Nearest/worst single-pose objectives and ensemble coverage are different metrics.
Neither docking score nor structural regret establishes affinity or mechanism.

## Read or admit preparation receipts

```python
from etalon.research.preparation import paid_preparation, preparation_cost_attribution
prepared = paid_preparation(journal, preparation_admission_id)
cost = preparation_cost_attribution(journal, preparation_admission_id)
```

Both read/check existing original settled charges and artifact bytes.
`preparation_cost_attribution` follows certification_only reused-preparation
lineage once, rejects cycles/mismatched task/frame/native artifacts and excludes
scientific parent_state from reuse cost. A cold strategy may separately require
both p0 and p1; do not sum already included native cost again.

`admit_preparation(journal, *, request_path, result_path, reservation=None,
invoke=None)` is a mutating admission boundary. When starting authorized work,
the supplied `invoke(request_path, result_path)` executes only after reservation.
When a prior execution exists, omission of invoke reads its terminal receipt;
missing receipt remains ExecutionPending with the original reserve, never a
redispatch. An existing receipt without original reservation is refused rather
than retrospectively fabricating a charge.

Request/result JSON use the pinned `prism-receptor-preparation-*/1` schemas.
Pass absolute content-bound files, task/state/resource/execution IDs, protocol,
recipe and explicit parent_state. `PREPARED`, `NOT_APPLICABLE`, `FAILED` retain
cost and reason; scientific_loss remains null. Grid uses center_A/size_A/spacing_A.
Identity validation may fail after a real charge has settled; failure does not
refund executed work. `validate(value, kind)` and `checked_file(ref)` allow
isolated schema/hash checks without invoking a preparer.

## Existing external adapters, only for authorized native work

`pose.PoseClient(transport)` and `producer.DiagnosticClient(transport)` expose
capabilities/bind/run. Transport is `etalon.boundary.prism_v1.CLITransport` with
explicit interpreter and source paths. Capability calls can launch the producer
CLI; they are not purely local metadata. Preserve interpreter path without
resolving a Linux venv symlink. Do not import a different PRISM checkout implicitly.

`pose.execute(journal, client, request, *, root, task_id, stage, source_kind,
decision_sha256=None)` executes/adopts a pose request through central resource
accounting and admission. Stage and source_kind retain development/validation
and new/adopted artifact meanings. Validation execution must bind the frozen
decision and request, using `validation_request_binding` checks. The request
distinguishes graph/mapping/pose, receptor/frame and attempted slots. All failed
slots remain in denominators. `measurement_methods(request, modules)` derives
readout identity; `slot_identity(requested_target, produced_identity)` retains
the requested identity when production fails.

`producer.execute(journal, client, request, *, root, task_id, source_kind)` is the
older fixed-core structural adapter. It reserves, invokes/verifies, settles and
appends evidence; it does not provide a general affinity or free-energy method.
Both adapters preserve unresolved reservations and prevent blind retry. Use
their current pinned wire schemas rather than inventing request dictionaries.
The historical `examples/pose_round3.py` and `diagnostic_round3.py` are optional
compatibility callers; they are not the entry sequence for a new scientific task.

Synthetic wire-receipt tests in `test_research_preparation.py` check all three
statuses, pending/late receipts, replay and certification accounting. Native
producer execution is a separate validation scope and is not run by this skill's
portable example.
