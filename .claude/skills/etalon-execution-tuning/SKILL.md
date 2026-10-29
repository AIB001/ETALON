---
name: etalon-execution-tuning
description: Improve the execution speed or resource use of an ETALON scientific task by diagnosing the limiting CPU, GPU, I/O, batching, or reuse path without changing its scientific objective.
---

# Tune scientific execution

Use when an active or planned computation has consequential waiting, low throughput,
repeated work, or a mismatch between requested and effective concurrency. A declared
worker count is not measured occupancy; inspect the current phase and actual work
before choosing a change. Status may be incomplete on older runtimes: use available
runner receipts and system observations and keep unavailable progress unknown.

For a genuinely large run, inspect actual throughput after the first representative
completed batch and after a material phase/resource change before leaving it on a
long wait. This observation is required; optimization is not. Read the existing
`execution_feedback.resource_review` and save the observation key and decision
in the task's existing note/checkpoint. Reuse that decision while conditions are
unchanged. Missing telemetry calls for a bounded system sample, not perpetual polling.
Use a native background notification/handle for the next observation and successor;
do not wait for the whole million-row stage to finish before noticing serial work.

Preserve typed intermediate artifacts at stage handoffs. For registered parents,
use `source.registered_parents` with `artifact_id`, optional `selection_path` and
`selection_sha256` (CSV/TSV/Parquet `parent_id`), and `standardize: null` in a cascade.
This selects in source-bundle order, retains the selected mappings and all original
rejection decisions with their original scope. Do not round-trip through raw SMILES.
For prepared geometry, replace only the preparation backend with
`docking.registered_conformers@0.1.0`; supply the source workspace `path`, geometry
`artifact_id` and the requested original preparation settings. The adapter selects
by incoming parent ID and verifies the complete parent rows and method/toolkit
identity. Missing outcomes or method differences require an explicit new decision;
stored `EMBED_FAILED`/`MINIMIZE_FAILED` outcomes are retained, not silently retried.

At a completed cascade boundary, use
[prepare_screen_continuation.py](scripts/prepare_screen_continuation.py) with
`--workspace`, `--completed-run`, `--next-config`, `--output-dir`, or call
`etalon_screen_prepare_continuation`. It prepares the existing cascade interfaces
with registered subsets and preserves the requested docking backend. An explicit
`--docking-backend docking.unidock@0.4.0` selects the pipelined version; the agent
may select another compatible version or change the workflow. The preparation
never launches or stops work, changes thresholds, or alters active run bindings.
Repeated preparation with the same inputs returns the same files. Use the returned
config/library with the existing screen plan/submit/CLI and a fresh downstream run
ID; keep the upstream owner and completion handle. Coordinate any old auto-launcher
before downstream submission so two owners cannot start the same stage. New native
search settings/poses still need their own quality checks. Existing quality
checkpoints are reusable only for the same stage identity and raw pose inputs.
For Uni-Dock v0.4, `batch_size` controls native GPU calls; `quality_chunk_rows`
controls CPU parent groups independently. Keep all poses of a parent together.
Use one shared CPU pool, not nested per-batch pools; produced batches and checked
chunks have separate recoverable checkpoints. Old explicit plugin versions remain valid.

Read the task's accessible CPU affinity/quota, free memory, permitted GPU lanes,
current occupancy, independent batches, native thread limits, and remaining work
when relevant. Avoid oversubscription and preserve other running tasks. A free GPU
does not accelerate serial CPU work. Low utilization alone does not require action.

When execution is substantial, actively inspect progress early and at meaningful
phase changes; a background wait is not a substitute for assessing throughput.
Separate CPU concurrency, accelerator selection, and CPU postprocessing. Treat
observations and prior tuning as suggestions: question plugin defaults, adjust
parameters or implementation, select another method, or retain the current path.
The host retains final choice; no resource hint is a gate. Distinguish the planned
shard total from directories created so far; unknown is not completion. Resource
tuning, method changes and workflow recomposition are distinct improvement options.

Identify whether work is independent, requires global coordination, or already
exists in a compatible artifact. Consider parallel maps with an ordered merge,
batch size and queue tuning, overlapping GPU production with CPU postprocessing,
and reuse of completed intermediates. Keep the known execution or no-change path
available; more workers are not a result. Select a small informative comparison
when its expected value warrants it; do not add a fixed Probe or repeat a known job.

Keep the required scientific inputs, output identities and quality checks. A seed,
batch membership, sampling or early-stop change may alter results. Compare relevant
outputs and complete elapsed cost, including startup, data movement, merge, checks
and publication. State the tested scope and any unknowns. CPU-seconds, token cost
and full-task benefit are separate from a stage wall-time improvement.

For an active run compare remaining old time with development, transition and the
new work still required; spent time is not counted twice. Preserve its bound source,
handle, completed artifacts, and ongoing owner. Only reuse a checkpoint or original
when the existing interface confirms compatibility; do not treat temporary files
or agent memory as a restartable checkpoint. A different configuration may be best
for later tasks even when the current run should continue unchanged.

Use the existing execution/status, method and research publication interfaces
when available. Save the problem, candidate, actual configuration, observed outcome,
cost, applicability and next useful action in the current task's research note or
step. Record failures and no-change as such; do not create a second research ledger
or mandatory review. Revisit when the workload, evidence or resource situation changes.
