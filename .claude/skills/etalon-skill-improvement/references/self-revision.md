# Revising G and retaining the generating call

When changing G, save and actually read its complete current bundle G0. Apply
the relevant G0 instructions to the released development problem, and preserve
the input, tool returns, edits and unknown/measured costs of that native run.
G1 is its output for later calls; the current run stays bound to G0. A code or
documentation fixture cannot substitute for that native generation trace.

For S0 -> S1 produced using G0, S1.parents contains S0, not G0. For G0 -> G1,
G1.parents contains G0 because G0 is also the object being changed. The separate
revision Artifact binds `producer_method_ref` and `producer_use_ref` to G0's
generating invocation. This supports later G0/G1 -> candidate -> actual-use
comparisons without conflating generation with ancestry.

Optional runnable helper, given an existing configured `journal`, an explicitly
bound workspace `source_id`, and recorded version/use/problem IDs:

```python
from etalon.research.method_revision import (
    journal_ref, build_method_revision, record_method_revision,
)

ref = lambda identifier: journal_ref(journal, identifier, source_id=source_id)
revision = build_method_revision(
    problem_refs=[ref(problem_id)], target_kind="G",
    base_method_ref=ref(g0_id), producer_method_ref=ref(g0_id),
    producer_use_ref=ref(g0_use_id), candidate_version_refs=[ref(g1_id)],
    change_summary="Separate selection evidence from content defects",
    mechanism_hypothesis="Might avoid revising a method that was never used",
    scope="Released development problem; later benefit not yet measured",
    counterexamples=["Direct SDK completion can be correct and cheaper"],
    trial_refs=[ref(trace_id)], accounting_refs=[ref(accounting_id)],
)
artifact_id = record_method_revision(
    journal, revision, output_directory, source_id=source_id,
    source_kind="new_execution",
)
```

`build_method_revision` only constructs JSON and accepts source-qualified refs.
`record_method_revision` verifies local metadata, candidate ancestry and producer
attribution, publishes immutable content, then registers an ordinary Artifact.
External evidence refs stay explicitly unresolved by this helper. Inspect them
through evidence reads; local method registration uses full imported snapshots
and provenance envelopes. Source IDs come from your workspace binding, not a
database path or an inferred changing database hash. The helper's role names are
`domain_skill`, `usage_method`, `improvement_method` for S/U/G; these are local
recording conventions, not execution prerequisites. The executable source example
`examples/skill_improvement.py` exercises S/U/G, no-change, partial use and stop.

Use `candidate_version_refs=[]` with a concrete explanation for no-change.
Registering a candidate neither runs nor promotes it. Local use can occur before
registration. Withdraw by choosing the prior version on a subsequent request;
retain the candidate and its failures. Replays in the same output directory do
not append duplicate events. An interrupted DB registration may leave an orphan
file, which is retained and can be registered by retrying the same content.

For a later benefit claim, freeze independent tasks and criteria before exposing
them to the generation run. Evaluate the descendants in subsequent actual use,
including no-change and skipped candidates. Attribute task success separately
from an unobserved candidate effect. Count generation, failed attempts and use
costs; a G self-score or number of versions is not evidence of improvement.
Use bounded event consumption only when useful; a new method note never implies
an authorized recursive generation or a fresh budget.
