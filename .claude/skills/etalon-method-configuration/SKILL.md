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
