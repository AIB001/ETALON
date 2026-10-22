# Tunable methods and research routes

These optional helpers extend the existing `ResearchJournal`, `research-step`,
`MethodVersion` / `MethodUse` / `MethodEvaluation` and MolCascade `Screen.plan`.
There is no new execution ledger, scheduler, resource allocator or fixed Probe
stage. R4b remains paused. Compilation and comparability are not evidence of
scientific benefit.

## Parameter contract

`etalon.research.method_contract` provides:

- `validate_contract(payload)` and `parameter_values(contract, configuration)`.
- `compare_parameters(contract, base_config, candidate_config)`.
- `compile_screen_revision(screen, config_path, *, contract, input_identities,
  conditions, stage_checks, library=None, target=None, evidence_bindings=(),
  parameter_bindings=())`.
- `assess_comparability(base, candidate, *, mode, base_coverage,
  candidate_coverage, verify_receipt=None)`.

A contract is ordinary immutable JSON with schema `etalon-method-contract/1`,
name, parameters, required semantic checks, a fixed external evaluation object,
and supersedes. Every parameter declares a JSON Pointer, category, strict type,
unit, default, numeric bounds or choices, required flag, immutable flag,
dependencies and expected effect. The effect is a hypothesis. Hard constraints
cannot be changed by a parameter comparison. Dependency conditions are explicit
equality requirements between contracted parameters; this is deliberately not a
general policy language.

The categories have different meanings:

| Category | Examples | Comparison treatment |
| --- | --- | --- |
| `execution` | batch size, implementation options | May change under the same scientific protocol |
| `method` | degrees of freedom, constraints, sampling, selection thresholds | Legal candidate; identify it as a method/workflow comparison |
| `evaluation` | external success threshold or evaluation definition | Disclosed; old evaluation cannot certify benefit after its standard changes |

Methods retain distinct authored byte identity, parsed declaration identity,
compiler revision and effective compiled configuration identity. The compiler
report records loaded module file paths and hashes, library bytes when a library
override is supplied, target, explicit scientific input identities, conditions,
preflight-confirmed backends/assets, and declared-to-compiled checks. Exact file
identity changes with LF/CRLF even when parsed configuration is unchanged.

The caller maps semantic checks explicitly, for example
`stage_checks={"standardize": ["chemical-identity"], "properties": ["properties"]}`.
The map records present and missing stage IDs. It does not assert that compilation
executed the check. A missing backend raises the native compiler/preflight error;
the caller retains that failed attempt in trace instead of manufacturing a ready
plan.

Parameter consumption is also explicit:

```python
parameter_bindings = [
    {"parameter": "/library/batch_size", "stage_id": "library", "path": "/batch_size"}
]
```

These pointers compare the authored value against the actual compiled stage
configuration. The report keeps `matches`, `missing` or `transformed`, together
with unbound parameters. No universal scientific transformation is inferred.
Unmapped or transformed changed parameters require further evidence before the
helper marks a comparison ready. Evidence bindings link parameter names to
existing evidence refs and optionally an existing scientific-input artifact;
they do not replace that module's conditioning/evaluation visibility validation.

## Compare obligations, not tool-count equality

Comparison modes are `same_protocol`, `method_change`, and `workflow_change`.
Input identities, library identity, target, conditions and the evaluation
contract are compared explicitly. Structural changes require workflow comparison;
method changes cannot be presented as unchanged-protocol optimization.
The comparison also checks the actual compiled plugin, stage order/membership,
input wiring, effective parameters, backends/assets and loaded compiler source
identity. An unchanged declaration cannot conceal an effective-program change.
Explicitly mapped execution-parameter changes can remain the same protocol;
unexplained effective changes require a method/workflow comparison, and changed
compiler bytes require re-establishing execution conditions. Moving the same
module bytes to a new path is reported in the plan but is not a code change.
Changing an implicit default into the same explicit value changes declaration
provenance only, not a scientific parameter or user hard constraint.

Coverage rows identify a required check, `passed` / `reused` / `failed` /
`missing`, receipt ref, optional reuse basis ref, input identity digest and
condition digest. A reusable check needs a receipt and explicit applicability
basis. A check omitted by compilation can only be covered through explicit
reuse; accidental omission is an unmet obligation. Removed redundant steps are
legal candidates, not automatically incomparable.

`verify_receipt(row, side)` must verify the actual producer evidence and semantic
coverage. Merely finding the ref or trusting a `verified=true` label is
insufficient. If no verifier is provided, the result stays `awaiting_evidence`.
There is no generic scientific verifier in this helper. A ready result means
ready for the existing MethodEvaluation flow, with `benefit_established=false`.
Quality, retention, regression and cost must still be evaluated under their
declared denominators. No comparison helper promotes a shared default.

## Four-stage trace in the same Journal

`research_trace.trace_event(...)` builds `etalon-research-trace/1` content.
Persist it through `continuation.record_content`; refs and artifact hashes use
the existing Journal publication mechanism. There is no extra trace database.

| Phase | What is preserved |
| --- | --- |
| `declaration` | proposed checks, alternatives, choice, reason, reopen condition |
| `compiled` | revision, declared/compiled/missing checks; compiler-added checks are allowed |
| `execution` | attempt, tool/call identity, response identity, receipt, failure, duration and cost if known |
| `admission` | linked execution attempt, criteria, accepted and rejected evidence |

Events bind task, branch, workflow/node/config refs, method refs and prior trace
refs. Sequence is supplied by the producer; the projection uses this declared
ordering and does not invent a global clock. Exact repeated event deliveries
deduplicate in a projection; conflicting payloads under the same event key are
rejected. Callers use stable event keys and existing idempotent producers when
publishing. Event projections do not enforce distributed exactly-once execution.

`origin=declared` labels an agent decision or interpretation. `origin=observed`
labels producer-reported observations and requires receipts. This field is not
authentication. Publication checks receipt identity, while verification of an
actual tool invocation remains with its producer/adapter. The HTML explicitly
states that the projection has not authenticated receipts. It records concise
decision reasons, not private chain-of-thought.

Admission is an additional decision, never deletion of a failed attempt.
Attempts that fail or are rejected remain in the denominator. Reuse has an
original-execution link and does not become another independent experiment.
Reanalysis and independent repeats should use new attempt identities and their
own receipts. Unknown costs stay null; the view does not manufacture totals.

`project_trace(records)` creates both a research route and a tool-level view
from the same selected records. `render_trace_html(records, title=...)` renders
standalone escaped HTML without scripts, remote assets or an independently
authored narrative. Scope is always `provided_records_only`; incomplete input is
not represented as complete history. The caller resolves the appropriate graph
or journal scope using the existing evidence API, then writes the returned HTML
only when a view artifact is requested.

Trace replay verifies restoration and attribution. A newly selected branch that
was never executed in the old trace still needs real outcomes to establish
scientific benefit.

## Validation scope

`tests/test_method_contract.py` uses real MolCascade compilation and explicit
fixture execution receipts. `tests/test_research_trace.py` tests rejected/failed
denominators, reuse, duplicate delivery, compiler-added checks, escaped HTML,
unknown costs and Journal-linked admission. These are engineering checks;
normal scientific execution and cross-target transfer require separate evidence.
