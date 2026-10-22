# One bounded method revision

An RSI cycle starts with an observed problem and a fixed base version. The host
may propose one local method/workflow change, measure it against the base, then
adopt within a declared scope, retain the base, or return to a prior version.
Recording a result does not start another cycle.

Reuse `MethodVersion`, `MethodUse`, `build_method_revision` /
`record_method_revision`, `composition.record_evaluation` and existing bounded
`host.consume_events`. The candidate producer G is separate from the target S/U;
host-reported attribution does not establish independent G benefit.

`method_revision.resolve_method_revision` binds an existing revision and
MethodEvaluation to `adopt`, `retain` or `rollback`. It records an ordinary
decision research-step and successor MethodUse. Adoption requires an exact
evaluated candidate, actual result references and accounting. Rollback requires
an earlier candidate use and explicit counterevidence. The host is responsible
for interpreting the comparison; this helper does not infer scientific truth
from a score, change defaults, or execute the selected method. The caller then
applies the chosen version explicitly and records that actual subsequent use.

Old records, methods, ongoing tasks and failures retain their versions. Replaying
the same resolution returns the same IDs. A file/DB append can leave verified
intermediate artifacts, as with existing research helpers; retry is idempotent.

The public `examples/research_rsi.py` exercises prepare → worker → finalize:

- Register a real earlier continuation trace with repeated source reads; freeze
  protocol, method implementations, source identities and a single candidate.
- Consume the concrete problem event with the existing finite hook. Stable event
  identity and exact problem selection prevent MethodUse/evaluation writes from
  recursively generating more candidates. The current host authored the code;
  this example registers that candidate rather than impersonating a new model.
- Compare the existing fresh-read method with an opt-in frozen-view method in
  paired fresh processes. Keep exact JSON equality and measured wall time
  separate; failed or missing trials cannot become an improvement.
- Record diagnostic evaluation, scoped disposition and a real later read using
  the selected version. Publish a checkpoint that preserves the original pause.

The caller supplies the immutable protocol JSON with problem path, two explicit
seen case entries/identities, eight pairs per case, `minimum_median_gain=0.2`,
quality-test receipt, exact loaded module paths/hashes, and paused status path.
Workers write raw outcomes; the orchestrator preserves commands/process costs in
`process-costs.json`. A missing trial blocks finalization for review; a completed
failed trial prevents adoption. No scientific or native-model holdout is implied.

## Candidate read method

`resume_research(..., read_mode="fresh")` remains the default. The optional
`read_mode="frozen_view"` reuses one complete closed view only when all visited
sources are bound by snapshot hashes. It falls back for live, partial or broader
dependency scopes. Every call starts from disk; sources, projected artifacts,
members and current authority are still checked. A detected mid-read source,
projection or WAL change requires review. It provides the same per-source read
consistency boundary as the existing interface, not a global atomic snapshot.

Any adoption is limited to the measured workload. Throughput gains do not imply
improved scientific conclusions, independent transfer, reduced model tokens or
net benefit after development/evaluation cost. Report those costs separately and
keep a no-change outcome when the fixed comparison does not justify adoption.
MethodEvaluation's independent-validation checks are unchanged. Resource
allocation, generic golden admission and automatic default promotion are outside
this increment.
