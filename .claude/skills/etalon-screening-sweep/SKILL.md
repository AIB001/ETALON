---
name: etalon-screening-sweep
description: Run a large screening campaign with no simulation stage — a generated library of 10^5 to 10^7 molecules swept through a MolCascade cascade. Use for throughput-limited screening where generation and screening overlap, batches are supervised across many GPUs, and no single measurement is worth authorising. Covers gate authorization, batch reservations, zero-hit outcomes, crash recovery and revision provenance. For the regime where each measurement costs GPU-hours, use etalon-campaign instead.
---

# Sweeping a large library, with no expensive stage

You are screening a pool too large to look at, through a funnel whose end-to-end survival is a
fraction of a percent, on a machine you share. This is not a smaller version of the campaign in
`etalon-campaign`. That one protects each expensive measurement; here there are no expensive
measurements, and everything that guards them is inert.

Read this whole file before the first `etalon_sweep_*` call. It is short, and every paragraph in it
is a thing that went wrong in a real 1,056,280-molecule campaign.

## Which regime you are in

| | expensive-stage-limited | **throughput-limited (this file)** |
|---|---|---|
| unit of work | one molecule through MD | one batch of ~20,000 through a cascade |
| what is scarce | GPU-hours per measurement | batches per hour, and your attention |
| irreversible act | a GPU-hour on a molecule that is a drawing | **a miscalibrated gate deleting the interesting chemistry, a million times, silently** |
| the guard | `etalon_authorize_spend` | `etalon_authorize_gates` |
| selection policy | choose the next 10 to measure | none — every molecule in the pool is screened |
| tool family | `etalon_active_*`, `etalon_check_handoff` | `etalon_sweep_*` |

If you are about to call `etalon_check_handoff` or `etalon_authorize_spend` in a campaign with no MD
stage, stop: they are guarding a path nothing takes. The check you need is the gate one.

## The one rule

**A gate configuration may not be applied at scale until a panel of known binders has shown it does
not delete them.** Everything else here is bookkeeping; this is the part that decides whether the
campaign returns anything.

The shipped defaults are not safe. Measured on SND1 with MolCascade's default docking thresholds
(`Uni-Dock <= -8.5`, `KarmaDock >= 40`):

| molecule | Uni-Dock | KarmaDock | known activity |
|---|---|---|---|
| BindingDB compound | −7.78 | 18.56 | Kd 23.6 µM |
| BindingDB compound | −7.76 | 17.26 | **Kd 570 nM** |
| BindingDB compound | −7.21 | 14.20 | Kd 279 µM |
| imatinib | −6.57 | 14.24 | *not* an SND1 binder |
| C-26-A6 | −6.05 | 14.89 | co-crystal 7KNX |
| C-26-A2 | −5.90 | 15.17 | co-crystal 7KNW |
| aspirin | −5.07 | 14.17 | *not* an SND1 binder |
| caffeine | −4.85 | 16.61 | *not* an SND1 binder |

The defaults reject **all eight, including both co-crystal ligands**. A campaign that applied them
unexamined produced an empty shortlist with every log line reading SUCCEEDED, and the first screening
test did exactly that.

The table carries a second finding, and you must report it: **imatinib outscores both co-crystal
ligands.** On this pocket the score cannot order the known binders, so it is a usable filter and an
unusable ranking. `etalon_calibrate_gates` computes this as `rankable_engines`; when it comes back
empty, the shortlist's score column must be described as a filter and never as potency.

## The shape of the whole thing

You do five things. A supervisor does the other five hundred.

```
   你(LLM)                              Supervisor（守护循环）
 ─────────────                        ────────────────────────
 1. 建面板                    ┐
 2. calibrate_gates           │  科学判断      ingest 完成的 chunk
 3. authorize_gates           │  只做一次  →   到水位线就发批
 4. devices + campaign_plan   │                批次交给空闲设备
 5. 启动 supervisor           ┘                终态 run 记进 ledger
                                               崩溃留下的认领自愈
 ── 之后每隔 15–30 分钟 ──                     生成循环轮转 chunk
 读 status，只在科学信号上介入                 退役 loop 的卡转给筛选
```

The last line is the one thing in that column that is off by default; see
`migrate_retired_devices` below.

One campaign was supervised by a person reading a status script every five minutes for 44 hours:
about 500 readings, **fewer than ten of which needed a decision**. `Supervisor.tick()` is the other
490. It is a single non-blocking pass — ingest, emit, claim, screen, record, recover, rotate
generation — and it rebuilds everything from the pool, the ledger and the chunk manifests on every
call, so killing the process and starting another one loses nothing.

```python
from etalon.campaign import (
    Generator, MolCascadeScreening, Pocket, PrismGeneration, Supervisor, Sweep,
)

supervisor = Supervisor(
    sweep,                                  # pool + ledger + the gate token
    revision_id=plan.revision_id,           # from etalon_screen_plan
    workspace="/abs/campaign/ws",
    generators=[Generator(tag="flowr_a", model="flowr", pocket=pocket,
                          device="cuda:0", protein=receptor,
                          output_root=gen_root, generation_config=cfg,
                          chunk=5000, total=200_000)],
    screen_devices=("cuda:5", "cuda:6", "cuda:7"),
    generation=PrismGeneration(),
    screen=MolCascadeScreening(screen, cascade, plan.revision_id, target=target),
    retire=lambda tag: tag in retired_by_productivity,   # your scientific rule
    migrate_retired_devices=True,           # a stopped loop's card joins the screeners
)
supervisor.run(interval=60)                 # returns when the campaign is complete
```

`migrate_retired_devices` is off by default and you almost always want it on. `screen_devices` was
fixed for the supervisor's life, so a campaign that retired four of five generation loops finished on
the screeners it started with while four cards sat idle. With it set, a device joins the pool on the
pass after its last generator stops — and only when **every** generator on that card has stopped,
since two loops on one card is a normal configuration.

It is off by default because of the machine rather than the science: on a shared host, cards you gave
to generation may be owed back when generation ends, and a supervisor that keeps them competes with
whoever was waiting. When off, the first pass that finds an idle card says so in `notes` once. It
never hands a device back — restart the supervisor with the lists it should have instead.

`retire` is the seam where your judgement enters the loop. The supervisor stops a generator on two
mechanical conditions — its molecule target, and five consecutive barren chunks — and on nothing else.
Exhaustion is a scientific call; feed it `etalon_generation_productivity`'s verdict.

## The order

### 0. Measure the machine, before sizing anything against it

```
etalon_devices                              # what cards exist, their memory, the cores
etalon_campaign_plan(pool_size=..., screen_devices=..., generation_devices=..., detect=true)
```

`etalon_campaign_plan` takes the device counts as integers and its shipped defaults — 3 screeners, 5
generators — describe the one campaign they were measured on. With `detect=true` it measures the host
and **refuses a plan asking for more devices than exist**, which is the one error here that no care in
the rates can catch. `recommended_split` names the balance point for the total.

Both shipped rates (95 minutes a batch, 4,100 unique molecules a generator-hour) were measured once
and **neither records the device it was measured on**, so detection cannot tell you they still hold.
The result says which of its numbers are yours and which are borrowed, in `basis`. Re-measure them on
your own cascade and target before trusting a split computed from them.

**And the plan counts devices, while the docking tier is CPU-bound.** The tier does not only run an
engine: PoseBusters checks every pose it produces, in Python, row by row, holding the GIL. Measured on
ALK2 — eight batches all at `docking_score`, every card at 0% across a three-sample peak, the campaign
holding 6.2 cores of a 96-core machine, and `py-spy` showing the main thread in
`posebusters/modules/distance_geometry.py` under `pandas.apply`. Adding a ninth GPU to that would have
bought nothing.

Do not answer it by turning pose checking off. On one measured shard, 278 poses that all passed the
score gate had **22.7% passing the minimum-distance-to-protein check**, and the score cannot see the
difference: its correlation with the closest protein contact is +0.014, and a pose that passes every
check has a median score of 44.4 against 44.5 for one that fails. The check is the only thing standing
between the shortlist and ligands packed into the receptor.

So when a sweep is slower than the plan says, measure which resource is actually scarce before moving
any. `etalon_campaign_plan` is arithmetic over device counts and cannot tell you this one.

### 1. Calibrate, before anything else

Screen a panel of known binders **and declared negative controls** through the exact cascade you
intend to use, with `--id-column` so members are traceable by name. Then:

```
etalon_calibrate_gates(workspace=..., run_id=<panel run>, panel_json=[
  {"parent_id": "C-26-A2", "known_active": true,  "evidence": "co-crystal 7KNW"},
  {"parent_id": "imatinib", "known_active": false, "evidence": "negative control"}, ...])
```

The panel run may come back `exhausted` — every member gated out. **That is the most informative
case**, not a failure: the docking scores are committed regardless, and they are what say how far the
threshold sits from the known binders.

Negative controls are not optional. A panel drawn only from binders cannot tell a discriminating gate
from one that keeps everything, and `known_active` is refused unless it is an explicit boolean for
every member.

### 2. Authorize, and do not route around the refusal

```
etalon_authorize_gates(...) -> {"gate": {...}}
```

If it refuses, it names which known actives the gate deleted. **Widen that gate and re-screen the
panel.** Do not lower `required_recall` to get past it: the panel is the only evidence the threshold
has, and a campaign that discards it has an unexamined assertion applied to every molecule.

Pass the returned `gate` object verbatim as `gate_json` to `etalon_sweep_emit`.

### 3. Fill the pool and carve batches

```
etalon_sweep_admit(molecules_json=[{"key": <InChIKey>, "smiles": ..., "source": <generator tag>}, ...])
etalon_sweep_emit(revision_id=<from etalon_screen_plan>, gate_json=...)
```

Deduplication is on `key`, global and permanent. Fix the standardisation policy **before** filling the
pool: the same molecule under two policies is two keys, and the pool cannot be un-deduplicated later.

`source` is the generator tag, not the model name. Run five loops of one model on five GPUs and a
report keyed on the model merges them — hiding both the death of one loop and the evidence that their
uniqueness rates were independent.

Batches are 20,000 by default. Do not shrink them for finer overlap. Measured: three 5,000-molecule
batches carved from one campaign's tail yielded 6, 2 and 0 hits — the zero not because those molecules
were worse but because 5,000 molecules do not reliably put anything through a funnel with 0.028%
end-to-end survival. Use `flush=true` only at the very end.

### 4. Claim, screen, record — one batch at a time

```
etalon_sweep_claim(batch_id=..., by="gpu5", library_path=<absolute .csv>)
   -> screen that CSV with etalon_screen_submit / molcascade
etalon_sweep_record(batch_id=..., run_id=...)
```

The claim is a reservation recorded **before** the screen starts, which is what makes a crashed
screener's batch findable rather than merely absent. `by` must be non-empty: an anonymous reservation
cannot be recovered.

`library_path` always writes an `id` column. Without one a run records no molecule names, and per-tier
recall, per-molecule explanation and a named shortlist all have nothing to key on — MolCascade's recall
measurement refuses such a run outright rather than reporting a funnel that lost everything.

### 5. Recover, whenever anything looks stuck

```
etalon_sweep_recover()                          # ask the run record
etalon_sweep_recover(abandoned="batch_0016")    # only after you confirmed the screener is gone
```

## Three outcomes, not two

`committed` — a shortlist exists. `failed` — a defect to fix. **`exhausted`** — every molecule was
gated out, which is a *measurement*.

MolCascade signals exhaustion by raising from whichever stage first finds nothing left, and which
stage that is depends on the cascade. One campaign saw three different codes —
`EVIDENCE_GATE_EMPTY_PARENT_INPUT`, `FEATURE_EMPTY_INPUT`, `POSE_STRAIN_EMPTY_PARENT_INPUT` — and a
supervisor enumerating the two it had seen misfiled two batches as failures. ETALON now matches the
family; you do not need to know the codes. What you do need to know:

- **An exhausted batch's scores are complete.** One held 7,545 docking scores from two engines. Harvest
  them; they are the batch's entire product and the largest part of what a sweep produces.
- Never retry an exhausted batch. It did what it was asked.

## Reading progress: ask the run record, never the GPU

`etalon_sweep_progress(workspace, run_id)` returns *stage k of n*. Use it.

Both docking engines in a default cascade allocate about 25 GB and release it, so a batch at stage 6
of 43 and one at stage 29 present **identically** on the GPU. A campaign log recorded "nearly
finished" for a batch at stage 27 twice in one night from exactly that read. The same applies to
`CPU ≈ 0` — during a stage transition a live run looks idle.

## Load on a shared machine: attribute before you throttle

Before lowering any worker's CPU share, find out whose load it is:

```
ps -eo pcpu,args | awk '/molcascade/{m+=$1} /prism-gen/{g+=$1} END {printf "screen %.0f%% gen %.0f%%\n", m, g}'
vmstat 1 2 | tail -1   # runnable queue, which the load average is not
```

Measured, twice in one night: load average 128.9 of which a *neighbouring tenant* was 62 cores and the
campaign was 16; and load average 84 with a runnable queue of 7, the rest being D-state I/O wait.
Throttling the campaign in either case would have slowed the bottleneck and relieved nothing.

Never touch another tenant's processes.

## Provenance: one revision per campaign, or say so

`etalon_sweep_status` returns `comparable: false` when recorded batches carry more than one cascade
revision. Retuning mid-campaign is legitimate; computing enrichment across the boundary is not, and a
sweep that recorded no revision per batch could not tell — which is exactly the hole 56
hand-recorded batches had.

`etalon_sweep_record` refuses a revision that disagrees with the one the batch was carved under.

## What you may decide, and what you may not

**Decide:** batch size, how many screeners, which GPUs, when to recover, when to flush the tail.

**Do not decide:** lowering `required_recall`, screening without a gate token, widening a gate without
re-screening the panel, or reporting a score column as potency when `rankable_engines` is empty. The
first three discard the only evidence the thresholds have; the fourth is an over-claim the calibration
explicitly measured against.

**Append to the campaign's decision record, with its numbers,** anything that changes what the sweep
means: a gate threshold, the pocket, which generators feed the pool, what counts as a hit. Decide and
tell; do not decide silently.

## Related

- `etalon-gate-calibration` — the panel itself: how to build one, and what its size can support.
- `etalon-generation-planning` — choosing and retiring the generators that fill the pool.
- `etalon-campaign-monitoring` — what normal looks like over 44 hours, and the three false alarms.
- `etalon-campaign` — the other regime. Read it when an MD, MM-PBSA or FEP stage exists.
