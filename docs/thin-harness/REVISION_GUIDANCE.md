# Decision-backed candidate guidance

`etalon.research.revision_guidance` connects existing decision, revision context,
candidate brief and normal-use evaluation records. It adds two read-only SDK
helpers and no schema, store, MCP tool, automatic selection or required phase.

`build_revision_input(workspace, *, decision_ref, prediction_id, context_ref,
differences=(), update_ref=None)` returns the selected prediction, all alternatives,
exact source observations, constraints, fixed evaluation originals, missing data,
counterevidence and a separately attributed evaluation followup. Differences use
RFC 6901 JSON pointers; each names the options it supports, opposes, is compatible
with, or leaves unknown. Relations and uncontrolled factors are explicit host
judgments. Value inequality alone establishes neither causal identification nor
the correct edit. References, task/scope, prediction/action and optional update
links are verified with the existing readers.

`build_candidate_from_decision(workspace, *, decision_ref, prediction_id,
context_ref, opportunity_ref, object_kind, falsifier, differences=(),
update_ref=None, **brief_arguments)` returns `revision_input` and an existing
`etalon-candidate-brief/1`. The caller supplies title, rationale, target_part,
change, applicability and optional brief provenance. `expected_effect` contains
metric, unit and direction; metric equals the decision observable. The expected
result, execution reference, discriminator and fixed check identities are derived
from the decision. `normal_execution`, `quality_criteria` and `base_method_ref`
cannot override those bindings. Constraints and counterevidence are retained.

Readiness also checks the proposed after-content against the MethodVersion used
by the selected action and named as a subject in a fixed evaluation that compares
the exact base. Matching the base or observable alone is insufficient. Verified
aliases with identical content remain the same candidate. Workflow edits require
the exact patch-to-candidate link and the selected node's after-workflow. If these
bindings are absent, the brief stays incomplete with explicit missing evidence;
an unrelated old action is retained as decision context but is not the brief's
normal execution reference. This is provenance checking, not permission to run.

```python
result = build_candidate_from_decision(
    workspace, decision_ref=decision_ref, prediction_id="read-failures",
    context_ref=context_ref, opportunity_ref=opportunity_ref,
    object_kind="content", falsifier="No reduction, or any lost original",
    differences=[{
        "difference_id": "reader-errors",
        "left": {"ref": diagnostic_ref, "pointer": "/before/errors"},
        "right": {"ref": diagnostic_ref, "pointer": "/after/errors"},
        "option_links": [{"option_id": "routing", "relation": "compatible",
                          "reason": "Same inputs; availability still uncontrolled"}],
        "uncontrolled": ["Availability"],
    }],
    title="Exact reader edit", rationale="Test the routing explanation",
    target_part="/reader", change={"kind": "method_content",
        "before_ref": before_ref, "after_ref": after_ref},
    expected_effect={"metric": "read-errors", "unit": "count", "direction": "decrease"},
    applicability={"applies_when": ["Same released input protocol"],
                   "does_not_apply_when": ["Sealed or missing originals"]},
)
```

Persist the brief through the existing `continuation.record_content`; if the
derived view is useful as a diagnostic report, ordinary Artifact publication
retains it without new typed dispatch. Keep its exact arguments for rebuilding.
An unknown expectation cannot produce a testable brief. Absent checks or null/
missing selected observations leave it incomplete. Zero and false remain observed
values. Only declared diagnostic inputs may supply differences; an excluded
selector is rejected before its original is opened. An optional update supplies
evaluation outputs in a separate section, not answers to the original decision.
No evidence window means timing is unspecified; retrospective remains seen.

The normal-use evaluation still uses MethodUse, executed node, original result,
fixed plan/contract, MethodEvaluation and experience. The decision update checks
exact plan and attribution; bridge-linked evaluations/experiences must occur in
that update and belong to the exact candidate patch under existing brief checks.
These checks do not certify native execution, causal truth, independent transfer,
or benefit beyond what the actual originals support.

Read terminal dispositions and duplicate candidate identity before proposing.
No-change does not require a candidate. Rebuilt guidance grants no execution and
cannot reopen a completed opportunity. The existing conditional-reopen API and
explicit original novelty remain authoritative. Diagnostics, candidate formation,
normal execution and independent confirmation have different cost and evidence
roles; this helper measures none of them automatically.
