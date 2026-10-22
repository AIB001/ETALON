# Component memory and optional composition

For one bounded improvement, reuse MethodRevision and MethodEvaluation, then
`method_revision.resolve_method_revision` to record scoped adopt/retain/rollback
as a decision step and MethodUse. Apply the selected version explicitly on the
next call; no shared default changes. A rollback needs prior candidate use and
counterevidence. Keep actual comparison costs and the evaluated scope.

The optional continuation method `resume_research(..., read_mode="frozen_view")`
can reuse a complete closed frozen source view within one call. `fresh` remains
the default; live/partial/broader sources use the existing path. Current pause and
file checks remain mandatory. Compare before adopting for a new workload.

The host may call existing component SDKs directly. No graph, catalog, skill or
method registration is required to execute an already authorized action.

`etalon.research.components.capabilities()` describes component owners, existing
entrypoints, native small skills and known integration gaps. It is not a live
availability check. MolQuarry owns its six `molquarry-*` skills. ETALON owns
cross-component evidence, library conversion, judgments and comparisons. Pure
SDK functions do not each need a skill wrapper.

`inspect_component(capability_id, location, run_id=...)` reads a known sealed
MolQuarry data snapshot, ETALON library snapshot, MolCascade run, or PRISM design
bundle using existing validators. It never executes the producer. The three
component entry IDs are `molquarry.data`, `molcascade.screen`, `prism.design`;
the cross-component library conversion is `etalon.library`.

`record_component_step(journal, ..., source_id=..., source_kind=...)` is a
separate explicit write. It saves original file refs and a receipt, then an
ordinary `research-step` node. Supply task, branch, node, reason and optional
workflow/method-use refs. MolCascade status bytes are captured immutably; their
old state is not a live heartbeat. Fixture origins remain fixtures. Reading a
bundle does not turn failed candidate slots into successful molecules or labels.

`etalon.research.composition.node_step` also describes standalone host nodes,
planned/skipped nodes, failures and pending work. Known FEP/lab integration gaps
are proposed/skipped nodes, not executable capabilities. For actual execution
continue using existing action APIs and references; the node helper never
submits, retries or charges. A skipped node has no output, receipt or MethodUse.

Optional `etalon-research-workflow/1` JSON names nodes, their capability IDs,
allowed method versions, E/J/X/R views, skip conditions, and data/support/control
edges. Store it with `record_content`. It is a versioned description, not a
mandatory DAG or executor. Preserve an old version when switching methods;
record a new definition or use a standalone node. `MethodVersion` stores small
or composite methods; `MethodUse` keeps its existing task/request/action/result
subjects, and nodes reference uses through `detail.use_refs`. Available,
selected, read and actually applied methods are different facts. Host-reported
attribution does not prove native model use or scientific efficacy.

Existing query/read SDK and MCP interfaces read these ordinary artifacts.
Besides source/task/method filters, `query_evidence` supports `branch_ids`,
`node_ids`, `capability_ids`, `workflow_refs`, `evaluation_targets` and node
`statuses`. Values are explicit lists; workflow filter values are record IDs.
Known refs can be read without a search. A checkpoint can reference node steps,
which in turn link the workflow, applied methods and original component files.

For future RSI, record an `evaluation_plan` step with target `method`,
`branch_selection`, `workflow` or `workflow_composition`. Bind candidate and
baseline MethodVersions, protocol Artifact, TaskSplit, initial-conditions
Artifact, concrete trigger evidence, and metric definitions. Keep regression,
positive retention, funnel and costs separate, including denominators and
missing-data policy. Unselected branches have unknown counterfactual outcomes.
An evaluation plan is neither an optimizer trigger nor completed evidence.

`composition.record_evaluation` binds an existing comparison to that plan using
the original MethodEvaluation validation. Diagnostic comparison may be
inconclusive. Independent validation still requires the original frozen-policy
receipts; ordinary workflow outputs do not qualify. Nothing automatically
optimizes a method, changes a default, promotes a candidate or resumes science.
General golden/evaluation adapters and deduplicated optimization triggers are
future work. Source distribution details: `docs/thin-harness/COMPONENT_MEMORY.md`.
