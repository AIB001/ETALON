---
name: etalon-method-configuration
description: Propose and compile versioned changes to a scientific method or MolCascade configuration, separating execution settings, scientific assumptions and fixed evaluation criteria.
---

# Change a method with an explicit comparison

Use the observed problem and applicable evidence to choose a small configuration
change. Keep the original configuration, input identities and resolved defaults.
Classify changed fields as execution settings, method/selection assumptions or
evaluation criteria. A mobility constraint is a scientific assumption even when
it is expressed as an optimizer setting.

Use the installed `etalon.research.method_contract` helpers and `Screen.plan`
to validate and compile a candidate before execution. Planning fixes an identity;
it does not run the method. See [comparison rules](references/comparison.md).

Choose what is being compared: same-protocol implementation, scientific method,
or workflow. Legitimate reuse and removal of redundant steps are allowed when
the required checks remain covered by applicable evidence. A missing backend or
unexplained omitted check cannot count as an improvement.

Execute only the authorized candidate through existing component interfaces.
Keep actual parameters, method version, failures, receipts and cost. Compare
using the fixed external evaluation contract; improved internal objective or a
looser acceptance threshold alone does not establish better scientific quality.
Use the RSI skill for conditional adoption, retention and experience; no fixed
Probe phase or global-default promotion is required.

Let an established computation or batch run under the ordered worker without
model calls between steps. A host may inspect compact feedback alongside it and
send `etalon_workflow_advance` a `kind: revise` proposal for future nodes: new
methods, changed parameters, additions or omissions. Read `status.plan.revision_id`
for the base; queued advice is not yet applied. The active node keeps its method.
If there is no useful change, let the worker continue without a proposal.

For a new algorithm outside built-in components, use `method.run` through the
existing action or workflow API. Its program receives `--request` and `--result`;
declare source/model files, inputs, outputs and a wall-time allowance. Return a
compact summary, outputs and optional feedback; keep large arrays in artifacts.
Resolve a concrete uncertainty or bottleneck before spending model turns on a
revision. Execution receipts establish what ran; quality comparisons still use
the task's scientific criteria. Recover existing handles before submitting again.
See the runtime resource for the method contract and side-channel revision shape.
