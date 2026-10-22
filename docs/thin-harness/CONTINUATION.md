# Research-step and checkpoint / 1

This increment reuses ResearchJournal, ordinary immutable Artifact files,
EvidenceRef/workspace, query/read, and existing action-result reads. It adds no
scientific ledger, executor, model, router, mandatory skill, or research loop.
The host owns scientific interpretation and the next action. Resource allocation
and R4b execution are outside this engineering batch.

## SDK

```python
from etalon.research.continuation import (
    validate_content, record_content, read_content,
    capture_watermarks, resume_research,
)

# `journal` is an already configured ResearchJournal.
record_id = record_content(journal, payload, content_directory,
                           source_id="research", source_kind="new_execution")
# Publish that journal explicitly in an evidence workspace, then:
content = read_content(workspace, exact_evidence_ref)
context = resume_research(workspace, checkpoint_ref)
```

`record_content` accepts research claim/1, step/1 or checkpoint/1, verifies local
record references and artifact bytes, and writes an ordinary Artifact. External
refs retain their source namespace and are explicitly unresolved at write time;
published evidence reads verify their available metadata/bytes. These checks do
not certify scientific truth, actual method adoption, independent evidence or
lab effectiveness. Existing claim writing and method_revision APIs remain valid.

Contents are at most 16 KiB; large derivations/results belong in separate linked
Artifacts. Lists are bounded. Unknown fields/operations are rejected. Full bytes
are published without replacing an existing content-addressed file, then the
existing journal transaction appends its Artifact. Failure between file and DB
leaves a complete orphan suitable for idempotent retry, not a completed record or
a claim of filesystem/DB atomicity. Old sources and records are never migrated.

## Content contracts

`etalon-research-step/1` requires `task_id`, `question`, `branch_id`, `operation`,
`reason`, `input_refs`, `output_refs`, `method_refs`, `accounting_refs`,
`source_watermarks`, `supersedes`, and `detail`. Reference lists contain ordinary
source-qualified EvidenceRefs. Branch IDs name logical research branches, not
action IDs. `detail` is selected by `operation`:

| Operation | Required detail |
| --- | --- |
| search | query, source_ids, conditions, coverage, selected_refs, excluded, refresh_when |
| judgment | claim_refs |
| plan | hypothesis_refs, prediction, controls, readouts, protocol_refs, decision_rule, stop_rule |
| result | experiment_refs, observation_refs, action_handles |
| decision | choice, alternatives, basis_refs, reconsider_when |

Search coverage lists source_id/status/truncated/errors; complete cannot conceal
errors/truncation. Exclusions retain ref/reason. Plan readouts list name/unit;
plans cannot carry fictitious actions. Actual result records point to existing
Experiment/Observation objects; local observations must belong to the referenced
experiments. An action handle, if present, must resolve through the existing
read_action_result API. Method metadata remains optional.

Decision choices are reuse/proceed/fork/merge/defer/stop/reconsider. Alternatives
retain branch_id/disposition/reason; an unexecuted branch has no invented outcome.
A changed decision creates a new Artifact with supersedes. Support/counter
relationships remain in claims; input/output references express data flow;
decision/plan details express conditions. They are not a compulsory execution DAG.

`etalon-research-checkpoint/1` requires task_id/question, step_refs/claim_refs/
counter_refs/method_refs, branches, pending_handles, source_watermarks,
status_guards, and supersedes. Each branch has branch_id, state
(open/active/waiting/resolved/stopped), basis_refs, next_read_refs, reconsider_when.
It is a read view of a research frontier, not a second mutable task registry.

Watermarks contain source_id/snapshot/read_watermark. `capture_watermarks` reads
explicitly chosen sources and their evidence dependencies, requiring complete
coverage. Capture stable evidence inputs, not the journal about to receive the
checkpoint: a record cannot contain its own post-append digest. Current exact
references, competing/successor checkpoints and superseding content in the
published view are checked separately. Same-task steps absent from the selected
checkpoint's reference closure also require review. Only published sources are discoverable;
the helper cannot detect a newer checkpoint in an unpublished archive.

## Scoped reads and compatibility

Existing query/read signatures and MCP tools are unchanged. A source_ids query
reads those sources plus their explicitly referenced evidence-source dependencies;
only requested sources supply result candidates. Direct ref reads start at its
source. No unrelated source is opened. Dependency expansion is bounded to 64
additional sources; exhaustion is unavailable/partial coverage, never a complete
empty search. Declared sealed sources are not opened, and their derivatives remain
hidden transitively. Scope filters are not a substitute for actual holdout access
isolation.

Coverage identifies requested/dependency sources. Scoped cursors bind visited
source configuration, mappings and watermarks; unrelated manifest changes no
longer invalidate them, while changed dependencies do. Existing unfiltered queries
retain their all-published-source meaning. Dependencies are expanded at source
granularity; this is not a record-level database index. Claim/revision projection
limits also apply to step/checkpoint (64 contents/source, 1 MiB each, 8 MiB total).

## Read-only new-chat continuation

An entry JSON needs `workspace` and `checkpoint_ref`. `resume_research` returns
the selected question/frontier, claim/step references, checked reference closure,
source changes, current authority, pending results, issues and warnings. It checks
up to 128 records in the published dependency closure; overflow requires review.
Original artifact bytes are verified. Missing/damaged or superseded evidence
marks the selected context for review; it does not rewrite conclusions or launch
new experiments. An old checkpoint remains readable, with newer/conflicting
checkpoints reported instead of silently selecting one.

Workspace `status_refs` may name explicit local locations (strings or objects
with location). A checkpoint status_guard contains location/sha256/bytes/state.
The state active/paused/stopped/unknown is the host's interpretation of those
exact authority bytes, not an inference by a prose parser. At resume, authority
locations are re-read. Changed/missing/unreviewed locations or unsupported status
entries require review; unchanged paused/stopped guards override old branch
descriptions. These guards are conservative context checks, not an authorization
mechanism. The helper always returns `execution_permission="not_granted"` and
`automatic_retry=false`.

Pending handles are deduplicated and read through read_action_result. Missing
jobs stay unresolved. No submit/reconcile/retry/cancel, cost write, status update,
resource allocation, or background polling is performed. Snapshots are per-source
reads, not a global atomic snapshot. The caller decides whether to inspect more
evidence, change a claim, create a new checkpoint or perform an authorized action.

## Reproducible acceptance example

`examples/research_continuation.py build --released-root ... --output NEW_DIR
--paused-status ...` consumes explicitly released R4 coordinator evidence. It
uses the frozen cost coefficient to recompute phi versus phi0, saves the search
scope, hypothesis, plan, real local reanalysis Experiment/Observation, claim and
branch decision, then publishes a checkpoint and entry.json. It configures no
executable endpoint and dispatches no science. Historical costs remain references;
local analysis timing has a separate measured scope.

Another Python process or host can run `examples/research_continuation.py resume
--entry .../entry.json`, or use the SDK with those same two entry fields. This
tests actual released-evidence reuse and continuation. It does not turn old R4
evidence into a fresh independent test or claim simulation/RSI benefit. Separate
fixture tests exercise pending jobs, corrupt files, stale cursors, visibility,
supersession, and changed/pause authority. Actual native-host observations must be
reported separately from these Python checks.
