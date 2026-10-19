---
name: etalon-campaign
description: Inspect scientific evidence and prepare bounded expensive execution through ETALON registered executors, including handoff validation, budget protection and result admission.
---

# Evidence and expensive execution

Use this capability when a question requires inspecting campaign evidence or
preparing an MD/MM-PBSA calculation. The host chooses the question, method and
next step; a campaign need not follow a fixed research sequence.

- Query existing campaign state and artifacts before deciding whether another
  measurement would distinguish the current hypotheses. A read-only answer may
  be sufficient.
- Inspect `etalon_capabilities` for installed tools and operations. Prepare and
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
