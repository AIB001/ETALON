# Improve normal execution when its cost matters

Use normal status/results, not a separate fixed Probe. ETALON screen progress
includes `execution_feedback`: phase, actual completed rows, new-row throughput,
sample age, requested/effective concurrency and a stable observation key. Read
the scientific task and remaining work alongside it. A pool width is not measured
CPU occupancy, and a stale snapshot or single busy process does not prove a stall.
Small remaining workloads, merge phases, startup and dependency waits may explain
low utilization. Missing telemetry is unknown, not proof of serial execution.

For large jobs, a first representative throughput observation and a new-phase
observation are required parts of ordinary continuation, not a fixed Probe stage.
`resource_review` uses the existing feedback key; its default large-population
hint is 100,000 inputs, and smaller tasks with a concrete discrepancy also qualify.
This is a host continuation requirement, not a compute gate. Keep one saved
observation/disposition per material condition and let the existing handle wait
between them. For pipelined stages, `effective_workers` counts producer lanes;
`cpu_postprocess_workers` is the separate CPU pool. Read quality-row throughput,
producer/chunk reuse and remaining chunks before interpreting a GPU-idle snapshot.

## Match the configuration to the available resources

Read the relevant resource envelope once: allowed CPU affinity and container CPU
quota, free memory, permitted GPU IDs and current occupancy; storage throughput
when I/O is implicated. Hardware totals are not this task's allocation. Preserve
other jobs and their source environments. Record unavailable limits as unknown.
Use existing system/runner facts rather than adding a resource scheduler.

Combine that envelope with the workload: independent batch count, per-worker
memory, native-library thread count, phase timing and remaining work. A CPU pool
multiplied by BLAS/OpenMP threads can oversubscribe the same cores. Eight free
GPUs cannot speed up serial chemistry preparation or CPU pose checks. The useful
change may be parallel mapping plus ordered merge, overlapping independent CPU
and GPU phases, balancing existing shards, or consuming a saved intermediate.
Select a bounded candidate; do not sweep every knob or assign one agent per GPU.

Distinguish execution settings from scientific changes. Workers, queue bounds and
thread limits can be execution settings; batch membership, seeds, sampling and
early termination may change the scientific computation. Verify actual outputs
and required checks, not just accepted counts. Reuse a measured configuration in
its applicable workload/environment; reconsider it after a material change, not
on every poll. Small workloads may favor the original serial path.

## Wait once and carry the handle forward

Prefer the existing host background task and its completion notification; save
its handle and pending successor in the task entry. A new chat reads this handle
and consumes finished outputs before considering any new computation. Do not
start another shell `until` loop for the same run and completion condition.

If no reusable notification exists, `etalon_wait_register(declaration, workspace)`
shares bounded observations of the existing task handle, without another worker.
The declaration is `etalon-host-wait/1`: `checks`, `wake_when`, `poll_seconds`,
`max_checks`. A `native_attempt` check binds `path`, `request_id`, `attempt_id`;
a `json_status` check names an immutable run-specific `path`, top-level `field`,
and disjoint `running`/`ready`/`failed` values. Prefer an exact attempt identity.
Read its returned handle with `etalon_action_result`; repeated registration in
the same workspace keeps the first policy. Concurrent readers share the snapshot;
only the first read after the saved interval refreshes the original task's state.
Terminal results are reused without another source check. Choose an interval
suited to phase duration, rather than model calls at each progress tick.
This does not itself notify/wake Claude or resume science:
use one host background subscription or the existing host lifecycle for that.
The lifecycle adapter accepts the saved handle itself as its turn's `wait.json`;
its waiting loop then shares these observations with other readers. With no
subscribers, no observation work runs; the scientific task continues independently.
Keep stage completion and whole-run completion distinct. A read error, exhausted
bound requires reconciliation, not another scientific launch.
Record the next action in the existing checkpoint; a wait result only supplies
a completion signal, not a scientific quality verdict.

For mixed CPU/GPU stages, read `worker_phases` and the bounded worker sample.
A declared GPU lane is not an active GPU call: preparation, pose relaxation,
checking and publication consume CPU inside the same scientific stage. Compare
the number and size of actual batches, not only requested workers. Preserve
engine batch membership and seeded calls when testing execution-only changes;
retain the required quality checks and count only checked batches as completed.

Distinguish a property of the problem from a property of today's implementation.
Identify what must be coordinated and what could vary independently, or what
already-computed work can be reused. Candidate changes can concern algorithms,
representation, batching, data movement, parallel work, or reuse; select the one
supported by the observed bottleneck. More workers is not itself a benefit.
Keep identity semantics, rejected cases and the required scientific outputs.
Reuse existing execution, cache and checkpoint interfaces when they fit. If an
exploratory candidate omits recovery or telemetry, name that limitation separately
from its measured speedup; a fast isolated candidate is not a deployment-ready replacement.

Measure a bounded, representative comparison in an independent version. Include
ingestion, computation, merge, output verification/publication and process startup
when relevant. Charge diagnosis, agent/model work, candidate development, failed
attempts and evaluation as one-time costs. Record unavailable token or compute
telemetry as unknown; do not turn per-call speedup into a full-cost claim. Do not
sum overlapping wall intervals as elapsed time.

Two distinct choices follow:

- **Current execution:** compare remaining development + transition + candidate
  work still required against the old job's remaining time. Account for which
  completed outputs/checkpoints can actually be reused. Spent time is reported,
  not counted twice. An active job can remain on its existing version.
- **Later use:** compare one-time development/trial/rollout cost plus repeated
  candidate executions with the same future work using the baseline. Name the
  workload and quality scope; small batches may never amortize an improvement.

`etalon.economics.execution.compare_execution_costs` computes these quantities;
its positive savings are arithmetic, not adoption authority or a quality verdict.
The host may trial, use locally, adopt for later work, retain or defer. Unknown
payoff can justify a small informative attempt; it need not justify interrupting
valuable running work.

Persist the selected feedback and comparison as ordinary evidence attached to the
existing research note/step. For a tracked revision, use `build_revision_context`,
`register_opportunity`, MethodVersion/MethodUse and existing evaluation/disposition
interfaces. Reuse the saved opportunity or note when only its progress timestamp
changes; `observation_key` groups a condition, but is not a new execution receipt.
Record what would warrant reconsideration: materially different workload,
input/implementation, observed scaling or a changed recovery capability. Neither
a new chat nor an unchanged status poll warrants another proposal.

Keep the task's measured timings and applicability in memory. Keep this procedure
in the skill. Continue the scientific work after retaining or deferring a change.
