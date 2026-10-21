# Released validation and later feedback

Use a released comparison to append evidence to the exact frozen decision, or
interpret an already evaluated transfer/baseline run. Do not read unreleased
labels to manufacture the example. These functions append records, not scientific
scores or automatic policy updates. After use, independent evidence becomes
seen development feedback for future work; its original record remains unchanged.

## A decision comparison

```python
from etalon.research.validation import attach_decision_validation
receipt = attach_decision_validation(
    journal, decision_sha256=decision_sha256,
    evidence=tuple(released_artifact_ids), release_evidence=release_artifact_id,
    protocol=validation_protocol, comparison=actual_comparison,
)
```

Inputs refer to an existing `decision-<sha>` record with a checked freeze and
`independent_validation_accessed=False`. Every referenced Artifact must have
role independent_validation and bindings.decision_sha256 equal to this decision;
files are hash checked. Protocol and comparison cannot be empty. Supply actual
release authority/provenance; the API cannot prove pretraining isolation.

The result retains parent_decision_unchanged, promotion="none",
phi_transfer="not_evaluated", future_use="seen_development_feedback_only".
Replay is idempotent. It does not rerun an oracle or charge historical computation
again; local attachment compute cost remains null unless separately measured.
Wrong roles, changed files or mismatched freezes stop attachment. Do not rewrite
the old decision, assign zero loss to failures, or describe this as phi transfer.

## Transfer feedback with immutable predictions

`feedback.attach_transfer_feedback(journal, policy, *, rows_path,
predictions_path, independent_release_path, calibration_report_path,
seen_audit_path, assay_source_audit_path=None, transfer_audit_path=None)` consumes
Path objects for exact released files. It checks policy/split/prediction hashes,
original journal predictions, training/calibration lineage and admitted paid row
evidence; it calls existing evaluation/probability methods and appends feedback.
Keep all source audits, failures, complete acquisition costs and path costs.
An assay source correction or an archived negative result is not a new training
update. Measured policy feedback requires its original registered policy; do not
replace missing provenance with a fixture release or another ledger.

For a retained baseline rather than a learned policy:

```python
from etalon.research.baseline_feedback import evaluate_retained, attach_retained_feedback
evaluation = evaluate_retained(
    journal, policy_path=policy_path, predictions_path=predictions_path,
    diagnostics_path=diagnostics_path, label_release_path=label_release_path,
    independent_release_path=independent_release_path, oracle_path=oracle_path,
)
# A separate call after the actual coordinator audit exists:
feedback = attach_retained_feedback(
    journal, evaluation_path=evaluation_path, coordinator_audit_path=coordinator_audit_path,
    seen_gain_audit_path=seen_gain_audit_path,
    support_replay_path=support_replay_path, support_replay_driver_path=support_replay_driver_path,
)
```

These variables denote real existing files, not generated placeholder grants.
`evaluate_retained` loads the pinned oracle and released labels: that is an actual
code execution/data-exposure side effect, so it is outside a read-only review or
paused run. Evaluation is returned, not automatically written to evaluation_path;
the caller persists it before the attachment call. Optional audits/replay remain
optional; absence stays explicit. Feedback never promotes a method automatically.

`controller.independent_feedback(journal, controller_id=..., decision_sha256=...,
validation_id=...)` interprets a prior registered finite controller and released
decision validation, appends an archive-linked diagnosis, and can return missing
capability/quote requirements. It does not dispatch that diagnostic or train phi.

### Existing decision validation: a local feedback example

For an already attached `etalon-decision-validation/1` record, the relevant
entrypoint is `controller.independent_feedback`. The transfer and retained-baseline
functions above consume different policy/run schemas. There is no need to run
them to process an existing decision validation.

The optional [released-feedback example](../scripts/released_feedback.py) accepts
an existing configured, caller-owned **writable copy** of a campaign database,
trusted validation and explicit identifiers. Run from the exported skill directory
with an interpreter containing ETALON:

```sh
python scripts/released_feedback.py --database writable-copy.sqlite \
  --controller CONTROLLER_ID --decision DECISION_SHA256 \
  --validation VALIDATION_ID --output feedback-receipt.json
```

It checks the existing record identities, then replays the attachment to verify the original file hashes, roles and
decision freeze, then appends controller feedback. It does not construct a new
comparison or execute an oracle. Keep the original Artifact files reachable at
their recorded paths when copying the database. A mismatch returns a retained
error receipt with exit 2; unchanged replay returns the same feedback identity.
Use a new output path for each request; an existing receipt is never overwritten
and is rejected before database access. Keep the writable copy and evidence stable
while running; this example does not coordinate concurrent writers. If output
writing fails after the SDK append, inspect the archive and replay to a fresh
receipt path; the append and filesystem receipt are not one transaction.
The output retains the comparison, including failed/missing values and historical
costs, without recomputing them. Local wall time is separate from these costs;
unknown currency cost remains null. Controller diagnosis uses a finite molecular
diagnostic vocabulary, not a learned general explanation or new scientific gain.

Use or adapt only the relevant calls; this example is not a required workflow.

For general frozen scientific policy records use
`journal.record_validation(policy_id, task_id, initial_configuration_sha256=...,
evidence=..., comparison=...)`; its TaskSplit and role checks are different from
the decision and transfer APIs above. It records a comparison, not superiority.
For optional skill S/usage/improvement evaluation use MethodEvaluation, not a
scientific policy record with relabeled fields. The skill-improvement entry
describes that separate method use.

Engineering checks are in `tests/test_research_validation.py`,
`test_research_transfer_feedback.py`, `test_research_baseline_feedback.py`, and
`test_research_feedback_analysis.py`. They use synthetic or isolated public
fixtures, not proof that the currently paused science was completed.
