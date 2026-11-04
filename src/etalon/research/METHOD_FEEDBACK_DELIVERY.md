# Conditional experience at the point of use

A saved method and its use feedback already live in the same research note.
Discovery now keeps that connection visible, and selecting a method delivers a
small page of experience together with the callable description. This changes
what the model can see; it does not choose a method, require reuse, or add an
approval step.

`build_resource_catalog(..., kinds=["method"])` still returns only methods.
When feedback exists, the method row's `related_feedback.next_read` selects it
from that row's immutable `content_ref`. A query is also carried into the
method's `resolve.arguments`. Feedback rows evaluate conditions on individual
cases, not the deliberately conditionless aggregate. Counts for a large
feedback resource explicitly cover its first page; they are not global counts.

`resolve_saved_method` adds `related_feedback` from the selected note and exact
method descriptor version. Each case includes its conditions and comparison,
the latest receipt for each outcome sign, measured costs, and a JSON pointer to
the full case. Positive evidence never replaces a negative or unknown outcome.
`same_version` means exact descriptor-digest equality; `other_version` means a
different version, without inferring chronological order or applicability.
Feedback is an observation, so it has no method execution-stage requirement.

The public read function is:

```python
read_resource_feedback(entry, resource_id, *, query=None, branch_id=None,
    resource_version=None, offset=0, limit=20, max_bytes=8192)
```

It supports 1–100 cases per page and 2048–65536 response bytes. Omitted
`resource_version` selects the method version in the same fixed note. The
result includes `content_ref`, `record_id`, `cases`, `case_count`,
`status_counts`, `version_counts`, `coverage`, and `next_read` arguments for
`etalon_resource_feedback`. No feedback returns `status="absent"` and an empty
page. Explicit read failures remain errors; no unverified experience is shown.

Pages use sorted case IDs. Within a page, the reader shows same-version cases
first and then matching, unknown and conflicting conditions. This is not a
global relevance search. `next_read` pins the original note, requested method
version, query and byte bound; advancing the live project cannot change the
remaining pages. When the response budget shrinks the page, the continuation
starts at the first undelivered physical case. Large receipts can be compacted
while preserving outcome signs, short lessons and measured costs. A case too
large even then is represented by its pointer and explicit `body_omitted` flag.

Selected-method enrichment uses only bytes left after the complete verified
method/request. If there is too little space, it returns an exact feedback link
or an omission marker. Optional feedback failure cannot invalidate a valid
method preflight. A missing or corrupt source still fails the existing method
resolver as before. Feedback delivery is read-only and performs no execution.

The direct `call_saved_method` path captures the same experience in the existing
invocation sidecar before submission. Its immediate result and later
`collect_saved_method` calls return that captured view. Replay does not follow
new note heads, backfill new experience, append feedback, or repeat execution.
Older invocation sidecars report `not_captured`; they stay readable without
being rewritten. If runtime output plus captured feedback would exceed 16 KiB,
the runtime output has priority and feedback becomes an exact snapshot link or
omission marker. This bound governs optional enrichment, not older runtime
response fields that already exceeded it.

For `/2` feedback storage, the paged reader verifies the descriptor artifact,
index, touched pages and selected case artifacts. It does not reconstruct the
full logical descriptor digest or read every case. Legacy inline or `/1`
storage still requires reading and verifying its complete descriptor artifact.
The index itself grows with case count; this is bounded case-body expansion,
not a claim of constant total I/O. The original complete descriptor reader and
all historical records remain available unchanged.

`tests/test_method_feedback_delivery.py` covers same-condition positive/negative
delivery, distinct method versions, note pinning across updates, conditional
conflict/unknown cases, read-only pagination, selected-case verification and
large-feedback preflight. These are software fixtures, not evidence of better
molecule selection or scientific transfer.
