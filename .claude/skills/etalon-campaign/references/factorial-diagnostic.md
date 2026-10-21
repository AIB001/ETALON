# Local two-factor diagnostic

Use when four conditions share a defined readout and a descriptive interaction
would answer a local question. This is a numerical function over existing
evidence, not a pipeline or evidence of independent improvement. You can use
this method without any other section of the campaign skill.

## Inputs and call

Use an existing campaign database and a recorded `Contrast` ID. Its `cells`
map `00`, `01`, `10`, `11` to distinct `Observation` IDs or `None`; `readout`
names the numeric measurement. Recorded experiments must belong to the same
task and compatible measurement method. Record shared inputs, paired
conditions, and assumptions in `Coupling` rather than claiming independence.
Diagnostic inputs must have diagnostic roles; held-out evidence is not a
source of design feedback.

```python
from etalon.active import CampaignStore
from etalon.research import ResearchJournal

journal = ResearchJournal(CampaignStore(database, read_only=True))
result = journal.factorial(contrast_id)
```

This is the actual existing SDK call, not an MCP tool name. The caller chooses
how to inspect or report its result. It does not create a MethodUse or
MethodEvaluation, schedule actions, authorize spending, or change records.
If reporting adoption separately, attribute a host declaration as
`host_reported`; an SDK call does not prove use of the whole skill.

## Outputs and decisions

- `computed`: `interaction = y11 - y10 - y01 + y00`, with the observed `cells`
  and `unit`. For synthetic values `1, 2, 3, 7`, the interaction is `3`.
- `unresolved`: `interaction=None`; `missing_or_failed` identifies absent,
  failed or otherwise unusable cells. Missing cells stay absent from `cells`.
  Preserve the original status; never complete the arithmetic with zero.
- Mixed units raise `ValueError('contrast units differ')`; nonnumeric measured
  values also raise `ValueError`. Stop the combination and inspect the
  readout definition or choose a different already-supported question.
- `uncertainty=None` means not estimated. The returned scope is descriptive;
  even a nonzero interaction does not establish causal identification,
  independent replication, transfer gain, or a scientific effect.

If an unresolved cell is essential, state the missing evidence and stop unless
the user separately authorized a suitable measurement. Do not dispatch work
just to complete a table. Recording and reading these records does not incur
a second execution charge; local audit CPU cost is unmetered, not certified
zero. Actual action costs and reservations remain in the existing ledger.
