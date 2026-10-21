# Diagnostic research kernel

This package records research decisions in `CampaignStore` and provides an exact finite
experimental-design baseline. It is not an affinity predictor, a scalable novel solver,
or evidence of recursive self-improvement.

`Experiment` distinguishes file audits, reanalysis, independent repetition, interventions
and recovery. `Observation` retains measured, missing, failed, rejected, unsupported and
unevaluated outcomes separately. Evidence is hash checked; diagnostic inputs exclude
independent-validation and sealed-holdout artifacts. Diagnostic observations are metadata,
not scalar active-learning labels. PRISM continues to own chemical verification/preparation.

`Contrast` records all four factorial cells including missing ones. Execution `method_id`
can differ between preparation conditions; a common `measurement_method_id` and explicit
paired bindings define comparable readouts. Shared artifacts and assumptions are retained.
Descriptive interactions are computed only when all cells exist with compatible units.
No independence, causal attribution or confidence interval is inferred from shared poses.

`Revision` materializes explicit before/after fields in design, preparation, evaluation and
budget into a new configuration. It never mutates old endpoints or promotes a protocol.
Use the existing protocol registry for separately reviewed executable protocol trials.
Unresolved evidence can support retaining the current configuration; it cannot certify a
better design or a better evaluator. Changed graph/charge/topology must not reuse an old
prepared system or checkpoint; this package does not authorize such execution.

`JointModel` represents full joint probe outcomes through finite hypotheses and nuisance
worlds, with declared probability provenance and an independent decision-loss table.
`exact_program` exhaustively searches bounded adaptive trees (at most 12 probes, depth 8),
including zero immediate-value prefixes. Shared resource identities are charged once;
every branch must fit the quoted budget. The myopic baseline deliberately uses one-step
decision value. XOR and independent-nuisance tests demonstrate different joint semantics,
not a new algorithm or chemistry advantage. Unknown joint observations return
`model_challenge`; posterior confidence remains conditional on the supplied model.

`record_program` freezes model/program identities. `compile_first_segment` emits just the
selected query through the existing fixed workflow schema; callers supply reviewed operation
bindings and runtime preflight separately checks their installed implementation. Compilation
does not certify the operation's scientific meaning or execute future branches. Compilation
and feedback recheck the model's evidence and reused artifact bytes. `observed_branch` checks
all declared probe bindings and reads a recorded discrete label, without thresholding numeric
readouts. Use `bind_outcome` with an explicit numeric rule before `condition`/`exact_program`
when discretization is needed. Undeclared task/method/unit identities cannot be inferred from
a probe name. A returned branch is a suggestion requiring an explicit next planning decision.

`policy_version` stores phi's concrete search configuration, parent and development evidence.
Task splits preserve disjoint development/validation/transfer tasks and initial workflow
identities. Validation/transfer evidence cannot train the same policy lineage. Recorded
held-out comparisons do not automatically certify transfer superiority. No actual T4
transfer split, learned phi improvement or RSI result is claimed by the CPU fixture tests.

## Producer diagnostic integration

Authoritative contracts live in PRISM's `prism/contracts/diagnostic-action-{request,result}-v1.schema.json`.
The byte copies here are pinned to request SHA256
`b947ceed663c91c047ecacdc0cd557f2cf659a861bd1b4744a526681f00a8a72` and result SHA256
`7c873911ea733ac9cec558acf4e5c495af00ff8114431282f43707c9a67a1cd9`.
PRISM's `contract.bind` defines method/instance identities; the consumer invokes it rather
than implementing a second chemical/identity engine. Capability is
`fixed-core-structural-diagnostics/1`, CLI is `python -m prism.diagnostic run --request ... --root ...`.
This initial adapter executes on the actual host with explicit interpreter and source paths.

`examples/diagnostic_round3.py` accepts a producer-bound request and an explicit new campaign
budget in wall seconds. It pins source/interpreter, reserves the central campaign budget,
invokes the producer CLI, verifies result identity and all artifact hashes, and records
Experiments and structural Observations. `CampaignStore.reserve_resource_execution` and
`settle_resource_execution` participate in the SAME `_balance` as active actions, without
creating fake affinity labels. Unknown outcomes keep reservations. Failed executions still
cost their measured amount. Actual overruns remain visible. CPU seconds are reported
separately; consumer audit/preflight overhead is not measured by producer execution costs.

If the producer already completed a request before campaign admission, its original measured
cost is attributed once to the new campaign and explicitly distinguished from incremental
execution on this call. A repeated consumer call reads its terminal receipt only; an absent
receipt remains pending and never silently redispatches. Concurrent modifications to source,
inputs or receipts are refused. Existing R1/R2 budgets and snapshots are not reused.

`examples/diagnostic_contrast_round3.py` consumes two admitted preparation conditions for the
same catechol graph. It preserves every slot and the common structural readout definition,
places those conditions in D1/E0 and D1/E1, and leaves unmeasured phenol cells missing. The
added-atom polar heavy-atom pair count is a descriptive proxy, not a hydrogen bond or affinity.
Its default decision freezes the existing executable request configurations with empty delta,
`proposed_unresolved_not_promoted`, until independent evidence justifies a change. It never
reads the withheld reference structure. Exact C2/C6 attachment mappings are preparation/design
constraints for one chemical graph, not two chemical designs. Same-core conformer seeds do
not demonstrate global binding-mode coverage or independent physical replicates.

Tests under `test_research_*` label synthetic fixtures. Producer CLI integration tests require
`ETALON_TEST_PRISM_SOURCE` to explicitly select a real producer checkout/snapshot. Previous
MolCascade registry tests with missing Arrow dependencies remain a separate environment gap.
# Released decision validation and next diagnostic

The subsequent executed joint-revision batch, independent scientific comparison,
complete archive and remaining RSI gaps are documented in [R3B.md](R3B.md).

The group-separated R4 learner, visible-query controller, cost-fair finite controls
and released same-loss pose diagnostic are described in [R4.md](R4.md). Generic
fixture correctness is separate from pending real multi-family transfer evidence.

`validation.attach_decision_validation` checks the exact frozen decision digest
and independently designated artifacts, then appends an immutable receipt.
It never rewrites a decision, promotes a protocol or uses the phi transfer API.
The public `examples/diagnostic_validation_round3.py` first creates a new SQLite
backup, copies released evidence, checks replay idempotence and preserves the
original ledger. Attachment CPU is unmetered; historical verifier cost remains
reported separately rather than charged again.

`calibration.bind_outcome` connects admitted numeric readouts to explicit finite
probe outcomes with task/condition/method/unit checks. `empirical_model` provides
an empirical joint-task distribution for the existing exact solver, subject to
complete paired outcomes/losses, declared independence groups and held-out task
separation. It does not fit chemical mechanisms from geometry. The public
`examples/diagnostic_calibration_round3.py` demonstrates real archived observations
while explicitly withholding a fitted model when independent data are insufficient.

See [NEXT_DIAGNOSTIC.md](NEXT_DIAGNOSTIC.md) for the capability-gated joint candidate,
fixed proposed structural J, matched baselines, calibration needs and unbound next
validation task. The released 1XEP report is seen development feedback thereafter.

