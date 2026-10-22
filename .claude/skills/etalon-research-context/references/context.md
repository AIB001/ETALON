# Context access

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
