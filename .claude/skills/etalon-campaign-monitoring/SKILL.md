---
name: etalon-campaign-monitoring
description: Inspect long-running ETALON jobs and distinguish progress, stalled work, partial results and uncertain outcomes before recovery.
---

# Inspect and recover a bounded job

Use this capability for an existing long job or recovery question. Read status
and receipts first; monitoring does not authorize new scientific execution.

- For durable workflows inspect status, heartbeat, process identity, node states,
  artifacts and actual resource usage. For sweeps inspect batch progress and the
  reservation ledger. GPU utilization alone cannot establish scientific progress.
- A finished chunk can resemble a crashed process. Establish ownership and the
  actual result before claiming a failure or replaying work.
- Cancellation and lost responses do not imply that scientific children stopped.
  Retain unknown outcomes and reservations until receipt-based reconciliation.
- Recover through the corresponding workflow/sweep interface. Preserve failures,
  costs and old evidence; an existing authorization does not permit changing the
  submitted job's protocol in place.
- Return a concise status and the next decision supported by evidence. Use a host
  scheduling mechanism only when monitoring is requested and supported; ETALON's
  worker is not an independent research planner.

Read [historical monitoring and recovery cases](references/legacy-workflow.md)
for a specific symptom or legacy sweep record. Machine loads and suggested
intervals there are historical examples, not universal operating limits.
