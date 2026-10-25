# Bounded continuation card and manifest

`etalon_research_context(entry=..., view="summary")` keeps its existing output
when that output fits. An unscoped summary that exceeds the mandatory metadata
budget switches to `etalon-continuation-card/1` inside the existing
`etalon-research-context/1` response. `view="card"` selects that presentation
explicitly. The default budget remains 16 KiB; existing immutable checkpoints
and the default `view="full"` are unchanged.

The card immediately delivers the selected task/checkpoint, authority and review
state, observed pending request states, every current branch ID/state, short
reconsideration conditions, and each branch's latest listed authored step when
present. The latter is checkpoint list order, not an execution chronology or a
newly inferred scientific conclusion. Complete conditions remain in originals.
Each branch's latest step retains its exact `ref` and `original_read` arguments,
so a question about that branch can read its authored decision directly without
paging the whole reference inventory. The checkpoint tail retains its exact
reference too. Optional excerpts and inline watermark rows use a target 2 KiB
below the default 16 KiB ceiling; authoritative controls may use the unchanged
full ceiling. This gives growing checkpoints room before optional detail yields.
No branch, counter or pending state is removed. Reading the card does not
authorize work.

`reading_menu` offers optional recipes for status, one exact original, branch
details, an explicit `root_refs` scope, and counter originals. `question` records
the host's purpose; it neither ranks references nor selects a scope. A status-only
answer may stop at the card while disclosing unknown body coverage. A changed
decision requires the relevant original conditions and counter bodies; the menu
does not require expanding unrelated artifacts, every manifest section or a
large receipt bundle.

Repeated branch references, full counter directions, source watermarks,
publication details and reference inventories are available through bounded
pages on the same tool:

```python
from etalon.research.research_context import build_research_context

card = build_research_context("research-entry.json", view="card")
page = build_research_context(
    card["read_context"], view="manifest", section="counters", limit=20,
    max_bytes=16384,
)
while page["next_cursor"] is not None:
    page = build_research_context(
        card["read_context"], view="manifest", section="counters", limit=20,
        cursor=page["next_cursor"], max_bytes=16384,
    )
```

For MCP, select the section with `view_options={"section": "counters"}`.
Sections are `branches`, `references`, `counters`, `watermarks`, `publications`,
`issues`, `authority` and `workset`. Ordinary items contain `index` and `value`.
A single oversized row uses `index` and an exact base64 UTF-8 byte `fragment`;
concatenate that row's fragments in offset order and decode JSON after its
`next_offset` becomes null. Follow the page's `next_cursor` in either case.
No row, branch reference, explicit checkpoint counter or counter direction is
removed to fit a page. The top-level `next_cursor=null` means that section ended.

For an already known node, the same manifest view supports exact lookup:

```python
page = build_research_context(
    card["read_context"], view="manifest", section="references",
    node_id=card["branches"][0]["latest_step"]["node_id"],
)
row = page["items"][0]["value"]
```

An exact selection returns only that row; `selection.inventory_count` still
reports the full reference count. Unknown node IDs fail instead of searching or
expanding the workspace. `node_id` requires section `references`; it does not
select or erase a counter scope. The full inventory and original/counter checks
are unchanged, and cursors bind the exact selection as well as the section.
Reference rows include `record_type`, declared content schema/kind when present,
`original_read`, and a file/member `original_locator` when applicable. Locators
describe server-side provenance; use the enclosing `read_context` to read them.

The manifest digest binds the full section set, checkpoint and observed view;
cursors also bind the selected section and pinned `read_context`. Source,
workspace or publication changes invalidate the cursor rather than silently
mixing pages. Use exact reference-row `ref` values with
`etalon_evidence_read(read_context=..., ref=..., **row["original_read"])` and its
existing offsets to read originals. `view="record"` reads journal records such
as `method_evaluation`; its linked result artifacts remain separate originals.
`view="artifact"` reads a file artifact or an explicitly published snapshot
member. For a snapshot parent, read its record metadata first and select the
published member with its identity. Follow `fragment.next_offset` to finish
bytes; a single page or reference row never establishes full-body coverage.
Branch IDs also work with the
existing `view="branch"` reader. No computation needs to be repeated for paging.

A reference belonging to another workspace requires the explicitly published
entry for that workspace and its own returned `read_context`. For example, a
summary's `RSI.entry` can lead to an independent terminal record without adding
that source to the summary's workspace. The tool does not automatically follow
arbitrary JSON links, discover paths, or expand permission. An exact source
snapshot selected outside a checkpoint likewise needs a separately declared
source scope; it does not become a member of the original context.

The manifest reuses the existing source collector, continuation provenance walk,
policy restriction, byte identity resolver and request-local verified read
scope. It indexes the checkpoint's exact references, the checked continuation
closure and recognized explicit reverse counter links in the permitted sources.
It does not search globally or infer dependencies from opaque artifact bodies.
Graph truncation and counter-context gaps remain explicit in card receipts.
`reference_coverage_complete` describes this reference index, while
`excerpt_coverage_complete` describes delivered node excerpts.
`original_bodies_read_by_host="not_established"` is independent of both:
verifying a file's bytes does not establish that its complete body was delivered
to or understood by the host. Original conditions and scientific correctness
remain separate questions.

Only exact metadata byte-bound failures select the summary fallback. Corrupt,
missing, changed or forbidden originals still fail through the ordinary
validators; the card is not a recovery bypass. Publication recovery details and
science pending states remain distinct. The response writes no journal, starts
no action, grants no execution permission and adds no scheduler or second store.

The delivery cap does not bound all backend indexing or hashing work. Explicit
graph scopes retain their existing response and completeness contract. Extremely
large mandatory task text, branch IDs, pending identities or pinned policy can
still fail the requested cap explicitly, especially the 4 KiB minimum; this is
not an arbitrary-size metadata guarantee. The growth tests exercise valid
immutable checkpoints under the existing 16 KiB content limit and default
16 KiB delivery limit, including full lossless navigation at 4 KiB per page.
