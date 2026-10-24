# Branch resource and experience view

`etalon.research.branch_context.build_branch_context` reads an exact branch of a
published checkpoint. It reuses the graph, evidence reader, task workset and
resource usage verifier. It creates no database, activation, decision, execution
or retry. A read cannot resume a paused task.

```python
from etalon.research.branch_context import build_branch_context

view = build_branch_context(
    entry, branch_id="panel", conditions={"target": "PoseBusters"},
    max_bytes=32768, max_nodes=128, limit=32,
)
next_page = build_branch_context(
    view["read_context"], branch_id="panel", conditions={"target": "PoseBusters"},
    cursor=view["next_cursor"], max_bytes=32768, max_nodes=128, limit=32,
) if view["next_cursor"] else None
```

The SDK signature is:

```python
build_branch_context(entry, *, branch_id, conditions=None, purpose="research",
                     policy=None, max_bytes=32768, max_nodes=128, limit=32,
                     cursor=None, usage_refs=None, as_of=None)
```

`entry` accepts the existing entry path/object or a pinned read context. Branch
selection is exact and must exist in that checkpoint. Pinned policy cannot be
changed. `conditions` are explicit strings, never guessed from a branch name.
`as_of` has the graph reader's frozen-source semantics; it does not reconstruct
what an earlier host knew.

The result schema is `etalon-branch-context/1`. `branch`, `checkpoint_ref`,
`task_id`, `continuation`, `read_context`, `task_workset` and coverage receipts
remain in every page. `items` contains these row types:

| item_type | Meaning |
| --- | --- |
| node | Exact graph node, including method/use, workflow and execution-node references where recorded |
| decision | Exact decision with alternatives and reconsideration conditions; explicit checkpoint membership and supersession status |
| resource_usage | Existing `read_resource_usage` verification result plus exact branch anchors |
| resource_part | Reviewed task-workset part, version identity, disposition and selected status |
| experience | Full sourced experience with applicable/reference/excluded navigation category |

`relations` contains graph relationships and labels them as declarations. They
share the row limit and pagination cursor with `items`. The view follows the
branch's steps, basis, exact dependencies and counter relationships. A shared
method does not pull all other branches into the selected branch. Current-task
experiences may appear as task references without claiming branch-specific use.

`current_decision_refs` lists unsuperseded decisions explicitly listed in the
checkpoint for the selected branch, as observed in the bounded graph. It may
contain multiple decisions. The helper does not invent chronological order from
hashes, timestamps or success, and never treats an uncheckpointed step as the
current checkpoint decision. Graph truncation limits this inventory.

## Attribution and applicability

Only the existing resource usage validator can produce an
`exact_chain_verified_not_consumption_or_benefit` association. It checks exact
workset/part, MethodVersion, MethodUse, node, evaluation and subsequent selection
references. A usage belongs to this branch only when its node, evaluation or
selection step explicitly names the same task and branch. Every matching anchor
is shown; a selection in a different branch does not rewrite the node's branch.

Selected, read, consumed and executed remain separate. Host reports remain host
reports. Retrospective usage preserves all historical phases as `unknown`.
Successful nodes, graph edges and selected workset parts cannot establish
consumption or benefit. The current read verifies bytes now, not past reading.

The workset remains **task-wide**. Parts show their original reviewed selected
status, disposition and exact content/version identity. Branch conditions that
differ from the task profile set `review_required`; they do not mutate the
workset or fabricate a branch activation. Usage from an older workset keeps its
own exact reference and is not merged into a newer part with the same name.
Template mode and its fallback reason remain explicit, including excluded
resource parts.

Experience categories are navigation:

- `applicable`: declared conditions match; scientific applicability and textual
  `invalid_when` still require host review.
- `reference`: no new conditions were supplied.
- `excluded`: conditions mismatch or an explicit supersession is visible.

Negative outcomes, counterevidence, invalidity conditions and superseded
experiences are retained. A rollback experience can be relevant negative
guidance; its outcome is not silently converted into a positive adoption.

## Bounds and failure behavior

`max_bytes` is 4096..65536; `max_nodes` is 1..128; `limit` is 1..64. These bound
the display and graph traversal, not total collection I/O or scientific scope.
Oversized mandatory control metadata raises an error. A single oversized detail
becomes an explicit exact-reference omission receipt; no partial conditions or
decision are presented as a complete item.

Automatic discovery reads at most the first 50 schema-query matches in the
permitted workspace. The evidence query's byte budget may return fewer rows;
the actual returned count and next cursor are authoritative. Usage is not an existing graph content projection, so
filtering that query by `task_ids` would miss the task ID inside its bindings.
The helper instead verifies usage and checks the payload's exact task identity.
Other-task discoveries are excluded using verified original payload identity;
their association chains are not evaluated or claimed verified. Explicit
other-task `usage_refs` are rejected. Incidental schema mentions are counted
separately. Discovered invalid usage joins remain exact-reference source gaps
with `unverified_count`; they never become verified association rows. An invalid
explicit `usage_refs` request raises the original error.

`coverage.usage_discovery.next_cursor` is the **evidence query** cursor, separate
from the outer branch-view cursor. Follow its reported query/filters with
`query_evidence`, then provide the returned exact refs through `usage_refs`
(at most 50) to supplement the view. This does not pretend that the first page
became complete: an outstanding automatic-discovery tail still reports partial
coverage. Narrow the published scope for complete large inventories.

Outer cursors bind the exact branch, conditions, candidate identities and source
watermarks. Changed scope or source state is rejected. `coverage.complete`
describes source/discovery/graph coverage, while `truncated` also records omitted
or paginated delivery. Missing/changed evidence, forbidden scope, invalid usage
joins and conflicting worksets produce errors or explicit existing source gaps,
never a trustworthy empty result.

## Validation

`tests/test_branch_context.py` exercises exact selectors, pause and counter
preservation, unknown historical phases, branch isolation, task-wide worksets,
condition conflicts, template fallback, exact usage joins, stale cursors,
tampering, cross-task rejection and real discovery pagination over 51 synthetic
usage Artifacts. Fixtures establish engineering behavior only.

Real archival acceptance is recorded under
`integration/etalon-resource-branches-20261024/branches/attempts/`. It reads the
preserved SND1/PoseBusters entries and resource usage archive through existing
SDK readers, snapshots original dependencies before/after, and records actual
interpreter/module paths and hashes. It creates no molecular computation or
scientific evaluation. In particular, the old `snd1-c26-pose-preparation` usage
must not become usage of the later torsion task merely because both are SND1.

The 2026-10-24 real replay's SND1 and PoseBusters graphs reach the 128-node
bound, so their graph/counter coverage is explicitly incomplete. SND1 shows no
usage attributed from the older preparation task. PoseBusters shows the exact
validated retrospective chain ending in `stop`, with all three historical
phases still unknown and checkpoint activation unknown. Its current task
workset remains in template mode with the archived fallback reason. One earlier
invalid PoseBusters usage declaration remains an exact-reference source gap;
the valid later declaration does not erase that failure. Outer pagination
recovers all assembled rows, but does not repair bounded graph coverage.
The successful archival attempt is `branches/attempts/004/report.json`: eight
branch views, 5,286 original dependency files unchanged, no new scientific
execution or adoption. Attempts 001–003 retain manifest-relocation and invalid
historical-declaration failures. The final focused suite is
`branches/tests-final-v2.xml` (33 passing tests).
