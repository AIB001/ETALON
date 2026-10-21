---
name: etalon-generation-planning
description: Compare or retire molecular generators using measured valid unique yield, real-pocket feasibility, cost and uncertainty.
---

# Choose, reuse or stop generation

Use this capability to interpret measured generation yield or choose a future
allocation. Existing compounds may already answer the question: skip generation
entirely in that case. Productivity analysis does not launch a generator and does
not require a screening campaign, receptor simulation or complete funnel plan.

## Inputs and callable method

`etalon_generation_productivity(loops_json, cost_ceiling=2.0)` accepts a JSON
array of measured loops. A minimal row has this shape:

```json
{"tag":"loop-a","model":"generator-id","pocket":"geometry-and-box-id",
 "chunks":[{"requested":100,"delivered":90,"unique":72,"seconds":60}]}
```

`tag` identifies the loop, not just the model. `unique` means new to the whole
library after deduplication, so it cannot be inferred from generator output
alone. Counts must satisfy the input contract and seconds must be positive.
Do not merge different boxes or protocols to make a weak loop appear viable.
Supply actual measurements; a label or guessed throughput is not a measurement.

The same independent SDK method is `etalon.generate.productivity.allocate`
over `Productivity` objects containing `Chunk` records. The returned per-loop
delivery/uniqueness intervals, viability/exhaustion reasons and allocation are
numerical advice. No `Campaign` or `Supervisor` construction is required.
The broader `etalon_campaign_plan`/`etalon_plan_campaign` suggestions are optional
when a whole campaign allocation is actually the question.

## Decide from the result

Separate poor delivery for a model/geometry pair from diminishing unique yield.
The current rule needs at least three chunks to declare exhaustion; a shorter
window is not evidence that exhaustion is absent. A null cost multiplier means
no unique delivery and an unbounded multiplier, not zero cost. `cost_ceiling`
is a relative duplication-cost multiplier, not permission to spend GPU-hours.
Reported elapsed production and future allocation are distinct from a charged
execution receipt or future budget reservation.

When feedback does not support another generation batch, stop that route or use
existing molecules. If a geometry change or different generator is justified,
it is new conditional work within the actual resource authority, not an
automatic remedy or mutation of old measurements. Retain failed/invalid/
duplicate output and provenance. Neither unique yield nor hit share establishes
affinity, and a revised generator is not superior merely because it was saved.

For a runnable `Chunk/Productivity/allocate` call and existing-library planning,
read [local SDK advice](references/local-sdk.md).

Read [historical generator measurements and operating notes](references/legacy-workflow.md)
for production diagnostics or interpretation of previous runs. Its hardware,
throughput and model-specific advice require validation in the current setting.
