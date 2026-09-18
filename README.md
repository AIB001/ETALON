# ETALON

A CADD campaign agent that knows how much to trust its own numbers.

The [2026-09-18 review](docs/review-2026-09-18.md) evaluates the architecture,
real execution coverage, LLM compatibility, active learning and related agent systems.
The [runtime guide](docs/runtime-guide.md) covers the new `doctor` command, persistent
background screening (`screen plan / submit / status`), and interchangeable OpenAI,
Anthropic and DeepSeek HTTP advisors. Screening jobs survive client disconnects and
duplicate submissions retain one job. They do not share the active campaign budget or
automatically launch downstream affinity calculations. See the
[validation record](docs/validation-2026-09-18.md) for what was actually exercised.

## Executable active learning and composable cascades

The current implementation adds a persistent **select → reserve → execute → admit → retrain**
controller in `etalon.active`. MolCascade is a component library, **not a mandatory sequence of
default screening tiers**: use an individual component, author a new cascade, or compile a flat
pipeline. Register redesigned protocols under new endpoint ids while retaining historical labels
and a fixed scientific objective.

Start with the [implementation and research guide](docs/active-learning.md), the
[prior-art and innovation assessment](docs/research-innovation-2026-09-17.md), the
[initial validation record](docs/validation-2026-09-17.md), the
[decision-controller validation](docs/validation-decision-2026-09-17.md), and the runnable
[real-component example](examples/component_learning.py). The guide separates verified engineering
behavior from unproven scientific efficacy, and documents crash recovery, budgets and limitations.
The next stage adds [bounded protocol evolution](docs/protocol-learning.md): persist an exact edit
space, propose a variant, compile its contracts, authorize a capped trial, then explicitly promote
or retire it against predeclared operational criteria. Recipes and evidence graphs survive restart;
retirement stops new queries without deleting acquired labels. See the
[protocol validation record](docs/validation-protocol-2026-09-17.md).
The [learned protocol-search baseline](docs/protocol-search.md) now ranks preauthorized edit
combinations from frozen, fixed-panel feedback. It includes shared-linear, random and fixed
selection, explicit missing-readout penalties, durable decisions and a separate synthetic ablation.
It does not autonomously authorize or promote protocols, nor establish CADD efficacy.
An opt-in [economic stopping policy](docs/protocol-stopping.md) compares the expected improvement
of a complete audit with an explicitly authorized score/cost exchange rate. It preserves legacy
searches, never truncates an ongoing panel, and includes a separate stopping/no-stopping ablation.
Use a dedicated virtual environment for the optional dependencies below. Run these commands from
the complete checkout root and retain `asset/MANIFEST.json` and both vendored trees. Live MolCascade /
PRISM workflows currently rely on this checkout plus editable installation: the ordinary wheel
packages `src/etalon`, not the asset trees, and is not a standalone live-infrastructure deployment.

The current [architecture map](docs/architecture-current.md) distinguishes the inner molecule–endpoint
loop, bounded protocol-search loop, and explicit human control boundaries. The
[90-minute integrity review](docs/review-2026-09-17.md) records the latest fixes, regression results,
compatibility changes and remaining limitations; engineering tests are not CADD efficacy evidence.
The [next experimental plan](docs/experiment-plan-2026-09-17.md) separates testable claims about
protocol learning, evidence admission and budget protection; it is a proposed design, not a completed study.

```bash
pip install -e '.[active]'                # numeric controller and offline oracle replay
python -m etalon active demo --workspace runs/demo --rounds 5
python -m etalon active plan --database runs/demo/campaign.sqlite
python -m etalon active benchmark --workspace runs/benchmark --output runs/benchmark/report.json
python -m etalon active demo --workspace runs/decision --policy decision_aware --rounds 100
python -m etalon active recommend --database runs/decision/campaign.sqlite
python -m etalon active decision-benchmark --workspace runs/decision-ablation \
  --output runs/decision-ablation/report.json

pip install -e '.[cascade]'               # actual CPU MolCascade components and molecular features
python examples/component_learning.py --workspace runs/components
python -m etalon active components        # criteria AND individual plugin contracts
python examples/protocol_learning.py --workspace runs/protocols --output runs/protocols/report.json
python -m etalon active protocols --database runs/protocols/campaign.sqlite  # read-only
python examples/protocol_search.py --workspace runs/protocol-search --output runs/protocol-search/report.json
python -m etalon active searches --database runs/protocol-search/campaign.sqlite  # read-only
python -m etalon active protocol-benchmark --output runs/protocol-benchmark/report.json
python examples/protocol_search.py --workspace runs/protocol-stopping --policy audit_ei \
  --opportunity-cost 0.025 --campaign-rounds 2 --output runs/protocol-stopping/report.json
python -m etalon active protocol-stopping-benchmark --output runs/protocol-stopping-benchmark/report.json

pip install -e '.[cascade,bundles,dev]'   # optional RF → ONNX → real MolCascade CPU prediction + pytest
python -m pytest -q tests/test_bundle_roundtrip.py
```

The bundled benchmark is synthetic. It does **not** establish improved binding affinity, superiority
over published agents, or a new active-learning algorithm. The quality-weighted heuristic does not
consistently beat simpler baselines in the initial smoke runs. Live PRISM workflows still need
explicit protocols, completed affinity readouts and independent-replica execution; they are not
silently replaced by replay or by equilibration results.

The opt-in `decision_aware` policy adds global decision-value acquisition, molecule-conditioned
admission estimates, bounded protocol scouting and an objective-confirmation quote reserve.
It reports unrestricted predictions separately from attainable and evidence-backed recommendations.
`mf_kg` is an established-method baseline, not a claimed invention. These policies use one real
observation per round; the new ablation harness matches this feedback frequency across all groups.

In metrology an *etalon* is the primary standard every other instrument is calibrated
against. In optics a Fabry–Pérot etalon extracts information from the interference between
two beams — that is, from their disagreement. Both meanings are the point.

ETALON runs two packages as infrastructure and modifies neither:

| | | |
|---|---|---|
| **MolCascade** | `asset/molcascade` | ligand triage, docking, the handoff contract |
| **PRISM** | `asset/prism` | system building, MD, MM-PBSA, FEP |

Both are vendored as digest-pinned trees and recorded in `asset/MANIFEST.json`. They are
dependencies *and* evidence: every measurement here cites a commit, so the code that ran
has to be the code the citation names. `src/etalon/boundary/infra.py` enforces that at
import and refuses when an editable install shadows the vendored copy — which it did,
silently, the first time it was checked.

> **Not in this repository yet.** `findings/` — the measurement records — and `docs/adr/` — the
> architectural decisions — are held back for now, so links to them below do not resolve here. The
> numbers they carry are quoted inline throughout this file and in the source, and the MCP server
> reports the two resources as absent rather than failing. Nothing else reads them.

## What it is for

A screening cascade is cheap and wrong in ways nobody can see. A free energy calculation is
expensive and right in ways nobody can check. The obvious move is to let the expensive stage
retune the cheap one, and that move is old and well made: DeepDriveMD drives adaptive MD
from a learned latent space, IMPECCABLE retrains a surrogate from downstream results,
Colmena steers ensembles from a thinker process. ETALON claims nothing over them; an earlier
draft did and was wrong.

What those loops assume, nowhere stated because it is too obvious to state, is that a number
arriving from the expensive stage is a measurement of the molecule it is filed under. Here
that is false, and it was measured rather than argued:

- MolCascade's shortlist exporter rebuilds geometry from SMILES. On propranolol: **19 heavy
  atoms, 0 explicit hydrogens, z range 0.00 to 0.00** — a drawing.
- PRISM's ligand validator accepts it. It checks existence, size, suffix and a positive atom
  count.
- Across **41 gaff2 builds from hydrogen-free input, the topology carried zero hydrogens in
  41 of 41**, with no warning at any stage. *That third one was measured in the vendored PRISM
  rather than by ETALON — `asset/prism/prism/generation/handoff.py` at the pinned commit — which is
  why it is the only headline number here with no `findings/` entry. The first two were measured
  here.*

A loop without a gate there does not record one bad number. It fits the thresholds applied
to every later molecule to a label from a molecule that was never simulated. So ETALON's
contribution is the admissibility criterion on the feedback, and the refusal to accept an
update it cannot distinguish from noise.

## The shape

```
src/etalon/
  boundary/     reach the infrastructure, and prove which copy was reached
    infra.py        which MolCascade, which PRISM — refuses a shadowed import
    toolchain.py    a gmx shim that seeds the one command PRISM calls unseeded
    screen.py       MolCascade: plan → run → read back verified
    simulate.py     PRISM: build → drive → verify products and controlled process completion
  faults/       would a number from this be about the molecule it is filed under?
    taxonomy.py     15 causes, each with a magnitude band and a consequence
    preflight.py    10 of them, from one handoff row, before a GPU-second
    postflight.py   5 of them, from a finished run's manifest and logs
    attribution.py  eliminate causes by magnitude; say UNEXPLAINED when nothing fits
  learn/        what may teach the screen, and whether a change beats noise
    admissible.py   the gate on the feedback
    calibrate.py    AUC with its standard error, and a refusal below it
  learn/        ... and what it predicts, and where to spend next
    surrogate.py    a forest over MolCascade's own feature blocks, on pIC50
    conformal.py    cross-conformal intervals; calibration-residual diagnostics, not test coverage
    acquire.py      the next batch, with an explicit share spent on the unfamiliar
    bundle.py       ship the model through MolCascade's prediction.custom_model slot
  judgment/     who may decide what
    proposal.py     an advisor proposes; the autonomy of each act is stated once
    advisor.py      the claude CLI, and an answer of the wrong shape is refused
    waiver.py       accepting a risk in writing — a person only
  economics/    what each tier costs, what it buys, whether it is worth its place
    stage.py        a priced catalogue; every number names its source
    allocate.py     optimise the keep fractions; refuse a tier that cannot pay
  tuning/       which screening knobs are worth turning, with published effect sizes
    knob.py         the catalogue, with each knob's precondition
    advise.py       rank them by actives added; refuse the unverifiable and the unconditioned
  fep/          the edge network a relative method needs
    network.py      MCS mapping quality, components, cycles, and what it cannot reach
  rescore/      the largest lever, and the half of it a GPU accelerates
    features.py     protein-ligand contact histograms; NumPy and batched-GPU, bit-identical
    model.py        a small network, measured throughput, and a refusal below one sample per feature
  generate/     the one stage a CADD pipeline normally has no feedback on
    audit.py        per-model yield, and the stage at which models stop being distinguishable
  council/      several advisors, and the measurement that decides whether they are an instrument
    seat.py         a seat declares what evidence it sees; identical scopes are refused
    reliability.py  Youden's J per seat, kappa per pair, Kish effective votes, and the refusal
    convene.py      the sitting, bounded so a council can only ever add a refusal
  authority/    the check's output is the expensive stage's argument
    grant.py        a token per surviving record, bound to a digest of that exact record
  campaign/     the agent
    loop.py         screen → authorise → measure → admit → decide → record
    expensive.py    the PRISM stage
    propose.py      acquisition, and scoring an edit against the panel
    pipeline.py     the whole campaign, rendered before anything is spent
    ledger.py       append-only; a rewind abandons without deleting
    design.py       compose individual components or arbitrary tiers; no fixed default funnel
  active/       the persistent feedback controller
    schema.py       molecule states, observable/protocol identities, costs and observations
    store.py        SQLite journal, reservations, actual costs and explicit crash recovery
    model.py        paired multi-endpoint GP; updates from admitted observations each round
    policy.py       molecule × endpoint × replicate selection, cost and reliability ablation
    knowledge.py    exact finite-pool, single-observation Gaussian knowledge gradient
    decision.py     evidence-attainable decisions, bounded scouting and confirmation-budget guard
    reliability.py  molecule-conditioned admission estimates, separate from hard QC
    recommendation.py  separate predictions, attainable decisions and acquired objective evidence
    runner.py       the batch that is selected is the batch actually executed
    cascade.py      real single-component/custom-cascade executor with versioned protocols
    graph.py        compile-only evidence DAG and exact input-bound execution-plan checks
    mutations.py    finite, reviewed edits; no implicit resource or authority expansion
    protocols.py    persistent proposals, capped trials and explicit promotion/retirement
    proposer.py     frozen audit panels, bounded variant selection and durable feedback
    protocol_score.py  leave-one-out panel response and shared linear protocol ranker
    protocol_benchmark.py  sealed synthetic search tables and equal-budget policy ablations
    replay.py       sealed offline oracle and reproducible synthetic fixtures
    benchmark.py    equal-budget baselines; no hidden-label access from the policy
```

## Driving it from a language model

```bash
python -m etalon.mcp            # MCP server over stdio
```

Nineteen tools, and **each declares what it spends in its own description** so the classification is
visible before a model chooses rather than after:

| cost | tools |
|---|---|
| **free** | `plan_campaign` `tune_screen` `stages` `infrastructure` `check_handoff` `check_stability` `rule_admissible` `authorize_spend` `council_reliability` `council_adjudicate` `active_status` `screen_status` |
| **cheap** | `design_fep_network` `active_plan` `active_replay` `doctor` `screen_plan` |
| **spends** | `screen_submit` (plan-bound bulk execution; cost not metered or reserved in the active journal) |
| **never by a model** | `recommend_waiver` |

A campaign has 750,000 actions, so the governance cannot be per-action confirmation the way a
small-scale workflow's is. It is that **the expensive steps are guarded by cheap checks, and the
guards refuse rather than warn.** The plan is confirmed once with the operator; after that the
refusals are automatic.

That used to say "cheap checks the model is expected to call," and the italics were carrying a
weight they could not hold — see [the gate is an argument](#the-gate-is-an-argument-not-a-suggestion)
below.

The complete workflow — generation through relative free energy, with the order, the decision points,
and the measurement behind every refusal — is
[`.claude/skills/etalon-campaign/SKILL.md`](.claude/skills/etalon-campaign/SKILL.md), served to MCP
clients as `etalon://skills/campaign`. One file, two consumers: a second copy would drift, and the
numbers are the part that must not. `etalon://findings` and `etalon://decisions` serve the
measurements and the ADRs, because a refusal quoted without its measurement looks like fussiness.

## The language model inside the campaign

An advisor can use the `claude` CLI with the operator's existing login, or the optional
`HttpAdvisor` transport for OpenAI, Anthropic or DeepSeek. HTTP credentials are read from
environment variables at request time; model names are explicit. These transports provide
proposals to the existing campaign and do not supply an autonomous tool-calling host.
See [configuration and verification limits](docs/runtime-guide.md).

**An advisor proposes and never grants**, and the gradation is by what being wrong costs rather
than by how confident the model sounds:

| proposes | acted on | because being wrong costs |
|---|---|---|
| which molecules to measure next | automatically | compute, and next round shows it |
| a parameter change | through the noise floor | nothing extra — it already refuses |
| which metric to calibrate against | needs a person | every later round, silently |
| a waiver for a `WRONG_SUBJECT` fault | **refused** | the validity of a recorded result |
| a hypothesis about a divergence | recorded, never acted on | nothing, while marked as one |

[`docs/adr/0003`](docs/adr/0003-an-advisor-proposes-and-never-grants.md) carries the reasoning and
the citations. The short version: the documented failure of these systems is not a wrong answer
but a confident, well-formatted one — "overexcitement" that declares success despite obvious
failure, and structured output that carries an impression of rigour its content has not earned.
A waiver reason is pure format, and a model writes one better than most people while being
unable to be held to it.

Answers are parsed strictly. A fence or a prefatory sentence is tolerated; an answer of the wrong
shape is refused rather than mined, because reaching into a malformed reply for the part that
looks like the number is the same operation that turns a hallucination into a record.

## The gate is an argument, not a suggestion

Count this server's tools by what they spend: ten free, one cheap, one no model may complete. Until
recently **none of them spent anything.** A model ran `etalon_check_handoff`, read a refusal, and
then called PRISM's own server to build the system, because that is where building lives. Nothing
connected the two. Every refusal was advice offered beside an action it had no relationship with,
and the sentence above about guards rested on a model choosing to be guarded in round nine of a
campaign whose workflow it read in round one.

[`docs/adr/0006`](docs/adr/0006-a-guard-on-the-path-nobody-takes-is-not-a-guard.md) named this
class of bug and fixed one instance of it. The class was larger than the instance.

`authority/` closes it by type rather than by documentation. `authorize()` runs the preflight
itself — it does not accept a verdict as an argument, because a function that took one would be the
same hole with extra steps — and mints a token per surviving record. `PrismStage` and anything else
implementing the expensive-stage protocol take those tokens and call `require()` before they build.
**The path that spends without checking does not exist**: the function that spends cannot be called
without the object that checking produces. Called with `None`, it raises rather than defaulting to
permission, because a gate whose default is "allowed" protects whoever remembers it.

A token is bound to a SHA-256 of the exact row, so three different mistakes get three different
refusals: no token, an expired or tampered one, and one minted for a different version of the record
— which is the taxonomy's `WRONG_SUBJECT` applied to the permission itself. Checking one row and
building another produces numbers about a molecule nobody ruled on, and an id matching is not
evidence the row is the one that was checked.

What this is not: a defence against a hostile caller in the same process, who can import the minter.
It is a defence against every way a correct intention becomes a wrong spend — the check skipped, the
check run on a different set, a waiver from an earlier round still releasing a fault weeks later, the
receptor changed in between. The signed token makes those non-constructible by accident rather than
cryptographically impossible, and the docstring says so where a README would be tempted to imply
otherwise. The nearest published relative is AWS's `ccapi-mcp-server`, which mints a token when a
check runs; that one attests that a check *happened*, and binding the token to the checked content
is the part that makes it attest to *what*.

## Several advisors, and the measurement that decides whether they are an instrument

`tuning/knob.py` has carried this sentence since before there was a second advisor:

> Each member performs relatively well on its own AND the members are appropriately diverse. … A
> member whose AUC is near chance contributes noise; two members correlating above about 0.9 with
> each other contribute one opinion at two prices.

It is the published precondition for consensus *scoring* — kinases, where it held: Top-1% enrichment
6.4 → 23.5; GPCR-Bench, where it did not: MM/GBSA combinations improving 32% and 19%. `tuning/advise.py`
refuses to recommend the knob until an operator establishes it.

Nothing in that condition is about docking. It is the condition under which pooling judgements beats
taking one, and Ueda and Nakano's decomposition of ensemble error is its formal statement: averaging
scales the variance term by 1/M and the covariance term by **(1 − 1/M)**, so as members are added the
variance term vanishes and the covariance term does not. Wang and Wang measured the same law in this
field in 2001 — consensus error cancels at roughly √N *because the members' errors were modelled as
independent*.

Applying that to scoring functions and not to the advisors scoring them is where the analogy stopped
being carried. Every multi-agent system surveyed for
[`docs/adr/0007`](docs/adr/0007-a-council-is-an-instrument-and-must-be-calibrated.md) — PharmAgents,
DrugAgent, Mozi, Robin, BioDiscoveryAgent, TxAgent, STELLA, PharmaSwarm, DeepMind's AI co-scientist —
**reports no agreement statistic for its own panel.** No kappa, no correlated-error analysis, no
measured single-agent comparison. Meanwhile the general literature has been reporting a **mean effect
of −3.5% for multi-agent against single-agent** across 260 configurations, errors amplified up to
17.2× by decentralised topologies, and nine LLM judges carrying **2.18 effective independent votes**.

So a council here is a tier, and every argument `economics/` makes about a tier applies to it:

| | measured as | refuses? |
|---|---|---|
| is each seat better than chance | Youden's J, conservative interval | **yes** — one seat at or below zero disqualifies the council |
| are the seats redundant | Cohen's kappa per pair | reported; the 0.9 is borrowed across a change of statistic |
| how many opinions is this really | Kish effective votes | reported, with the decomposition that says why |

Youden's J rather than accuracy, because J is zero for a seat at chance **and** for one that refuses
everything **and** for one that clears everything. The characteristic failure of a gate is an
unconditional verdict, not a wrong one — that is `docs/adr/0002`, a rule that refused every molecule
in the population — and accuracy flatters an unconditional refuser on a set where most records are bad.

**Diversity is declared as evidence, not written as a prompt.** A seat says which slice of the record
it is shown and `charter()` refuses two seats with identical scopes at construction. Two readers of
one row through different personas are one opinion at two prices however different the instructions
sound: a persona cannot change what is in front of it, and both are wrong together on precisely the
record that is itself misleading — the case a council is convened for. The scope is enforced by
handing each seat a dictionary, not by asking it not to look.

### The one thing a council may do

**It may move a check from unevaluable to fired. It may never move one to cleared.**

Its jurisdiction is only what `preflight.unchecked` reports — the checks that could not be evaluated.
A deterministic check that ran is a measurement and no vote overturns a hash comparison. Within that
jurisdiction the authority is one-way, and the asymmetry is `learn/admissible.py`'s: withholding a
good measurement costs one molecule's information, admitting a bad one costs a shift in the policy
applied to all of them. Seats agreeing they see no problem returns `CLEARED_BUT_STILL_UNCHECKED`, the
observation stays `evaluable=False`, and `unchecked()` still reports the cause — because an advisor
saying "this looks fine" is not the check having run.

So the worst a miscalibrated, compromised or simply wrong council can do is **refuse molecules that
were fine**, which costs compute, collapses the admission rate, and is visible in the ledger. It
cannot manufacture a clean record. Given a literature whose headline is that panels often
underperform, a mechanism whose failure mode is bounded in the safe direction is the only kind worth
adding.

Majority vote, debate between seats, and a supervisor agent resolving disagreement were all
considered and rejected; ADR 0007 says why each. The short version: majority vote contradicts the
asymmetry, debate raises the one quantity the design keeps low, and a supervisor replaces a measured
quantity with an unmeasured one. **A split goes to a person** — ordered by how close it was, which is
`learn/acquire.py`'s argument with operator attention in place of GPU-hours — and is deliberately not
encoded as a refusal, because "a person must look" and "the molecule is bad" are different claims.

### And then the council was measured

Two seats of `claude-sonnet` with disjoint evidence, 24 labelled handoff rows — 12 with coordinates
rebuilt from SMILES, 12 from real poses — with `coordinate_source` withheld from both, which is the
case the deterministic check cannot rule on
([`findings/0012`](findings/0012-a-council-of-two-carries-less-than-two-opinions.json)):

> Both seats qualified. The pair's Cohen's kappa was **+0.442** and the two seats carried **1.39
> effective votes** of a nominal 2.

Two things in that are worth more than the pass. Disjoint evidence bought real but partial
independence — 1.46 of a nominal 2 — so **declaring different evidence is necessary and not
sufficient**, which is what `composition()`'s note already said and now has a number.

And it caught a hole in this module. On the first run `provenance-reader` scored J = 1.000 having
abstained on **ten of the twelve records that carried the fault**, answering two and getting both
right. The figure was true of what it judged and useless as a summary of the seat. Its overall
abstention rate was 46%, under any threshold worth setting. Abstention is now counted **per class**,
because the quantity that matters is not how often a seat declines but which class it declines on.

The second result is the larger one and it is a gap rather than a feature. **The council's own
measurement is not reproducible**: two runs of the same script at the same seed moved J from 1.000
to 0.917, the kappa from 0.371 to 0.442, and the effective votes from 1.46 to 1.39. The seed fixes
record generation, not the model. `docs/adr/0001` met this shape before — a build that was not a
pure function of its inputs — and fixed it with a shim that supplied the missing seed. There is no
equivalent here, so a council that qualified once has not been shown to qualify, and repeated
qualification with an interval is what `reliability.py` needs next. Both runs agreed on every sign;
it is the precision that is unestablished.

## The model that learns

A random forest on pIC50 over nine named RDKit descriptors and a 1024-bit Morgan counts
fingerprint — computed by **MolCascade's** featuriser, so the bundle manifest cannot declare a
featurisation the model was not trained on. On the real STK17B panel: scaffold-grouped CV MAE
**0.569 pIC50** (≈0.8 kcal/mol), Spearman 0.718, against a training MAE of 0.216 — the 2.6×
gap being the overfitting made visible.

Conformal prediction wraps the forest's ensemble spread in an interval with a finite-sample,
distribution-free guarantee. Cross-conformal over scaffold-grouped folds, because 231 molecules
cannot spare a held-out third and a random fold puts near-duplicates on both sides.

Then the measurement that shapes everything downstream
([`findings/0003`](findings/0003-marginal-coverage-hides-the-best-compound.json)):

> Marginal coverage **90.5%** against a nominal 90% — essentially perfect. The scaffold group
> holding the panel's most potent compound: **37.5%**. Held out by scaffold the forest predicts
> 6.14–6.47 pIC50 for all eight members while the truth spans 6.15–9.59, missing a 0.26 nM
> inhibitor by 3.13 pIC50 — 4.4 kcal/mol — and giving it the **narrowest interval in the series**.

Confidently wrong about the best compound available. So coverage is reported twice, and
acquisition does not trust an uncertainty estimate to find the unfamiliar: a stated share of the
budget is reserved for scaffolds the model has never seen. Over five seeds on the real panel, 60
seen and 20 chosen from 171:

| strategy | best hit, median | sub-100 nM, mean |
|---|---|---|
| greedy UCB (explore 0) | 8.00 nM | **8.8** |
| default (explore 0.25) | 8.00 nM | 7.4 |
| all exploration (1.0) | **5.00 nM** | 3.6 |
| random | 11.00 nM | 5.4 |

**Five seeds, one target, no intervals on any arm.** 8.00 against 5.00 nM across five draws is not a
separation this design can establish, and the table is here because the *direction* of the
disagreement between the two columns is what the finding predicts — not because the numbers in it are
resolved. Read it as a shape, not a ranking.

Every informed arm beats random, and then the columns disagree — exploitation finds more hits,
exploration finds the stronger single compound, exactly as the finding predicts. The default is
best on neither and is not claimed to be.

## The two ideas that do the work

**A fault carries a band and a consequence, and they are different questions.** The band says
how large an error a cause could produce, so a cause whose band tops out at 3 kcal/mol is
*refused* as the explanation of a 6 kcal/mol divergence even though its flag fired. The
consequence says whether the number will be about the molecule at all. Conflating them
produced a rule that refused every molecule in an unseeded campaign, which is why
`docs/adr/0002` exists.

**An update must beat the panel's own resolution.** On the STK17B panel — 231 knowns, ~40
potent — the Hanley–McNeil standard error at AUC 0.75 is **0.047**, so the smallest
resolvable difference is about **0.10 AUC**. A round reporting 0.71 → 0.74 has reported the
same measurement twice. `learn/calibrate.py` refuses such an update and records the refusal,
because a loop that silently declines to learn looks exactly like one with nothing to learn.

## The funnel, priced

A cascade is filters of increasing cost and increasing accuracy, and the exchange rate between
those two is normally left to convention: the funnel has the tiers the field's funnels have.
`economics/` writes it down, with a source for every number, and three of them are uncomfortable.

- **MM-GBSA over 8 ns reaches Spearman 0.767 — 0.087 below FEP** on the same comparison. A
  231-molecule panel resolves about 0.10, so that gap is the size of what the panel can see.
- **A single-trajectory MM-PBSA estimate is not reproducible to better than ~12 kcal/mol.**
  Calculations from identical structures varied that much on HIV-1 protease, replicas within one
  ensemble by up to 15, and the distributions are not Gaussian. The honest price of a rankable
  number is five replicas: **10 GPU-hours a molecule, not 2**.
- **PMF by umbrella sampling costs ~2.1 µs per complex** — hundreds of GPU-hours against tens for
  an FEP edge — and a PARP1 head-to-head found the physical route no more accurate. Its cost buys
  mechanism, not rank, so **it is not a tier**. Spend it on the few molecules whose mechanism is
  the question.

`python -m etalon plan` renders a whole campaign for free — no GPU, no vendored asset, nothing
touched. On 750,000 molecules from five generative models, a GPU-year, and a 0.1% active rate:

```bash
python -m etalon plan --pool 750000 --budget 8760 --active 0.001 --deliver 10
python -m etalon plan --measured docking=0.52      # substitute your own number
python -m etalon tune --established rescore_ml     # what to change, ranked by actives gained
python -m etalon fep candidates.csv --cycles 2     # the edge network, and what it cannot reach
python -m etalon stages                            # the priced catalogue, with sources
python -m etalon infra                             # which MolCascade and which PRISM would load
```


```
  tier                         in      out      keep   GPU-hours  actives left
  lilly_demerits          750,000  255,330    0.3404           0         585.0
  docking                 255,330      726    0.0028          77          22.3
  mmgbsa_ensemble             726       45    0.0620       7,265          13.5
  md_stability                 45       28    0.6222       1,083          12.2
  fep_edge                     28       12    0.4286         336           9.9
```

Of 750 true actives, 9.9 reach the end — and **docking alone discards 96% of what reaches it**.
That is what a rank correlation of 0.35 does when the tier below costs 10 GPU-hours a molecule and
can only afford 726. The consequence
([`findings/0004`](findings/0004-a-better-cheap-tier-beats-ten-times-the-compute.json)):

| | expected true actives in the final 10 |
|---|---|
| 1 GPU-year, docking ρ = 0.35 | 9.87 |
| **10 GPU-years**, docking ρ = 0.35 | 13.54 |
| 1 GPU-year, docking **ρ = 0.50** | **13.61** |

Improving the cheapest ranking tier by 0.15 of a rank correlation beats ten times the compute,
while MM-GBSA consumes 82.9% of the budget and docking 0.9%.

### And then the convention was measured

Docking's 0.35 was a placeholder. Docked for real — 231 molecules with measured affinities, Uni-Dock
against the STK17B receptor, 51 seconds on a 4090
([`findings/0009`](findings/0009-docking-on-this-panel-is-not-distinguishable-from-random.json)):

> **Spearman 0.108, 95% interval −0.021 to 0.234.** The interval includes zero.

Checks that did not explain it: the sign convention is right (the ten most potent average −8.96 against
the ten weakest at −8.29); two impossible positive scores removed move it 0.108 → 0.101; and grouped by
scaffold the correlations scatter from **−0.700 to +0.586 with a weighted mean of +0.012**, which is the
pattern of no signal and which *strengthens* the result, since docking is supposed to be better within
a series.

What it is not: an enrichment measurement. Most docking benchmarks report actives-over-decoys
enrichment, which docking does far better at and which is a different statistic — the economics module
currently only speaks rank correlation, and that is a gap. The panel is also ChEMBL-derived across mixed
assays and labs, which depresses any correlation measured against it by an unknown amount.

The consequence, at `resolvable_spearman` = 0.133 (the Spearman's own interval at n = 231):

| docking ρ | affordable? | achievable retention |
|---|---|---|
| 0.350 convention | yes | 0.0132 |
| 0.234 interval upper | yes | 0.0086 |
| **0.108 measured** | **no** | **—** |

At the measured value the tier is refused for being indistinguishable from random, the end-point method
is left screening 255,330 molecules, and the plan costs **292 GPU-years**. That is not a failure of the
planner; it is the planner saying the cheap tier does not do what the pipeline assumes.

It also caught two bugs in my own modules. `Funnel.retention` exists on an infeasible plan, and I
compared one across three plans without checking — reading the 292-GPU-year funnel as the *better*
option because its retention was higher. And `advise` measured every knob's gain against that baseline
and reported **negative** gains: improving the screen appeared to deliver fewer actives. Both are fixed
and pinned; `achievable_retention` returns `None` when a plan cannot be paid for.

Every plan also names the tiers resting on numbers nobody measured — docking's correlation, the
ensemble's inferred spread, MD stability's two rejection rates — because a calculation over guesses
reads exactly like one over measurements.

## 1000 ns of MD is three questions

"Is the pose stable" hides three with different consequences. **Did the ligand stay** — if not, every
number describes a solvated ligand near a protein. **Did it settle** — a pose still moving was
averaged over a non-stationary segment. **Is it the pose that was scored** — a ligand can hold the
site and lose every contact the docking score was about, which does not make the free energy wrong
but makes the comparison with the screen meaningless. The first two block; the third withholds the
molecule from *teaching* without withholding the molecule.

The settling test is the trend over the final third against the fluctuation within it —
dimensionless, because an absolute tolerance reported a ligand creeping 0.15 → 0.40 nm over 1000 ns
as settled. The slope's nominal t-statistic is deliberately not used: frames are serially
correlated, so a t computed from them reaches 30 where the honest ratio reaches 8.

And what a single run establishes, as a function rather than a comment because it is the sentence
that gets dropped: it can show a pose does **not** survive and cannot show that one does.

## Which knob is worth turning

`findings/0004` says where the value is and names no action. The literature says what achieves it,
and the ordering is not the one a configuration file suggests: **the scoring function dominates and
the sampler is close to interchangeable** — DiffDock-L sampling scored with Gnina matched Vina
sampling (BEDROC 0.33 vs 0.36, EF1% 16.22 vs 17.88), and DOCK3.x ranks on a single pose.

`python -m etalon tune` puts engineer-hours and GPU-years in the same units
([`findings/0006`](findings/0006-eight-engineer-hours-equals-a-gpu-year.json)):

| action | added GPU-h | engineer-hours | actives gained |
|---|---|---|---|
| three-score consensus (kinases) | **0** | 24 | **+6.1** |
| ML rescoring | 0 | 8 | +3.7 |
| *ten times the compute* | *+78,840* | *0* | *+3.6* |

**Eight engineer-hours equals a GPU-year** — with the GPU-year side priced from published effect
sizes and **the engineer-hours side an estimate this project made, not a measurement**.
`findings/0006` says so and this line did not. The claim that survives without the estimate is the
ordering: a scoring change costing no GPU time outranks ten times the compute.

Two knobs are refused as below the panel's resolution —
sizing the docking box to 2.9× the ligand's radius of gyration is a real measured effect (EF1% 7.67 →
8.20) worth +0.013 of rank correlation against a panel that resolves 0.10: turn it once, don't expect
to observe it. Exhaustiveness is recorded as approximately **zero**, which is a claim and is sourced.

And consensus scoring is refused until its precondition is recorded, because the precondition is
published rather than invented: it improves enrichment *only if* each member is individually good and
the members are diverse. Kinases, where that held: Top-1% EF 6.4 → 23.5. GPCR-Bench, where it did
not: MM/GBSA-containing combinations improved only 32% and 19% of combinations.

## Three models, three different skills, and the metric decides

The rescorer, the ligand-only surrogate and docking, on the **same 230 molecules**, 5-fold
scaffold-grouped, out of fold
([`findings/0011`](findings/0011-the-three-models-are-good-at-different-things.json)):

| model | Spearman | MAE pIC50 | EF@1% | EF@10% | EF@25% |
|---|---|---|---|---|---|
| ligand-only surrogate | **+0.728** | **0.552** | 0.00 | 0.00 | 0.99 |
| pose rescorer | +0.352 | 1.410 | 0.00 | 0.00 | 1.65 |
| docking | +0.121 | — | **9.58**✻ | 2.50 | 1.65 |

✻ the only point whose interval excludes an enrichment of 1 — **and it rests on one active compound
among the top two molecules**. The interval (1.82 to 17.43) excludes 1 only because the base rate is
5.2%; it is one coin flip from not doing so. Every EF column in this table inherits that: at n = 231
with 12 sub-10 nM actives, the top 1% is two molecules.

The ligand-only model out-correlates docking **six-fold** and reading the pose made things *worse*
(MAE 1.410 against 0.552). And **neither learned model puts a single sub-10 nM compound in its top
10%**, while docking — the worst ranker by a wide margin — is the only one with enrichment at the head.

The cause is textbook: both learned models are fitted with squared error on pIC50, and squared error
shrinks toward the conditional mean. The panel's 0.26 nM compound sits at pIC50 9.59 and gets predicted
near the mean of 6.2. A model that never predicts an extreme cannot rank one first. Docking's score is
fitted to nothing, has no shrinkage, and its noise occasionally lands a potent compound at the top.

So the remedy is the objective, not the features: a ranking loss, a classifier on the active threshold,
or high-quantile regression. None costs more than what was run — the rescorer trains in seconds on 230
complexes.

**And it caught a bug in the tuning module.** `advise` ranked knobs by `delta_spearman`, so a change
worth +0.23 of correlation that destroys head enrichment would have been recommended enthusiastically.
A knob acting on a tier whose head enrichment is measured is now refused until the knob's own enrichment
effect is measured too.

A second thing that module now explains rather than reports. Raising docking to ρ = 0.637 puts MM-GBSA's
0.767 **inside the panel's 0.133 resolution**, so the planner drops it as one tier at two prices and the
funnel loses its most selective stage — a net **−2.4 actives** for an improvement. The refusal is
defensible and the loss may be an artefact of panel size, since 0.767 probably *is* better in truth. A
bare negative number says none of that.

## The GPU belongs in the featuriser, not the model

`findings/0006` says rescoring existing poses is the largest lever a campaign has, and the obvious
reading — that a rescorer is a GPU inference job — is wrong. Measured on 28 real docked poses against
the STK17B receptor
([`findings/0008`](findings/0008-the-gpu-belongs-in-the-featuriser.json)):

| | throughput | 750,000 poses | GPU advantage |
|---|---|---|---|
| contact featuriser, NumPy | 927 /s | 0.22 CPU-h | — |
| **contact featuriser, GPU batched** | **9,554 /s** | **0.022 GPU-h** | **10.3×** |
| the model (39 k parameters) | 6.8 M /s | under a second | 2.2× (irrelevant) |
| *a grid-CNN-scale model* | *2.2 M /s* | *0.3 s* | *24.9×* |

**The NumPy baseline is one core.** `findings/0008` says so and this table did not: a 32-core host
closes most of the 10.3× on CPU alone, so that figure is a single-process comparison rather than a
statement about devices. What survives the caveat is the ratio the section is actually about — the
featuriser against the model, which is 3,000× and is not a threading artefact.

The model was never the bottleneck — it is **3,000× faster than the thing feeding it**. And batching
mattered more than the device: a first GPU version looped pose by pose and got 2.4×, on the stated
reasoning that padding and masking would cost more than the loop. The loop was the bottleneck; the mask
is one comparison. Both paths are verified **bit-identical** (maximum difference zero) — and that claim
was true on the sample and false as a rule until recently: `np.digitize` and `torch.bucketize` spell
their binning convention in opposite directions, so the GPU put every distance landing exactly on a
shell edge one shell lower. Float32 coordinates make an exact hit rare, which is why 28 real poses
agreed. The convention is now pinned by a test that runs on CPU, since `bucketize` needs no device.

This also corrects a cost I had guessed. `rescore_ml`'s `added_gpu_hours` was set to docking's own
per-molecule figure, because a rescorer reads docking's poses and seemed comparable. Measured, it was
**high by four orders of magnitude**: 750,000 poses cost about 80 seconds, not 225 GPU-hours.

Two correctness traps are pinned in tests. A receptor truncated to a pocket sphere must use a radius
covering the ligand's extent *plus* the outer shell — at 18 Å against a 12 Å shell, counts changed by
up to 17, because a ligand atom near the site edge reaches further than the centre does. And
`Rescorer.fit` **refuses below one complex per feature**: 28 samples against 87 non-zero features
reproduces any labels exactly and predicts the mean out of fold. No accuracy is claimed — `as_stage`
returns a stage with no rank correlation, which the planner then refuses, correctly.

## FEP needs edges, and a diverse shortlist has none

The last tier scores *differences*, so a set of molecules is not an input to it. Measured on the real
panel ([`findings/0007`](findings/0007-the-survivors-are-what-fep-cannot-relate.json)): of the 120
pairs among the 16 most potent STK17B molecules, **110 map through a common core below half the larger
molecule**, and **7 of the 16 have no usable edge to anything**. Those sixteen are not a series —
they are sixteen chemotypes that bind the same kinase.

That is the pipeline pulling against itself. The early tiers and the acquisition layer select for
diversity and scaffold novelty *on purpose* (`findings/0003` is why), and those are exactly the
molecules a relative method cannot relate.
[`docs/adr/0005`](docs/adr/0005-if-fep-is-the-endpoint-generation-is-constrained.md) records the three
ways out and says the choice belongs before generation, not after.

```bash
python -m etalon fep candidates.csv --reference lead --cycles 2
```

Exits 1 when any molecule is unreachable. And each edge beyond a spanning forest closes one
independent cycle — the deviation of the sum of differences around it from zero is **hysteresis**,
the one error estimate in this whole pipeline that is a measurement rather than a literature value.

## Which generator was worth running

The one stage of a CADD pipeline with no feedback on it. Where feedback is attempted it uses the
final hit counts, and ([`findings/0005`](findings/0005-generation-feedback-belongs-where-n-is-large.json))
those counts can support nothing:

| stage | n | of 10 model pairs, distinguishable |
|---|---|---|
| QC passed | 150,000 | **10** |
| alerts tier | 150,000 | **10** |
| docking top | ~450 | 2 |
| end-point | ~42 | **0** |
| final hits | 0–4 | **0** |

Four of ten final hits carries a 95% Wilson interval of 17% to 69%. Reallocate generation on the
deepest stage whose counts can still tell models apart — and read survival rate beside scaffold
novelty, because a generator that rediscovers the training panel passes every filter calibrated on
known actives and contributes nothing a campaign could not have bought.

## Decisions, with the measurement that forced them

Every architectural decision is an ADR carrying its own evidence rather than its own
argument.

- [`docs/adr/0001`](docs/adr/0001-a-build-is-not-a-pure-function.md) — a PRISM build is not a
  pure function of its inputs. `gmx genion` takes no seed: two runs from identical inputs
  gave `7c3e3472…` and `098089b3…`. Fixed from outside with a `gmx` shim; same seed gives
  `65ef4554…` twice.
- [`docs/adr/0002`](docs/adr/0002-a-magnitude-band-cannot-decide-whether-to-refuse.md) — a
  magnitude band cannot decide whether to refuse a molecule. The rule that said otherwise
  blocked a molecule that was entirely correct.
- [`docs/adr/0004`](docs/adr/0004-a-tier-must-pay-for-itself.md) — a tier must pay for itself, and
  a stage answering a different question is not a tier.
- [`docs/adr/0005`](docs/adr/0005-if-fep-is-the-endpoint-generation-is-constrained.md) — if relative
  FEP is the endpoint, generation is constrained: 110 of 120 pairs among the panel's most potent
  molecules are not alchemical edges.
- [`docs/adr/0006`](docs/adr/0006-a-guard-on-the-path-nobody-takes-is-not-a-guard.md) — a guard on
  the path nobody takes is not a guard. The MCP tools took `waived` as a list of fault codes and
  honoured it without constructing a waiver, so every check in `judgment/waiver.py` was bypassed on
  the only path a model uses: **a model could release `F_COORDINATES_ARE_A_DEPICTION` — the cause
  the same tool calls unfixable by a waiver — by typing its name.** `refuse_if_not_an_advisors_decision`
  had zero production callers, and `Act.SPEND` had no implementation at all.
- [`docs/adr/0007`](docs/adr/0007-a-council-is-an-instrument-and-must-be-calibrated.md) — a council of
  advisors is an instrument, and one that has not been calibrated does not get used. The precondition
  was already in this repository, applied to scoring functions; none of the published multi-agent
  drug-discovery systems applies it to its own agents. Measured here, two seats with disjoint evidence
  carried **1.39 effective votes of 2**.
- [`findings/0002`](findings/0002-driver-exit-code.json) — PRISM's `localrun.sh` exit code
  cannot distinguish a finished run from a failed one. A fresh broken build exits 1; the same
  directory re-driven exits **0 with `em`, `nvt` and `npt` all failed** and 23 error lines in
  the log; a timed-out run has no exit code at all.

## Running it

ETALON needs two environments and knows it. MolCascade runs in `prism`; PRISM's build needs
AmberTools, which lives in `AmberTools23`. `boundary.simulate.discover()` probes for
`antechamber`, `parmchk2`, `acpype` and `tleap` and names what is absent **before** a build
starts, because "PRISM failed" an hour into a parameterisation is a much worse message.

```python
from pathlib import Path
from etalon.boundary.infra import load
from etalon.boundary.simulate import Simulate, discover
from etalon.campaign import Campaign, PrismStage, Waiver, WaiverSet

load("molcascade")                       # refuses a shadowed import

campaign = Campaign("work/ws", "work/campaign.jsonl", waivers=WaiverSet((
    Waiver("F_PROTONATION_UNDECIDED",
           "Screened at pH 7.4; the ligand is accepted in the standardizer's neutral form.",
           "your-name", date(2026, 12, 31)),
)))

stage = PrismStage(
    simulate=Simulate("work/sim", discover("/path/to/AmberTools23/bin/python", seed=20260912)),
    receptor=Path("/abs/path/receptor.pdb"),
)

outcome = campaign.round("r1", "cascade.yaml", "library.csv", measure=stage)
print(outcome.admission.as_dict()["withholding_reasons"])
```

The handoff tier is composed by ETALON, not by editing the operator's config:
`Screen.with_handoff(config, out, prefer="docked_pose" | "embedded_conformer")`.

## Changing the infrastructure

Edit the live repo, run *its* test suite, **commit there**, then re-vendor:

```bash
python tools/vendor_assets.py --write
```

`tools/measure_council.py` is the other falsification probe, beside `tools/falsify_determinism.py`:
it runs a real two-seat council against labelled records and exits 1 when the council does not
qualify. Both exist because a threshold only ever exercised on fixtures is a threshold nobody knows
the height of.

`tools/vendor_assets.py` refuses a dirty source tree, so the other order fails by design —
the manifest records a commit beside a tree digest and a reader may assume the first
describes the second. A bare run compares what is vendored against the manifest and changes
nothing.

## Tests

```bash
python -m pytest -q          # install .[test] first; no MD/FEP, network, or API key required
python -m ruff check src tests tools
python tools/verify_assets.py --deep
```

The count is a pass count. It used to be a collection count — `277 tests` was what pytest *collected*,
and one of them failed on any machine without pydantic v2, which the vendored MolCascade needs. The
suite's real requirements are now declared (`pip install -e '.[test]'`) and the two tests that cannot
run without a CUDA device or a usable MolCascade skip with a reason naming what is missing, rather
than one of them failing and the headline reporting the other number.

The failure detection is written to be judged from files on disk precisely so that the part
most likely to be wrong is not the part that needs a GPU to exercise.
