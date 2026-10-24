# Published resource inventory

`etalon.research.resource_catalog.build_resource_catalog` discovers resources in
one explicitly published workspace, entry, or pinned read context. It also accepts
an explicit selection of installed skill bundles. The result is a read-only
inventory, not a selected workset, execution permission, or adoption decision.
There is no catalog database, ambient filesystem search, or compulsory workflow.

```python
from etalon.research.resource_catalog import build_resource_catalog

page = build_resource_catalog(
    entry="/published/task/entry.json",
    source_ids=None,                 # all sources in this published workspace
    kinds=None,                      # all eight resource kinds
    skill_refs=["research-index"],  # only these installed skills
    limit=20,
    scan_limit=50,
    max_bytes=65536,
)
while page["next_cursor"] is not None:
    # Retain this page's rows, source coverage, warnings and gaps first.
    page = build_resource_catalog(
        entry="/published/task/entry.json",
        skill_refs=["research-index"],
        cursor=page["next_cursor"],
        max_bytes=65536,
    )
```

The complete signature is:

```python
build_resource_catalog(
    workspace=None, *, entry=None, read_context=None,
    source_ids=None, kinds=None, skill_refs=(), purpose=None,
    limit=20, cursor=None, scan_limit=50, max_bytes=65536,
)
```

Supply exactly one of `workspace`, `entry`, and `read_context`. `source_ids=[]`
means no evidence sources. It does not fall back to all sources. The accepted
kinds are `skill`, `method`, `workflow`, `claim`, `evidence`, `experience`,
`verifier`, and `template`. Unknown/duplicate source and kind names are errors.

`purpose=None` inherits a pinned read context's purpose, otherwise defaults to
`research`. The supported values are `research` and `evaluation`; callers cannot
override a pinned context's purpose. Context policy is applied before discovery,
and source selectors cannot expand the restricted workspace. Evaluation-only
descriptors are removed before their summaries or parts enter research output.
Purpose labels do not grant access to unpublished records.

An unpinned skill slug selects its currently installed bytes. To name a fixed
version, pass `{ "slug": "research-index", "manifest_sha256": "..." }`, with
an optional `bundle_ref` containing an absolute `root` and optional manifest hash.
Existing workspace `skill_bundle_map` relocation is honored for pinned versions.
Missing, changed, or unmatched versions are errors; there is no current-version
fallback. No skills are selected by default. Skill snapshots use the existing
bounded bundle reader and retain every exact member path, hash, and byte size.

## Discovery and declarations

The catalog reuses `query_evidence`, `read_evidence`, `read_material`, and the
request-local verified read scope. Artifact rows require original-byte
verification. Typed content is read only through published schema declarations;
generic large/binary evidence is inventoried by its original record identity.
The code never scans a journal directly to bypass evidence visibility.

MethodVersion records, research workflows, claims, and method experiences are
classified by their explicit schemas. Other published records remain evidence.
Explicit resource descriptors, resource indexes, and task worksets contribute
their declared resource kinds and parts. Verifiers and templates therefore need
an explicit resource declaration; file names and prose are not type inference.
Workset artifacts must bind their complete content/support/counter dependencies.

Descriptors reuse `describe_resource`/`index_resources` validation, including
original content, all support references, and all counter references. A broken
descriptor becomes an explicit coverage gap; its unverifiable summary and
conditions are withheld. Original resource IDs, descriptor digests, source refs,
conditions, exclusions in declared parts, and support/counter pointers remain
unchanged. Multiple origins or versions are retained as separate catalog rows.
Older/superseded records are not silently dropped.

A bare method or skill has `descriptor=null` and `applicability="unknown"`.
Skill member identities are known, but their `conditions` remain null. The catalog
does not manufacture irrelevant conditions, dependency lists, execution stages,
or generic applicability to make an inventory candidate pass a workset review.
For a bare MethodVersion, `verification.method_content="not_checked"` explicitly
distinguishes the verified journal record from its opaque content reference.
A validated descriptor has `applicability="declared_only"`: compatibility and
scientific applicability still need the task profile and review/evidence.

## Return structure and pagination

The result schema is `etalon-resource-catalog/1`. Each `items` row contains:

- `catalog_id`, `kind`, `resource_id`, `content_ref`, and `origin_ref`;
- `summary`, `parts`, `descriptor`, and `declared_scope`;
- complete declared `support_refs`, `counter_refs`, and `supersedes`;
- `provenance`, `verification`, `availability`, `applicability`, and `missing`.

`origin_ref` names the original record that published the resource declaration;
`content_ref` identifies what the resource describes. They are not interchangeable.
An explicitly selected skill has no evidence `origin_ref` and carries its exact
snapshot manifest instead. Catalog IDs identify inventory rows, not replacement
scientific record identities.

`coverage.sources` preserves ordinary evidence source/dependency coverage.
`coverage.gaps` and `warnings` describe failures without converting them to empty
success. `coverage.discovery_complete` means traversal is exhausted, not that all
originals were available. `coverage.status` remains partial when prior pages had
gaps; `gap_observations_prior_pages` counts those observations. Keep every page to
retain the corresponding references. Gap observations may repeat when a large
descriptor container resumes across pages.

`limit` is 1..50 output rows; `scan_limit` is 1..200 evidence records inspected per
call. A filtered page may be empty and still have a `next_cursor`. Continue until
that cursor is null. Evidence discovery retains its existing source and
dependency limits; these limits are not a claim to have searched every source in
the host environment. The selected skill list is bounded at 64 bundles.

`max_bytes` is 4096..65536 and bounds `canonical(result).encode("utf-8")`, including
coverage and cursor. MCP callers should emit canonical JSON or budget their own
extra formatting. The reader stops before a row exceeds the remaining budget and
returns a continuation. If one complete row/manifest or the required coverage
metadata cannot fit, it raises `EvidenceError`; increase the budget or narrow the
source scope. Counter pointers and descriptor parts are never truncated to fit.

Cursors bind the workspace definition, source scope, kinds, purpose, selected
skill versions, evidence page, and partially emitted descriptor verification.
Changed source bytes, descriptor verification, or skill versions invalidate the
cursor. Each outer call rechecks the original bytes; there is no persistent
catalog cache. `limit`, `scan_limit`, and `max_bytes` can change between pages.

## Validation boundary

`tests/test_resource_catalog.py` covers the eight kinds, unknown applicability,
explicit skill members, purpose restriction, hidden counters, source isolation,
missing originals, stale cursors, bounded empty pages, byte pagination, and gaps
that survive onto the final page. These are engineering fixtures.

The local batch's frozen SND1/PoseBusters replay is recorded under
`integration/etalon-resource-branches-20261024/catalog/` outside this repository.
Its pages, original-file hashes, interpreter, and actual imported module paths
support read-only inventory acceptance. Reading prior results does not establish
independent transfer, scientific benefit, adoption, or newly observed consumption.
