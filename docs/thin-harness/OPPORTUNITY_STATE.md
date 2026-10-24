# Optional opportunity dispositions and reopening

An exact `etalon-revision-opportunity/1` can end with a bounded `no_change` or
`retain` judgment even when there is no candidate, patch or MethodEvaluation.
`adopt` and `rollback` require an already recorded evaluation and scoped
resolution. This layer records that authority; it does not create one.
`waiting` and `awaiting_evidence` remain nonterminal.

The optional JSON schemas `etalon-opportunity-disposition/1` and
`etalon-opportunity-reopen/1` are ordinary append-only Artifacts in the existing
journal. They add no journal record type, database, fixed Probe, proposer,
scheduler, execution or default-method change. Existing context/opportunity
identities and the first episode's `revision-<opportunity_key>` stay unchanged.

## API and publication

`etalon.research.opportunity_state` exports `SCHEMAS`, `validate_content`,
`validate_local_links`, `read_opportunity_state`, `build_opportunity_disposition`,
`build_opportunity_reopen`, and `resolve_opportunity_state`.

Build a disposition with an exact `opportunity_ref`, `outcome`, `reviewer`,
`reason`, supporting and negative refs, and explicit reconsideration conditions:

```python
condition = {
    "condition_id": "new-receipt",
    "condition": "A newly released receipt contradicts the bounded diagnosis",
}
payload = build_opportunity_disposition(
    journal, opportunity_ref=opportunity_ref, outcome="no_change",
    reviewer="host-review", reason="Available evidence does not justify a change",
    support_refs=support_refs, counter_refs=counter_refs,
    reconsider_when=[condition], source_id=source_id,
)
record_id = record_content(
    journal, payload, directory, source_id=source_id, source_kind="diagnostic",
)
terminal_ref = journal_ref(journal, record_id, source_id=source_id)
```

`previous_state_ref` is `None` for the first disposition. Supply the exact head
when closing a waiting or reopened episode. Candidate/evaluation/resolution refs
are optional for bounded no-change/retention. Evaluated judgments require both
`evaluation_ref` and `resolution_ref`; evaluation must compare the exact candidate
and base, and resolution must select the correct method through an existing
MethodUse in the opportunity's scope. A legacy `retain` resolution with no output
MethodUse can be referenced without inventing a use: the evaluation must have the
exact opportunity scope and the resolution the exact task. This exception does
not authorize adoption or rollback. Adoption additionally needs measured result
and accounting refs. Rollback retains counterevidence and prior-use lineage.

To reconsider, build `build_opportunity_reopen` with the exact
`previous_terminal_ref`, `new_evidence_refs`, a `matched_condition` copied from
that terminal, and the reviewer's specific `reason`. The new episode preserves
the original opportunity and gets a derived request ID. Nothing invokes a model
or runs a method. Rebuilding with the same predecessor and arguments returns the
same payload; publishing to the same content directory is idempotent.

The builders retain prior supporting/negative refs automatically. Direct
`record_content` publication also validates them, so a handwritten payload cannot
silently omit prior evidence or bypass novelty checks. `supersedes` stays empty;
exact predecessor refs describe the chain without replacing historical facts.

## Original identity and reviewed relevance

Reopening requires verified byte identities absent from the prior opportunity's
context and full retained history. A renamed Artifact, changed locator/bindings,
or new record ID around identical bytes is not new information. JSON envelopes
containing exact evidence refs inherit their original leaves' identities;
rewriting their title cannot make the originals novel. Malformed `_ref`/`_refs`
envelope fields are rejected. Method metadata records alone are not new original
evidence. Every leaf offered as new must be unseen; use `original_evidence_refs`
for prior evidence, not `new_evidence_refs`.

The mechanical check establishes different original bytes, not scientific
relevance, independence, causality or benefit. The host must explain why those
originals satisfy the named reconsideration condition. Unrelated changed metadata
is not a valid scientific reason. The review is an attributable host judgment,
not a claim that software can infer relevance from hashes or textual similarity.

Origins use a bounded closure of at most 256 refs, with at most 1 MiB per original
and 8 MiB of original bytes per closure. External originals use existing artifact
pages of at most 32 KiB; every page must retain the exact identity, offset, total
and continuation, and assembled bytes must match the original SHA256 and length.
Local originals are verified before and after the bounded read. JSON is fully
parsed before following exact refs, including support/counterrefs beyond the
first page. Malformed or truncated JSON-looking envelopes fail closed; they are
never treated as novel binary leaves. Non-JSON binary originals retain their
verified byte identities within the same size bounds. Oversize originals require
narrower explicit originals. Typed state content stays limited to 16 KiB; no
global evidence-view limit changes. This is a conservative evidence interface,
not general semantic deduplication of arbitrary encodings.

## Recovery and boundaries

`resolve_opportunity_state(journal, opportunity_ref, source_id=...)` returns:

- `state`: `open`, `terminal`, `reopened`, `waiting`, or `awaiting_evidence`;
  `terminal` and `outcome` distinguish completion from waiting.
- `head_ref`, latest `terminal_ref`, `episode_ref`, and `request_id`.
- `history_refs`, all `terminal_refs`, `retained_support_refs`,
  `retained_counter_refs`, `seen_evidence_refs`, and `reconsider_when`.
- `execution="not_performed"` and coverage limited to the explicit local journal.

History reads inspect at most 256 published state Artifacts by default (explicit
`max_history` accepts 1–1024). The bound covers all such Artifacts in the journal,
because membership is verified from original content. Overflow, missing/corrupt
originals, forged predecessors, disconnected chains and concurrent sibling heads
raise; they never become an apparently open opportunity. There is no implicit
last-writer-wins conflict resolution.

The pure publication preflight reader can validate the explicitly cited
predecessor chain without a database interface. It cannot discover sibling heads;
the journal resolver must be used for current-state recovery. Read helpers use a
bounded request-local verified read scope; it ends before publication writes.

An old opportunity/context and its evidence may live in an immutable external
journal. Pass an explicit `external_workspace` to build, publish and resolve its
new local disposition chain. `source_id` identifies the local diagnostic journal,
not the external opportunity's source. The old journal and negative decisions
stay unchanged. There is no cross-journal global head inference.

Tests are engineering fixtures for termination, repeat publication, conditional
reopen, exact resolution authority, old-byte rejection, counterevidence retention,
external immutable provenance and fail-closed recovery. They establish no new
scientific result, independent replication or RSI benefit. R4b remains paused.
