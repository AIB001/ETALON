---
name: etalon-campaign
description: Run a complete structure-based drug-discovery campaign end to end — generative models through screening, MD stability, and relative free energy — with a budget, a learned screen, and refusals that stop a wrong number before it becomes a recorded one. Use for any large-scale CADD campaign, virtual screening funnel design, compute-budget allocation across docking/MM-PBSA/FEP tiers, or when asked how much a screening change is worth.
---

# Running a CADD campaign with ETALON

You are driving a campaign that will spend GPU-months. The characteristic failure is not a wrong
calculation — it is a funnel whose shape nobody examined until the compute was gone, or a number that
looked fine and was about a different molecule.

This workflow is built so that **everything cheap refuses, and everything expensive is guarded by
something cheap.** You cannot confirm 750,000 actions with the operator, so the governance is not
per-action confirmation: you confirm the *plan* once, and then the refusals are automatic and you read
them.

## The one rule

**Call the free tools before the expensive ones, and read what they refuse.** A refusal always names
the remedy. If a tool refuses and you cannot see why, read `etalon://findings` — the numbers are the
argument, and a refusal quoted without its measurement looks arbitrary.

Never put a waiver in a `waived` argument on your own authority. `etalon_recommend_waiver` exists and
it deliberately cannot grant. The `waived` arguments take **granted waivers**, not fault codes —
`[{"code": ..., "reason": ..., "granted_by": ..., "expires": "YYYY-MM-DD"}]` — and a bare code is
refused. `granted_by` is a person's name; writing your own there is refused too, and the tools echo
the name back in their result so the operator can see whose it is.

## The pipeline, with what each stage costs

| stage | tool | GPU-hours per molecule | what it answers |
|---|---|---|---|
| generate | PRISM `generate_ligands` | ~0 (inference) | candidates |
| QC the output | PRISM `check_generated_ligands` | ~0 | is it a molecule |
| screen | MolCascade `screen` | 3e-7 to 6e-3 | liabilities, properties, docking |
| hand off | MolCascade handoff tier | ~0 | what a force field would receive |
| **refuse** | `etalon_check_handoff` | **0** | would the number be about this molecule |
| **authorise** | `etalon_authorize_spend` | **0** | and may it therefore be built |
| build + equilibrate | PRISM `build_system` + driver | ~24 | does the pose hold |
| **judge** | `etalon_check_stability` | **0** | what 1000 ns established |
| end-point ΔG | PRISM MM-PBSA, ×5 replicas | **10** | ranking |
| **adjudicate** | `etalon_council_adjudicate` | **0** | what the unevaluable checks came to |
| **admit** | `etalon_rule_admissible` | **0** | may this teach the screen |
| relative ΔΔG | PRISM FEP per edge | ~12 | local optimisation |
| **design first** | `etalon_design_fep_network` | **~0** | can these even be related |

Two stages are *not* in that table on purpose. **PMF by umbrella sampling** costs roughly 2.1 µs per
complex — hundreds of GPU-hours per ligand against tens for an FEP edge — and a head-to-head on PARP1
found the physical route no more accurate than the alchemical one. It answers mechanism, not rank, so
it is not a tier: spend it on the few molecules whose mechanism is the question, after the ranking is
done. And **a single MM-PBSA trajectory** is not a tier either, because it is not a measurement — see
the numbers section.

## Step 0 — check the infrastructure

```
etalon_infrastructure
```

Free. Confirms which MolCascade and which PRISM would load. Do this first in any new environment. An
editable install shadowing the vendored copy is silent: the import succeeds, the version is right, and
every number afterwards cites a commit that did not produce it.

## Step 1 — plan before generating anything

```
etalon_plan_campaign  pool=750000  budget_gpu_hours=8760  active_fraction=0.001  deliver=10
```

Free, and call it several times. Read three things from the result:

**`unmeasured_inputs`** first. It names the tiers whose numbers nobody measured. A plan over guesses
reads exactly like a plan over measurements, and docking's rank correlation is the input the whole
plan turns on.

**`refused_tiers`**, each with its reason. Expect PMF to be refused as answering mechanism, and expect
Boltz-2 to be refused for having no measured rank correlation — that refusal is correct and the remedy
is to measure it, which is two columns and a Spearman on your panel.

**`expected_actives_at_the_end`** against `true_actives_in_pool`. On a default plan this is around
9.9 of 750. That is not a defect of the plan; it is what a funnel does, and it is why the next step
matters more than buying compute.

Then show the operator the rendered plan and get agreement on the budget. **This is the one
confirmation the campaign needs up front.**

## Step 2 — find out what is worth changing before you change anything

```
etalon_tune_screen  (then again with established=...)
```

Free. This is the step most likely to change what the campaign does, and the result is usually
surprising:

> Eight engineer-hours of ML rescoring delivers the same gain as **ten times the compute budget**.
> Twenty-four hours of three-score consensus delivers 65% more, at zero added GPU cost.

The mechanism: at a rank correlation of 0.35, docking discards most of the actives it cannot identify,
and the expensive tier's price forces it to cut hard — an end-point method at 10 GPU-hours a molecule
sees about 870 molecules in a GPU-year, so docking must deliver 870 out of 255,000. Nothing downstream
recovers a molecule already discarded.

Two knobs will be refused as **below your panel's resolution**. That is not "don't do it" — sizing the
docking box to 2.9× the ligand's radius of gyration is a real measured effect. It means: turn it once
on the strength of its publication, and do not expect to observe it working, and do not report it as
an improvement you verified.

One knob will be refused for an **unestablished precondition**, and this one matters. Consensus scoring
improves enrichment *only if* each member performs well individually **and** the members are diverse.
That is the published condition. Where it held — kinases — Top-1% enrichment went from 6.4 to 23.5.
Where it did not — GPCR-Bench — MM/GBSA-containing combinations improved only 32% and 19% of
combinations. **Establish it before turning it**: score each candidate function on the panel
separately, then correlate their rankings with each other. A member near chance contributes noise; two
correlating above about 0.9 contribute one opinion at two prices.

## Step 3 — generate, and account for each model separately

Use PRISM's `generate_ligands` with a generation config naming the models and their pinned checkpoints.
Five models at 150,000 each is the scale this workflow is sized for.

Then **QC every model's output separately** with `check_generated_ligands`, and keep the counts. Two
things make per-model accounting worth the bookkeeping:

These models emit **heavy atoms only**. Measured: across 41 gaff2 builds from hydrogen-free input the
topology carried zero hydrogens in 41 of 41, with no warning at any stage. So "generated" and "usable"
are different counts, and PRISM's `prepare_generated_for_md` is what verifies hydrogens are actually
on disk.

And a model that **rediscovers known chemotypes** passes every filter calibrated on known actives. Its
survival rate will look excellent and it contributes nothing a campaign could not have bought. Read
survival rate beside scaffold novelty, always.

**Where to read the per-model comparison:** at the stage where the counts are still large. A model's
share of the final ten hits can distinguish nothing — four of ten carries a 95% interval from 17% to
69% — while its QC rate measured on 150,000 molecules distinguishes every pair. Reallocate generation
on the deepest stage whose counts can still tell models apart, which is usually the docking tier.

## Step 4 — screen

Use MolCascade's `screen` with a cascade config. Compose the handoff tier into the config rather than
editing the operator's file — `etalon.boundary.screen.Screen.with_handoff` does it, choosing the pose
mode when there is a receptor and the conformer mode when there is not.

Pick the keep fractions the plan gave you. They will look wrong: the plan makes the cheap tier
permissive when it can afford to and brutal when it cannot, and at a realistic budget it is brutal.
That is the expensive tier's price setting the cheap tier's cut, not a mistake.

## Step 5 — refuse before spending. This is the cheapest valuable step in the campaign

```
etalon_check_handoff  records_json=...  receptor_path=...  toolchain_seeded=true
```

Free. Every molecule that passes the screen gets one `md_system_input/v1` row declaring where its
coordinates came from, whether there are hydrogens, the formal charge, the stereochemistry, the
receptor and the protonation state. This tool rules on those rows.

It exists because of a measurement. MolCascade's shortlist exporter rebuilds geometry from SMILES — 19
heavy atoms, **zero explicit hydrogens, every z exactly 0.00** — PRISM's ligand validator accepts it
because it checks existence, size, suffix and a positive atom count, and 41 of 41 gaff2 builds from
such input produced a topology with no hydrogens at all.

**How to read the verdicts:**

- `blocking` non-empty → do not spend. A record built from a drawing, or with no explicit hydrogens, is
  **not fixable by a waiver** — fix the producer. Use the docked pose or the embedded conformer the run
  already computed; never rebuild geometry from a name at a handoff.
- `F_PROTONATION_UNDECIDED` alone → this is the one reasonable thing to accept, because nothing in
  MolCascade predicts a ligand protonation state. Call `etalon_recommend_waiver` and show the operator.
  If they grant it, pass back the whole waiver object — code, reason, their name, an expiry — and
  check `waivers_in_force` in the result says what you expected. An expired waiver releases nothing
  and its fault blocks again, which is the point of the expiry.
- `could_not_be_checked` → not clean. A report of "0 blocking" over four unevaluated checks says the
  opposite of the truth. Supply `receptor_path` to turn the receptor-identity check from unevaluable
  into a digest comparison.
- `qualifies_the_claim_only` → spend, and do not claim reproducibility. Without the seed shim, ion
  placement draws from the clock.

## Step 5b — authorise, which is the same ruling with a result the next step needs

```
etalon_authorize_spend  records_json=...  receptor_path=...  toolchain_seeded=true
```

Free, and prefer it over `etalon_check_handoff` whenever anything downstream will actually spend.

The difference is not the check — it is the same preflight — it is that this one returns a **token
per surviving record**, and `PrismStage` and every other expensive stage call `authority.require`
before they build. A record that blocks gets no token, so the spend on it is *unreachable* rather
than discouraged.

This exists because of an honest reading of what the rest of this document could not do. Nothing
here spends. You read a refusal from a free tool and then call PRISM's own server to build, because
that is where building lives, and nothing connected the two — so every refusal was advice offered
beside an action it had no relationship with, and the governance rested on you choosing to be
governed in round nine of a campaign whose workflow you read in round one. ADR 0006 named that class
of bug. This closes it.

Three refusals, and they are different mistakes with different fixes: no token (the check refused
the record, or was never run on it), an expired or tampered token (it outlived the preflight it came
from — re-run this, it costs nothing), and a token minted for a **different version of the record**
(something edited the row between the ruling and the build, so what was checked is not what would be
built). The last is the taxonomy's `WRONG_SUBJECT` applied to the permission itself.

Read each token's `unchecked` before treating an authorisation as clean. A token over an unevaluable
check authorises a spend; it does not assert the check passed.

## Step 6 — build and equilibrate, then judge the trajectory by three questions

Build with PRISM's canonical defaults and **do not override box, salt or temperature** — those belong
to the protocol. Then judge what you got:

```
etalon_check_stability  ligand_rmsd_nm=[...]  nanoseconds=1000  replicas=1  scored_contacts=...
```

Free. "Is the pose stable" hides three questions with three different consequences:

1. **Did the ligand stay?** If not, every number from the run describes a solvated ligand near a
   protein. Blocks.
2. **Did it settle?** A pose still moving at the end was averaged over a non-stationary segment, so the
   average estimates a time-dependent quantity. Blocks. The test is the trend over the final third
   against the fluctuation within it, not an absolute tolerance.
3. **Is it the pose that was scored?** A ligand can hold the site and lose every contact the docking
   score was about. This does *not* make the free energy wrong — it makes comparing it with the screen
   a comparison between two poses. So it withholds the molecule from **teaching** the screen without
   withholding the molecule.

And what one run does not establish: replicas differing only in their initial velocities have
disagreed by up to **15 kcal/mol** on one system. A run that loses the pose has not shown the pose is
wrong, and a run that holds it has not shown it is right. Pass `replicas` honestly.

## Step 7 — the end-point free energy, priced correctly

A single MM-PBSA trajectory is **not a measurement**. Calculations started from identical structures
varied by up to **12 kcal/mol** for small molecules bound to HIV-1 protease, and the distributions are
not Gaussian — skewness and excess kurtosis definitively non-zero across 500-replica runs, normality
rejected for all nine systems tested.

So: **five replicas, and report a median with a quantile interval**, not a mean with a standard
deviation. The honest price is 10 GPU-hours a molecule, not 2. A plan that budgeted one run budgeted a
number that cannot be ranked on.

Also prefer **MM-GBSA over MM-PBSA when the task is ranking.** MM-GBSA predicts absolute values worse
and ranks better, and ranking is what a funnel does. Over 8 ns it reaches Spearman 0.767, which is
0.087 below FEP on the same comparison — about the same size as what a 231-molecule panel can resolve
at all.

## Step 7b — the checks nothing could evaluate, if you have a council

`could_not_be_checked` is not clean, and until now there was nothing to do about it. A council of
advisors can rule on those — under one condition and one bound, and both matter more than the
mechanism.

**The condition.** Measure the council before you use it.

```
etalon_council_reliability  votes_json=...  truth_json=...  labels="where these came from"
```

`tuning/knob.py` carries the published precondition for consensus scoring: each member good on its
own AND the members diverse. Kinases, where it held: Top-1% enrichment 6.4 → 23.5. GPCR-Bench, where
it did not: 32% and 19% of combinations improved. Nothing in that condition is about docking — it is
the condition under which pooling judgements beats taking one, and it applies to advisors exactly as
it applies to scoring functions. Every published multi-agent drug-discovery system surveyed for ADR
0007 adds agents without measuring it. The general literature reports a mean effect of **−3.5%** for
multi-agent against single-agent across 260 configurations.

Read **effective votes** first. Measured here on two seats given deliberately disjoint evidence:
**1.39 of a nominal 2**. Giving advisors different slices of a record does not make them independent
— it helps, partially, and the number is the only place that shows. Nine LLM judges have been
measured at 2.18 effective votes.

A seat whose Youden's J is at or below zero disqualifies the whole council, and J is zero for a seat
at chance, one that refuses everything, and one that clears everything. Also read the per-class
abstentions: a seat can score J = 1.000 having declined most of the class that matters, which is
exactly what happened the first time this was measured.

**The bound.**

```
etalon_council_adjudicate  code=...  ballots_json=...  reliability_qualified=true
```

A council may move a check from **unevaluable to fired**, and may **never** move one to cleared.
Seats agreeing they see no problem returns `cleared_but_still_unchecked`, and `could_not_be_checked`
still reports the cause. An advisor saying "this looks fine" is not the check having run, and a
campaign must never buy a clean record from a model — including from you.

That bound is what makes the layer safe: the worst a wrong council does is refuse molecules that
were fine, which costs compute and shows in the admission rate. It cannot manufacture a clean result.

**A split is the product, not a failure.** It has found the record a person should read and spent no
GPU time doing it. Show them the `dissent` ballots first — the minority's sentence is the thing worth
reading, and a three-to-one count does not carry it. Do not resolve a split yourself by weighing the
arguments; that is the supervisor pattern ADR 0007 rejects, and it replaces a measured quantity with
your opinion.

## Step 8 — decide what may teach the screen

```
etalon_rule_admissible  measurements_json=...
```

Free, and this is the step published CADD loops do not have. They put machine learning inside the
campaign — old and well done — on the assumption that a number arriving from the expensive stage is a
measurement of the molecule it is filed under. That assumption is false here and it has been measured.

A loop without this gate does not record one bad number. It fits the thresholds applied to **every
later molecule** to a label from a molecule that was never simulated. The asymmetry is the argument:
withholding a good measurement costs one molecule's information, admitting a bad one costs a shift in
the policy applied to all of them.

**Read `admission_rate` before acting on the result.** Below about two thirds, what survived is a
selection rather than a sample — and on a congeneric series the survivors are systematically the
molecules whose geometry was easy, which are not the ones a screen is getting wrong.

Any change the admitted measurements support must still beat the panel's resolution. On a 231-molecule
panel with 40 actives the Hanley-McNeil standard error of an AUC is 0.047, so **nothing below about
0.10 AUC is an improvement**; it is the same measurement twice. Report a refused update as a result,
not as a disappointment: a loop that silently declines to learn is indistinguishable from one with
nothing to learn.

## Step 9 — design the FEP network before committing to FEP

```
etalon_design_fep_network  molecules_json={...}  references=lead  cycle_edges=2
```

Cheap — about 34 ms per pair — and it will usually tell you something uncomfortable.

Relative FEP scores *differences*, so a set of molecules is not an input to it. Measured on the 16 most
potent molecules of a real kinase panel: **110 of 120 pairs map through a common core smaller than half
the larger molecule, and 7 of the 16 have no usable edge to anything.** Those sixteen were not a series
— they were sixteen chemotypes that bind the same kinase.

That is the pipeline pulling against itself, and it is worth saying to the operator plainly: the early
tiers and the acquisition layer select for diversity and scaffold novelty **on purpose**, because the
surrogate is most confident exactly where it has no evidence. Those are precisely the molecules a
relative method cannot relate.

Three ways out, and **the choice belongs before generation, not after**:

- **Seed the generation with a series.** Use PRISM's reference-guided models — MolCRAFT, FLOWR,
  DiffSBDD — with a conditioning ligand. This makes the pipeline coherent and costs the novelty the
  diverse screen existed to find.
- **Accept many small networks.** Rank within each component, do not compare across them. Right when
  the question is "improve each of these" rather than "which is best".
- **Use an absolute method for the singletons.** A deliberate trade of precision for coverage, and
  record it as one.

And **ask for cycles deliberately.** A spanning forest is the cheapest network and has no internal
error estimate at all. Each edge beyond it closes one independent cycle, and the deviation of the sum
of differences around a cycle from zero is hysteresis — the only error estimate in this whole pipeline
that is a measurement rather than a literature value.

## What needs a person, and what does not

You may decide, on your own: which molecules to measure next (being wrong costs compute and the next
round shows it), and any parameter change that passes the noise floor.

You may **not** decide: which metric the screen is calibrated against — a silently wrong comparator
invalidates every later round — or any waiver. `etalon_recommend_waiver` records a recommendation with
`status: AWAITING_A_PERSON`. Show it to the operator and let them grant it under their own name.

That line is now enforced in code rather than asked for here. It was not: both `waived` arguments took
a comma-separated list of fault codes and honoured it without constructing a waiver at all, so a model
could release `F_COORDINATES_ARE_A_DEPICTION` — the cause this document calls unfixable by a waiver —
by typing its name. Every guard that was supposed to stop that lived in a module the tools did not
call. They call it now, and a grantor that looks like a model identifier is refused.

What no parser can catch is a model writing a plausible human name into `granted_by`. That is why
both tools echo `waivers_in_force` with the name, the reason and the accepted consequence: the defence
is an operator reading it, so put it in front of them rather than leaving it in an argument.

The reason is measured rather than principled: structured-looking output carries an impression of rigour
its content has not earned, and a waiver reason is pure structure. A plausible justification and a sound
one are indistinguishable at the point of reading, which is the only point at which anyone reads it.

## The numbers a refusal rests on

Quote these when you explain a refusal. Without them the refusals look like fussiness.

| measurement | value |
|---|---|
| gaff2 builds from hydrogen-free input with zero hydrogens in the topology ✻ | **41 of 41** |
| MM-PBSA run-to-run spread from identical structures | up to **12 kcal/mol** |
| replicas within one ensemble | up to **15 kcal/mol** |
| MM-GBSA Spearman over 8 ns, against FEP | 0.767, **0.087 below** |
| PMF cost per complex | ~**2.1 µs**, hundreds of GPU-hours |
| smallest AUC difference a 231-molecule panel resolves | ~**0.10** |
| conformal marginal coverage vs. the worst scaffold group | **90.5%** vs **37.5%** |
| pairs in a diverse top-16 that are not alchemical edges | **110 of 120** |
| engineer-hours equal to a GPU-year, via rescoring ✻✻ | **8** |
| docking's rank correlation on this project's own panel, against the 0.35 the catalogue ships | **0.108**, interval includes zero |
| effective independent votes from two advisors given disjoint evidence | **1.39** of 2 |
| published mean effect of multi-agent against single-agent, 260 configurations | **-3.5%** |

✻ Measured in the vendored PRISM, not by ETALON — `asset/prism/prism/generation/handoff.py` at the
pinned commit. It is the one number in this table with no `findings/` entry, for that reason.

✻✻ The GPU-year side is priced from published effect sizes; the engineer-hours are this project's
estimates and not measurements. Say so when you quote it.

## When something refuses and you are stuck

1. Read the `next_step` field. Every tool returns one.
2. Read `etalon://findings` for the measurement behind the refusal.
3. Read `etalon://decisions` for the architectural decision, which says what was considered and
   rejected.
4. If a tool fails unexpectedly, call `etalon_infrastructure` — a shadowed import is the usual cause.
5. If the campaign cannot proceed without a waiver, recommend one and stop. Do not route around a
   refusal; that is the one failure mode this whole system is built to prevent.

## What ETALON does not do

It does not make the campaign autonomous. A council adds refusals and splits, and a split is a stop;
its contribution is to find the records a person should spend attention on, which is a smaller claim
than the field's and the one the measurements support.

It does not make the campaign autonomous. Two decisions stop and wait for a person, and your job on
those is to draft rather than to settle. A campaign with you attached runs further between human
decisions than one without; it does not run without them.

It also cannot tell you whether your target is druggable, whether your receptor structure is the right
conformation, or whether a 10 nM compound will survive a rat. Those are outside every number here.
