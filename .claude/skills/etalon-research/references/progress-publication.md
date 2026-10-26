# Archive progress and advance the research entry

For an open research goal, preserve both the scientific residual and the next
executable segment when one is known. The optional
`research_segment.work_from_entry(goal, entry, branch_id=...)` builds a compact
ordinary work Artifact from the current branch. `run_segment(...)` invokes a
task-owned Python producer once, saves its compact result and updated work, and
automatically publishes through the existing result API. Let that producer
batch known operations until new evidence needs judgment; do not return every
deterministic substep to the model. Its response keeps measurements, decisive
examples/candidate indices, failure denominators, residuals and original refs.
The host still chooses interpretations and may change the method or question.
For an already understood conditional followup, call it inside the same producer
(for example, mixed paired signs trigger the known endpoint-sensitivity readout)
and return only after a new scientific residual appears. An unchanged ordinary
next command can run directly from its pinned inputs. Source code, publication
intents and old result bodies need expansion for a concrete contradiction, changed
dependency or error, not for each successful method invocation. The compact
response and existing publication receipt already provide the normal handoff.

Use `read_work(entry, branch_id=...)` in a fresh context to retrieve the current
verified work and optional ordinary command. Expand original evidence for an
unresolved judgment. Reuse the same producer operation/directory after a saved
response or publication interruption; an unknown producer outcome needs its
existing outputs/handles inspected, not an automatic rerun. An already completed
method can call `publish_segment_result(...)` directly. Natural goal plus an
existing authority/runtime profile is supported by the lifecycle example; see
`docs/thin-harness/RESEARCH_RUN.md` for its API and independent-context option.

For an existing native PRISM job, use `read_action_result(handle)` with the
`etalon-native-handle/1` stored in the checkpoint. It verifies the bound runtime,
contract and original outputs. Read or publish failure is not a scientific retry.
The fixed profile is captured once in the actual execution environment using
`boundary.runtime_profile.capture`; `execution_recovery.bind_native_handle`
binds that profile and the deployed reader files. Keep the descriptor available
beside the entry; its identity is part of the handle. The profile belongs to the
execution machine and never silently falls back to a local/default environment.

When typed result and method records already exist, prefer
`prepare_result_publication` to writing a new journal/copying boilerplate. Supply
existing Experiment/Observation/MethodUse refs, explicit output files, and your
decision `{choice,state,reason,reconsider_when,basis_refs}`. This compiles ordinary
result/decision steps and delegates to the same publisher below. Use
`pending_handles` only to explicitly track a job or settle it after a terminal
result; settlement keeps the handle on the result step. Old counters and paused
authority remain. No callback chooses a scientific successor for you.

Save the returned intent path and resume it after host exit. Do not regenerate
timestamped result files or pick a new operation ID on reentry. Read scientific
conclusions and relevant counter bodies via the evidence reader; a verified
native completion establishes execution, not affinity, synthesis yield or method
adoption. If the branch is resolved and identity unchanged, reuse it. Otherwise
choose the next useful action from the actual evidence within existing authority.

Let a scientific producer emit the explicit `prepare_result_publication` keyword
arguments as a saved request, including output identities, its unresolved question
and existing handles. `examples/research_publication.py publish-result REQUEST.json`
prepares and resumes that same durable intent and returns a compact receipt.
Reuse the exact request after interruption; no task-specific publisher is needed.
Publish when a result changes a decision, a job must outlive the host, or ownership
passes to another session. A completed step need not end the research objective.
For process exit and bounded automatic wakeup, see
`docs/thin-harness/RESEARCH_RUN.md`; scientific choices remain with the host.

Use `etalon.research.publication` for completed local outputs and explicit
research steps. Publication copies existing bytes, records them through
ResearchJournal, writes a checkpoint and frozen workspace, then replaces the
entry only if its expected identity still matches. It calls no scientific
producer and triggers no RSI candidate.

1. Read the current entry, checkpoint and any unfinished publications. Reuse
   existing result files and pending handles; a publication failure is not a
   reason to submit another calculation.
2. Prepare an explicit intent with a stable operation ID and expected entry
   identity. Results include absolute path, SHA-256/bytes, source kind, role and
   bindings. Steps use the existing research-step contract. Bind an earlier
   named result or step with `{"publication_ref": "name"}`.
3. Call `resume_publication(intent_path)` to finish the archival operation. On
   interruption, inspect that same intent and resume it. Do not create a new
   operation ID to hide a conflict or missing output.
4. Read the published checkpoint through the entry. State what was reused,
   what was newly recorded, and which scientific question remains unresolved.

```python
from etalon.research.evidence_refs import file_identity
from etalon.research.publication import (
    discover_publications, prepare_publication, resume_publication,
)

pending = discover_publications(entry)  # read-only; errors remain visible
intent = prepare_publication(
    entry, operation_id="archive-comparison-01",
    expected_entry_identity=file_identity(entry),
    results=explicit_results, steps=explicit_steps,
)
published = resume_publication(intent["intent_path"])
```

An entry sidecar (`<entry>.publications.json`) identifies interrupted publication
without scanning historical directories. A selected checkpoint's empty
`pending_handles` means no recorded scientific jobs; it does **not** imply no
unfinished memory publication. Hooks/context expose both states separately.

An entry conflict preserves the saved results and intent. Inspect the newer
checkpoint, reconcile explicitly, and prepare a new operation against the new
entry identity. Never overwrite the newer pointer or merge conclusions silently.
Reusing an operation ID with different content is rejected.

Source artifacts must remain available until copied. The publication mechanism
does not recover a producer's unrecorded output or guarantee power-loss recovery
on every filesystem. Existing evidence is archival reuse, not an independent
observation. Keep pauses, permissions, branches and pending jobs explicit.

See `docs/thin-harness/RESEARCH_PUBLICATION.md` for the supported checkpoint
updates and `examples/research_publication.py` for a file-based CLI.
