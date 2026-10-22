# Research graph and bounded context

The research graph is a read-only projection of existing Journal records,
Artifacts, research steps, workflows and checkpoints. It is not a new database
or scheduler. Search, Judge, Quick and Deep are work areas; MolQuarry,
MolCascade and PRISM remain capability providers that can serve several areas.

## Record identity and graph structure

`build_research_graph(workspace, checkpoint_ref=...)` returns
`etalon-research-graph/1`. A node has one stable ID derived from the source,
record ID, record digest and optional member. Several branches refer to that
same node; raw structures, trajectories and observations are not copied.
An alias published under another source ID remains a separate provenance
identity; the view does not silently deduplicate files by molecular name.

Each node has one `home`: Search, Judge, Quick or Deep. E/J/X/R are small `kind`
metadata fields, not four additional graphs. Content type, identity verification,
task scope, conditions and limitations are separate fields. `home_overrides`
annotate an immutable record or a workflow logical node for a particular view.
They do not rewrite history. Older computational records default to Quick with
`home_basis=legacy_record_projection`; no resource cost is inferred from a PRISM
or MolCascade name. Deep attribution must be explicit when history lacks it.

Edges link existing record identities and retain a basis reference. They include
dependency, derivation, support, refutation, testing, supersession and workflow
flow. Workflow edges describe declared dependencies; they are not proof that a
tool ran. Repeated instances are not ordered by artifact hashes. A decision
node retains its alternatives, disposition, evidence and reopening condition.
`supersedes` preserves the earlier record and claim scope; it does not erase it.

The view starts at the checkpoint, follows exact references and includes current
task steps and explicitly related counterclaims across work areas. Unrelated
tasks do not enter through text resemblance. Nodes from another task are marked
`explicit_shared_reference`, never relabeled as belonging to the current task.
`validate_research_graph` checks IDs, endpoints, homes, kinds and task scope.

## Research context entry point

```python
from etalon.research.research_context import build_research_context

context = build_research_context(
    "path/to/existing/entry.json",
    node_id=None,
    purpose="research",
    max_bytes=16384,
)
```

The entry remains the existing `{workspace, checkpoint_ref}` object. A JSON
entry path may use a workspace path relative to itself. A dictionary may also
contain an already validated EvidenceWorkspace. No migration of old SND1
Journals is required.

The selection strategy is versioned. It considers the requested node, explicit
counterevidence, its neighborhood, the same branch, current judgments and other
checkpoint/task references. It preserves claim conditions and limitations,
decision alternatives, branch states, pause authority and observed pending
states. Source snapshots and journal watermarks accompany the returned context.
The context ID hashes the assembled output, including the selection receipt.
Repeated branch refs are normalized into `reference_index`; branches contain
`basis_node_ids` and `next_read_node_ids`. The index retains the exact source,
record digest and identity, including references to items omitted by the byte
budget. This reduces repeated bytes without dropping pause or branch state.

Current-task method experiences are prioritized with their outcome, lesson,
invalidating conditions, evidence refs and supersession refs. Optional
`conditions={name: value, ...}` performs the existing exact-condition matching
rule; a match still requires host review. With no explicit conditions the view
says `not_checked_for_new_branch`, rather than treating a remembered success as
permission to transfer it. Trace records keep their explicit research workspace;
method contracts and comparability reports are Judge records, compilation is a
Quick activity.

Raw artifact excerpts are bounded UTF-8 text only. Binary artifacts remain exact
references. All summaries are deterministic excerpts of verified published
content, not new scientific claims or model-generated reasoning.

The response states its candidate count, selection rule, policy digest, byte
omissions and graph truncation. It does not claim knowledge of everything a
global search might find. If no eligible counterevidence exists in the checked
scope, it says so; partial coverage is reported separately. There is no minimum
counterevidence quota and no invented counterclaim.

Counter-context includes both endpoints of a declared counter relation, including
claims discovered by following reverse links. If B lists A in `counter_refs`,
the edge remains A refutes B; B is not relabeled as evidence refuting A.
`counter_node_ids` and context `selected_refs` describe this conflict neighborhood,
whereas `counter_evidence_node_ids`/`declared_counter_refs` identify the declared
evidence endpoints. Dropping either endpoint or the directional relation from a
bounded response makes counter-context coverage incomplete.

`question` records the current decision question; it does not silently change
the research task or impose a semantic filter. This version does not perform
embedding retrieval, remote search, entity-equivalence inference or automatic
scientific judgment. Exact refs can be expanded through existing evidence APIs.

## Permissions before summaries

The published workspace is the outer access boundary. `policy` can only narrow
it, before projecting artifact content or assembling summaries:

```python
policy = {
    "version": "project-policy/1",
    "source_ids": ["research", "released-inputs"],
    "source_purposes": {"released-inputs": ["research", "evaluation"]},
    "record_rules": [{
        "source_id": "research",
        "record_id": "artifact-...",
        "allowed_purposes": ["evaluation"],
        "seen_by": ["previous-reviewer"]
    }]
}
```

`record_ids={source_id: [ids]}` is an optional allowlist. A seen annotation does
not authorize use. Excluding a dependency also excludes derived records under
the existing evidence rules. If a required checkpoint becomes inaccessible, the
operation fails closed instead of returning a potentially revealing summary.
Errors and selection receipts do not enumerate hidden paths or record contents.

This is an SDK boundary for explicitly supplied local sources. It is not a
replacement for host authorization, filesystem permissions, remote service ACLs
or isolation of other tools. A caller already possessing arbitrary file access
can read outside this API. Untrusted document excerpts are data, never host
instructions.

## Continuation, temporal limits and persistence

Continuation reuses `resume_research`, the existing collector/read verifier,
authority byte checks and read-only pending result inspection. It never submits,
retries, reconciles, updates branch state or grants execution. Pause is retained
even when a small byte budget forces evidence omissions. If mandatory control
metadata alone exceeds the response bound, the call fails explicitly.

Default mode observes the currently published view. `as_of` accepts only
`{"source_snapshots": {source_id: sha256, ...}}` matching a fully frozen supplied
workspace. It does not reconstruct old versions of a live journal. A frozen
snapshot is not proof of what a historical agent had seen. Historical strategy
evaluation still requires the actual earlier visibility and decision receipts.
Reads are checked per source; the output does not claim an atomic transaction
across several independent sources.

Generated graph/context JSON can be saved and recorded as an ordinary Artifact
through ResearchJournal by the caller. Generation itself is read-only and does
not register another claim or write to the old Journal. Their derived schemas
are deliberately not required in `record_content`; the existing Artifact path
provides byte identity and provenance without enlarging the typed 16 KiB content
contract. Native hook injection receipts can reference these outputs separately.

## Acceptance scope

`tests/test_research_graph_memory.py` and `tests/test_research_context.py` use
explicit synthetic fixtures to exercise cross-home counterevidence, shared
references, skip/reopen records, graph validation, workflow projection, policy
filtering, changed hashes, bounded selection, absent counterevidence, pause and
pending continuation. Mocked pending reads test the adapter contract, not real
job recovery. These tests do not establish molecular improvement, workflow
transfer, live host lifecycle delivery or resource scheduling. R4b remains
paused and resource allocation is outside this change.
