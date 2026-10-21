# Caller-defined configuration, shortlist export and sweep operations

## Compose only the requested components

Use `etalon.campaign.design.catalogue()` to inspect installed component contracts
when needed. If the component is known, call it directly. This synthetic CPU
example saves a configuration without executing it:

```python
from pathlib import Path
from etalon.campaign.design import component, compose
from etalon.runtime.artifacts import configure

configuration = compose("properties-only", [{"id": "measure", "title": "CPU properties",
    "criteria": [component("properties", "features.rdkit_properties@0.1.0")]}])
saved = configure(Path("/absolute/new-config-workspace"), configuration)
print(saved["config_path"])
```

MCP `etalon_screen_configure(workspace, configuration)` uses the same `configure`
entry. This writes immutable configuration bytes; it is not a status read.
`component/compose` add the required I/O/registration, not a hidden sequence of
scientific gates. Missing dependencies or ambiguous producers are errors to
correct, not reasons to reinstate the full example funnel. Plan/check only the
backends needed by the chosen configuration.

## Export an already completed shortlist

`etalon_screen_export(workspace, run_id, output)` delegates to
`Screen(workspace).export_shortlist(run_id, output)`. It verifies the completed
run's final exporter artifact and writes a **new** SDF/SMILES output plus available
docking sidecars. Supply an absolute unused output path. A run without the final
exporter stage cannot be exported this way; retain that refusal, choose a future
configuration if needed, and do not relabel intermediate rows as a final shortlist.
An identity SDF is not an MD-ready complex pose. For sourcing of an exported SDF,
the existing MolQuarry compound-sourcing method is optional and independently
bounded by the current task's acquisition authority.

## Choose one operation on a real sweep

The SDK is `etalon.campaign.sweep.Sweep(screen, ledger, pool, *, batch_size, gate,
prefix)` with an existing `Screen` and `etalon.campaign.ledger.Ledger` object. Constructing
it opens/migrates writable pool state; use `read_sweep_status(pool)` for inspection.
MCP operations use absolute `workspace`, `pool` and `ledger` paths. No fixed
sequence is required merely to ask about an existing batch.

| MCP | SDK / needed input | Effect and stop boundary |
| --- | --- | --- |
| `etalon_sweep_admit` | `Sweep.admit((key, smiles, source) for ...)` | Writes pool candidates and deduplicates globally. Fix source identity policy first; duplicate count is not a new molecule. |
| `etalon_sweep_emit` | `Sweep.emit(revision_id=..., flush=False)` | Carves batches under a valid revision-bound gate token. A refusal is not permission to drop the gate; `flush=True` changes the final-batch denominator. |
| `etalon_sweep_claim` | `Sweep.claim(batch_id, by=...)`, then `library_rows(...)` | Reserves one batch and writes its library. `by` is nonempty. In MCP supply `library_dir` or `library_path`, not both; content-addressed `library_dir` supports reuse. A write failure can leave a named reservation to inspect. |
| `etalon_sweep_record` | `Sweep.record(batch_id, ScreenResult)` | Appends a terminal outcome and ledger entry; committed/exhausted/failed stay distinct. Unfinished or unknown run is not recordable success. |
| `etalon_sweep_recover` | `Sweep.recover(abandoned=())` | Mutates unresolved batch state from durable records. It can record/requeue, keep running, or abandon explicitly named batches; it is not a read-only monitor and never establishes a stopped child merely from missing PID. |
| `etalon_sweep_status` | `read_sweep_status(pool)` | Reads pool/status without creating schema or invoking a driver; reports source-revision comparability. |
| `etalon_sweep_progress` | `Screen.read_progress(workspace, run_id)` | Reads stage progress; does not submit, recover or release a reservation. |

Batch run digests also depend on the library, so two batches can have different
revision IDs under the same funnel. `Sweep.record` is not an automatic proof of
cross-batch protocol equivalence. The existing process driver checks configuration
against a fixed reference library; callers outside it must retain and compare the
actual frozen configuration/input identity for their intended comparison. Do not
compare arbitrary per-library digests as though they identify only the funnel.

Old sweep/worker reliability evidence remains in `tests/test_sweep.py` and
`tests/test_screening_service.py`; configuration independence is covered in
`tests/test_cascade_design.py`. These paths are source examples/tests, not packaged
commands. Read-only WAL/missing-path/unknown-cost cases are in
`tests/test_function_capabilities.py`. Reuse their existing evidence when unchanged;
an exposed operation is not evidence of newly rerun production or scientific work.
