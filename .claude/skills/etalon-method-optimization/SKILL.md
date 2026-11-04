---
name: etalon-method-optimization
description: Improve a task-local scientific method when normal results expose an inadequate algorithm, objective, parameterization, action representation, or unresolved mechanism.
---

# Improve a scientific method

Use when a concrete scientific result or failure suggests that a method's algorithm,
inputs, parameters, search space or internal objective is limiting the next decision.
Read the current question, relevant observations, contrary evidence and method
contract; do not turn one task's molecular thresholds into general defaults.

The deliverable is a useful method or scientific result. Be willing to invent a
task-local algorithm, change the representation, combine tools or explore a
different hypothesis. Use the user's existing authorization to try a promising
idea; missing proof of benefit is a question to investigate, not a prerequisite
for acting. Let checks answer concrete questions and let code handle repetitive
bookkeeping. An audit or a proposal alone does not complete an implementation task.

Distinguish a limitation of the scientific problem from a limitation of the current
implementation. Connect the current gap to an object the host can change and the
feedback that would alter the next decision. A known fix can be implemented
directly; an uncertain mechanism calls for a specific, testable intervention.
Depending on the problem, adjust the algorithm, search
representation, scoring objective, method parameters or input state. Existing methods,
conditional reuse and no-change remain valid choices. A task-specific improvement
is useful even if it cannot transfer to another task.

Use existing method execution, ordinary task feedback and saved outputs. Try an
informative candidate within the task's authorization when warranted; do not require
a separate Probe stage or a confident expected payoff. Compare the candidate with
an appropriate available baseline on the decision-relevant scientific endpoint,
including relevant quality, actual complete costs, and failure or unknown outcomes.
Changing a scientific standard or molecular state is a different comparison, not a
free performance gain. An internal score alone need not establish scientific value.

Use the existing research note/step and, where applicable, MethodVersion, MethodUse,
evaluation and disposition interfaces to retain the candidate, conditions, inputs,
result, limitations, costs and next action. Keep failed and negative findings as
conditional experience; retain, locally use, revise or roll back as supported.
Continue the scientific task after a no-change decision. Do not create a new ledger
or promote a general algorithm solely because it worked on one task.

When an executable improvement is useful again, save its callable form via
`etalon_method_publish` (CLI `research method-save`) in the existing entry.
Use `method.run` arguments and keep scope, evidence and limitations alongside it.
The callable consumes `--request`/`--result`; its result contains `schema` set to
`etalon-method-result/1`, `summary`, `outputs` and optional `feedback`. Put richer
timings or native receipts in `feedback` or referenced artifacts. If only that
envelope fails, repair it from saved outputs without repeating the calculation.
Discover with the resources view and resolve its exact pointer using
`etalon_method_resolve`; submit the returned request through `etalon_action_submit`
and keep its handle. For an ordinary note, `etalon_method_call` can resolve,
submit and save the handle in one call; `etalon_method_collect` attaches existing
results and costs. Both use the same action and request identity, without requiring
a research graph. A repeated use should invoke the saved method, not re-derive
or rewrite it. Read the contract on demand with
`etalon_skill_read(slug="research-index", member="references/layered-methods.md")`.
Keep selection advice in a skill and conditional findings in
memory; useful code need not become a new SKILL.md or transfer to another target.

For a retained method, bind later useful feedback to its catalog `resource_version`,
used parts and actual conditions through `etalon_research_progress(resource_feedback=...)`.
Use a stable observation id; a new chat does not produce a new use. A new dataset is
an input change, tuning is usually a configuration, and a changed algorithm is a
method version. Compare conditional failures as well as successes before reusing
it; do not reread all prior runs when the selected contract and conditions remain
unchanged. The layered-method reference gives the exact optional feedback shape.

When a predictor improves but scientific outcomes or shortlist quality do not,
see [decision quality](references/decision-quality.md) for optional ways to
separate observation aggregation, learning, acquisition and final selection.
These are diagnostic tools, not a mandatory loop or a prescribed scoring formula.
