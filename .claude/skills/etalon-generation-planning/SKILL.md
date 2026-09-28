---
name: etalon-generation-planning
description: Choose, size, run and retire structure-based generative models that fill a screening pool. Use when planning which of PRISM's generators to run, how many GPUs each gets, why one delivers almost nothing, whether a model has exhausted its chemical space, or whether one generator is genuinely outproducing another. Covers the pocket-box trap, uniqueness decay as a stopping rule, and why hit share cannot decide between two decent models.
---

# Choosing and retiring generators

Five models producing 200,000 molecules each is a decision about where to spend a GPU-week, and it is
normally made once from published throughput figures. This file is about making it from measurements,
because three of the four decisions below cannot be made from a paper.

## Measure on the real pocket, at production batch size, before committing

A 500-molecule trial per model costs minutes and changes the plan. Measured on SND1:

| model | delivered / requested | mol/s per GPU | 200k would take |
|---|---|---|---|
| PocketXMol | 455 / 500 | 4.14 | 13.4 h |
| MolCRAFT | 497 / 500 | 3.40 | 16.3 h |
| DiffSBDD | 498 / 500 | 3.19 | 17.4 h |
| FLOWR | 200 / 200 | 1.13 | 49 h |
| Pocket2Mol | 72 / 200 | 0.21 | 264 h |
| TargetDiff | **2 / 100** | 0.003 | not viable |

### The trap: that last column is about the box, not the model

TargetDiff returned **2 of 100 on a 28 Å pocket box and 92 of 100 on a 22 Å one**. Pocket2Mol: 72/200
and 95/100. PocketXMol was indifferent to box size.

Read as a model ranking, the table retires two usable models. Read correctly, it says the box is wrong
for them. `etalon_generation_productivity` reports this as `viable: false` and names the remedy —
*shrink the box and re-measure* — rather than condemning the model, and it judges on the confidence
interval's upper bound so that a model which *might* be delivering acceptably is not retired before the
cheap fix has been tried.

So: **`pocket` labels the conditioning geometry including the box**, and viability is a property of the
model and the box together. A campaign screening two pockets needs a profile per pocket.

### Throughput alone does not decide

FLOWR is the slowest viable model in that table — 49 h for 200k, four times PocketXMol — and it produced
**112 of 294 hits from 163,191 molecules** while PocketXMol produced 7 from 251,113. A plan that
allocated GPUs by mol/s would have been backwards.

## Retiring a model: uniqueness, not molecule count

A generator conditioned on one reference ligand eventually resamples the space it already covered. When
it does, a GPU-hour buys duplicates. Measured over one 44-hour campaign:

| model | delivered | unique | uniqueness | cost per screenable molecule |
|---|---|---|---|---|
| FLOWR | ~187,000 | 163,191 | **87%**, flat across 39 chunks | 1.15× |
| MolCRAFT | 219,492 | 76,027 | **32%**, falling to 24% | 3.1× → 4.2× |

MolCRAFT was not broken. It had finished — and nobody noticed for hours, because nothing was watching.

```
etalon_generation_productivity(loops_json=[{"tag": "flowr_b", "model": "flowr",
    "pocket": "pocketA_22A", "chunks": [{"requested": 5000, "delivered": 4840,
    "unique": 4210, "seconds": 3700}, ...]}], cost_ceiling=2.0)
```

`unique` is new-to-the-library **after global deduplication**, so it comes from the sweep's pool, not
from the generator: only the pool knows what the library already held.

The threshold is a **cost multiplier**, not a uniqueness floor, because that is the form the decision
takes. At uniqueness *u* you pay 1/*u* GPU-hours per screenable molecule, and the question is never "is
32% low" but "is 3.1× worth paying here". A floor answers the wrong question with a number that looks
objective.

Two guards on the verdict, both learned the hard way:

- **Three chunks minimum.** A sampler discarding its own reconstruction failures produces an empty chunk
  routinely. One campaign's generation loop aborted on the first empty chunk and killed a working model;
  the fix was to require five consecutive failures.
- **Judge the tail, not the lifetime.** MolCRAFT's lifetime uniqueness was 32%; its last six chunks were
  24%, and the second number is what the next chunk will cost. Use `window(chunks, keep=6)`.

`trend` distinguishes a model that has plateaued at a usable rate from one still losing ground. FLOWR
was flat near 0.87 for 39 chunks; MolCRAFT fell steadily. Only the second is a reason to plan a
replacement.

## Do not reallocate on hit counts

This is the mistake to avoid, and it was made twice in one campaign in opposite directions:

- At 15:45 a model was stopped on **unique molecules per GPU-hour**. Wrong metric — molecules are not
  the product.
- At 18:35 it was reinstated on **hits per batch**. Also wrong, for a reason that took the full dataset
  to see.

`findings/0005`: at realistic counts, a model's share of the final hits distinguishes **none** of five
models from each other. Four of ten hits carries a 95% interval from 17% to 69%.

Run the final SND1 numbers through `etalon.generate.audit.compare`, which requires disjoint Wilson
intervals before it will call a comparison decidable:

| model | hits per 100k | 95% CI |
|---|---|---|
| flowr | 68.6 | [57.0, 82.6] |
| flowr_e | 66.7 | [48.6, 91.5] |
| flowr_d | 65.4 | [48.0, 89.0] |
| flowr_c | 61.8 | [44.9, 85.2] |
| flowr_b | 59.4 | [43.1, 81.8] |
| molcraft | 21.0 | [13.0, 34.2] |
| pocketxmol_pocketA | 3.9 | [1.9, 7.9] |
| pocketxmol | 2.8 | [1.4, 5.8] |
| diffsbdd | 0.0 | [0.0, 3.0] |
| pocket2mol | 0.0 | [0.0, 24.9] |

| comparison | decidable? |
|---|---|
| FLOWR vs MolCRAFT (3×) | **yes** |
| MolCRAFT vs PocketXMol (7×) | **yes** |
| five FLOWR loops against each other | no — intervals overlap |
| PocketXMol vs DiffSBDD (2.8 vs 0.0) | no — intervals overlap |

So hit rate decides between **families**, where the differences are order-of-magnitude. It cannot decide
**within** a family, and throughput must. That is the division `etalon_generation_productivity`
implements and `etalon.generate.audit.compare` enforces.

Note the last row. "DiffSBDD contributed zero hits and PocketXMol contributed seven" is true and is
*not* a supported ranking of the two — both are decisively worse than MolCRAFT, and indistinguishable
from each other. Pocket2Mol's interval reaches 24.9 on 15,447 molecules: it was dropped for throughput
(264 h for 200k), not for hit rate, and saying otherwise over-reads the data.

## When a generator's molecules are the wrong shape

DiffSBDD produced **fragments, not ligands**: heavy-atom median 13, against a 22-heavy-atom co-crystal
reference and 26 for molecules that passed every gate. It supplied 34% of one campaign's library and 0
of 294 hits — and while it ran, it was occupying screening capacity with molecules that could not pass a
ligand-efficiency gate.

Check this before scaling a model up, not after: compare the generator's heavy-atom distribution against
the reference ligand's. PRISM's DiffSBDD adapter accepts `--num-nodes-lig`, which pins the sampled size
to the reference's heavy-atom count instead of using the model's learned size distribution. That is the
fix, and it was not applied in that campaign only because screening capacity was the bottleneck at the
time.

## Running the loops: three operational facts

**GPU numbering is not uniform across PRISM's generators.** The `flowr`, `molcraft` and `diffsbdd`
wrappers *override* an external `CUDA_VISIBLE_DEVICES` and treat `--device cuda:N` as the physical
index; `targetdiff`, `pocket2mol` and `pocketxmol` respect the mask. Set no external mask and pass
physical indices everywhere.

**DiffSBDD runs inside MolCRAFT's environment.** Killing `prism-gen-molcraft` processes kills DiffSBDD
too.

**A chunk outliving its loop is not a failure.** A generation loop's shell can die while its worker keeps
computing; the worker finishes and writes its manifest normally. What is lost is only the *next* chunk.
Distinguish "stopped" from "computing, with nothing queued behind it" before restarting anything, and
see `etalon-campaign-monitoring` for the shapes.

**Seeds must differ per loop.** Running one model on five GPUs with one seed buys five copies. Derive the
seed from the device index and the chunk number.

## Related

- `etalon-screening-sweep` — the pool these generators fill, and the `source` attribution that survives
  to the shortlist.
- `etalon-campaign-monitoring` — telling a consolidating chunk from a dead one.
- `etalon-gate-calibration` — why a fragment-producing generator fails a size gate rather than the gate
  being wrong.
