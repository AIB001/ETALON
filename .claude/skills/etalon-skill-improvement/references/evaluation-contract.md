# Optional fixed evaluation rules

Use this when a concrete comparison would change the next action. Reuse ordinary
results before dispatching anything else. A short host review can record the
external quality endpoint, comparability, measured changes, complete costs,
contrary results and the next action without creating a contract or calling an
additional model. The code computes declared metrics and checks; the host judges
their scientific meaning and applicability. Routine reading needs no judge call.

Use `etalon.research.evaluation_contract.build_evaluation_contract` to declare
named exact result/accounting inputs, protocol and initial-condition refs,
comparability checks, required execution checks, numeric metrics, denominators
and provenance. Fixed JSON pointers and four operations (`value`, `difference`,
`abs_difference`, `ratio`) avoid executing expressions supplied by a resource.
The source distribution documents the fields in
`docs/thin-harness/EVALUATION_CONTRACT.md`.

Replay through `etalon_method_evaluate(contract=..., entry=...)`, or replace the
inline declaration with `contract_ref`. Supply exactly one entry, workspace or
pinned `read_context`. The report preserves individual metric values and reasons
for `criteria_failed`, `insufficient` or `incomparable`. `criteria_passed` means
only that the declared checks passed on these originals. It does not establish
scientific truth, improvement causation or applicability to a new task.

An insufficient or failed rule should identify what remains unknown and what
could resolve it: revise a local assumption or operation, collect a discriminating
result, continue exploration, retain the current method, or stop under the task's
conditions. Avoid replacing an unsupported claim with an invented numeric score.
Exploratory use within the authorized task can continue before adoption is
justified. An explicit adoption decision remains separate from measured benefit;
scope it to the conditions and endpoint that the comparison actually supports.

For example, SND1 solver-cost evidence requires equivalent outputs and unchanged
checks before comparing wall time or energy-call counts. A PoseBusters ranking
comparison needs all attempted pairs, failures and lost valid poses; fewer CNN
calls alone is not quality improvement. Do not exchange these objectives or
transfer thresholds merely because both workflows handle molecules.

`record_evaluation_artifact` optionally archives a contract/report using the
existing Journal Artifact helper; reports are recomputed before recording.
The resulting ref can be passed to existing `composition.record_evaluation`,
revision resolution and scoped experience records. Those existing operations
remain the explicit publication/adoption path. Direct SDK use and no change
remain valid. Nothing here adds a fixed Probe phase or execution prerequisite.
