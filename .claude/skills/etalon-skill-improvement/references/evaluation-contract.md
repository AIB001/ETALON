# Optional fixed evaluation rules

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
