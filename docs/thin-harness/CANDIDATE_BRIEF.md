# Optional candidate brief

`etalon-candidate-brief/1` is ordinary immutable JSON Artifact content in the
existing ResearchJournal. It connects an exact revision opportunity and context
to a concrete proposed replacement, its rationale, expected measurable effect,
frozen invariants, quality criteria, applicability, counterevidence and the
smallest discriminating existing normal execution. It creates no method version,
execution stage, Probe, journal, scheduler, automatic proposer or adoption rule.
Hosts may omit the brief. R4b authorization and pauses remain external authority.

The public Python API is in `etalon.research.candidate_brief`:

- `SCHEMAS`, `CANDIDATE_BRIEF`, `validate_content(payload)`.
- `build_candidate_brief(workspace, **arguments)` reads explicitly supplied
  originals and returns a detached JSON-compatible value.
- `validate_links(workspace, payload)` and `read_candidate_brief(workspace, ref)`
  recheck source identities and semantic bindings, including prior briefs.
- `validate_local_links(journal, content, source_id, external_workspace=None)`
  supplies the existing `record_content` publication check. External references
  require their explicit verification workspace.

Use the existing `record_content` or `record_json` Artifact publication mechanism;
`record_json` alone does not perform candidate semantic validation. For it, call
`validate_links` or `validate_local_links` before publication. Shape validation is
never proof that an original exists or that its declared findings are correct.

## Minimal proposal

The following names represent exact verified EvidenceRefs, not paths or new
measurements. All descriptive values in this example are synthetic fixtures.

```python
brief = build_candidate_brief(
    workspace,
    opportunity_ref=opportunity_ref,
    context_ref=context_ref,
    base_method_ref=base_method_ref,
    title="Read generic released evidence",
    rationale="The current reader assumes a typed record schema",
    target_part="/read",  # Stable part/path, never a display title.
    change={"kind": "method_content", "before_ref": original_content_ref,
            "after_ref": intended_content_ref},
    expected_effect={"metric": "read-errors", "unit": "count",
                     "direction": "decrease", "hypothesis": "Fewer routing errors"},
    frozen_invariants=["Preserve original identities and negative observations"],
    quality_criteria=["Verify originals before interpreting their content"],
    normal_execution={
        "execution_ref": existing_node_ref,
        "description": "Use the existing ordinary reader node",
        "discriminator": "Compare failure counts on the same originals",
        "metric": "read-errors", "unit": "count",
        "success_criterion": "Fewer read failures with the invariants satisfied"},
    applicability={"applies_when": ["Released originals are available"],
                   "does_not_apply_when": ["Evidence is sealed or unavailable"]},
    support_refs=[original_problem_ref],
    counter_refs=[prior_failure_ref],
)
```

The base method must already exist. For `method_content`, its `content_ref` must
be an ordinary Artifact exactly matching `before_ref`; `after_ref` identifies
actual intended replacement bytes, without requiring a new MethodVersion.
This version does not replace a skill-bundle manifest directly. `workflow`
changes instead reference before/after `etalon-research-workflow/1` Artifacts;
the original workflow must explicitly reference the exact base method.

`task_id`, `scope`, and `conditions` come from the exact context. The opportunity
must reference that context and its opportunity key. The normal execution
reference is an existing research `node` or `evaluation_plan` with the exact
base method in `method_refs`. Its declared metric and unit must match the
expected effect. A missing execution reference or nonempty `missing_evidence`
produces `status="incomplete"`. Otherwise `ready_for_normal_execution` means
the brief is complete enough to describe that comparison; it does not certify
runtime availability, grant permission, submit work, or claim the comparison ran.

## Candidate content and evidence episodes

`candidate_key` binds the exact base record identity, scope, conditions, stable
target part and before/after content identities. Title, rationale, supporting
prose, task display text and opportunity/context episode are excluded. JSON
whitespace/key order, verified source aliases and alternative Artifact locators
of identical content do not manufacture new candidates. Workflow display names
and `supersedes` links do not count as workflow edits. Actual method content edits
remain meaningful, including prose that is itself part of the intended method.

`episode_key` additionally binds the exact opportunity/context refs. Selecting
the same intended edit under revised evidence can therefore preserve its
candidate key while recording a new episode. This does not fabricate a new
MethodVersion or establish that reconsideration was justified; reviewed changed
evidence and reconsideration conditions belong to the separate opportunity state.

Pass `duplicate_refs=[...]` to compare against at most 32 explicitly supplied
prior briefs. There is no catalog scan, text clustering or global uniqueness
claim. Matching verified refs appear in `same_candidate_refs`; their counterrefs
are retained automatically. `supersedes=[...]` permits a new brief for the same
candidate and also preserves its earlier counterrefs. Different base, content,
scope or target part cannot silently supersede that candidate. Originals remain
immutable and addressable.

## Existing relationships and claim boundaries

Optional `family_ref` must include this exact opportunity as an accepted reviewed
member. Optional `revision_ref` links the existing ordinary method-revision
Artifact and its exact base; a no-change revision has no candidate. Optional
`patch_ref` binds the original context/opportunity/base and actual intended
replacement to an existing candidate MethodVersion. `evaluation_refs` require
that exact patch and comparator/candidate identities. `experience_refs` require
the same patch; their counterrefs are retained. Prior unrelated evaluations or
no-change decisions can instead remain explicit supporting/counterevidence refs.

Every brief always declares `claim_status="hypothesis"`,
`independent_benefit="not_established"`, and `adoption="not_performed"`.
Linking an evaluation or a negative/adopted experience preserves its original
conclusion and attribution; it never converts this proposal into measured
improvement or adoption. Proposal-only work requires no evaluation. If intended
content does not change, do not manufacture a brief: retain the existing
no-change/retain decision without a candidate.

Reads verify originals through the evidence reader, with request-local bounded
read reuse and a 128-ref provenance-closure ceiling. Generic large/binary evidence
is byte-identity verified without requiring its entire content to parse. Typed
structured references require complete content within the existing 32 KiB read
bound. All fixture tests are engineering checks, not independent scientific or
method-benefit evidence. Frozen historical archives support retrospective
documentation only; linking them creates no new independent trial.
