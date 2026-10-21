---
name: etalon-campaign
description: Inspect scientific evidence using factorial, bind_outcome, condition, exact_program or empirical_model; review handoff, stability, admission or FEP network feasibility; prepare bounded execution through ETALON registered executors.
---

# Evidence, diagnostics and bounded execution

Use this capability when a question requires inspecting campaign evidence,
checking a diagnostic model, or preparing an MD/MM-PBSA calculation. The host chooses the question, method and
next step; a campaign need not follow a fixed research sequence.

For a local two-factor question with already recorded measurements, use only
[the factorial diagnostic](references/factorial-diagnostic.md) when appropriate.
It calls the existing `ResearchJournal.factorial` function without scheduling,
training a policy, or dispatching a calculation. A missing cell or incompatible
unit is a reason to retain uncertainty or stop, not to fill in a zero.

For mapping an existing readout to an outcome, checking finite joint support,
or obtaining a bounded numerical suggestion, read only the relevant section of
[finite diagnostics](references/finite-diagnostics.md). These three existing
SDK methods are independently callable; using one does not require the others,
a skill record, policy fitting, or a full campaign.

When complete paired development rows are available, or a saved diagnostic
query needs an execution/feedback boundary check, consult
[paired models and recorded segments](references/paired-models-and-segments.md).
Model construction, provenance admission, compilation, runtime preflight and
execution remain different operations; only use the part the question needs.

For group-separated `fit_policy/calibrate_policy/next_action/evaluate_policies`,
released validation/feedback, pose/preparation or fixed-pool/online methods,
select the separate `etalon-research` skill and only its relevant reference.
These methods do not require this campaign recipe or a historical round script.

For a specific handoff, trajectory stability, measurement admission, council
finding, spend token or relative-network question, use
[governance and FEP feasibility](references/governance-and-fep.md).
Those checks are independent tools; a network suggestion does not execute FEP.

The legacy `Campaign.round` remains an optional complete screening/measurement
recipe. `Supervisor` is an explicitly invoked local scheduler whose constructor
can initialize stores; it is not a read-only monitor or a required research
controller. `ParameterChange.judge` and `Acquisition.propose` return optional
advice, not applied configuration or authority to execute. Keep relevant panel,
identity and budget checks when using them; you may reject advice and stop.

- Query existing campaign state and artifacts before deciding whether another
  measurement would distinguish the current hypotheses. A read-only answer may
  be sufficient.
- If installed execution support is unknown, inspect `etalon_capabilities`;
  known tools may be called directly. Prepare and
  register an explicit executor for its endpoint. Bind the actual molecule,
  state, pose, receptor and protocol; a ligand name or a successful process exit
  does not certify those identities.
- Use the existing budget, handoff, reserve/execute/resolve/admit checks. Existing
  user authority carries across routine steps within that scope. Granted waivers
  retain their attribution; `etalon_recommend_waiver` cannot grant one.
- Submit bounded work only when the requested calculation is justified and
  authorized. Preserve uncertain outcomes and reconcile receipts before retrying.
  A new independent replicate needs a new execution identity and resource scope.
- Report accepted observations separately from rejected or partial results, and
  computation separately from experimental efficacy. Current registered PRISM
  execution supports a receptor-bound MM-PBSA path; this does not establish a
  complete FEP or independent-replica workflow.

For screening batches, consider `etalon-screening-sweep` and its
`etalon_authorize_gates` contract when applicable. Neither skill is a required
entrypoint for unrelated queries or new methods.

Read relevant sections of [the historical workflow](references/legacy-workflow.md)
only for an existing workflow, measurement rationale or compatibility question.
Its historical measurements and machine conditions are not current validation.
The durable operation contract is available at `etalon://runtime`.
