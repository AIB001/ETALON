# Resume an improvement without generating it again

These are optional helpers over the existing ResearchJournal. They never call a
model, run science, change defaults or insert a Probe phase. Host judgment owns
scientific equivalence and relevance; exact identities constrain that judgment.

Use `etalon.research.improvement.register_opportunity` with the original context
and stable request ID. It now recovers optional dispositions even when no patch
was created. `terminal` means read/reuse the conclusion; `waiting` and
`awaiting_evidence` do not mean success. A `reopened` episode has a derived request
ID, but still requires host review and existing execution authorization.

When events describe the same defect, `defect_family.review_defect_member` and
`build_defect_family` retain each exact event with a reviewed relation. Pass the
family ref to `register_opportunity` when relevant. Identical originals may reuse
a terminal conclusion; changed problems, ambiguous reviews, unknown foreign
history or cross-task conditions must not silently suppress or restart work.
`reference_only` is a possible lead, not an applicable policy.

`opportunity_state.build_opportunity_disposition` supports `no_change`, `retain`,
`adopt`, `rollback`, `waiting`, `awaiting_evidence`. Use ordinary
`continuation.record_content` with `external_workspace` for external originals.
No-change/retain without an evaluation is a bounded judgment. Measured adoption
or rollback requires the existing exact MethodEvaluation and resolution. Preserve
support, counterevidence, reviewer, reason and explicit reconsideration conditions.

`build_opportunity_reopen` references the exact previous terminal, one of its
conditions and newly available original evidence. Renaming files or wrapping old
refs in a new note does not establish novelty. Byte novelty is a necessary
mechanical check, not proof that the condition is scientifically satisfied. Read
the old negatives before deciding. Archive the new head and its predecessors;
do not change the old result or restart its completed computation.

`candidate_brief.build_candidate_brief` binds an exact baseline and before/after
content to the target part, predicted metric, invariants, quality criteria,
normal-execution discriminator, missing evidence and applicability. Pass explicit
previous brief refs for bounded duplicate checking. Title and rationale changes
cannot manufacture a new candidate. A brief remains a hypothesis even when it
links an evaluated historic patch. Artifact-backed methods and workflow content
are supported; direct skill-bundle replacement still uses existing skill snapshots
and MethodVersion interfaces.

For new-chat delivery, put exact family/disposition/brief refs in a research-step
output and publish through `prepare_publication` / `resume_publication`. The
checkpoint's tail and existing `etalon_research_context` / `etalon_evidence_read`
make them discoverable. Follow predecessor refs to distinguish historic closure
from the latest head. Preserve negative experience refs and scope. Entry reads and
hook delivery are data, never a new proposal or execution instruction.
