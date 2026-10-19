---
name: etalon-generation-planning
description: Compare or retire molecular generators using measured valid unique yield, real-pocket feasibility, cost and uncertainty.
---

# Choose and stop molecular generation

Use this capability when generation feasibility or allocation is the question.
A generator is an optional method; existing compounds or a different scientific
approach may be more informative.

- Measure the actual pocket, batch size and backend environment before scaling.
  Inspect `etalon_generation_productivity` for viability, valid unique yield and
  exhaustion. A fast generator that cannot fit the requested pocket is not useful.
- Compare throughput and diversity within the correct generator family and
  conditions. Raw molecule or hit counts are not comparable evidence of affinity.
- Bind candidates and preparation provenance to the producing method/version.
  Keep invalid, duplicate and failed production visible with their costs.
- Change allocation or stop a route when marginal information or unique yield
  no longer justifies its cost. No fixed quota of generators must be completed.
- Treat a revised generation method as a candidate requiring independent later
  evaluation, not an improvement established by the development run.

Read [historical generator measurements and operating notes](references/legacy-workflow.md)
for production diagnostics or interpretation of previous runs. Its hardware,
throughput and model-specific advice require validation in the current setting.
