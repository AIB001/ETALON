# Connect selected experience to an action and its normal result

Use this optional adapter when an existing decision would benefit from a compact,
reusable basis and exact feedback. Ordinary exploration can proceed without it.
`etalon.research.action_learning` reads existing evidence and returns dictionaries;
it does not generate a mechanism, run a predictor/checker, execute a method or
choose the next action. Existing ResearchJournal Artifacts and publications carry
its results; no additional store or global workflow is needed.

After saving normal feedback, `gather_rule_revision_context(workspace,
feedback_ref=..., model_ref=...)` follows the existing model's prediction bindings
to the assumptions behind different or unknown outcomes. Shared rule bodies are
returned once; potentially affected dependents are IDs rather than full histories.
An explicit `prediction_ids` selection can also study a successful prediction for
missed opportunities. No selection triggers a proposal or blocks unrelated work.
Use this context to revise the scientific method or, when its improvement strategy
is itself unproductive, the improvement procedure. For that optional guidance,
read `etalon_skill_read(slug="skill-improvement", member="references/mechanism-revision.md")`.
If unavailable, keep the limitation explicit and choose from the available methods;
the optional skill need not be located through a whole-system search.

The host chooses relevant rule fragments and experience from context, reasons
about the missing mechanism, and selects an actual action. For example, an
available precursor can support a route choice only alongside identity and its
role in the proposed transformation; a catalog listing alone cannot establish
reaction feasibility or product formation. The next useful action may be a
conditions calculation, a different route or a local algorithm change, depending
on what the normal result leaves unresolved. This is a reasoning example, not
evidence for a particular molecule.

## Keep the useful part executable

A lesson earns a place in the next action by changing a concrete operation:
candidate generation, sampling, search, ranking, analysis or branch choice. For
example, a recurring state distinction may support stratified sampling; the
state extractor and selector are methods, while the evidence that the distinction
matters is a scoped rule. Neither requires a new universal skill.

Use the existing owners for the different parts:

| Useful part | Existing home |
| --- | --- |
| Repeatable extraction, prediction, selection or computation | Versioned method/component code and its ordinary MethodUse |
| Applicability, expected effect, exceptions and dependencies | Rule in a research-model Artifact, or a small existing rule Artifact |
| Measured benefit, failure and conditions for a later choice | Existing evaluation and conditioned experience |
| Completed work, next action and unfinished handle | Research step, checkpoint and publication |

The harness binds identities and feedback; it does not interpret a saved rule as
code or execute a newly retrieved method. The host deliberately selects and runs
the method through its component interface. Keep the decision brief small: link
the exact rule fragment and method version instead of copying code, source
history and all prior outcomes into the skill or checkpoint. A new representation
or algorithm can remain task-local; cross-task reuse is a separate hypothesis.

Before spending more samples on a stalled method, check whether its allowed
actions can change the failing observable. Separate a fixed anchor, a movable
substructure and a receptor-frame change. If the observable is invariant under
the current actions, change the representation or action family; more iterations
of that family cannot remove the invariant. Implement the useful extractor and
generator as a parameterized method, then test the newly reachable candidates
through normal downstream work. A geometric improvement may disappear when
relaxed; that discrepancy can select the next method without requiring a Probe.

For repeated deterministic work, `method_reuse.request_identity(operation=...,
implementation=..., environment=..., inputs=..., parameters=...)` and
`method_reuse.execute_or_reuse(directory, request, execute)` can retain a result
under its exact request. Import from `etalon.research.method_reuse`. Include all
consumed dependencies, such as state-extractor/selector versions and seed; a path
alone is not an identity. The callback returns `result` and absolute `artifacts`
paths. Reuse verifies the saved receipt and originals, preserving original cost
and reporting new read overhead. An unresolved started request needs its existing
recovery path, not a new callback invocation. Tiny calculations may cost less to
repeat than to cache; choose reuse for meaningful execution or continuation value.

When comparing a learned selector with a fixed method, hold the question,
available inputs and quality endpoint comparable. Fix the learned choice before
its comparison outcomes arrive. Equal observation counts alone are not equal
complete budgets: report representation building, selection, model tokens,
failures and recovery along with calculation. Shared full-resolution references
belong to measured evaluation cost. Subsequent decisions may learn from those
results, but the same cases then become seen evidence.

## Before the relevant normal action

Use an existing `research_decision` whose selected action has an `execution_ref`
to the planned normal node. Its prediction already names an observable and an
expected statement. A declared prediction `basis_ref` supplies a structured
expectation, selected by an RFC 6901 pointer. The selected object has exactly:

```json
{
  "observable": "the decision prediction's observable",
  "expected_statement": "the decision prediction's expected statement",
  "checks": [{"pointer": "/selected_output_field", "expected": true}],
  "next_if_match": "The host's proposed next action if all checks match",
  "next_if_different": "The host's proposed next action if a known check differs",
  "next_if_unknown": "The host's proposed next action when feedback is insufficient"
}
```

These are expectations and conditional choices, not measured outcomes or new
scientific rules. The observable and statement must equal that decision
prediction's values. Include every condition needed for the intended interpretation;
the adapter cannot infer omitted measurements. Preserve the basis before the
corresponding outcome when making a prospective claim. A saved immutable file
alone does not prove outcome ignorance; the existing evidence-window machinery
remains available when controlled publication timing matters.

```python
from etalon.research.action_learning import (
    gather_action_basis, compare_action_feedback,
)

basis = gather_action_basis(
    workspace,
    decision_ref=decision_ref,
    action_id=selected_action_id,
    conditions=action_conditions,
    expectations=[{
        "prediction_id": prediction_id,
        "ref": expectation_original_ref,
        "pointer": expectation_object_pointer,
    }],
    rule_selectors=[{"ref": rule_original_ref, "pointer": rule_pointer}],
    experience_refs=selected_experience_refs,
)
```

`conditions` is a nonempty mapping of named dimensions to strings or null.
Rule selectors and experience refs are optional. Rules retain their selected
value and source scope/counters; experiences retain their exact conditions,
conflicts and supersession within the selected set. `condition_comparison`
reports match/different/unknown for declared dimensions, or `not_declared` when
the selected rule has no declared conditions. Missing or null query
dimensions remain unknown; free-text `invalid_when` and unselected history still
need host judgment. A match is not automatic applicability or adoption.

Save `basis` through existing `components.record_json` when persistence is useful,
obtain its ordinary exact Artifact ref, and expose it through the existing
workspace/publication mechanism. Execute the actual method using its existing
component interface, concrete inputs and dependencies. The saved basis performs
none of that execution.

## After normal execution

Use the completed/reused/partial/failed normal node and, when available, an exact
JSON output ref from that node. The feedback node must preserve the planned
action's inputs, methods, workflow, capability and node identity, and either be
that node or explicitly supersede it.

```python
feedback = compare_action_feedback(
    workspace_with_saved_basis_and_results,
    basis_ref=saved_basis_ref,
    execution_ref=normal_execution_ref,
    observation_ref=normal_output_ref,
)
```

The result includes `comparisons[].checks`, `comparison`, `next_choice_hint`,
receipt refs and `next_choice_basis.evidence_refs/hints`. Checks use exact typed
JSON equality: false differs from zero, and missing/null is unknown. A prediction
matches only when every check matches; a known contradiction gives `different`,
otherwise a missing check gives `unknown`. Failed or waiting execution cannot
produce a prediction match. Planned/skipped actions have no execution feedback.
Omitting `observation_ref` preserves unknown observations.

There is no numeric tolerance or range comparator in this adapter. For noisy
scientific output, use the task's existing versioned evaluator to produce the
appropriate observation, or interpret raw differences with uncertainty. Do not
round or loosen an endpoint after seeing the result to manufacture a match.

The hints are the host's previously stated alternatives. Compare them with the
actual discrepancy, unresolved observations, other rules and total cost, then
make the next ordinary choice. If a result reveals a new mechanism, the next
choice can go beyond these hints. Reuse the feedback refs in an existing decision
update, revision context or conditioned experience when useful. A field match
does not establish scientific success, causal explanation or method benefit;
those use the same external quality evidence as the normal task.

For a composed candidate, compare the endpoint the executor actually observes.
Graph construction, condition support and product formation are distinct outputs;
one successful component must not fill the others with `true`. Retain unknown
components and case-level disagreements, including alternatives worth keeping.
Bind source support to the actual input/output graphs and chemical states; a
shared formula, mass, fragment or compound label can still name another isomer.
Reuse unchanged route portions with their original identities; a new graph output
does not turn their historical evidence into a new replicate.

When the new action changes endpoint, the previous experience can remain a
related input with an explicit mismatch. Save the host's resulting conditional
choice through the ordinary decision/step interfaces. For a real method change,
the optional revision records describe how to retain the new feedback as experience
without inheriting the old patch's conditions or manufacturing a revision for
ordinary progress. Retrieve that member with
`etalon_skill_read(slug="skill-improvement", member="references/revision.md")`
when the record would help the current choice.

For a local synthesis-route edit, the optional
`etalon.research.reaction_candidate.compile_reaction_candidate(tree, binding, operations)`
splices an ordered operation chain into an exact native edge. `binding.replaced_edge`
names the original product, precursors and native reaction; each operation supplies
`product_smiles`, `reactant_smiles`, `retro_smarts` and `source_anchor`, with process
conditions retained as context. It preserves salt/charge/stereo state and unchanged
precursor subtrees, and removes stale route scores. It does not predict a reaction.
The repository example `examples/execute_reaction_candidate.py` consumes the result
with AiZynthFinder templates, its existing filter, stock and scorers. Keep the native
outcomes: graph generation, learned-filter opinion, stock coverage and source-supported
conditions can disagree. A failed transformation can identify a concrete template
repair; a high filter score cannot settle conditions that its inputs do not contain.

To jointly consider poses and an already retained complete synthesis route, use
`reaction_candidate.bind_pose_route(molecule_smiles, pose_refs=..., route_path=...,
route_identity=..., observation_refs=...)`. Supply the graph/state from the actual
pose input and the pinned full-route artifact. The compact result links the full
tree once and preserves its stock, native-evaluation and experimental unknowns.
A different graph/state remains a candidate with an unmatched route; find its
own route instead of inheriting the old scores. Do not force a molecular edit
merely to demonstrate this connection. Store the binding and new observations
through the existing result publisher, keeping source bodies outside the skill.
