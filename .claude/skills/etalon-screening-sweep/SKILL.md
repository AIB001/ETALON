---
name: etalon-screening-sweep
description: Prepare and supervise bounded MolCascade screening batches, including calibrated gates, batch reservations, duplicate submission and recovery.
---

# Screen an existing batch or manage a sweep

Use this capability for a concrete library/configuration, a batch outcome, or a
sweep reservation question. Existing molecules can be screened without generation,
campaign allocation, simulation or an active-learning loop. Select only the
components needed for the question; neither default tiers nor this skill are a
universal prerequisite.

For multiple receptors or later tiers, inspect existing typed preparation before
submitting another raw-library run. `etalon_screen_prepare_continuation` reuses
parents and conformers; `through_stage="conformers"` explicitly branches from a
committed preparation prefix even while later docking runs. Otherwise it selects
the completed run's final parents. Keep receptor-dependent docking and checks in
each branch. Different chemistry/preparation settings require a new preparation.
For a paired receptor comparison, branch before receptor-specific selection so
both arms receive the same prepared population; a final shortlist is a different
population and should be selected only deliberately.

When status exposes a new `execution_feedback.improvement_opportunities` item,
use its exact skill address if that procedure is not already available. For slow
or repeated work this is host skill `etalon-execution-tuning`, MCP slug
`execution-tuning`. Apply it once to the concrete observation, or reuse the
recorded decision while its basis holds; do not restart optimization on every
poll. For a scientific limitation use `method-optimization`. Reading a skill is
not evidence that an improvement was applied or helped.

## Inputs and independent operations

- For one existing batch, supply absolute `config_path`, `workspace` and, for a
  cascade, `library_path`. A flat pipeline can carry its own inputs.
  `etalon_screen_plan` compiles dependencies and returns input-bound `plan_id`;
  it neither executes nor estimates/reserves cost. The matching SDK entry is
  `etalon.screening.plan_screen(config, library, workspace)`.
  Read `plan.execution_contract` for the compiled budget/scaffold allocation and
  actual redock gates; the requested library size is not an affinity criterion.
  For a new task requiring per-gate SMILES and coordinate delivery, pass
  `delivery={}` to both plan and submit. The worker writes these outputs when the
  run ends. For a requested final strict `<2 Å` redock protocol, use
  `delivery={"require_redock":true,"rmsd_threshold":2.0}` and an appropriate
  redock gate in the configuration. Early library-only work need not redock.
- If actual execution is authorized, `etalon_screen_submit` accepts that plan,
  a new `run_id`, workers and devices. Identical request replay identifies the
  same job; changing a submitted protocol is not a retry. These detached workers
  require their supported POSIX environment. The direct screen interface does
  not implicitly debit an active-campaign budget or impose a cost cap.
- `etalon_screen_status(workspace, run_id)` is sufficient to inspect an existing
  batch without submitting anything. If a pool already uses the sweep ledger,
  use its `etalon_sweep_status/emit/claim/record/admit/recover` operations with
  the actual workspace, pool and ledger identities; no ledger is required merely
  to inspect a standalone screen.
- A sweep gate token binds a tested revision and calibration verdict.
  `etalon_authorize_gates` supplies it to `etalon_sweep_emit` when applicable.
  Calibration/authorization is about that gate, not a requirement to run an
  unrelated generator or simulation.

## Interpret the returned evidence

Inspect `result.outcome`, not only the terminal job state: `committed` has a
final population, `exhausted` means none survived, and `failed` means failure.
A worker can finish successfully with an exhausted screen. Missing/partial
artifacts are not zero hits. `cost_status=not_metered` is unknown cost, not free
execution; retain measured or declared costs from their actual source.
Confirm final identity, protocol and population evidence before admitting a
readout; a shortlist file is not automatically an accepted observation.
`result.delivery` reports output completion separately. If it failed, finish
exporting the committed artifacts; do not restart successful scientific work.

Stop after a valid zero-survivor result when further execution would not answer
the current question. If evidence implicates a gate or unsuitable input, the
host may diagnose it or choose a different future protocol. Use a new revision
and request for that future work; keep old results and reservations intact.
An input hash mismatch, unavailable backend, unknown worker outcome or missing
authority is a reason to inspect/correct/stop, not to bypass the execution path.

For independent Python planning/status calls and their different workspace
requirements, read [local SDK calls](references/local-sdk.md).
For caller-defined configuration, final shortlist export or an existing sweep's
individual mutations, read [configuration and sweep operations](references/configuration-and-sweep.md).
For gate-by-gate SMILES, library partitions, requested RMSD criteria and actual
dock/redock coordinates, read [result delivery](references/result-delivery.md).

Read [historical workflow and calibration measurements](references/legacy-workflow.md)
for existing sweep ledgers, gate-token usage or diagnostic examples. The values
there describe particular panels and campaigns, not universal cutoffs.
