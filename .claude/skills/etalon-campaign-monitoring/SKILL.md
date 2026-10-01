---
name: etalon-campaign-monitoring
description: Supervise a long-running ETALON campaign — hours to days, many GPUs, possibly a shared machine. Use when watching generation loops and screening batches on an interval, deciding whether something is stuck or merely between phases, recovering from a crash, or judging whether high load is yours to fix. Names the specific false alarms that waste an operator's attention and the readings that are actually authoritative.
---

# Watching a campaign for two days without acting on noise

A 44-hour campaign was checked every five minutes: roughly 500 readings, of which fewer than ten needed
an action. The skill being taught here is not vigilance. It is **knowing what normal looks like**, because
every false alarm costs either an unnecessary intervention or the credibility of the next real one.

Three shapes below account for every false alarm in that campaign. Learn them before you learn anything
else in this file.

## False alarm 1: a chunk finishing looks exactly like a chunk dying

A generation loop between chunks presents as: **CPU near 0, GPU memory released, log file 0 bytes**.
That is also what a dead worker looks like.

Encountered five times; every time it was a chunk in its CPU-side consolidation and QC phase, and PRISM
wrote the manifest a minute or two later.

The distinguishing evidence is **not** in the process table or on the GPU:

```
find gen/<tag> -name manifest.json -newer <a file from ten minutes ago>   # did it just finish?
ps -eo ppid,args | grep '[g]enerate_loop'                                 # is the loop itself alive?
```

A loop alive with no worker is between chunks. A worker alive with no loop is computing with nothing
queued behind it — real, but it means only the *next* chunk is at risk, which calls for a deferred
restart rather than an intervention now. Those two states need opposite actions, and neither is "kill it".

Corollary: **a loop disappearing is not necessarily a failure.** One model's loop "died silently" and had
in fact reached its 200,000-molecule target and exited normally. Check for the completion record before
concluding anything.

## False alarm 2: GPU memory says nothing about progress

Both docking engines in a default cascade allocate about 25 GB and release it. A batch at stage 6 of 43
and one at stage 29 are **indistinguishable** on the GPU. A campaign log recorded "nearly finished" for a
batch at stage 27 twice in one night from exactly that inference.

The authoritative reading:

```
etalon_sweep_progress(workspace, run_id)   ->  {"stage": 29, "stages": 43, "current": "docking_score_2"}
```

MolCascade writes run state as each stage commits, so this is durable, survives the process table, and
works on a run executing in another process or left by a crash. Use it for every progress question.

A related non-signal: a *single* GPU-utilisation sample of 0% during a multi-minute stage means nothing.
One engine re-initialises its model per shard, so instantaneous samples land in the gaps; counting
initialisations in the log is a better progress proxy than utilisation.

**And the inverse, which looks much more alarming than it is: a batch reported at the docking stage
may leave its GPU at 0% for ten minutes or more.** The stage has a long CPU prologue — every surviving
molecule is written to PDBQT before the engine is invoked once. Measured on ALK2: five batches all at
`docking_score`, four of their cards at 0% and 0 MiB for ten minutes while the fifth held 78.8 GB at
99%. Nothing was wrong; the four were still preparing ligands, at about 1.2 cores each.

Before concluding that batches are serialising on one card, check that each screen really holds a
distinct device — that is the failure this resembles, and it is one `ps` away:

```
for p in $(pgrep -f 'molcascade screen'); do
  tr '\0' '\n' < /proc/$p/environ | grep ^CUDA_VISIBLE_DEVICES=
done
```

Eight distinct values means eight cards, and the idle ones are working.

Two stages have long CPU prologues on a 20,000-molecule batch and are the usual cause of a long quiet
spell: `ligand_conformers` (measured: 10–30 minutes, single-threaded per batch, and `--workers` does
not shard it because MolCascade's shard count follows the visible GPU lanes — one per batch here) and
the PDBQT preparation inside `docking_score`. Neither is stuck.

## False alarm 3: load average is not your load

Before lowering any worker's CPU share, attribute the load:

```
pidstat -u 5 1               # a 5-second interval, per process
vmstat 1 2 | tail -1          # runnable queue — what the load average is not
```

**Not `ps -eo pcpu`.** That column is total CPU time over the process's whole lifetime, not a current
reading, and the two tenants on a shared host are never the same age. Measured at one instant on ALK2,
with the campaign 25 minutes into a run and the neighbour's processes days old:

| | campaign | neighbour |
|---|---|---|
| `ps -eo pcpu` | 2043% | 2210% |
| `pidstat -u 5 1` | **904%** | **4166%** |

`ps` reports a dead heat; the interval says the neighbour is 4.6x the campaign. The error is not a
constant factor and it has no reliable sign — a campaign that just left a CPU-heavy tier reads high,
a long-lived neighbour reads low — so there is no correction to apply, only a different command. The
earlier version of this file recommended `ps` for exactly this job.

Two readings from one night, both of which would have produced a wrong action:

| load average | actually |
|---|---|
| **128.9** | a neighbouring tenant at 62 cores; the campaign at 16 of 96 |
| **84** | runnable queue of **7** — the rest was D-state I/O wait from artifact writes |

In the first case throttling the campaign would have freed cores the neighbour absorbed instantly, while
slowing the campaign's own bottleneck. In the second there was no CPU contention to relieve at all.

**Load average counts runnable *and* uninterruptible-sleep processes, and every tenant's.** Attribute
first. And never touch another tenant's processes — route it to a human instead.

## What to check, and what each answer means

```
etalon_sweep_status(workspace, pool, ledger)
```

| field | act when |
|---|---|
| `batches.outstanding` | nonzero and unchanging across two checks → `etalon_sweep_recover()` |
| `batches.by_outcome.failed` | nonzero → read the run's error; a *real* failure, not an exhausted batch |
| `pool.awaiting_batch` | growing past the batch size with no emission → the gate token expired or the revision changed |
| `comparable` | `false` → the funnel changed mid-campaign; enrichment across that boundary is attributable to nothing |
| `pool.by_source` | a source flat for more than ~2 chunk-durations → check its loop with false alarm 1 |

Plus, on the generators: `etalon_generation_productivity(...)` for viability and exhaustion, and disk
free. A campaign holding raw generated molecules consumed about 76 GB in 44 hours.

## Recovery

```
etalon_sweep_recover()                             # ask the run record
etalon_sweep_recover(abandoned="batch_0016")       # only after confirming the screener is gone
```

Four actions, and the reason recovery never looks for a process:

- **`recorded`** — the run reached a terminal state, whatever became of its claimer. This is the case that
  cost a batch six hours: its screen succeeded at 17:30 and the batch sat claimed until 21:16 because
  nothing owned the reservation.
- **`running`** — left alone. A dead supervisor does not stop a committing screen, and requeueing here
  discards real work.
- **`requeued`** — claimed but no run was ever started.
- **`abandoned`** — only for batches you name. A non-terminal run whose process is gone cannot be
  distinguished from a slow one by anything durable, so it takes an assertion from someone who looked.

Two things that made hand-rolled supervision fail, for anyone still writing shell around this:

- `pgrep -f "<run id>"` matches the supervising shell itself.
- `setsid` without `--fork` does not detach when job control is active; the parent remains the calling
  shell and the worker dies with it.

The run record is right in every case the process table is wrong. Use it.

## Reporting

Report **what changed** and **what you did**, not the full reading. A status line repeated 500 times
trains the reader to stop looking.

When nothing changed, say so in one line. When something did:

- name the number that moved, and the number it moved from;
- if you acted, say what and why in one sentence;
- if you did not act on something that crossed a threshold, say why not — an unexplained non-action is
  indistinguishable from not having looked.

**Anything that changes what the campaign means goes in the decision record with its numbers**: a gate
threshold, the pocket, which generators run, what counts as a hit. Scaling a generator from one GPU to
four changes the library's composition and belongs there. Restarting a dead worker does not. Decide and
tell; never decide silently.

## Do not be the loop

If you find yourself checking every five minutes, the supervisor is not running. Start it
(`etalon-screening-sweep`) and check its report instead: `TickReport.quiet` is true for a pass that
changed nothing, which is most of them, and a supervisor that has been quiet for an hour is a
supervisor that is working.

The three false alarms above are the reason this matters. Each one cost an intervention or nearly did,
and every one of them is a question about a *mechanical* state — is the chunk finished, is the batch
progressing, is the load mine. The supervisor answers all three from durable records, on every pass,
without asking. What is left for you is the part it deliberately cannot decide: whether a generator
has exhausted its space, whether the hit rate justifies continuing, whether a threshold should move.

## Interval

Five minutes is too often for a campaign whose unit of work is 90 minutes. It produces ~500 readings for
~10 actions, and the cost is not compute — it is that the operator stops reading. Fifteen to thirty
minutes matches the chunk and batch cadence, with a tighter loop only while something is actually being
diagnosed.

Stop the monitoring when the campaign has no mutable state left. A finished sweep produces identical
readings forever.

## Related

- `etalon-screening-sweep` — the sweep being watched, and its three outcomes.
- `etalon-generation-planning` — the productivity and exhaustion verdicts.
- `etalon-gate-calibration` — what to do when the shortlist is empty.
