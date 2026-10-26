# Recoverable research publication

The publication API archives **explicit existing files and caller-authored research steps**, creates a checkpoint that retains the selected research state, and atomically advances a compatible `{workspace, checkpoint_ref}` entry. It does not infer a scientific conclusion from chat, execute a method, retry an action, settle costs, or trigger RSI.

`etalon.research.publication` reuses `ResearchJournal.record(Artifact)`, `continuation.record_content`, existing evidence references and workspace manifests, SQLite backup, and the host's cross-platform OS lock. Each operation gets a separate archival journal with zero budget and a non-queryable endpoint. This is a memory store; it is not an execution ledger or a second resource allocator. Frozen source journals and original artifacts are never opened writable.

## API

```python
from etalon.research.evidence_refs import file_identity
from etalon.research.publication import (
    prepare_publication, resume_publication,
    inspect_publication, discover_publications,
)

prepared = prepare_publication(
    entry="/research/current/entry.json",       # new destination in this example
    parent_entry="/research/frozen/entry.json", # selected frozen history
    operation_id="archive-completed-result-01",
    expected_entry_identity=None,              # destination must still be absent
    results=[{
        "name": "completed_result",
        "path": "/research/producer/result.json",
        "identity": file_identity("/research/producer/result.json"),
        "source_kind": "existing_real_artifact",
        "role": "diagnostic",
        "bindings": {"protocol": "explicit-producer-protocol/1"},
    }],
    steps=[{
        "name": "reuse_decision",
        "content": explicit_research_step,     # ordinary etalon-research-step/1
    }],
)
published = resume_publication(prepared["intent_path"])
```

The step may refer to the newly archived result using `{"publication_ref": "completed_result"}` wherever the existing step schema accepts an evidence reference. Earlier named steps can also be referenced. All other references remain ordinary exact evidence references. The caller supplies the question, reason, decision, conditions, existing experiment/observation references and accounting references; the publisher does not invent them.

For a subsequent operation against an existing entry, omit `parent_entry` and supply `expected_entry_identity=file_identity(entry)`. For a new destination, `parent_entry` is mandatory and `expected_entry_identity` must be `None`. The chosen parent's file identity and frozen source identities are bound into the intent. A changed parent requires explicit review and a new operation.

`directory` is optional and defaults to `<entry parent>/.etalon/publications`. Operation identity is scoped to the destination entry. Repeating the same operation ID and identical request returns the existing operation, including if a different storage directory is supplied. Reusing that ID with changed content fails. Normalized request equality, rather than merely a filename, protects against conflicts.

The bounded input is at most 32 existing result files and 1–16 explicit steps. Results require all six fields shown above. Supported provenance and roles are the existing `Artifact` values. Metadata is limited to 4 MiB; normal step/checkpoint size and reference limits still apply. Large scientific results remain referenced files, not checkpoint text.

Before registering an intent or copying a result, `prepare_publication` resolves result and step aliases in order and reuses the existing content, evidence and composition validators. It validates every step's fields and enum values, the exact existing references, local typed links, and the final combined checkpoint's limits. Unknown, self or forward aliases fail. A result cannot masquerade as a claim, Experiment or Observation, and a sealed result cannot enter visible step context. Deterministic future Artifact identities are computed in memory; there is no temporary CampaignStore, scientific execution or persisted simulated journal. The actual writer and its digest algorithm remain unchanged. Runtime availability and file identities are checked again on resume.

Validation failures may leave the per-entry lock file/directory, but do not create a publication index entry, intent, copied result or new journal. Exact replay of an existing intent retains the older API's behavior; old unfinished malformed requests remain inspectable and can be explicitly closed.

## What the checkpoint retains

The publisher copies the selected checkpoint, appends the new step references, and sets `supersedes` to the parent checkpoint. It retains the task and question, all earlier steps, claims, counterevidence, methods, branches, pending handles, authority guards and source watermarks. Optional `checkpoint_additions` can append explicit `claim_refs`, `counter_refs`, or `method_refs`; it cannot replace those lists. Reference identities and typed step/claim links are checked before the entry changes.

By default publication preserves every branch. Optional `branch_updates` explicitly change an **existing** branch, bound to a newly published same-task, same-branch decision step:

```python
branch_updates=[{
    "branch_id": "md-controls",
    "expected_state": "waiting",
    "state": "resolved",
    "basis_refs": [{"publication_ref": "analysis"}, {"publication_ref": "limitations"}],
    "next_read_refs": [{"publication_ref": "analysis"}, {"publication_ref": "limitations"}],
    "reconsider_when": "A new prepared system or analysis question changes the scope",
    "decision_ref": {"publication_ref": "reuse_decision"},
}]
```

The decision's `basis_refs` and `reconsider_when` must match the transition. Next reads must belong to that evidence. The resulting branch also links the decision itself. `resolved` accepts `reuse`, `stop`, or `merge`; `waiting` requires `defer`; `stopped` requires `stop`; `open`/`active` require the corresponding explicit reconsideration/proceed/fork choice. Reopening a terminal branch always requires `reconsider`. This records a host decision, not automatic scientific judgment. A missing, stale, duplicate or cross-branch update fails during preflight before archival writes. The transition is included in immutable intent equality and replayed through the same crash-safe publication stages.

Task/question, authority guards, watermarks and existing counterevidence cannot be replaced through this option. A branch marked active does not grant execution permission or relax a pause. Legacy producer receipts remain valid historical evidence. Native PRISM jobs can now be attached explicitly using `execution_recovery.bind_native_handle`; do not fabricate runtime handles. Empty `pending_handles` is not a global idle-machine assertion.

`handle_update={"expected": old_handles, "replacement": selected_handles}` is a
separate exact pending-set update. Removed handles must remain on a result step
and be terminal; completed jobs require passed native verification. Paused or
interrupted jobs remain unresolved. Prepare and resume freshly read retired
handles; successful publication replay verifies saved evidence without repeating
native execution. No update settles scientific charges or starts/resumes a job.

## Compact result publication and fixed native jobs

`prepare_result_publication` accepts explicit results, existing typed
experiment/observation/method refs and a host-authored decision, compiles the
ordinary steps/branch update, and calls `prepare_publication`. It does not create
observations from untyped JSON or decide whether a method is better. Its CLI is
`examples/research_publication.py prepare-result REQUEST.json`. Use the same
`resume_publication` after interruption. Exact replay compiles against the saved
parent checkpoint, not the already advanced entry.

The decision fields are `choice`, `state`, `reason`, `reconsider_when`, and
`basis_refs`; results retain the six explicit provenance fields shown above.
`experiment_refs`, `observation_refs`, `method_refs`, `accounting_refs`, and
`input_refs` link existing records. `counter_refs` only adds to inherited
counterevidence. `pending_handles=None` leaves jobs unchanged; an explicit list
tracks or settles jobs while preserving result lineage. No new records, stages
or reference limits are required.

For native continuation, capture `boundary.runtime_profile.capture(spec)` on the
execution machine with capability `prism.inspect`. Pin the PRISM import, its
source root and a check for `GromacsJob.inspect`. Deploy the two stdlib-loadable
files `boundary/native_result.py` and `boundary/runtime_profile.py` together in
an immutable version directory. `bind_native_handle(directory, native=handle,
profile=profile, transport={"kind":"ssh","host":"alias"}, reader_files=files)`
binds both reader hashes and the original PRISM handle. Each `files` member is
`{path,sha256,bytes}`, keyed by `native_result` and `runtime_profile`.

`runtime.api.read_action_result(handle)` (also the existing MCP result tool)
uses that descriptor, verifies actual imports/checks in the fixed Python, and
calls PRISM `inspect()`. That call rechecks the job contract and all completed
output hashes. The descriptor is a small immutable sidecar, not a job store;
keep it available or republish an explicitly relocated handle when moving hosts.
Remote errors remain unavailable, not completion. No polling daemon or scheduler
is installed. Results distinguish execution, original verification, unknown
scientific admission and accounting not resettled.

For *new* PRISM jobs, `GromacsJob.create(..., execution=config)` pins native
backends, checkpoint interval, reproducibility/PME switches, GPU ID, maximum
hours and optional explicit CPU/device placement in the existing contract.
`mdrun_command` compiles it; no task-specific argv-rewriting wrapper is needed.
TPR scientific parameters remain separate. Omission retains legacy CPU argv.
Execution profiles still bind the actual interpreter, imports and environment.
Existing jobs keep their original supervisor snapshot and configuration.

## Durable sequence and recovery

| Boundary | Saved state | Recovery action |
|---|---|---|
| Prepare index | Entry-side index names the intended operation | Missing intent is reported as unavailable, not an empty frontier |
| Prepare intent | Exact parent, request, identities and checkpoint content | Repeat identical prepare, or resume the saved intent |
| `results_saved` | Complete verified copies of explicit producer files | Reuse saved bytes even if producer files disappeared |
| `results_recorded` | Idempotent Artifact records in the new journal | Re-record the same identities; no extra events |
| `steps_recorded` | Explicit immutable step content and references | Reuse the same records |
| `checkpoint_recorded` | New checkpoint and ancestry reference | Finish snapshot/publication only |
| `snapshot_saved` | SQLite backup plus source/snapshot identities | Verify and reuse the same snapshot |
| `workspace_saved` | Immutable workspace and candidate entry | Verify references, then compare the current entry identity |
| `entry_replaced` | New entry visible through atomic replacement | Complete receipt even if the process died before writing it |
| `complete` | Publication completion receipt | Read-only verification; no duplicate scientific work |

Stage receipts are append-only. A process can die after an individual journal append or after pointer replacement but before the following receipt. Existing content-addressed identities and the saved candidate entry make these gaps recoverable too. Failed/unpublished data and intermediate files remain available for inspection; the API does not clean them up.

`resume_publication(intent_path)` repeats only archival operations. A stale entry produces a compare-and-swap conflict and preserves the candidate's data; it never merges competing scientific branches or silently selects a winner. A per-entry OS lock serializes cooperating publishers and is released on process death. The identity check protects against stale inputs. Writers that ignore the lock and edit the entry during replacement are outside this concurrency contract.

File contents are flushed before publication; POSIX directory entries are also fsynced. The acceptance guarantee here is process-crash recovery on the supported local filesystem, not a claim of storage-controller or power-loss durability on every filesystem. Shared network filesystems and multi-host lock semantics require separate verification.

## Entry-only discovery and hooks

Preparation registers the operation in `<entry>.publications.json` **before** saving the intent or copying results. The index is an explicit list, not an ambient directory scan. The existing entry bytes are unchanged until final publication. A missing new destination entry can still be inspected through its sidecar.

```python
frontier = discover_publications("/research/current/entry.json")
# schema, entry, operations, incomplete_count, automatic_retry=False

status = inspect_publication(frontier["operations"][0]["intent_path"])
# operation_id, phase, status, needs_recovery, checkpoint_ref,
# entry_conflict, stage names and entry identities
```

`prepared`, `in_progress`, `conflict` and `unavailable` require attention. `published` means the current pointer equals the saved candidate. `superseded` means a completed publication's pointer has subsequently changed; inspection does not endorse that later pointer. The inspection API is metadata-only. Calling `resume_publication` for an already completed operation verifies saved journal, snapshot, workspace, result bytes and content references before returning idempotent success.

Missing or unreadable indexed intents remain visible as `unavailable`. A malformed overall index raises an error; context readers must show unknown state rather than assume no pending work. Read-only discovery and inspection create no lock, directory, journal event or receipt. Hooks may expose these statuses and exact intent references; they must not invoke recovery automatically.

## Close an unsuccessful publication or explicitly correct it

An unsuccessful intent is not deleted or silently retried. `close_publication` writes one immutable `disposition.json`, using the same per-entry process lock as prepare/resume:

```python
from etalon.research.publication import close_publication, inspect_publication

state = inspect_publication(intent_path)
closed = close_publication(
    intent_path,
    disposition="withdrawn",
    reason="The protocol reference was wrong; retain this attempt for audit.",
    expected_intent_identity=state["intent_identity"],
)
```

The exact intent identity binds the decision to the reviewed request. Existing intent, journal, results, stage receipts and entry bytes are preserved. The same close request is idempotent; a changed reason, disposition or replacement conflicts with the immutable terminal decision. `withdrawn` has `terminal=true` and `needs_recovery=false`. It is an archival decision, not a statement that the underlying scientific experiment failed. `resume_publication` refuses a terminal operation rather than executing or reopening it.

A published operation cannot be reclassified as failed. This includes a successful entry replacement whose process died before recording the completion receipt, and a successful publication whose entry later advanced (`superseded`). Those remain successful history. Missing/corrupt intent metadata must be recovered or reviewed separately; it cannot be hidden by fabricating a terminal decision.

To correct an unfinished request, prepare a **new operation ID** with explicit corrected results/steps and add `corrects_intent=old_intent_path` plus a nonempty `correction_reason`. Preparation captures the old intent identity in `intent.corrects`; it does not mutate or automatically close the old attempt. Once the correction is prepared, close the old intent with `disposition="replaced"` and `replacement_intent=new_intent_path`. This requires a same-entry, same-task correction that points to that exact old intent. The replacement is still prepared work until explicitly resumed; `replaced` does not assert that it succeeded.

The terminal record carries the reason, replacement identity, phase and entry identity at close. Inspection/discovery expose `intent_identity`, `terminal` and `disposition`; `withdrawn` and `replaced` no longer count as incomplete work. If a host needs the full reason or replacement reference, read `disposition.request`. The disposition is append-only and never confers retry or execution permission.

This version does not invent correction links for historical operations or infer that matching task names prove cross-entry ancestry. A historical failed operation whose valid successor lives in another destination can be explicitly withdrawn with an accurate reason and successor locator. No old intent is rewritten to pretend it had declared `corrects`.

The index is bounded to 256 operations. Explicit archival/compaction of long histories is not implemented in this batch. No extra MCP write surface is required: a host can use the SDK while ordinary MCP context/evidence reads consume the published entry.

## Current locality and scope

Parent sources must already be frozen `snapshot_sha256` manifests with no live WAL. The publisher preserves old identities, converts source locations to explicit absolute locations, and copies explicitly mapped artifacts into the new workspace when needed. Authority references must be absolute, local current-host paths. A foreign or missing authority path needs an explicit relocation/review first; copying a historical pause file is not a substitute for checking current authority.

This batch does not claim that archiving results proves convergence, affinity improvement, method transfer, workflow selection quality, or scientific benefit. Fixture tests distinguish archival correctness from science. SND1 integration separately checks reuse of real existing evidence without restarting its computations. R4b remains paused and resource allocation remains out of scope.
