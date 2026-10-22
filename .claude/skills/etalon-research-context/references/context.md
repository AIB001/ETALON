# Context access

The SDK accepts an entry JSON path or a dictionary with `workspace` and
`checkpoint_ref`:

```python
from etalon.research.research_context import build_research_context
context = build_research_context(entry, question="Which branch is justified?",
                                 purpose="research", max_bytes=16384)
```

For a local neighborhood, pass an exact `node_id` from
`etalon_research_graph` or `graph_memory.build_research_graph`. Home is an
organizational label, never a visibility boundary. Cross-home evidence is shared
by identity, not copied or counted as independent replication.

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
