---
name: etalon-screening-sweep
description: Prepare and supervise bounded MolCascade screening batches, including calibrated gates, batch reservations, duplicate submission and recovery.
---

# Screening batches

Use this capability for batch screening when throughput, gate selection or batch
recovery is the present problem. The host may inspect results, alter a future
protocol or stop; there is no required generation-to-screening sequence.

- Inspect the installed cascade, its immutable revision and the actual input
  library. Choose components for the question rather than assuming default tiers.
- Before applying a gate at scale, use a relevant known-control panel and inspect
  both gate retention and score ranking. `etalon_calibrate_gates` and
  `etalon_authorize_gates` bind authorization to the tested revision and verdict.
  Calibration failure is evidence about the method, not evidence of no binders.
- Use the sweep claim/emit/record/admit interfaces for bounded batches; preserve
  their reservations and revision identity. Confirm completion and final
  population survival before admitting readouts as observations.
- Treat duplicate submission as the same request. Inspect status and recover
  abandoned work only when the actual worker outcome is established. Empty
  shortlists, partial output and unknown cost remain distinct outcomes.
- Reconsider gates, inputs or the research question when evidence warrants it.
  Do not rerun every batch solely to finish an earlier plan.

Read [historical workflow and calibration measurements](references/legacy-workflow.md)
for existing sweep ledgers, gate-token usage or diagnostic examples. The values
there describe particular panels and campaigns, not universal cutoffs.
