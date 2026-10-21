# Optional finite diagnostics

Choose a section for the current question. These are existing Python SDK
functions, not new MCP scientific tools or a mandatory sequence. Each can be
used alone, partly combined, or declined. The caller can stop after any result;
no `Campaign`, `Supervisor`, policy fit, MethodVersion, or skill gate is needed.

## Bind an existing readout to a discrete outcome

Use `etalon.research.calibration.bind_outcome` when an admitted Observation
should be interpreted under a specific fixed threshold rule. Supply an existing
`ResearchJournal`, Observation ID, task ID and nonempty expected condition
bindings. `OutcomeRule` names the probe, measurement method, readout, unit,
threshold, and two outcome labels:

```python
from etalon.research.calibration import OutcomeRule, bind_outcome

rule = OutcomeRule(probe_id, measurement_method_id, readout, unit,
                   threshold, below, at_or_above)
bound = bind_outcome(journal, observation_id, rule,
                     task_id=task_id, expected_bindings=condition_bindings)
```

The rule should be fixed before the next task. The function verifies identity
and content but does not prove that the threshold was preregistered. Do not
silently change a rule after inspecting the result and call it a frozen test.
For measured values, `< threshold` yields `below`; equality yields
`at_or_above`. Other statuses remain their named status, including `failed`,
`unsupported` and `not_evaluated`; they are not numeric zero.

The result retains `observation_id`, `probe_id`, rule, task and bindings;
`likelihood` and `causal_hypothesis` remain `None`. Binning is neither a fitted
likelihood nor causal attribution. If composing with another method, use this
actual result, for example `{bound['probe_id']: bound['outcome']}`.

**This call appends provenance.** It writes one immutable derived
`probeoutcome` record and event in the same journal. Exact replay is idempotent;
it does not execute, reserve, settle, create a training label or rewrite the
Observation. A writable store is required for this call. Task/condition,
measurement, unit, evidence hash or evidence-role mismatch refuses the binding.
Independent-validation and sealed-holdout evidence cannot be used as diagnostic
inputs. Stop and inspect the binding; a refusal is not permission to dispatch a
new measurement or relabel held-out evidence.

## Check support in an explicit finite joint model

Use `etalon.research.diagnostics.condition(model, observed)` to ask whether
already known outcomes fit a declared model. It is a pure function and can use
valid observed outcomes directly without the binder or a journal:

```python
from etalon.research.diagnostics import condition

support = condition(model, observed)  # observed: {probe_id: outcome}
```

`model` is a validated `JointModel`: explicit `World` probabilities, complete
joint probe outcomes and decision losses; `Probe` identities/dependencies;
`Resource` identities, quotes and cost unit; probability provenance, assumptions
and independent loss definition. Use actual dataclasses from
`etalon.research.diagnostics`, not an unvalidated JSON dictionary. If loading a
serialized model, reconstruct its World, Probe and Resource entries before
constructing JointModel. Synthetic probabilities must retain
`probability_basis='synthetic_fixture'`; evidence-derived probabilities need
their declared evidence. This function does not build or fit that model.

`conditioned` returns compatible world indices and a normalized posterior over
the declared hypotheses, together with `observed` and `model_id`. It does not
exclude mechanisms outside those worlds or establish predictive calibration.
`model_challenge` returns an empty posterior when the **joint** observation has
no support. Individual marginal support is insufficient. Unknown probe IDs
raise `ValueError`; an unmodeled failed/missing outcome stays unsupported rather
than becoming a successful or zero-valued observation. Stop the inference,
report the model boundary, or consider a separately identified model revision.

## Obtain bounded numerical advice

Use `etalon.research.diagnostics.exact_program` when a small explicit joint
model and a remaining budget can inform a next query or stop decision. This
pure function computes advice; it does not launch the returned tree:

```python
from etalon.research.diagnostics import exact_program

advice = exact_program(model, budget=remaining_budget, max_depth=depth,
                       cost_weight=loss_per_cost_unit, observed=observed)
```

`observed` is optional, so this can be the only method used. Optional
`acquired_resources` records already acquired resource IDs. Shared resources
are charged once in the finite calculation, and the implementation infers
resources of observed probes. These modeled acquisitions are not proof of
actual resource execution. Preserve their evidence separately.

`program.kind` is `query` or `stop`. Unsupported joint outcomes return
`model_challenge`, `program=None` and `decision=None`. No budget/depth can lead
to stopping or abstention. A host may decline a recommended query; advice is
not spending authority. Enumeration is bounded to 12 probes and depth 0..8,
and is an exact reference within the supplied finite model, not a universal
research optimizer. `myopic=True` is an optional depth-one value baseline.

Per-branch quotes constrain the budget. Optional `world_resource_costs` must
cover every world/resource with measured costs within quotes; missing
counterfactual costs cannot be filled with zero. Optional `decision_resources`
must bind every terminal decision and include a zero-resource abstention.
Bad dependency/resource identities, incompatible units and invalid cost rows
are errors to report, not workflow constraints to remove. Keep quoted cost,
actual measured cost and model loss separate.

## Evidence and cost interpretation

`condition` and `exact_program` do not modify the journal or its reservations.
`bind_outcome` changes provenance only; it does not incur a second execution
charge. Actual failed costs and pending reservations remain in the existing
ledger. Local processing costs may be unmetered; that is not certified zero.
Record synthetic examples as engineering evidence, including their fixture
cost unit. A changed posterior, saved method or successful composition does
not demonstrate scientific benefit or independent improvement of S, phi or G.


## Complete local SDK example

This self-contained engineering example runs with the installed ETALON SDK.
It needs no MCP call, journal, paid execution or source search. Edit or use only
the relevant part; it is not a required workflow. Conditional branches use real
SDK returns in the same local script. The printed summary retains unknown costs;
keep full returned objects or save them to a new report file when needed.

<!-- runnable-example: finite-diagnostics -->
```python
"""Synthetic finite model: no journal, execution or scientific benefit."""
import json
from etalon.research.diagnostics import (
    JointModel, Probe, Resource, World, condition, exact_program,
)

model = JointModel(
    hypotheses={'h0': 'keep wins in this fixture', 'h1': 'change wins'},
    worlds=(
        World('w0', 'h0', .5, {'q': 'low'}, {'keep': 0., 'change': 1.}),
        World('w1', 'h1', .5, {'q': 'high'}, {'keep': 1., 'change': 0.}),
    ),
    probes=(Probe('q', 'measure', {'condition': 'fixture'},
                  ('low', 'high'), ('readout',)),),
    resources=(Resource('readout', .1, 'fixture_credit'),),
    probability_basis='synthetic_fixture',
    assumptions=('two equally weighted synthetic worlds; not measured probabilities',),
    independent_loss_definition='synthetic wrong-decision unit loss',
    cost_unit='fixture_credit',
)

# JSON is not a JointModel. Reconstruct nested dataclasses when loading it.
payload = json.loads(json.dumps(model.as_dict()))
restored = JointModel(**{
    **payload,
    'worlds': tuple(World(**row) for row in payload['worlds']),
    'probes': tuple(Probe(**row) for row in payload['probes']),
    'resources': tuple(Resource(**row) for row in payload['resources']),
})
advice = exact_program(restored, budget=.1, max_depth=1, cost_weight=1.)

# Existing feedback can be inspected without accepting or executing the advice.
observed = {'q': 'failed'}  # Synthetic failure; never substitute zero or 'low'.
support = condition(restored, observed)
if support['status'] == 'model_challenge':
    next_choice = 'stop_inference'  # Caller choice; can instead revise the model.
    following_advice = None
else:
    following_advice = exact_program(
        restored, observed=observed, budget=.1, max_depth=1, cost_weight=1.)
    next_choice = 'inspect_advice'  # Still no automatic execution.
summary = {'support': support['status'], 'next_choice': next_choice,
           'query_quote': advice['program'].get('quoted_incremental_cost'),
           'actual_execution_cost': None, 'local_processing_cost': None}
print(json.dumps(summary))
```
