# Connect an existing decision to an editable candidate

Use `etalon.research.revision_guidance` when a saved decision can explain the
change. It is an optional read-only bridge, not a new record type or trial stage.

`build_revision_input(workspace, decision_ref=..., prediction_id=...,
context_ref=..., differences=[...], update_ref=None)` verifies the exact decision,
context and optional normal-use update. Each difference names `difference_id`,
`left` and `right` selectors (`ref`, RFC 6901 `pointer`), `option_links`
(`option_id`, `relation`, `reason`) and `uncontrolled` factors. Relations are
`supports`, `against`, `compatible` or `unknown`, attributed to the host. A
different value does not identify the cause. Keep competing and unknown causes.

Choose differences that matter for the proposed edit: the same input through two
versions, a failing case versus a relevant passing case, or a changed observation
under a stated intervention. State uncontrolled conditions. Missing and null
values are separate from zero/false and do not establish a measured difference.
Only declared decision inputs can be diagnostic selectors. Later results belong
to `evaluation_followup`; using them in a new decision requires explicit seen
input handling. A diagnostic observation is not a hidden evaluation answer.

`build_candidate_from_decision` accepts the same arguments, an existing
`opportunity_ref`, `object_kind` (method/content/selection/composition), a concrete
`falsifier`, and the existing brief's title, rationale, target_part, change,
applicability and optional provenance refs. `expected_effect` supplies only
metric/unit/direction; metric must equal the decision's observable. The helper
preserves the prediction, normal action, constraints and exact evaluation
identities. Selection/composition edits bind the existing workflow before/after.
The target artifact change must be real; prose changes cannot rename it into a
new candidate. Unknown predictions remain inputs for further thinking, not
testable candidates. Missing observations or checks leave a brief incomplete.

The return is `{revision_input, candidate_brief}`. Persist `candidate_brief`
through existing `record_content` when useful. `revision_input` is a derived
read-only view: if retained for review, use an ordinary diagnostic Artifact and
keep the exact arguments so it can be rebuilt. No new schema dispatch is needed.
Publish refs through the usual research step/checkpoint to support continuation.

For an actual evaluation, normal execution must identify the applied MethodUse,
node and outputs. The existing decision update verifies the frozen plan and
attribution. Candidate evaluation/experience refs are accepted by the bridge only
with that exact update; the existing brief also checks its candidate patch. A
successful rule or observation is still separate from causal truth and adoption.

Read existing disposition/family/brief heads first. Terminal retain/no-change
does not request another proposal. `duplicate_refs` retains identical content
identity and previous counterevidence. Reopen only through the existing reviewed
condition and new-evidence interface. Historical results, negative experiences
and replayed comparisons remain seen; a new diagnostic wrapper creates no new
independent evidence or chronology. Fixed criteria identify which checks apply;
they do not prove those checks are scientifically adequate.

A ready bridge-built brief also needs the actual after-content bound to the same
candidate MethodVersion in the selected action and fixed plan subjects, with the
exact base comparator. Identical-content aliases are accepted after verification.
For workflow edits, the patch must link the candidate and after-workflow, and the
selected node must use that workflow. A new algorithm with an old action/plan is
incomplete; do not treat the old execution as evaluating the new change.
