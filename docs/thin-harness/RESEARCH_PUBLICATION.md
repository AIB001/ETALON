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

This batch deliberately **does not resolve a pending action, close/reopen a branch, replace a scientific judgment, or relax a pause**. Those changes need explicit resolution evidence and a separate reviewed checkpoint transition. A completed action may therefore remain in `pending_handles` until such a transition; existing continuation reads its current state and still never resubmits it. This publisher closes the archival/publication path, not the entire scientific lifecycle.

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
