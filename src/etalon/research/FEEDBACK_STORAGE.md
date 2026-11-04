# Feedback storage

The public feedback description still contains `cases`, `last_receipt`, outcome
counts and evidence references. Its `descriptor_digest` remains the digest of
that complete logical description. Resource selection and feedback arguments
do not change.

Feedback with at most 32 condition cases keeps the original `/1` inline
encoding: at these sizes, extra objects and journal metadata can cost more than
they save. This also covers repeated feedback for a single condition. Larger
collections automatically use `etalon-note-resource/2`. Its physical `description`
omits `cases`; `cases_ref` points to an ordinary immutable resource index.
That index contains small copy-on-write pages of case references. Each case is
an `etalon-note-feedback-case/1` Artifact. The index, pages and cases are linked
by Artifact parents; evidence originals keep their existing references. The
existing journal and handoff preserve both. Readers
reconstruct the complete description and check its original logical digest.
Resource index rows mark this encoding with `storage_schema`.

The new reader accepts historical inline resources and
`etalon-note-resource/1` descriptors. Old records are never rewritten. An old
reader requires an upgrade to read the new `/2` encoding; compatibility is
new-reader/old-data, not bidirectional. Full-description callers automatically
use the new storage without selecting a mode or running a migration.

An append writes the changed case, affected leaf pages, the page index and
small current descriptor, plus the usual note/catalog update. Unchanged case
objects and pages retain their identities. Page-index size still grows with
the number of pages, and complete logical hashing/reading still visits the
cases. More small Artifacts also increase journal metadata and file count.
This reduces copied case payloads; it does not promise constant total IO.

`usage_id` remains the idempotency key within a task/branch/memory scope. A
published ancestor makes replay a no-op. A journal record interrupted before
entry publication is resumed if its parent is current, or its single receipt
is merged onto later host progress under a derived publication ID. Other
feedback, negative/unknown outcomes, host prose and resources remain present.
