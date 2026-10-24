# Task-local resource adaptation: first batch

The host remains the scientific decision maker. This batch adds an optional,
versioned selection layer using existing evidence, publication and host APIs.
It does not add a scheduler, scientific executor, fixed Probe or automatic RSI.

## Implemented chain

```text
published originals + exact skill versions
    → bounded cross-type descriptors → per-part condition checks
    → explicit host adaptation review → pinned task workset
    → existing decision step / checkpoint / CAS publication
    → existing research_context / evidence reads / hook delivery
    → optional host export of selected skill members
```

`research/meta_context.py` exposes `make_task_profile`, `describe_resource`,
`index_resources`, `prepare_adaptation_context`, `review_adaptation`,
`build_task_workset`, `read_task_workset`, and `publish_task_workset`.
The `research-index` skill is registered in the existing memory library and MCP
catalog, with its supporting API reference included in wheel/host packaging.

Index entries can describe skills, methods, workflows, claims, evidence,
experiences, verifiers and templates. They retain original locations and exact
identities; this is a bounded, explicitly supplied catalog, not global semantic
search. Each part declares conditions, required/irrelevant dimensions, availability
stage and dependencies. Missing premises remain unknown; an agent may propose a
new scope but cannot quietly rewrite the original evidence.

Review dispositions are `reuse`, `adapt`, `reference` and `exclude`. Only explicit
`reuse` entries with matching declared conditions become enabled. Adaptation is
still a candidate. A fallback workset enables only the named compatible template
and its dependency closure, with a reason and reconsideration conditions.

## Existing interface reuse and compatibility

| Seam | Implementation |
| --- | --- |
| Original identity | Existing evidence refs, artifact/member byte verification, skill manifest digest |
| Visibility | Purpose-restricted workspaces; evaluation-only descriptors filtered before summary; full evidence dependency bindings on the workset Artifact |
| Publication | Existing `decision` step, unchanged checkpoint schema, durable intent and CAS entry update |
| Activation | Only checkpoint-reachable marked decisions; explicit same-task parent chain; conflicting heads, omitted dependency bindings and foreign predecessors rejected |
| Context | Existing `etalon_research_context` adds `task_workset`; exact details remain available through existing evidence reads |
| Hook | Task/workset change gives a baseline with removed and enabled resource versions; same version keeps existing delta/unchanged behavior |
| Skill export | Optional `host.export(..., task_workset=...)` physically omits unselected members; complete discovered skill-root inventory is verified |
| Legacy behavior | No workset means `legacy_unscoped`; old export calls still export the full library; no new content projection enum |

The first storage format packs separately versioned profile/index/review metadata
inside one ordinary workset Artifact, limited to 32 KiB and at most 64 resources.
Its `bindings.evidence_refs` lists every non-skill content, support, counter and
parent workset dependency. Generic artifact readers therefore apply existing
transitive visibility restrictions even without interpreting the new schema.
Activation also checks that these bindings are complete.

The first batch reused frozen collections only inside a read. The subsequent
[verified incremental-read implementation](INCREMENTAL_HOOK_READS.md) also gives
hooks a bounded, disposable cache of frozen Journal decoding. Every reuse checks
original source/projected bytes; cross-source visibility and reverse-counter
search are recomputed. This never introduces mtime-based or stale-byte trust.

## Use

Start with the current task's existing entry. Read the `research-index` skill and
only the detailed API reference needed to build a review. Use the SDK to construct
descriptors and an explicit review, then:

```python
intent = publish_task_workset(
    entry, workset, operation_id="task-selection-1", directory=publication_dir,
    expected_entry_identity=file_identity(entry))
resume_publication(intent["intent_path"])
```

For a new destination, supply its same-task `parent_entry` and expected identity
`None`. Switching tasks requires the other task's own checkpoint; never rename
SND1's checkpoint as PoseBusters. Repeating a publication reuses the same intent
and never starts a scientific action. The example CLI
`examples/research_meta_workset.py` accepts a reviewed JSON plan, fixes the original
parent/CAS identity and supports replay or `--prepare-only`.

New readers receive workset identity, enabled versions, inactive decisions and a
detail reference. Changed explicitly supplied task conditions produce
`review_required` and no enabled defaults. Unavailable originals or scope errors
are reported as unavailable, not as an empty index or permission to fall back.

## Acceptance scope and remaining work

Tests include missing scope, partial adoption, template closure, original/member
changes, evaluation filtering, generic-reader visibility, unsafe/extra skill
files, conflicting activation lineage, version replacement, condition changes,
actual subprocess death at all eight publication stages and CLI replay.

The real-case acceptance uses verified copies of the existing SND1 release and
the already-observed PoseBusters panel. It preserves their distinct tasks and
scientific checkpoints, original successes and counterevidence. A new paused local
checkpoint explicitly scopes this read/archive acceptance. It is not a historical
pre-CNN decision replay, new docking run, blind evaluation or proof of transfer
benefit. Server current and old tasks are unchanged.

Hook acceptance here uses the real adapter API and durable receipts. Native host
events/consumption remain a separate acceptance after the existing hook review.
PostToolUse remains observational; updated context arrives at the next supported
injection event. No code erases conversation text already seen by an agent.

Two independent fresh-context subagents checked entry-only continuation, original
scientific conclusions, inactive defaults and the absence of pending computation.
The copied joint review workspace lacked each task's authority locator. Separate
read-only handoff views restore that task's already-pinned paused guard, preserving
the checkpoint, frozen sources and complete publication envelope. This does not
transfer ownership or authorize execution.

At the first-batch checkpoint, default summary reads took about 57 seconds for SND1 and
92 seconds for PoseBusters locally. PoseBusters also reported graph/reference
truncation and insufficient counterevidence coverage; the independent reader
followed exact original refs before concluding. These reads exceed the exported
native hook's 30-second timeout. The following batch addresses this read path;
native event consumption still requires its own receipts. Adapter success does
not establish native delivery latency or complete evidence coverage.

Ongoing
research should use a visible new chat when the host exposes that capability;
this module never creates one itself. The current session has no visible-thread
creation tool, so a CLI session or subagent is not reported as a sidebar chat.

Further work remains: adapters that discover descriptors from each source library,
mechanical execution bindings for arbitrary semantic method parts, independent scientific adaptation-benefit comparisons,
and native visible-chat handoff/ownership receipts. Selecting a part is not proof
that execution consumed it. RSI can reference the saved review/workset as a
concrete improvement context, but this batch does not automatically trigger or
claim improvement.

## Explicit resource use and feedback associations

`research.resource_usage` connects an exact workset part to existing MethodVersion,
MethodUse, research node, evaluation step and subsequent decision. It stores one
ordinary `etalon-resource-usage/1` Artifact with complete evidence dependencies
and source-qualified graph links. It creates no MethodUse, evaluation, cost entry,
executor or activation. Use `build_resource_usage`, `record_resource_usage`, then
`read_resource_usage` to verify the association. `workset_resource_part` resolves
the reviewed part and its selection disposition.

The reader checks exact identities, same-task MethodUse, explicit node/use/method
links, evaluation plan and compared methods, and the later decision's evaluation
basis. The decision may refer through one explicit existing method experience
whose evaluation, task and method match. A method resource must identify that
exact MethodVersion; a skill must match its manifest identity. No name matching
fills a missing edge.

| Field | Evidence and limit |
| --- | --- |
| `selected` | Derived from the reviewed workset, including inactive parts. It proves neither activation nor use. |
| `read` | Historical phase observation; the reader separately reports that it verified the named resource now. Reading bytes does not demonstrate comprehension. |
| `consumed` | `unknown` or explicit `host_reported`, never inferred from a MethodUse, delivered context or successful node. |
| `executed` | `unknown` or explicit `host_reported`; a report requires an applied node and receipt, while success/failure/reuse remains separate. |

Every phase report includes evidence refs and a note. Reports require MethodUse
to bind `host_conditions.task_workset_ref`, name the exact `used_parts`, and appear
in the report's evidence. Executed reports also cite their node. Mechanical
resource-consumption telemetry is not implemented; `tool_observed` is rejected.
Retrospective associations require explicit provenance/source refs and keep all
three historical phases `unknown`, even when old MethodUse records are host
reported. Optional `checkpoint_ref` checks activation at that exact checkpoint;
without it activation is unknown. It is never a latest-head claim.

Verification covers the named resource and explicit association chain, not every
indexed resource. Corrupt/missing sources are errors; absent optional links remain
unknown. `chain_complete` means all specified association edges were checked, not
causal benefit or proven execution of a skill. An association Artifact can enter
`build_revision_context` as a problem/evidence ref without triggering a revision.
A historical SND1/PoseBusters association cannot reconstruct pre-CNN knowledge,
imply independent transfer, or enable incompatible parts.

Graph links for ordinary usage Artifacts are labeled `declared_*`. Artifact
metadata can declare a relationship; only `read_resource_usage` performs the
semantic joins. Unknown relation names and links absent from dependency bindings
are omitted by the graph adapter, never presented as verified resource use.
