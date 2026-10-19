---
name: etalon-gate-calibration
description: Evaluate whether a proposed screening gate rejects known controls and whether its scoring engines rank a relevant calibration panel.
---

# Calibrate a screening gate

Use this capability when setting or changing a gate, or diagnosing a suspicious
empty shortlist. It is optional when no screening gate is involved.

- Identify the exact configuration revision and a relevant panel with documented
  provenance, endpoint units and independent evidence. Keep evaluation labels
  separate from materials used to revise a method.
- Run `etalon_calibrate_gates` and distinguish `admissible` (known controls survive)
  from `rankable_engines` (scores can order that panel). One does not imply the
  other, nor does either prove prospective binding activity.
- Inspect failures at the stage that removed the candidate. Do not silently
  convert missing or rejected results into successful observations.
- If the configuration is suitable for the authorized batch, obtain its
  revision-bound token through `etalon_authorize_gates`. Recalibrate changed gates;
  never reuse an authorization for another revision.
- A small panel supports bounded diagnostics. Preserve its limits, and propose
  another method or stop if it cannot answer the research question.

Read [panel construction and historical examples](references/legacy-workflow.md)
when designing controls or interpreting old calibration records. Historical
thresholds and correlations are evidence about their original conditions.
