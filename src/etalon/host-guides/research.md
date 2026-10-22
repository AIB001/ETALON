# Research and evidence on demand

Choose a question, evidence source or next action using the project's current
authority and resource limits. This guide is optional; direct SDK/tool calls,
partial use, combinations, modified methods and stopping are all valid.

For existing knowledge, a project may provide `etalon-workspace.json`. The SDK
`etalon.research.evidence.query_evidence` returns bounded summaries with source
coverage; `read_evidence` reads a known source-bound ref without a prior query.
MCP equivalents are `etalon_evidence_query` and `etalon_evidence_read`. They read
published sources without creating a campaign, running models or redoing work.
The `research` skill's `references/evidence-reuse.md` covers exact calls,
relocation, counterevidence and cross-campaign reuse. Known files remain usable.

For scientific methods, exact optional entrypoints include:

| Need | Existing interface or skill member |
| --- | --- |
| Transfer, validation/feedback, pose/preparation, fixed-pool/online | `research` references for the relevant method |
| Finite diagnostics, paired models or segment execution | `campaign`: `references/finite-diagnostics.md`, `references/paired-models-and-segments.md` |
| Local design import and evidence roles | `etalon.data.design`; `data-design` skill |
| Literature/structure evidence collection | Individual `molquarry-*` bundles |

The finite methods `condition`/`exact_program` live in
`etalon.research.diagnostics`; `bind_outcome`/`empirical_model` in
`etalon.research.calibration`. A catalog or skill read is not required to call
them. If discovery helps, ask for exact skills/tools and a summary, then read a
specific member. Omitted selectors include all; empty lists include none.

For authorized execution, existing campaign/workflow status and receipts reveal
what already ran or remains uncertain. The `etalon_action_prepare`,
`etalon_action_submit`, `etalon_action_result` surface supports a host-selected
action with empty method refs; a future action need not be in an earlier DAG.
Execution still checks endpoint, executor, identity, evidence and budget.

Separate original observations from reports and interpretations. Keep supports,
counterexamples, applicable conditions, source roles and limitations. Reusing
history incurs no second execution charge; new analysis has its own actual cost.
A shared trajectory is not an independent repeat. Reading released validation
makes it seen evidence for the new task. Missing counterfactuals remain unknown.
Consult the project's authoritative status reference when action depends on a
pause; a manifest or an old successful result cannot authorize restarting work.

For optional component receipts, small-method attribution, workflow-node memory
and comparison plans, see [Component memory and composition](component-memory.md).
