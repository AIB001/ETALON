# Paired models and recorded diagnostic segments

Use the relevant section independently. Building a model does not require
recording or executing it; reading feedback does not require launching another
segment. These are existing Python SDK functions, not scientific MCP tool names.

## Empirical joint model from complete paired development rows

`etalon.research.calibration.empirical_model` constructs a finite joint
distribution from already available paired tasks. Each row has `task_id`,
`independence_group`, `evidence` IDs, full `{probe_id: outcome}` outcomes and
full `{decision_id: nonnegative_loss}` losses. Losses need an independently
defined objective; they are not automatically derived from a model's own labels.
All rows must cover the same full probe and decision spaces. Missing pairs are
not filled, and correlated poses are not independent task rows.

```python
from etalon.research.calibration import empirical_model

model = empirical_model(
    rows=paired_rows, probes=probes, resources=resources, evidence=evidence_ids,
    development_tasks=development_ids, heldout_tasks=heldout_ids,
    loss_definition=loss_definition, cost_unit=cost_unit)
```

`probes` and `resources` are tuples of `diagnostics.Probe` and `Resource`;
their dependencies, outcome labels, resource references and units must agree.
Rows need unique task IDs, at least two development tasks and at least two
declared independent groups. The nonempty held-out task set must be disjoint
from development, and row task IDs must belong to development. Every row's
nonempty evidence set must be a subset of the declared evidence. These are
necessary declarations, not proof of a real prior freeze or real independence.

The default gives equal task weight and rejects repeated group IDs. If repeated
tasks within groups are meaningful for the question, `group_balanced=True` is
an explicit alternative: equal mass per group, then equal weight per task in
that group. It still requires at least two groups and unique task IDs. Choose
and disclose this assumption rather than relabeling correlated rows as new
groups or silently dropping them. If neither weighting assumption is justified,
stop. A numerical model does not make an unidentifiable study valid.

The returned `JointModel` has `probability_basis='estimated_from_evidence'`,
the evidence IDs, split digest and weighting/finite-support assumptions. This
is an empirical distribution over supplied development rows, not HD/HP/HE
causal attribution. Synthetic rows still mean an engineering fixture even
though this constructor labels its empirical estimation procedure that way.
Prediction calibration and held-out gain remain untested. You may feed the
actual returned model to an optional diagnostic function or stop at inspection.

This constructor is pure. It cannot inspect artifact bytes or roles without a
journal. Successful construction is **not** evidence admission; do not claim
that held-out/hash checks passed merely because it returned a model.

## Record or compile only the selected query

`etalon.research.runtime.record_program(journal, model, plan)` takes an actual
diagnostic plan for that exact model and returns its program ID. It checks plan
content identity and current model/reused-resource artifact hashes and
diagnostic roles before appending immutable model/program provenance in the
same journal. Exact replay is idempotent. A failed check must not be bypassed
by relabeling independent-validation/holdout evidence, changing old records,
or supplying an invented plan ID. Recording does not reserve or spend resources.
An `exact_program` result with `model_challenge` has no valid program schema/ID
and is refused by `record_program`; do not describe it as a saved no-root plan.
A valid stop program can be saved, but has no query to compile or branch from.

If an executable first query is wanted,
`compile_first_segment(journal, program_id, operation_bindings)` accepts a
mapping `{probe_id: {'operation': name, 'arguments': arguments}}` supplied by
the caller. The concrete operation and its arguments must actually implement
the chosen probe's scientific meaning; a mapping alone does not certify that.
Only the current query becomes one node, with its quoted resource cost/unit.
Descendant branches are not submitted or observed. Stop/no-root plans have no
segment; missing bindings and future-node references are refused. Current model
and reused-resource artifacts are checked again, so a saved record cannot
silently authorize changed bytes at the same paths.

The return status is `compiled_not_executed`, with the workflow and its hash.
Compilation appends a segment receipt but does not prove that input paths,
operation prerequisites or an executor are ready. Existing runtime `plan`
performs real preflight/input binding; `submit` is a separate execution step
using existing authorization, budget, receipts and admission. A syntactically
valid compiled segment can still fail preflight. Do not run it merely to make
compilation appear useful; these calls do not enlarge the user's execution
scope. The host may discard the advice or stop before any of these operations.

## Relate actual feedback to a saved query

`observed_branch(journal, program_id, observation_id, readout=...)` reads an
already admitted Observation for the current query. It checks the selected
probe ID and **every condition identity declared in that probe's bindings**,
the experiment/readout relation, and current model/resource/observation artifact
hashes and diagnostic roles. A same-named probe under another declared condition
is not interchangeable. Undeclared task, method or unit identities cannot be
verified by this interface; bind the identities the question needs explicitly
and retain upstream measurement/admission checks.

This interface uses direct categorical labels; a measured numeric value is
converted to its string representation, not thresholded. `0.2` is not the label
`low`. For a continuous readout, use the existing fixed
[`OutcomeRule`/`bind_outcome`](finite-diagnostics.md#bind-an-existing-readout-to-a-discrete-outcome)
and then condition/replan from its actual returned outcome; do not guess a
branch label or pass a derived record as if it were an Observation.

A matching categorical result returns `observed_branch` and an optional
descendant program. An unmodeled failure or label returns `model_challenge`
with `program=None`. Missing readout, wrong identity/type or changed evidence
is a refusal. `requires_replanning=True` remains even when a descendant exists:
it is advice, not an executed future branch. Report the refusal/challenge or
choose a new bounded question; no automatic retry, repair or query is implied.

Feedback reads do not change records, fees or reservations. Compiled quotes,
failed measured cost and pending/unknown reserves remain distinct; local
processing may be unmetered rather than zero. Model/plan saving, successful
feedback matching and a changed numerical recommendation do not establish
independent scientific benefit or improvement of S, phi or G.


## Complete local SDK example

This self-contained engineering example runs with the installed ETALON SDK.
It needs no MCP call, journal, paid execution or source search. Edit or use only
the relevant part; it is not a required workflow. Conditional branches use real
SDK returns in the same local script. The printed summary retains unknown costs;
keep full returned objects or save them to a new report file when needed.

<!-- runnable-example: paired-models -->
```python
"""Synthetic paired rows: pure empirical construction, not evidence admission."""
import json
from etalon.research.calibration import empirical_model
from etalon.research.diagnostics import Probe, Resource, condition, exact_program

# These evidence IDs are illustrative, NOT admitted Artifact records.
# Real use supplies existing immutable paired evidence and independently defined losses.
evidence_ids = ('synthetic-row-table-not-admitted',)
paired_rows = [
    {'task_id': 'dev-a', 'independence_group': 'group-a', 'evidence': list(evidence_ids),
     'outcomes': {'q': 'low'}, 'losses': {'keep': 0., 'change': 1.}},
    {'task_id': 'dev-b', 'independence_group': 'group-b', 'evidence': list(evidence_ids),
     'outcomes': {'q': 'high'}, 'losses': {'keep': 1., 'change': 0.}},
]
model_inputs = dict(
    rows=paired_rows,
    probes=(Probe('q', 'measure', {'condition': 'synthetic-paired'},
                  ('low', 'high'), ('q-readout',)),),
    resources=(Resource('q-readout', .1, 'fixture_credit'),),
    evidence=evidence_ids, development_tasks=('dev-a', 'dev-b'),
    heldout_tasks=('unseen-fixture-only',),
    loss_definition='synthetic externally specified wrong-decision loss',
    cost_unit='fixture_credit',
)
model = empirical_model(**model_inputs)

# Partial use is valid: inspect support, then decide whether more advice is useful.
feedback = {'q': 'low'}  # Already available categorical engineering fixture.
support = condition(model, feedback)
if support['status'] == 'conditioned':
    advice = exact_program(model, observed=feedback, budget=0., max_depth=0,
                           cost_weight=1.)
else:
    advice = None
summary = {'support': support['status'],
           'suggested_kind': advice['program']['kind'] if advice else None,
           'decision': advice['program'].get('decision') if advice else None,
           'evidence_admitted': False, 'heldout_gain': None,
           'actual_execution_cost': None, 'local_processing_cost': None}
print(json.dumps(summary))
```
