# Plan one batch or read one existing result

These calls are independent. Use existing files and the exact workspace/run
identity; replace the example paths. No generator, active campaign, sweep ledger
or method record is required.

```python
from etalon.screening import plan_screen

plan = plan_screen("/absolute/config.json", "/absolute/library.csv",
                   "/absolute/screen-workspace")
print(plan["plan_id"], plan["plan"], plan["cost"])
```

Planning compiles dependencies and can initialize the local workspace/revision;
it does not execute stages, reserve money or prove backend runtime readiness.
Keep the plan's bound inputs if subsequently submitting. A dry run is not a
scientific screening result, and copied plan IDs do not authorize execution.

For an existing direct `Screen.run` result, avoid constructing a fresh writable
runner merely to inspect it:

```python
from etalon.boundary.screen import Screen

workspace, run_id = "/absolute/screen-workspace", "existing-run"
result = Screen.read_state(workspace, run_id)
if result is None:
    print("unknown run; stop and check the supplied identity")
else:
    print(result.as_dict())
    print(Screen.read_progress(workspace, run_id))
    if result.terminal and result.outcome == "exhausted":
        print("valid zero-survivor result; stop without retry")
```

Detached `etalon_screen_submit` jobs additionally have a job journal; read them
with `etalon.screening.screen_status(workspace, run_id)`. A directly run Screen
does not necessarily have that journal. Neither status entry dispatches or
recovers work. A raw FAILED status with recognized exhaustion may be a valid
zero-survivor outcome; arbitrary input/engine failures are not exhaustion.

For verified stage rows, `Screen.open_existing(workspace).read(artifact_id,
contract_id="property/v1")` checks the stored artifact. Use the actual contract
and artifact returned by that stage. `Screen.open_existing` refuses writes;
missing or incompatible workspace contents are not repaired by a read.

The repository's `tests/test_function_capabilities.py` contains the already
implemented synthetic CPU two-molecule screen, zero-survivor stop and changed
future revision examples. It is engineering evidence, not an affinity claim;
the direct Screen CPU work is not the detached POSIX worker path.
