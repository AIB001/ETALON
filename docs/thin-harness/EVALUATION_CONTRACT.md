# Optional external evaluation contracts

`etalon.research.evaluation_contract` replays declared numeric rules against
exact published evidence. It does not execute a method, select or adopt one,
change defaults, certify scientific truth, or require callers to use a gate.
Existing SDK, MethodEvaluation and composition APIs remain available directly.

The public functions are:

- `build_evaluation_contract(**declaration)` and
  `validate_evaluation_contract(payload)` for `etalon-evaluation-contract/1`.
- `evaluation_rules_digest(contract)` for stable rule identity independent of
  observed input refs, provenance and optional typed wrapper refs.
- `evaluate_contract(workspace=None, contract=None, *, contract_ref=None,
  entry=None, read_context=None)` for a read-only `etalon-evaluation-report/1`.
  Supply exactly one evidence selector, and exactly one inline contract or
  contract Artifact reference.
- `record_evaluation_artifact(journal, payload, directory, *, source_kind,
  workspace=None, entry=None, read_context=None)` for optional ordinary Artifact
  archival. Report archival recomputes the report and rejects modified statuses.
  Contract archival records a declaration; it does not attest its evidence.

## Declaration

Required top-level fields are `task_id`, `scope`, `conditions` (nonempty textual
identities), `protocol_ref`, `initial_conditions_ref`, `inputs`, `metrics`,
`comparability`, `required_checks`, and `provenance`. The builder supplies the
schema and defaults the optional typed bindings to null and method lists to
empty. All refs use the existing source-qualified EvidenceRef with record
digest; artifact original bytes are checked by the evidence reader.

```python
from etalon.research.evaluation_contract import build_evaluation_contract, evaluate_contract

select = lambda path: {"input": "paired", "pointer": path}
contract = build_evaluation_contract(
    task_id="paired-example", scope="One fixed matched panel",
    conditions={"target": "explicit target", "protocol": "paired/1"},
    protocol_ref=protocol_ref, initial_conditions_ref=initial_ref,
    inputs=[{"name": "paired", "kind": "result", "ref": result_ref}],
    comparability=[{"check_id": "protocol", "selector": select("/protocol"),
                    "expected": "paired/1"}],
    required_checks=[{"check_id": "completed", "selector": select("/status"),
                      "expected": "completed"}],
    metrics=[{
        "name": "solver_wall_ratio", "family": "cost",
        "measure": {"operation": "ratio", "left": select("/candidate_seconds"),
                    "right": select("/baseline_seconds")},
        "operator": "lt", "threshold": 0.9, "unit": "ratio",
        "denominator": "All 12 matched pairs including failed attempts",
        "denominator_selector": select("/attempted"),
        "missing_policy": "insufficient",
    }],
    provenance={"mode": "retrospective", "note": "Rules translated after results",
                "basis_refs": [protocol_ref]},
)
report = evaluate_contract(entry=entry, contract=contract)
```

Input kinds are `result`, `accounting`, and `context`. Each result/accounting
input needs at least one explicit required check. Hosts must declare relevant
execution completion, identity, geometry, check coverage, failed-attempt and
other domain obligations. A verified JSON object alone does not establish
scientific applicability. Context/protocol refs may be non-JSON artifacts; metric
inputs must be complete JSON.

Selectors use an input name and JSON pointer, including escaped keys and list
indices. An optional `reducer: "count"` counts an explicitly selected array;
there is no implicit filtering or dropped-row denominator. Measures allow only
`value`, `difference`, `abs_difference`, and `ratio`; the last three have a right
selector. Comparators are `le`, `lt`, `ge`, `gt`, and `eq` with finite numeric
thresholds. Booleans are not numbers. There is no expression interpreter,
callable field, imported plugin, or supplied code execution.

Every metric declares `unit`, a textual `denominator`, a measured positive
`denominator_selector`, and `missing_policy: "insufficient"`. The denominator
records cohort/exposure and does not implicitly divide the measure. Unit meaning
is a declaration: bind units/protocol fields with comparability checks when
the source supplies them. No unit conversion is inferred.

## Outcomes and limits

`regression`, `retention`, `funnel`, and `cost` have separate metric/family
reports. An undeclared family is `not_declared`, never silently passed. A metric
can numerically pass while required coverage fails; the overall/family status
includes prerequisites and cannot pass in that case. Cost success never erases
quality failure.

`criteria_passed` means every declared criterion and prerequisite passed.
`criteria_failed` means a measured bound or required check failed.
`insufficient` includes unavailable, changed, hidden, incomplete, missing,
non-numeric or non-finite observations and zero denominators/divisors.
`incomparable` includes declared condition or typed-binding mismatches. Overall
precedence is incomparable, failed, insufficient, passed; all individual reasons
remain visible. There is no vote, weighted score, imputation or adoption action.

The report embeds its contract, exact input refs, verified refs, contract digest,
rule digest, scope and provenance. Contracts may be reused by replacing observed
input refs while keeping the rule digest; the full contract digest will change.
`retrospective` always returns `retrospective_not_prospective`. `predeclared`
requires basis refs but returns `predeclaration_claim_chronology_not_verified`:
identity alone cannot establish when the author first saw outcomes. Replay never
creates independent validation or restores the independence of seen results.

Reads preserve the existing published-source visibility, purpose policy and
original-byte checks. The API accepts at most 64 entries per declared list and
64 KiB contracts. JSON originals are read in verified 32 KiB pages, bounded to
1 MiB per input and 8 MiB total. Larger/incomplete material remains a gap.

## Existing method interfaces

Optional `subjects` and `comparators` identify actual MethodVersion records.
`plan_ref` binds an existing composition `evaluation_plan` for the same task,
methods, conditions and exact metric declarations. `evaluation_ref` binds an
actual MethodEvaluation with matching methods/conditions and membership of each
named result/accounting input. Existing conclusions are provenance and are not
used as numeric observations.

`method_contract_ref` binds an existing method contract whose `required_checks`
are covered and whose `evaluation` equals
`{"schema": "etalon-evaluation-contract/1", "rules_digest": digest}`. Compute
`evaluation_rules_digest(contract)` before adding this optional ref; wrapper refs
are excluded from the rule digest to avoid a circular identity dependency.

Archival calls existing `components.record_json` and binds exact `evidence_refs`.
It adds no table or schema to the journal kernel. A host may explicitly pass the
report Artifact to `composition.record_evaluation` as a result. Ordinary readers
can inspect the Artifact without installing a new projection or mandatory gate.

## Existing-result acceptance

The 2026-10-24 acceptance translates already observed SND1 MMFF-gradient and
PoseBusters conditional-ranking protocols into retrospective contracts. It reads
the immutable relocated handoff entries and preserves original decisions.

The SND1 criteria pass for 12 seen correlated pairs: objective/coordinate
equivalence, retention/check coverage, energy-call ratio below 0.5 and solver
wall ratio below 0.9. This is scoped solver engineering; excluded overhead,
fixed-core preference, pose quality and affinity limits remain explicit.

PoseBusters fails the original conditional-policy criteria: valid Top1 is
34/48 versus CNN 35/48 and empirical 40/48; physical validity is 45/48 versus
empirical 46/48. Selected CNN calls are 38/48, so the cost criterion passes
separately. This agrees with the original `retain` decision and does not claim
independent end-to-end savings or statistical noninferiority.

Local integration artifacts are under
`integration/etalon-resource-branches-20261024/evaluation/`, including exact
contracts/reports, replay script, before/after original hashes and runtime/source
identity. No molecular computation or production deployment is performed.

`aggregation-audit.json` separately recomputes SND1 ratios from recorded totals
and PoseBusters counts from all 48 published comparison rows. Native coordinate
differences and the upstream pose verifier outputs are reused; their scientific
correctness is not independently recertified. The PoseBusters top-level
`candidate_successes=35` describes fixed CNN, so policy evaluation explicitly uses
`metrics/policy/valid_top1=34`, independently checked against the policy rows.
