# Evidence windows and controlled outcome publication

`etalon.research.evidence_window` adds ordinary `etalon-evidence-window/1` and
`etalon-outcome-release/1` JSON Artifacts to the existing ResearchJournal. Each
payload is limited to 16 KiB. It adds no store, scheduler, scientific promotion,
mandatory host route, or execution budget. R4b remains paused.

A window binds a task, branch, exact workset, method refs, visible refs, excluded
refs, explicit coverage and missing information. Its digest checks that this
declaration has not changed. Exact refs are read through existing EvidenceRefs
readers and bounded read scopes. The existing original-evidence reader verifies
and assembles bounded pages: at most 1 MiB per original and 8 MiB per explicit
closure. Structured evidence beyond those bounds is rejected; partial JSON is
not treated as a complete provenance closure. Artifact parents and JSON evidence
refs are followed. Selected
envelope members use their selected bytes, not the envelope's bytes, as identity.
Evaluation-only originals and their diagnostic aliases/derivatives cannot silently
become conditioning input. Local publication checks same-byte protected originals
in the journal, as well as the explicit bounded reference closure.

The timing modes have deliberately different meanings:

- `retrospective`: a later reconstruction; it establishes no prospective order.
- `host_declared`: an attributable visibility declaration without controlled order.
- `controlled`: requests the ordered API publication path below. The declaration
  alone does not prove that this path completed.

Hashes, timestamps, graph `as_of`, and API call order do **not** establish that a
model never saw an answer. Arbitrary host filesystem reads, other chats, external
tools and pretrained knowledge are not sandboxed. This API verifies local
publication order and exact attribution; it is not a scientific blinding system.

## Frozen outcomes

`outcome_slot(...)` supports two cases. `retained_artifact` freezes an existing
Artifact's complete metadata, exact byte identity and `independent_validation` role. The
original must not already have been registered in this journal when released;
knowledge outside this journal is unknown. Existing registered outcomes require
retrospective treatment. A retained original cannot change role, path or bindings
under the same frozen slot.

The existing reader keeps `sealed_holdout` originals sealed. This helper rejects
their direct release; it does not silently change a protected original's role or
bypass the existing source-visibility rules.

`future_execution` freezes an outcome slot, an existing planned research node,
protocol/input/method refs, producer identity and receipt contract. Future result
bytes do not need to exist. The node must bind the inputs/methods and belong to
the task/branch. The decision must select that node and bind the same evaluation
contract and methods. `expected_outcome_bindings(window, outcome_id)` supplies
the exact metadata expected in the new result Artifact and producer receipt.
The receipt must also identify `artifact_identity = {sha256, bytes}`. Matching
metadata verifies attribution, not native tool execution or scientific validity.
The caller must retain the producer's original verification and accounting.

## Ordered use

1. Build the window with `build_evidence_window(workspace, ...)` and persist it
   through existing `continuation.record_content(...)`.
2. Call `deliver_evidence_window(journal, window_ref, source_id=...)`. This
   returns the complete bounded visible materials and a snapshot-bound token,
   after recording `evidence_window_delivery_prepared` in the same journal.
3. Once that response has returned, the host explicitly calls
   `acknowledge_evidence_window(journal, window_ref, token=..., source_id=...)`.
   This is **host acknowledgement**, not model consumption telemetry. Both
   functions accept `external_workspace` for external originals.
4. Build and durably record the research decision. Its input refs must belong to
   the exact visible snapshot; task/branch/workset and controlled evaluation
   contract/method/action bindings must match.
5. Use the existing producer to perform the selected work and record its receipt.
   For a future slot, that receipt must have a committed journal event after the
   decision. Construct the result Artifact with its real bytes and evaluation-only
   role, leaving its registration to the next step.
6. Call `release_outcomes(journal, window_ref=..., decision_ref=...,
   outcomes=[{outcome_id, artifact, receipt_ref}], directory=..., source_id=...,
   source_kind=...)`. All frozen slots must be accounted for, including failures
   and unknown outcomes. This API does not run/retry the producer or charge a fee.

The helper derives actual event sequences from the existing committed
`research_recorded` events. It refuses missing/duplicate/conflicting chronology,
outcomes already present under any byte-identical alias, substituted refs,
premature receipts, mismatched contracts and incomplete slot coverage. It never
trusts a caller-supplied timestamp or sequence. Outcome registrations and the
release Artifact registration commit in one journal transaction using the
existing journal append API and immutable publication writer.

`window_delivery="host_acknowledged"` describes the earlier visible-input
window. `outcome_delivery="prepared_for_return"` describes the release response;
it does not claim that the recipient received the newly released outcomes.
`consumption="not_attested"` remains explicit. A crash after database commit but
before the response is received cannot certify delivery. The same exact request
and directory can resume without repeating registration; repeat delivery and
acknowledgement calls also preserve their original events. A changed delivery
token cannot create a new acknowledgement of the old window.

A failed atomic append rolls back both output and release registrations, retains
the immutable orphan file, and appends `evidence_outcome_release_failed`. Retry
checks the original references and bytes again. Prior failures are not deleted.
Ordinary `record_content` and publication link validation apply the same local
receipt/identity/order checks, preventing a constructed release JSON from
bypassing the helper's chronology. Publication preflight's detached reader can
check exact links; the real journal append performs the durable event checks.

## Validation boundary

`validate_content` checks detached shape and meaning;
`validate_local_links(journal, content, source_id, external_workspace=None)`
verifies originals and local durable chronology where applicable.
`validate_decision_window(...)` verifies the linked decision's input scope.
`read_evidence_window(workspace, ref)` verifies a source-backed window without
executing work or granting permission.

`tests/test_evidence_window.py` uses explicitly labelled local fixtures and actual
before/after API calls. It covers successful ordered release, failed outcomes,
idempotent delivery/release, premature receipt and acknowledgement, exact identity
substitution, evaluation-only aliases/parents, duplicate chronology, generic
publication bypass, interrupted atomic append/resume, retained originals and
complete paged originals, late-page evaluation references, and oversized JSON
rejection. These checks establish engineering behavior;
they do not establish independent scientific validation or model ignorance.
