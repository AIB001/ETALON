---
name: etalon-gate-calibration
description: Prove a screening cascade's gates do not delete known binders, before spending compute on them. Use when setting or changing any hard threshold — docking score, ADMET, pose strain, ligand efficiency, scaffold cap — or when a screen returned an empty shortlist, or when deciding whether a score column may be reported as a ranking. Covers panel construction, what a small panel can and cannot support, and the two independent verdicts.
---

# Calibrating the gates against molecules you already know about

Every hard gate in a cascade is one assertion: *molecules like this are not worth looking at*. The
cheapest way to test it is to count how many known binders it deletes. A campaign that never does this
has no evidence for the only claim its shortlist rests on.

This is a free measurement that takes one screening run of a few dozen molecules. It is the highest
value-per-minute act in a screening campaign, and skipping it is how a campaign returns nothing while
reporting success.

## The measurement that makes the case

SND1, MolCascade shipped defaults, 2026-09-25:

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

Default gate: `Uni-Dock <= -8.5` **and** `KarmaDock >= 40`. It rejects all eight, including both
co-crystal ligands — molecules whose binding is not in question because someone solved the structure.
Recalibrating against this table is what made a 294-hit result possible; the first screening test,
before it, returned zero.

## Two verdicts, and they are independent

`etalon_calibrate_gates` returns both. Never report one as if it implied the other.

### `admissible` — does any gate delete a known active?

Recall over the panel's declared actives, and the required value is **1.0**. The asymmetry is the
argument, and it is the same one `learn/admissible.py` makes about measurements:

- A gate tuned to keep every known active may pass molecules that do not bind. The cost is screening
  time on a population the next tier rejects, and it is visible in the survival rate.
- A gate that deletes one known active deletes an unknown number of unknown actives that resemble it,
  **silently, for every molecule the campaign will ever screen** — and no later measurement can
  recover them, because they were never docked.

The first error is observable. The second is invisible by construction. That is why the default is 1.0
and why lowering it is a decision to record, not a parameter to tune.

Fails closed: an unreadable panel returns `recall: null` and `admissible: false`. "We could not tell"
must not read the same as "they kept everything".

### `rankable_engines` — can the score order the panel at all?

On the table above it is **empty**. Imatinib, which does not bind SND1, outscores both co-crystal
ligands; caffeine is within 1 kcal/mol of one.

When this is empty:

- The score is a usable **filter** — "better than every known binder" is still a defensible cut.
- The score is not a **ranking**. Do not order a shortlist by it and call the order potency. Do not
  report "best hit" as "most potent".
- The ordering must come from something else. In practice that means MM-PBSA or FEP, and it means the
  screening stage's job was to produce a queue, not an answer.

This is not a defect in the engine. A shallow protein-protein interface is a known-hard case for
empirical scoring functions, and the point of measuring it is to stop the campaign over-reading its own
numbers.

## Building the panel

**Include negative controls.** A panel drawn only from binders cannot distinguish a discriminating gate
from one that keeps everything. `known_active` is refused unless it is an explicit boolean for every
member, precisely so that omitting the negatives is impossible rather than merely discouraged.

**Carry the evidence.** A co-crystal structure and a 279 µM binding constant are both "active" and are
not the same claim. `evidence` is free text and a reader a year later has only that sentence.

**Sources, in the order worth trying:**

1. `molquarry-target-modulators` — measured activity against the target, with assay context.
2. `molquarry-structure-templates` — co-crystal ligands. These are the strongest panel members: their
   binding is structurally established, and a gate that deletes one has refuted itself.
3. `molquarry-selectivity-evidence` — compounds measured *against* the target, which are better
   negative controls than random drugs because they were actually tested.

Approved drugs from other target classes (imatinib, aspirin, caffeine) are acceptable negatives and
also weak ones: they may fail for reasons unrelated to the pocket. Measured-inactive compounds are
better when you can get them.

**Size.** Eight is enough to catch a gate that deletes everything, which is the failure that actually
happens. Thirty to fifty is enough to see a gate that deletes the weak binders only. Beyond that you
are paying for a statistic the next section says you cannot compute.

## What a panel that size cannot support

Do not compute an AUC, an enrichment factor or a Spearman correlation on a calibration panel. With
eight to fifty molecules the standard error is wider than any difference you would act on — the same
argument `campaign/pipeline.py` makes with `resolvable_spearman`, where a 231-molecule panel with 40
actives resolves 0.10 and no better.

`etalon_calibrate_gates` therefore reports counts and extremes, and exactly one boolean:

- `best_active`, `median_active`, `worst_active`, `best_inactive`
- `actives_below_best_inactive` — how many known binders an inactive beats. This is what `separates`
  turns on.
- `inactives_above_best_active` — whether a top-N cut would be contaminated. A weaker question.

The first implementation of `separates` used the second number and reported the SND1 panel as
separating, while imatinib was outscoring two of the five known binders. If you write your own version
of this check, the predicate is *every active outscores every inactive*.

## When a screen returns an empty shortlist

Calibrate before doing anything else. In order of likelihood:

1. **A gate is too strict.** The panel names which one and which actives it deleted.
2. **The batch was too small.** End-to-end survival is a fraction of a percent; 5,000 molecules
   routinely put nothing through. Three such batches yielded 6, 2 and 0 hits from the same pool.
3. **The molecules are wrong for the funnel.** A generator producing fragments — heavy-atom median 13
   against a 22-heavy-atom reference ligand — will fail a ligand-efficiency or size gate en masse. That
   is the generator's problem, not the gate's; see `etalon-generation-planning`.

An empty shortlist reported as `exhausted` is not a failure. The scores are all committed, and reading
them tells you which of the three it was.

## After it passes

`etalon_authorize_gates` mints a token bound to the compiled `revision_id` **and** to a digest of the
calibration verdict. `etalon_sweep_emit` requires it. So:

- Editing the cascade after calibrating invalidates the token, by construction rather than by
  discipline.
- The token records `SCORE_NOT_SHOWN_TO_RANK` in `unchecked` when no engine separated the panel, so a
  reader of the shortlist does not have to find the calibration to learn whether the score column means
  anything.

Record the panel and its numbers in the campaign's decision log. A threshold without the panel that
justified it is a number nobody can argue with a year later.
