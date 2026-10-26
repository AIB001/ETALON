# Local revision, attribution and later evaluation

## Inputs and effects

Start with a concrete problem trace, the authorized scope, and the original
bundle. The trace may show a failure, an unsupported inference, or lookup waste;
it need not show a scientific failure. State what would falsify the proposed
fix. Keep source identities and unsuccessful attempts. Unknown host/model, money
or counterfactual outcomes remain null or not run.

`skills.snapshot` and `verify_snapshot` read bytes. `persist_snapshot` writes a
new directory and refuses overwrite. `ResearchJournal.record` appends records
and committed events to its existing campaign. These operations do not execute
science, promote methods or update a predictor. The snippet assumes an existing
`journal` and authorized absolute source/output paths:

```python
from etalon import host, skills
from etalon.research import MethodVersion

original = skills.persist_snapshot("local-review", old_snapshot,
                                  bundle_ref={"root": original_bundle})
old_id = journal.record(MethodVersion(
    "local review", ("domain_skill",), original,
    verification_basis="content_verified"))
# Edit a task-local copy, retaining the supplied original and saved snapshot.
candidate = skills.persist_snapshot("local-review", new_snapshot,
                                   bundle_ref={"root": candidate_bundle})
new_id = journal.record(MethodVersion(
    "local review", ("domain_skill",), candidate, parents=(old_id,),
    change_summary="Narrow correction tied to the retained problem trace",
    verification_basis="content_verified"))
use_id = host.record_method_use(
    journal,
    subject_refs=({"kind": "request", "id": "local-trial",
                   "scope": str(journal.store.path)},),
    selected_methods=({"method_version_ref": new_id,
                       "scope": "only the amended evidence interpretation"},),
    host_conditions={"model": None},
    reason="Bounded candidate trial; other sections skipped")
```

Usage guidance can use the same mechanism with role `usage_method` and its own
parent lineage. Set `usage_method_ref` on a use record only to the guidance
actually used. Multiple selected versions express composition; an empty list
expresses no selection. Later stopping or switching appends another use record,
possibly with `parents=(use_id,)`; it does not rewrite the original decision.
`basis="host_reported"` is supported. The current journal rejects
`tool_observed` because it does not yet attest execution telemetry; preserve raw
calls/artifacts separately instead of upgrading a self-report.

## What to try and measure

For a wrong inference, test the offending input and a nearby counterexample.
For lookup cost, first check whether the description and exact SDK/member pointer
are visible in the host's actual installation; try a short pointer or smaller
relevant reference. Known SDK calls can bypass both catalog and skill. A local
script may combine independent reads and branch on real returns in one call.
Do not require full skill adoption or use adoption rate as the objective.

Keep quality, actual adoption and cost separate: retain raw tool returns, edits
and output, wall time, shell/MCP counts, cumulative and cached input/output
tokens where available, failed attempts and unknown monetary cost. Narrowing
returned bytes is a component measurement, not established host time savings.

`MethodEvaluation` can record a **diagnostic** comparison referencing protocol,
`TaskSplit`, initial-conditions Artifact, result Artifacts and existing accounting
references. An accounting Artifact needs `bindings={"kind": "accounting"}`;
it describes measured/unknown costs and does not create a second budget ledger.
See the executable `examples/skill_improvement.py` in the source distribution.

For a later independent claim, retain unexposed task groups and freeze subjects,
comparators, protocol, split and initial conditions before release through
`journal.policy_version(split, {"method_evaluation": binding})`, where `binding`
contains those five fields exactly. Actual held-out Artifacts and
`journal.record_validation(...)` receipts must bind that policy and task.
`MethodEvaluation(assessment_role="independent_validation", ...)` verifies those
links and actual accounting references. A planned comparison has no receipt:
leave it `not_run`, rather than inventing an independent result. Scientific
labels and paused campaigns retain their own access and execution boundaries.

## Optional event use and rollback

An explicit `host.consume_events` call can examine one bounded page using
`event_types=["research_recorded"]` and
`record_types=["etalon-method-evaluation/1"]`. Bind the cursor to a handler
version and preserve its stable request ID for idempotent downstream effects.
The callback may recommend a candidate, skip or stop. Do not poll forever or
trigger again on every MethodVersion/MethodUse note; hook failure does not undo
committed facts. Changing filters/handler requires a separate cursor.

Retain or withdraw is a host choice, not a promotion gate. Select the prior saved
version for a future task when withdrawing; preserve the failed candidate and
its actual use. Keep any default-selection change separate from immutable
historical records. Stop when the local question is answered, the evidence is
insufficient, or the trial budget is reached; no automatic science retry or
background self-improvement follows.

## Reusing normal execution

For task-local algorithm trials, `etalon.research.method_reuse.execute_or_reuse`
retains exact completed requests. The caller supplies the complete implementation,
environment, input and transitive dependency identity. A cache hit reuses a result;
it is not a new confirmation replicate. Changed dependencies require examining
which prior checks still apply, not automatically repeating every check.

`examples/research_discovery.py` archives pinned executions, verifier reports,
evaluation, scoped experience and a resumable entry without running science.
The optional [action-learning adapter](../../etalon-research-decision/references/action-learning.md)
instead compares selected pre-action expectations with an already produced normal
result. Use its differences to inform the next choice; neither helper supplies a
predictor, chooses a method or proves quality benefit by itself.

## Saving the part that can guide the next choice

Keep task results, exact sources and failures in the existing journal and
published entry. A useful conditional lesson states which observed difference
changes an operation or branch, with its counterexample and unresolved endpoint.
Choose conditions that a future host can actually observe and use, such as the
input chemical state, operation and endpoint. A target name alone may locate an
example but cannot explain why its selection rule applies.

For an existing method patch, `improvement.record_experience` copies `task_id`,
`scope`, `conditions` and `method_refs` from that patch; it has no conditions
override. Save feedback for a changed endpoint under the corresponding new
context/patch only when an actual method revision is being recorded. Link the
old experience as evidence without rewriting its scope. Ordinary new results
can instead remain a decision/step plus Artifact; they need no artificial patch.

Preserve each outcome that changes the next choice rather than collapsing mixed
cases into one success flag. A shared fragment may be reusable while a changed
partner or final operation is still unknown. Exact output and feedback refs can
support the measured component; the lesson explains what to use, retain or
investigate next. Use `supersedes` for a revised lesson within its scope, not to
erase valid experience from a different endpoint.

Promote only the reusable part into a skill: the operation/API binding or a
selection distinction that removes demonstrated work. Keep molecule-specific
results, source conditions and current hypotheses in published experience. A
changed executable operation belongs to S; changed selection guidance belongs
to U. Revise G only when the improvement procedure itself changed. None requires
rewriting all three or claiming an unmeasured speed benefit.
