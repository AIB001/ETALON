# Local advice from measured chunks

This synthetic example exercises the actual SDK without launching generation,
constructing a Campaign/Supervisor or submitting an action:

```python
from etalon.generate.productivity import Chunk, Productivity, allocate

profile = Productivity(
    tag="spent-loop", model="fixture-generator", pocket="fixture-pocket",
    chunks=tuple(Chunk(requested=100, delivered=100, unique=0, seconds=10)
                 for _ in range(3)),
)
advice = allocate([profile])
print(advice)
if profile.tag in advice["retire"]:
    print("stop this route; retain the recorded chunks")
```

For actual work supply measured chunks and stable model/pocket/box identities.
You may analyze just one loop or compare several; allocation advice neither
starts them nor changes their scheduler. Three zero-unique chunks can support a
retirement recommendation, while a short window leaves exhaustion unresolved.
`median_unique_per_hour=None` when no loop remains is not a measured zero rate.
Retain null/unbounded duplication cost, failures and actual production costs.

Existing-library planning is independently available through
`etalon.campaign.pipeline.Pipeline(pool=1000, budget_gpu_hours=1, final_count=10,
stages=("lilly_demerits",)).dry_run()`. The numbers here are synthetic planning
inputs, not permissions or measured costs. Its `generators` is `None`; no generator
is necessary merely to use an existing library. Keep old full-campaign planning
and numerical strategy defaults as optional methods.

The source example `examples/data_design_methods.py` runs this advice alongside
an independent calibration diagnostic in one local Python process. That is an
editable composition, not a mandatory cross-skill order. Yield alone establishes
neither affinity nor a better generator or method.

## Optional funnel estimates and resource balance

- `etalon_stages()` exposes `etalon.economics.stage.STAGES` and the source/status
  of their costs/correlations. Catalogue conventions are not measurements of the
  current target.
- `etalon_plan_campaign(pool, budget_gpu_hours, ..., stages="", measured="")`
  delegates to `Pipeline(...).dry_run(overrides=...)`; choose just the stages
  relevant to this question. `measured="docking=0.52"` is a format example, not a
  justified value for another target. Predicted retention/cost depends on these
  assumptions and does not reserve resources.
- `etalon_tune_screen(..., established="")` uses `etalon.tuning.advise.advise`.
  It compares proposed changes under modelled gains and separates unestablished
  preconditions/unresolvable effects. Do not mark a precondition established only
  to obtain a recommendation; uncertainty can support stopping the comparison.
- `etalon_campaign_plan(pool_size=1000, batch_size=100, screen_devices=1,
  generation_devices=0, minutes_per_batch=10, unique_per_generator_hour=100,
  detect=False)` is a pure arithmetic MCP example for draining an existing pool.
  These rates are synthetic. With generation stopped, its screening duration is
  the relevant estimate; it does not justify restarting generation. `detect=True`
  checks machine device count, not allocation ownership or throughput validity.

The resource-balance arithmetic currently lives in the MCP `sweep` module; do not
invent a same-named SDK function or duplicate that calculation to add an API.
Existing tests are `test_optional_campaign_methods.py`, `test_tuning.py` and the
planning/productivity cases in `test_sweep.py`. Actual execution adapters remain
separate; none of these estimates dispatches the returned proposal.
