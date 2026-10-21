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

## Inputs and independent operations

- For one existing batch, supply absolute `config_path`, `workspace` and, for a
  cascade, `library_path`. A flat pipeline can carry its own inputs.
  `etalon_screen_plan` compiles dependencies and returns input-bound `plan_id`;
  it neither executes nor estimates/reserves cost. The matching SDK entry is
  `etalon.screening.plan_screen(config, library, workspace)`.
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

Read [historical workflow and calibration measurements](references/legacy-workflow.md)
for existing sweep ledgers, gate-token usage or diagnostic examples. The values
there describe particular panels and campaigns, not universal cutoffs.
