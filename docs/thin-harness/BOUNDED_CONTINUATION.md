# Paged declared-reference continuation

`etalon_research_context` accepts the optional existing manifest view with
`view_options={"section":"closure"}`. Use the returned `read_context` and
`next_cursor` to continue. Each page verifies at most 50 reference envelopes;
the one-shot `resume_research` limit remains 1024 and its historical issues are
unchanged. No execution, retry, adoption, source write or journal write occurs.

The closure starts from the pinned checkpoint and its declared references. It
follows the existing structured relationships and transitive counter incidence
across every permitted published source, including declarations outside the
checkpoint task or displayed graph. A counter target refutes its declaring
record; reverse discovery never reverses that direction. Opaque original bodies
are not semantically interpreted. Source indexing retains its existing byte,
projection and record limits; the closure additionally rejects more than
100,000 indexed records, reference envelopes, ordinary relations or counter
relations instead of returning a complete-looking truncated result.

Metadata indexing, original byte verification and host body consumption are
separate. `reference_index_complete` describes the declared metadata closure;
`original_verification_indices` covers only reference rows in that response.
`navigation_exhausted` only means that there are no later rows. No individual
page claims all originals were verified or that the host read their bodies.

Keep the exact responses in order. `audit_closure_pages(workspace,
checkpoint_ref, handoff, pages)` checks the current source/index identity,
implementation identity, every complete row or UTF-8 fragment, contiguous
positions, original-verification indices and every cursor. Missing, reordered,
duplicated or changed pages fail. A partial transcript produces a standard
`etalon-continuation-closure-receipt/1` body with `next_read`; pass that exact
request to the same existing context tool after a chat/host restart. The host
may archive the transcript and receipt as ordinary Artifacts in its new task;
the library creates no cursor store or mandatory workflow.

Only an exhaustive, gap-free transcript with complete permitted counter search
can return `declared_reference_and_counter_closure_complete=true`. A narrowed
policy, hidden records, incomplete source or missing legacy dependency keeps
coverage insufficient. The receipt records verification evidence from those
pages, not a later atomic rehash of every opaque original. Source manifests,
watermarks, authority observation and implementation changes invalidate cursors.
Original mutation is rejected when its page is verified. The consistency model
remains per-source/per-page; consumers needing a current all-original snapshot
must independently check those byte identities before and after their audit.

A complete declared closure receipt can resolve the particular coverage gap
left by a one-shot 1024-record walk. It does not erase that old walk's truncation
record, reconcile authority/pending/successor issues, grant execution, certify
scientific dependency completeness, or establish body consumption. The existing
continuation card remains the control/status view; its optional reading menu
exposes the closure request without imposing it for every status question.
Request-local reuse also covers frozen explicit artifact manifests and resolved
local paths. Data snapshots with decoder-generated member paths and live journals
remain outside the source cache. Path entries are bounded by the existing cache
budget, re-resolved before the outer read returns, and never reused across calls.
Original hashes are still checked; neither matching timestamps nor cached path
results establish original identity. Source bytes, projections, authority and
skill versions retain their existing final verification.

When a continuation card follows graph assembly, it may reuse the same neutral
workset summary. A host-specific `conditions` selection cannot replace the
neutral workset in the pinned manifest. Visibility and counter coverage remain
part of the response, including incomplete legacy walks and unread bodies.
