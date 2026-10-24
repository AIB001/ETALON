# Optional native attempt handoff and recovery

`etalon.research.execution_recovery` is a small opt-in handoff for a host that
explicitly launches a native command. It is not a scheduler, policy, retry
service, scientific validator, or replacement for existing runtime adapters.
Use an existing adapter's native recovery when it already owns the attempt.
The host still decides whether/what to execute, freezes scientific identities,
uses the existing budget ledger, and archives observations through the existing
ResearchJournal and batch/publication interfaces.

```python
from etalon.research.execution_recovery import (
    launch_detached_attempt, inspect_attempt, recover_attempt,
)

status = launch_detached_attempt(
    "/absolute/new-attempt",
    {"request_id": "frozen-request", "attempt_id": "trial-1",
     "protocol_sha256": "host-supplied protocol identity"},
    ["/absolute/venv/bin/python", "/absolute/frozen-science.py", "run"],
    cwd="/absolute/new-science-run",
    outputs={"native_receipt": "/absolute/new-science-run/receipt.json"},
)
status = inspect_attempt("/absolute/new-attempt")
status = recover_attempt("/absolute/new-attempt")
```

An immutable intent binds request/attempt, a random launch nonce, exact argv, working directory,
required output paths, actual Python executable and recovery module bytes. The
explicit launch starts one detached worker. The worker records its own and the
native child's PID and creation identity before waiting for completion. Linux
identity uses boot ID and `/proc` start ticks; the observed command hash is
retained separately because `env`/`taskset`/`nice` can exec the scientific
program within the same process creation. Windows uses kernel creation FILETIME
and retains the observed executable path. A matching live
PID without matching creation identity is not sufficient. A controller can end
without terminating the worker's native session.

`inspect_attempt` returns one of four states:

| State | Evidence and allowed continuation |
| --- | --- |
| `running` | A recorded worker or native identity is still live. Inspect again; no restart. |
| `finished-unpublished` | Worker completion binds the native identity, exit code, stdout/stderr and every declared output or explicit missing member. Current output bytes verify. `recover_attempt` can archive them and write a receipt. |
| `published` | The completion and copied originals match the existing receipt. Repeated recovery or launch returns it; producer outputs may have disappeared after archival. |
| `unknown` | Durable intent exists, but matching live-process or completion evidence is absent. Recovery and repeated launch leave it unknown. |

The nonce and full intent digest accompany both process handles and completion;
the digest includes the complete argv (including explicit `env` assignments),
working directory, request and output inventory. Creation identity alone does
not associate a completion with a request. Older receipts without a nonce can
still be inspected and archived through their original full intent digest;
the helper does not retrofit evidence into an already executed attempt.

The intent is the no-retry boundary. A crash before the detached worker records
its identity, a dead worker with no completion, or PID reuse stays `unknown`.
Existing output files alone do not prove native completion. The helper never
infers a return code, reconstructs a missing scientific receipt, or repeats an
ambiguous command. Explicitly authorized independent repeats need a new attempt
identity/directory. The module does not kill processes.

`recover_attempt` has no execution branch. It verifies current declared output
bytes, makes durable content-addressed copies using the existing publication
primitives, and writes an immutable receipt. Missing declared outputs or a
nonzero native exit code produce `execution="failed"`; the missing inventory
and native diagnostics remain explicit. Exit zero with complete output bytes
means native execution completed, not scientific correctness, convergence or
affinity validity. Scientific status remains in the original native receipts.

The receipt records `new_execution_by_recovery=false` and
`new_scientific_charge=0`. These mean recovery did not start or charge new
science. They do not make the original work free. Native measured consumption
remains in the original receipts, worker wall time is separate, and recovery
overhead is not silently booked as zero. The existing host owns cost settlement
and deduplicates by its execution identity. `recover_attempt` does not append
journal events or move continuation entry files.

Tests actually terminate a controller while its labelled fixture native child
is running, verify the same native creation identity survives, and verify one
physical execution after repeated entry/recovery. They also test the unknown
launch window, PID identity mismatch, changed current/archive bytes, nonzero
exit, and missing output. Real scientific interruption evidence is a separate
integration result; subprocess fixtures do not establish molecular validity.
