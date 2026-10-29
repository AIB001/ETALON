# Improvement from existing evidence and normal execution

Use this optional guide when an actual research step reveals a method or
workflow problem. The host may retain the current method. No fixed Probe stage
or automatic optimization follows a journal write.

Normal execution can be successful yet unnecessarily expensive. Existing screen
status exposes `progress.execution_feedback` (or `Screen.progress`'s
`execution_feedback`) from checkpoint counters; these are observations, not a
forced trigger. A selected snapshot can be ordinary problem evidence below.
For cost and adoption timing use `etalon.economics.execution.compare_execution_costs`:
include development and failed trials, distinguish current remaining work from
future amortization, and retain unknown token/cost fields. The optional
skill-improvement execution-efficiency reference explains how to investigate the
limiting operation. Unchanged reads do not warrant repeated proposals.
Compare the observed CPU/GPU phase with the task's available resources, not
hardware totals. Reuse the existing host completion handle; when absent,
`etalon_wait_register` shares bounded observations and `etalon_action_result`
refreshes only when due. Neither waiting nor low utilization forces an RSI attempt.

Large runs still require observing throughput/reuse after a representative first
completion and material phase changes. Existing status returns `resource_review`
with a stable condition key; save one disposition in the current note/checkpoint
and reuse it while unchanged. This is a host continuation obligation, not an extra
scientific stage or a gate. Mixed stages report GPU lanes and CPU pool separately;
partial quality progress does not imply that the whole docking batch is published.

1. `improvement.build_revision_context(workspace, task_id=..., scope=...,
   conditions=..., target_ref=..., problem_refs=..., evidence_refs=...)` reads
   only the selected existing evidence. Inspect missing evidence and originals;
   summaries are previews. Publish with `continuation.record_content` if useful.
2. `register_opportunity(journal, context_ref, directory, source_id=...,
   source_kind=...)` deduplicates the exact problem/target/conditions/evidence
   set across events and chats. `resume_pending` means recover the same request,
   not generate another candidate. `already_proposed` returns the prior patch.
   Use its stable request ID with any existing idempotent executor. This helper
   does not invoke a model or promise exactly-once external execution.
3. Save a complete candidate as an existing MethodVersion. `build_method_patch`
   checks its base lineage and produces a manifest and optionally a new workflow.
   `apply_workflow_edits` checks exact before values; publish the returned graph,
   fill `workflow_after_ref`, then publish the patch. Never mutate an old graph.
   S/U/G remain methods, not fixed agents; composition rules can be versions too.
4. Continue ordinary authorized work. Attribute actual use with MethodUse and
   `host_conditions`; link outputs, receipts and accounting through a node step.
   `assess_revision` checks these against the candidate and evaluation plan.
   With insufficient evidence it returns `awaiting_evidence`, without execution.
   Failed normal executions remain failures available for evaluation.
5. Use the existing MethodEvaluation and scoped resolve_method_revision after
   interpreting the evidence. `ready_for_evaluation` is not a success score.
   Baseline problem evidence is not evidence of candidate benefit; historical
   comparisons remain observational unless independently validated.
6. `record_experience` retains applicable conditions, lesson, invalidation,
   support and counterevidence, including retain/rollback/unresolved outcomes.
   `retrieve_experience` takes explicit source IDs, capability and conditions.
   Follow pagination and inspect supersedes/conflicts; no match or incomplete
   coverage never means no useful experience exists. Only the host selects.

These are typed ordinary Artifacts, readable via existing query/read SDK and
MCP. Checkpoint branch basis/next_read_refs can point to context, opportunity,
patch, assessment and experience. A new chat first calls resume_research, then
reads these references. A resolved or awaiting-evidence branch is not a request
to repeat generation, execute an old task, or reopen a paused batch.

For a known evidence reference, `improvement.read_material(workspace, ref)`
reads a record or generic JSON artifact with the existing identity-checked
read_evidence interface. It returns `complete`, `content`, and bounded fragment
metadata. Inspect complete/fragment; do not treat truncated bytes as full data.
`read_content` is for supported typed research schemas, not arbitrary results.

Scientific identity, protocol, missing outcomes and pause authority stay with
their existing interfaces. Component SDK calls need no new mandatory wrapper.
