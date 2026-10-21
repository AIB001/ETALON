---
name: etalon-gate-calibration
description: Evaluate whether a proposed screening gate rejects known controls and whether its scoring engines rank a relevant calibration panel.
---

# Diagnose or revise a screening gate

Use this capability to inspect a gate on a relevant control panel, compare
retention with ranking, or explain an empty shortlist. A task with no gate can
skip it. Reading an existing panel run does not require generating new molecules,
running another screen, allocating a campaign or requesting a gate token.

## Inputs and returned evidence

`etalon_calibrate_gates(workspace, run_id, panel_json, required_recall=1.0)`
reads an existing MolCascade panel run. Supply an absolute workspace, its real
run ID and explicit panel members:

```json
[{"parent_id":"control-a","known_active":true,"evidence":"documented source"},
 {"parent_id":"control-b","known_active":false,"evidence":"documented source"}]
```

Names must match the screened molecules. The caller chooses a relevant panel
and justified recall requirement; unavailable labels stay unavailable rather
than being invented to obtain a verdict. Keep development/calibration labels
separate from independent evaluation evidence.

The SDK `etalon.campaign.calibrate.calibrate(screen, result, panel,
required_recall=...)` uses the same existing run. `separation` and `enrichment`
are independent numerical helpers for their corresponding explicit score/
reference inputs; do not infer their input shape from a different helper.
The report binds `revision_id/run_id`, panel coverage, per-stage losses,
`admissible`, `rankable_engines` and notes. Gate retention, ranking quality and
prospective activity are three different claims. Missing coverage, unreadable
retention, NaN or uninformative ties cannot be replaced by a successful verdict.

## Revision, authorization and stopping

Use the actual loss stage and direction to decide whether to abandon the gate,
change a future threshold, gather a relevant panel, or stop. A completed but
exhausted panel run can be informative; it is not evidence of no binders.
Changing the gate creates a new revision whose behavior must be checked; never
reuse the old token or rewrite the old result.

Only when a downstream sweep needs authorization, `etalon_authorize_gates`
returns a revision/verdict-bound `gate` or a refusal. That deterministic check
remains in the kernel. A refusal is not fixed by lowering recall merely to pass.
Do not rerun a panel just because a read/diagnosis was requested. If coverage,
identity or the intended ranking is unsupported, report that limit and stop or
choose another justified method; a small-panel diagnostic is not independent
evidence of improved screening.

For runnable independent `separation/enrichment` input shapes and reading an
existing panel, read [local SDK examples](references/local-sdk.md).

Read [panel construction and historical examples](references/legacy-workflow.md)
when designing controls or interpreting old calibration records. Historical
thresholds and correlations are evidence about their original conditions.
