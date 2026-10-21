---
name: etalon-campaign-monitoring
description: Inspect long-running ETALON jobs and distinguish progress, stalled work, partial results and uncertain outcomes before recovery.
---

# Inspect a job and choose whether to intervene

Use this capability for an existing job, batch or uncertain response. A read-only
diagnosis may be the entire task. It does not require a generation, screening,
calibration or campaign-planning step and grants no new execution authority.

## Pick the matching existing identity

- For a durable workflow use `etalon_workflow_status(workspace, job_id)` and,
  when relevant, its observe/artifact tools. Inspect worker/process identity,
  heartbeat, node state, receipts and resource usage as separate facts.
- For an action facade, `etalon_action_result` requires the complete handle
  returned by `etalon_action_submit`. An offline replay action ID is not that
  handle; use `etalon_active_status(database)` and committed campaign events for
  an offline campaign.
- For a standalone screen use `etalon_screen_status(workspace, run_id)`;
  `etalon_sweep_progress(workspace, run_id)` supplies stage progress.
  For a real sweep ledger, `etalon_sweep_status(workspace, pool, ledger)`
  exposes batches/reservations and cross-revision comparability.
- Optional event reads use `etalon_events_read` with
  `{"kind":"campaign","database":"/absolute/campaign.sqlite"}` or
  `{"kind":"runtime","workspace":"/absolute/runtime"}`. Keep the returned
  log-bound cursor; a sequence number from another log is not the same event.

## Interpret before choosing a mutation

Report execution, artifact verification, admission, cost and liveness separately.
For standalone screens, queued/running is the last journaled state, not a fresh
heartbeat. A successful worker can have `result.outcome=exhausted`; a zero-hit
population differs from unavailable/partial output. GPU occupancy and a missing
process alone do not determine scientific progress or outcome.

Stop after a sufficient read-only diagnosis. Unknown child state, missing
receipts or an interrupted client means unknown outcome; keep reservations and
do not submit a replacement calculation. Cancellation is a request, not proof
that all children stopped or that reserved money may be refunded.

If intervention is actually authorized, use the matching cancel/reconcile/recover
interface on the original job. Workflow reconcile defaults to `resume=false`
but still changes reconciliation state; it is not a status query.
`resume=true` can dispatch work. Recovery must retain original identities,
protocols, failures and actual costs; known partial charges do not justify
discarding the unresolved remainder. A later new protocol needs its own request.

Give the evidence-supported status and decision. Host scheduling is optional
when requested; ETALON's execution worker is not a background research planner.

## Several known handles in one local check

For an existing complete `etalon-action-handle/1`, the direct SDK call is
`etalon.runtime.api.read_action_result(handle)`. No discovery or skill record is
required. A bare job/action ID is insufficient: retain the original schema,
backend, workspace, request_id, job_id, plan_id and node_id returned by submit.

You may write or modify a local script to read a finite list of handles. Keep
per-item errors distinct from failed executions; one unreadable item need not
hide the others. A read error leaves its execution status and cost unknown.
Repeated handles read the same execution, so do not sum repeated or nonadditive
cost references. Preserve pending reservations, partial charges, units and nulls.
Return concise status/verification/accounting plus a path and hash of each full
result when that helps the next decision. This is a result snapshot, not a second
ledger; inspect full receipts before scientific inference or recovery.

In a source checkout, `examples/local_sdk_batch.py` is a small editable example:
`python examples/local_sdk_batch.py --handles handles.json --output new-directory`.
It consumes a JSON array of complete handles, saves each original result/error
to a new directory, and returns summaries and original-file hashes. It is a repo
example, not a promised wheel entrypoint. An installed package can call the SDK
directly. Neither form submits, retries, reconciles or releases reservations.

Read [historical monitoring and recovery cases](references/legacy-workflow.md)
for a specific symptom or legacy sweep record. Machine loads and suggested
intervals there are historical examples, not universal operating limits.
