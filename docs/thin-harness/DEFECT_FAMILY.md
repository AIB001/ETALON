# Reviewed defect families

`etalon.research.defect_family` provides optional ordinary
`etalon-defect-family/1` Artifacts. A host can relate explicitly reviewed problems
without changing the existing revision-context/opportunity `/1` identity or
request ID. The module does not discover clusters, propose a candidate, schedule
a Probe, adopt a method, or grant scientific execution permission.

The host first calls `review_defect_member(workspace, context_ref=...,
opportunity_ref=..., relation=..., grouping_reason=..., review_refs=[...],
counter_refs=[...])`. The exact context and opportunity, their original bytes,
problem and evidence refs, target, review evidence, and counterevidence are
verified. `relation` is an explicit host judgment:

| Relation | Meaning |
| --- | --- |
| `same_issue` | Accepted for the declared family, subject to exact task, scope, conditions and optional target checks. |
| `reference_only` | Retained as potentially relevant material; it does not accept the member or close its task. |
| `rejected` | A negative grouping review, retained with its reason and counterevidence. |

Unknown conditions are not wildcards. Empty conditions are rejected. Explicit
unknown markers (`unknown`, `unspecified`, `not_checked`, `not checked`, `*`, `?`)
cannot establish a same-issue match. The host still owns the meaning and adequacy
of its declared conditions; arbitrary prose is not interpreted as a proof.

```python
from etalon.research.defect_family import review_defect_member, build_defect_family
from etalon.research.continuation import record_content

member = review_defect_member(
    workspace, context_ref=context_ref, opportunity_ref=opportunity_ref,
    relation="same_issue", grouping_reason="Host-reviewed mechanism and scope agree",
    review_refs=[review_ref], counter_refs=[negative_ref],
)
payload = build_defect_family(
    workspace, task_id="reader-task", scope="released-json-reader",
    conditions={"protocol": "released-json/1", "operation": "read"},
    capability_id="etalon.evidence", target_ref=base_method_ref,
    mechanism_hypothesis="A typed reader was used for a generic JSON Artifact",
    members=[member],
)
record_id = record_content(
    journal, payload, directory, source_id="research", source_kind="existing_real_artifact",
    external_workspace=workspace,
)
```

`target_ref=None` declares a capability-level family; `capability_id` is always
explicit. Every member keeps its original target and method refs. Supplying a
family target additionally requires each accepted member's exact target ref to
match. Builders return detached JSON-compatible values; publication uses the
existing immutable Artifact path and append-only journal.

Three identities remain distinct. The existing opportunity ref/key identifies
the exact event. `family_key` identifies the reviewed family and its applicability
boundary. A candidate's content relative to its exact base is a separate concern
and is not derived from a family key.

For evidence identity, an Artifact's verified SHA-256 and byte count are used,
not its filename, source alias, label or record ID. Non-Artifact records use their
canonical record content. A selected member uses its own verified bytes, so
distinct members of one published envelope do not collapse into one issue.
An explicit context's `evidence_refs` anchor the issue
when present; otherwise `problem_refs` do. Thus renamed problem descriptions
backed by the same original evidence do not count as a second issue. Every exact
alias and problem ref remains available in the member rows. Byte identity does
not prove independence or semantic equivalence: only explicit `same_issue`
reviews accept members.

`accepted_issue_keys` deduplicates accepted evidence identities. Exact duplicate
review rows are removed. The family key excludes the wording of the mechanism
hypothesis and review reason, and is anchored to one accepted issue plus the
declared boundary. To add genuinely different reviewed evidence to an existing
family, pass `prior_family_ref` to `build_defect_family`. The builder retains the
original key and every earlier review, links the prior Artifact in `supersedes`,
and rejects boundary changes or loss of previous negative evidence. There is no
implicit search for an older family; the host must supply its known prior ref.

Conflicting acceptance/rejection rows are deliberately retained. There is no
latest-wins rule. `accepted_issue_keys` alone must not be used as a terminal
decision or authorization to suppress proposals. A caller must inspect the
exact accepted context/opportunity, conflicts and any separately reviewed
terminal decision. A cross-domain `reference_only` row can never close another
task, even if it shares bytes with an accepted member.

`validate_content` validates the declaration's structure and identity arithmetic.
`record_content` calls `validate_local_links` to recheck original bytes and member
bindings; external refs require `external_workspace`. `read_defect_family`
rechecks the same evidence through existing verified readers and their bounded
read scope. Hand-built rows cannot certify fabricated fingerprints or altered
context fields by passing structural validation alone. Each Artifact remains
subject to the existing 16 KiB publication limit; split broad reviews into
explicitly linked bounded declarations instead of introducing a second store.

`tests/test_defect_family.py` uses explicitly synthetic fixtures to check
idempotent publication, renamed original evidence, exact scope and target
boundaries, cross-domain reference-only reuse, changed source bytes, negative
retention and extension of a family. These are software checks. They establish
neither measured method improvement nor new independent scientific evidence.
