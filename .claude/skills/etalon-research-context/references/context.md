# Context access

## Decision-directed reading

The optional thin SDK view selects declared structure and produces a paged
reading plan. The host supplies the question and exact branch/root selection;
natural language does not classify relevance.

```python
from etalon.research.decision_reading import build_decision_reading
page = build_decision_reading(
    entry, question="Does the existing branch remain justified?",
    scope={"branch_id": "existing-branch-id", "ordinary_hops": 0},
    reading_mode="decision", conditions={"chemical_state": None},
    max_bytes=16384,
)
```

The context/MCP adapter exposes the same read as `view="reading"`, with
`view_options={"reading_mode": "decision"}`. `scope`, `question`, `conditions`,
`policy`, `purpose`, `cursor` and `limit` keep their existing meanings. The SDK
accepts `relevance=[{"ref": exact_ref, "why": "host rationale",
"affects_decision": "named choice"}]` and `decision_changed=True/False/None`;
the adapter accepts them inside `view_options`. These are declarations, not
proof of relevance or changed scientific progress. Reading rejects a purpose
or policy that differs from the pin. Pass `purpose="evaluation"` explicitly when
reusing an evaluation context.

Without scope, only the published task's tail step is a starting root, with zero
ordinary hops. Its explicit dependency boundary remains readable. Status can
stop earlier; decision and diagnosis retain stronger original-reading obligations.
To expand, use an item's exact `expand.scope` or select a known root/branch with
`ordinary_hops=1`. An explicitly requested oversized graph fails; it is never
silently truncated, and the reading transport cap remains 16 KiB.

Rows are grouped by `section`: current decisions, controls, support,
counterevidence, experience, optional detail, gaps and source scope. The header
always gives controls, total counter counts and counter coverage. Pagination is
not a top-k relevance filter: undelivered rows remain behind `next_cursor`.
Use the same options and entry/read_context on each call. A row may be fragmented;
join base64 bytes by item index and fragment offset before decoding its JSON.
Changing source identities, scope, question, conditions or mode invalidates the
cursor. A stale read_context requires resolving its explicit entry afresh.

Use each item's `ref` and typed `original_read` with `etalon_evidence_read`, in
the returned read_context. Follow original `fragment.next_offset` to null before
claiming full original delivery. A fully delivered plan still says host reading
and comprehension are not established. `optional_detail` means metadata identity
was checked at the ordinary-hop boundary; reading its original performs the
existing original verification. Do not describe it as already read or verified.

Counter incidence covers all permitted published source records, including
declared cross-branch relations. Hidden records, source restrictions, unavailable
sources and deliberate dependency boundaries remain explicit gaps. No semantic
analysis of opaque bodies or global completeness is implied. Same-task or
explicit shared-capability experience is retained even when conditions conflict;
`invalid_when` remains a host review and a matching condition does not imply
activation. Empty conditions or omitted host dimensions leave applicability
unknown. Discovered experience is conditional; only exact decision basis and
its challenging counter relations create original-reading obligations. Current
controls are required metadata, not a demand to reread every checkpoint body.
A published locator into another workspace requires its own context;
the reader does not discover or authorize that workspace.

## Existing views

The SDK accepts an entry JSON path or a dictionary with `workspace` and
`checkpoint_ref`:

```python
from etalon.research.research_context import build_research_context
context = build_research_context(entry, question="Which branch is justified?",
                                 purpose="research", view="summary", max_bytes=16384)
```

For a local neighborhood, pass `view="node"` and an exact `node_id` from
`etalon_research_graph` or `graph_memory.build_research_graph`. Home is an
organizational label, never a visibility boundary. Cross-home evidence is shared
by identity, not copied or counted as independent replication.

Summary is a navigation view, not a substitute for evidence verification. Use
its follow-up references to expand the relevant node, then
`etalon_evidence_read(view="artifact", limit=32768)` for original bytes (continue
with offsets if needed). `view="full"` is the backward-compatible default.
The supported artifact selector is `"bytes"`; do not invent a JSON-path selector.
Track the artifact's total bytes, each returned offset and `fragment.next_offset`.
If a source is too large for one read, obtain the enclosing method/branch label
before assigning metrics in a later fragment. Integrity verification and complete
semantic reading are separate claims. If the native host additionally shortens
the tool result, its persisted-output pointer must be expanded or the affected
details must remain unverified.
Question text records the intended decision; it does not perform semantic
ranking. Preserve selection exclusions and the reported counterevidence scope.

Explicit policies narrow the published scope before summaries are assembled.
They do not release sealed data or revoke information already seen by the host.
Use only the supported policy fields in the installed SDK documentation; unknown
fields must fail instead of silently weakening restrictions. Historical evaluation
needs the actual historical visible snapshot; current evidence is not an as-of replay.

Inspect matching and conflicting experience through
`etalon.research.improvement.retrieve_experience` with explicit source IDs,
capability and conditions. Retrieval does not choose or promote a method. If
conditions changed, revisit applicability rather than silently inheriting advice.

Existing pending handles are inspected, not resubmitted. Keep pause guards,
unresolved reads and missing results in the answer. An existing resolved decision
does not trigger a second improvement attempt.

`publications` reports archival work discovered through the entry's explicit
sidecar. Pending publication and pending scientific jobs are different states.
If inspection is unavailable, report uncertainty rather than claiming nothing
is pending. Resume only the named publication when that write is authorized;
reading context itself performs no repair.

Hook delivery is either a self-contained baseline, a delta naming its baseline,
or an unchanged notice. A delta uses top-level replacements/removals; it is not
an independent full context. If the named baseline is absent after compaction
or a host restart, read a fresh summary through the entry. A hook receipt proves
output preparation, not model consumption or scientific admission.

Some hosts deliver long additionalContext as a verified file plus a preview.
This is a transport difference, not evidence that the full baseline was inlined.
Use the existing entry read or the host's file-reading capability for recovery;
do not treat a truncated JSON preview as a usable patch base. Keep source checks,
counterevidence coverage and current task/workset guards from the complete read.
