# Optional scoped graph reads

An explicit scope selects a research neighborhood within the already authorized
published workspace. It never grants access, changes task authority or proves
that all scientific counterevidence is known. The existing graph/context tools
remain the entrypoints; calls without `scope` keep their previous behavior.

```python
from etalon.research.research_context import build_research_context

context = build_research_context(
    entry, view="summary", max_bytes=65536,
    scope={"root_refs": [decision_ref], "ordinary_hops": 1},
)
# Continue against exactly the same published identity and requested scope.
same = build_research_context(
    context["read_context"], view="summary", max_bytes=65536,
    scope=context["scope"],
)
```

Alternatively use `scope={"branch_id": "exact-branch-id", "ordinary_hops": 1}`.
Roots then come from the checkpoint branch's exact references and published
records for that task/branch. `root_refs` and `branch_id` are mutually exclusive.
`ordinary_hops` is 0 or 1; omitted means 1. Expanding a boundary is a subsequent
explicit read using its exact references, not an automatic increase in the limit.

Both `etalon_research_context` (summary/node/full) and `etalon_research_graph`
accept the same optional `scope`. Resource catalogs and branch resource-usage views
retain their own selectors and reject this graph option. No new MCP tool or
permanent index is introduced.

## What the scope contains

- Exact roots and their declared ordinary reference neighborhood.
- Checkpoint and latest progress controls, with all branch/pause/pending state.
- Both endpoints and the original direction of all applicable declared counter
  relations, including reverse relations from other tasks. Counter incidence is
  checked across the permitted published record collection and followed to closure.
- Explicit boundary references for ordinary dependencies beyond the selected
  neighborhood. Counter-added nodes do not silently expand their ordinary
  dependencies; their counter relations continue to be checked.

Scientific evidence is not filtered by similarity or by whether it agrees with
the current conclusion. Identity errors, unavailable originals, hidden required
dependencies and insufficient limits do not become evidence absence. If mandatory
counter references and controls cannot fit, the call refuses instead of trimming
them. Existing graph limits (1–128 nodes, 4–256 KiB) and context limits (4–64 KiB)
are unchanged. Context still uses its existing internal graph byte budget.

## Read the receipts separately

| Returned field | What it means |
| --- | --- |
| `scope` | Exact requested neighborhood, reusable with pinned `read_context` |
| `scope_receipt.reference_coverage_complete` | Required references in that declared scope passed the scoped checks |
| `scope_receipt.counter_reference_coverage_complete` | Declared counter incidence and required endpoint references passed within the recorded search universe |
| `scope_receipt.deliberate_boundary_node_ids` | Ordinary dependencies outside this neighborhood; resolve exact refs in `reference_index`, then read originals or select them as new roots |
| `scope_receipt.counter_relation_source` | Points to the mandatory directional `refutes` relations and their basis IDs |
| `scope_receipt.global_reference_coverage_complete` | Always false for an explicit local scope |
| `excerpt_coverage` | How much of the selected node/counter material was shown as excerpts |

Reference verification, excerpt delivery and host consumption are different facts.
A summary still has at most four node excerpts; exact counter pointers remain
available beyond that cap. A complete reference receipt does not mean full source
bodies were shown or read by the model. The legacy unqualified coverage flags stay
conservative under explicit scope. Missing sources or visibility restrictions
remain visible in the receipts; narrowing authorization must not turn an unknown
counter into an assertion that none exists.

Context uses one `reference_index` for full immutable references. Its
`etalon-research-context-scope/1` receipt uses node IDs for roots, controls,
boundaries and counter endpoints; directional relations contain `from`, `to`
and `basis_node_id`. Ordinary boundary edges are grouped by source and relation
with `to_node_ids`, retaining their original count. This is lossless reference deduplication, not omission of
evidence. The direct graph response uses `etalon-research-graph-scope/1` and
retains its corresponding full-reference lists. Always interpret the recorded
schema. Boundary references receive metadata identity checks; original-byte
verification covers the included nodes. Expand a boundary through the existing
original reader before relying on its content. Counter search inspects declared
structured relationships, not the scientific meaning of arbitrary raw text.
Source-level record allowlists and a narrowed policy are recorded as coverage
limits. A malformed counter target cannot disappear from the search merely
because its digest does not match the root's identity.

## Continuation and hooks

Read the entry's latest progress and branch state, then choose an explicit scope
when the broad view is too large. Follow exact boundary/counter refs using existing
`etalon_evidence_read` pagination and its original-byte checks. An incomplete
summary is not a request to repeat a completed computation or RSI proposal.

The existing hook still delivers the ordinary continuation summary. This feature
does not install new hooks, change trust settings or force a particular scope on
every task. A fresh host can request the recorded scope through the ordinary MCP
context tool. Re-reading pinned sources does not write journals, resume jobs or
activate methods.
