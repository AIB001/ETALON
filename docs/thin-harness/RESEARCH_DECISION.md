# Optional research decisions

`etalon.research.research_decision` adds two ordinary JSON Artifact contracts to
the existing `ResearchJournal`. Use them when a real research choice needs an
auditable connection from its question and alternatives to observable predictions,
normal execution, evaluation, and a later judgment. They create no journal,
scheduler, mandatory Probe stage, scientific promotion, or resource allocation.
The host may skip this annotation entirely.

The contracts work for causal explanations, combined or unknown causes,
engineering options, descriptive curation, and decisions to retain or defer.
They do not require two mechanistic hypotheses. An empty prediction list is
allowed and remains `pending` in an update; it cannot establish success.

## Decision shape

`etalon-research-decision/1` records:

- `task_id`, `scope`, `branch_id`, `question`, and exact `problem_refs`;
- `structure`: free-text `task_family`, `objective_kind`, `subject`,
  `intervention`, `readout`, plus `constraints` and `unknowns`; an unknown
  dimension is null and its name appears in `unknowns`;
- `attribution`: `actor`, `basis="host_judgment"`, exact `model_ref` or null,
  and exact `skill_refs` / `verifier_refs`; unknown versions remain absent;
- exact `method_refs`, `scientific_inputs`, and `evaluation_refs`;
- options `{option_id, kind, statement, claim_refs}`, where `kind` is
  `hypothesis`, `option`, `combined`, or `unknown`; existing research claims
  retain their original epistemic status, conditions, limitations and sources;
- predictions `{prediction_id, action_id, option_ids, observable, expected,
  distinguishes, status, basis_refs, unknown_reason}`; `status` is `testable`
  or `unknown`, and the latter requires an explicit reason;
- actions `{action_id, description, disposition, reason, execution_ref,
  prediction_ids}`, with `selected`, `skipped`, or `deferred` disposition;
- support, counterevidence, unresolved gaps, optional exact `workset_ref` /
  `evidence_window_ref`, and explicit supersession links.

An action's `execution_ref` names an ordinary research node. A planned node
can later be superseded by an executed node. Selected methods and chosen actions
do not claim consumption, execution, or benefit. The builder derives `input_refs`
from the declared problem, support/counterevidence, option claims, prediction
bases, and non-evaluation scientific inputs; it checks normal-node inputs against
that exact set. Evaluation refs name existing evaluation plans or declarative
evaluation contracts. Later results need not exist when the decision is written.

Scientific inputs use `{ref, use, reason}`, where `use` is `conditioning`,
`context`, or `evaluation_only`. Exposed evaluation-only originals, declared
ancestors, and mixed scientific-input envelopes cannot enter decision inputs.
Local publication also checks protected byte aliases in the same journal.
Publish an explicit clean view when an envelope includes held-out values.
Historical scientific-input source paths are resolved through the existing
explicit workspace location map with exact byte identities. Only a validation
copy receives local paths; the persisted envelope and its digest remain unchanged.
Absent or conflicting relocation mappings fail closed.
These checks cover declared/published evidence; they cannot sandbox arbitrary
host file reads, undeclared inputs, pretrained knowledge, or hidden semantic
leakage. Missing declarations are not evidence of blindness.

## Later updates

`etalon-research-decision-update/1` binds an immutable `decision_ref`, optional
`previous_update_ref`, exact `execution_refs`, `evaluation_refs`, and
`experience_refs`. It copies the original task, scope, branch, and methods.
Every original prediction receives one judgment:

```python
{
    "prediction_id": "preservation",
    "status": "contradicted",  # supported, unresolved, not_tested also allowed
    "observation_refs": [exact_output_ref],
    "evaluation_refs": [exact_evaluation_ref],
    "reason": "Attributable host interpretation of this observation",
}
```

Supported or contradicted judgments require a testable prediction for a selected
action, an exact executed node (or its direct supersession of the planned node),
observations among that node's outputs, and the same observations in the named
evaluation. A normal evaluation step must reference the decision's exact plan
and attribute the executed node. A declarative evaluation report is recomputed
through the existing `evaluate_contract`; its rules digest must match a contract
named by the decision. New observation references may change while rules remain
fixed. Report `insufficient`/`incomparable` cannot count as supported; replay
does not itself certify scientific applicability or predictive timing.

`outcome` is derived as `pending`, `unresolved`, `supported`, `contradicted`, or
`mixed`. Remaining gaps force `unresolved` once observations are judged.
`disposition` (`retain`, `revise`, `reject`, `defer`, `no_change`) is separate
from outcome. A negative result may correctly leave the method unchanged.
Neither field adopts a method. All records retain
`scientific_truth="not_certified"` and `adoption="not_performed"`.

Previous counterevidence and newly contradicted observations cannot disappear.
Previous gaps remain until an explicit `gap_resolutions` entry provides the gap,
exact `basis_refs`, and a host reason. Resolutions are attributable judgments,
not machine proofs. Experience references must match task, scope, methods, and
their exact evaluation when supplied; their counterevidence is retained.

## API and persistence

Public entry points are `SCHEMAS`, `validate_content(payload)`,
`validate_links(workspace, payload)`,
`validate_local_links(journal, content, source_id, external_workspace=None)`,
`read_research_decision(workspace, ref)`, `build_research_decision(...)`, and
`build_decision_update(...)`. Readers and builders are read-only and verify
exact EvidenceRefs through the existing evidence reader. The reader accepts
either schema. Shape validation alone does not verify source identities.

Write the returned payload with existing
`continuation.record_content(journal, payload, directory, source_id=...,
source_kind=...)`; no additional record class or store is required. Each payload
is bounded by the existing 16 KiB publication budget. Explicit provenance
closure is bounded to 128 selected references and 8 MiB of original bytes;
each original is limited to 1 MiB. Larger-than-page JSON originals reuse the
existing exact 32 KiB paged reader and are assembled and hash-verified before
inspecting their complete declared lineage. Overflow requires a narrower
explicit evidence view, never silent truncation. Payloads remain at 16 KiB.

Publication's in-memory preflight adapter validates exact declared identities and
links without creating a temporary journal. If a report uses not-yet-published
local inputs, its numeric replay is deferred to the authoritative real-journal
commit; that commit must recompute the report before publication completes.

Evidence timing is delegated to `evidence_window`. A decision without a verified
controlled window is not certified prospective. Retrospective reconstruction,
host declarations, timestamps, hashes, and graph order do not establish what
the host knew before choosing. Controlled delivery/release is a separate opt-in
protocol and does not change these scientific claim boundaries.

`tests/test_research_decision.py` uses synthetic engineering fixtures. It checks
forged/corrupt references, scope mismatches, declared label leakage, exact
execution/evaluation linkage, unknown and unrun predictions, negative unchanged
updates, fixed-rule replay, counterevidence retention, and bounded reads. These
tests do not establish scientific gain, transfer, or independent validation.
