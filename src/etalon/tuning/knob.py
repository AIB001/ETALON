"""Which screening parameters are worth turning, with the published effect size for each.

``findings/0004`` says improving the cheapest ranking tier's correlation by 0.15 outweighs ten times
the compute budget. That is a conclusion about where value is and it names no action. This module is
the other half: what a campaign can actually change about its docking tier, how much each change is
published to be worth, what it costs, and -- the part that makes it a catalogue rather than a list
of ideas -- which knobs are not worth turning.

The literature is unusually consistent on the ordering, and it is not the ordering a pipeline's
configuration file suggests.

**The scoring function dominates; the sampler is close to interchangeable.** ML pose sampling with
DiffDock-L paired to the Gnina scoring function gave early enrichment comparable to Vina sampling --
BEDROC 0.33 against 0.36, EF1% 16.22 against 17.88 -- and classic DOCK3.x pipelines rank molecules
on a single lowest-energy pose. Exhaustiveness and pose count are worth one convergence check and
almost never worth a campaign's attention after it.

**Rescoring is the largest single lever.** SCORCH2 surpassed the native Glide score in more than
half of the cases evaluated, including on targets that already met stringent enrichment thresholds.

**Consensus scoring is conditional, and the condition is published rather than invented.** Combining
scoring functions improves enrichment only if each function performs relatively well individually
and the functions are appropriately diverse. Where that holds the effect is large: on kinases,
Top-1% EF went from 6.4 to 23.5 combining three scores. Where it does not, it is close to nothing:
across GPCR-Bench, consensus performance was modest overall and even MM/GBSA-containing combinations
improved only 32% and 19% of all combinations at EF1% and EF5%.

So the catalogue carries a precondition field, and :func:`etalon.tuning.advise.advise` refuses a knob
whose precondition the campaign has not established. A consensus tier assembled from one strong and
two weak correlated scores is a published failure mode, not an untested idea, and an agent that
turns that knob because it is available has spent a campaign on a 19% chance.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Lever(StrEnum):
    """What part of the screen a knob acts on. Ordered by published leverage, not by convenience."""

    #: The scoring function applied to poses. The largest published effects live here.
    SCORING = "scoring"
    #: Where and how large the search space is.
    SEARCH_SPACE = "search_space"
    #: How hard the sampler looks. Published effects are small.
    SAMPLING = "sampling"
    #: The property and liability windows above docking.
    FILTERS = "filters"
    #: What the pool contains in the first place.
    LIBRARY = "library"


class Effect(StrEnum):
    """How an effect size was established."""

    #: A published benchmark number, cited.
    BENCHMARKED = "benchmarked"
    #: Measured in this project.
    MEASURED_HERE = "measured_here"
    #: Asserted by somebody, including me. Labelled.
    ASSERTED = "asserted"


@dataclass(frozen=True, slots=True)
class Knob:
    """One thing a campaign can change about its screen, priced and sized."""

    id: str
    label: str
    lever: Lever
    #: Published change in rank correlation, where a benchmark reports one. Expressed as a delta on
    #: Spearman so it can be compared against a panel's resolution, which is the whole point of
    #: recording it this way.
    delta_spearman: float | None
    #: Published change in EF1%, where a benchmark reports that instead. Kept separately rather than
    #: converted, because the conversion depends on the active fraction and a converted number would
    #: look like a measured one.
    ef1_multiplier: float | None
    #: GPU-hours per molecule this adds to the tier it acts on. Zero for a knob that only changes a
    #: setting.
    added_gpu_hours: float
    #: One-off cost in engineer-hours. Recorded because a campaign's scarcest resource is often not
    #: the GPU, and a knob worth 0.1 of a rank correlation for a week of work competes against one
    #: worth 0.05 for an afternoon.
    setup_hours: float
    effect: Effect
    source: str
    #: What must be true for the published effect to transfer. An empty string means none is known,
    #: which is itself worth seeing.
    precondition: str = ""
    #: How to check the precondition, when there is one.
    how_to_check: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "lever": self.lever.value,
            "delta_spearman": self.delta_spearman,
            "ef1_multiplier": self.ef1_multiplier,
            "added_gpu_hours_per_molecule": self.added_gpu_hours,
            "setup_hours": self.setup_hours,
            "effect": self.effect.value,
            "source": self.source,
            "precondition": self.precondition,
            "how_to_check": self.how_to_check,
        }


_RESCORE = Knob(
    id="rescore_ml",
    label="Rescore existing poses with a learned scoring function",
    lever=Lever.SCORING,
    delta_spearman=0.15,
    ef1_multiplier=None,
    # Measured, not guessed. The guess was 3e-4 -- docking's own per-molecule cost, chosen because a
    # rescorer reads docking's poses and seemed comparable -- and it was high by four orders of
    # magnitude. findings/0008: the featuriser runs at 9,554 poses per second batched on a 4090 and
    # the model at 6.8 million, so 750,000 poses cost about 80 seconds of GPU time rather than the
    # 225 GPU-hours the guess implied. The knob was already the best value in this catalogue at the
    # wrong price; at the right one its cost is not a consideration.
    added_gpu_hours=2.9e-8,
    setup_hours=8.0,
    effect=Effect.BENCHMARKED,
    source=(
        "Cost measured here (findings/0008): 9,554 poses per second for the contact featuriser "
        "batched on an RTX 4090, 6.8 million per second for the model, so 750,000 poses cost about "
        "80 seconds. Accuracy from the literature: "
        "SCORCH2 surpassed the native Glide score in more than half of the cases evaluated, "
        "including on targets already meeting stringent enrichment thresholds (SCORCH2: A "
        "Generalized Heterogeneous Consensus Model for High-Enrichment Interaction-Based Virtual "
        "Screening, 2025). GNINA's CNN rescoring outperformed AutoDock Vina across ten "
        "structurally heterogeneous targets. The 0.15 delta-Spearman is this project's reading of "
        "'more than half of cases improved' onto a rank-correlation scale and is the weakest part "
        "of this entry: the benchmarks report enrichment factors, not Spearman."
    ),
    precondition="The poses are already roughly right; rescoring cannot rank a pose the sampler never found.",
    how_to_check=(
        "Run the pose-validity checks on a sample. PoseBusters found deep-learning docking methods "
        "routinely produce physically invalid poses that satisfy an RMSD criterion, and a rescorer "
        "applied to those is ranking artefacts."
    ),
)

_CONSENSUS = Knob(
    id="consensus_three",
    label="Linear-combination consensus over three scoring functions",
    lever=Lever.SCORING,
    delta_spearman=None,
    ef1_multiplier=3.67,
    added_gpu_hours=6e-4,
    setup_hours=24.0,
    effect=Effect.BENCHMARKED,
    source=(
        "On kinases, Top-1% enrichment factor went from 6.4 to 23.5 combining three scores, a "
        "factor of 3.67, with AUC averaging 14% higher for three-score combinations; linear "
        "combination gave an average 17% increase in Top-1% EF over the single best score "
        "(Rescoring and Linearly Combining: A Highly Effective Consensus Strategy for Virtual "
        "Screening Campaigns, 2019). The same approach on GPCR-Bench -- 24 structures, about "
        "254,646 actives and decoys -- was modest overall, with MM/GBSA-containing combinations "
        "improving only 32% and 19% of all combinations at EF1% and EF5%."
    ),
    precondition=(
        "Each member performs relatively well on its own AND the members are appropriately diverse. "
        "This is the published condition rather than a caution added here, and the GPCR result is "
        "what it looks like when the condition does not hold."
    ),
    how_to_check=(
        "Score each candidate function on your own panel separately, then correlate their rankings "
        "with each other. A member whose AUC is near chance contributes noise; two members "
        "correlating above about 0.9 with each other contribute one opinion at two prices."
    ),
)

_BOX = Knob(
    id="box_size",
    label="Size the docking box to the ligand's radius of gyration",
    lever=Lever.SEARCH_SPACE,
    delta_spearman=None,
    ef1_multiplier=1.07,
    added_gpu_hours=0.0,
    setup_hours=2.0,
    effect=Effect.BENCHMARKED,
    source=(
        "Pose accuracy peaks when search-space dimensions are about 2.9 times the compound's radius "
        "of gyration, and the optimised box also improved ranking: average EF1% rose to 8.20 from "
        "7.67 and EF10% to 3.28 from 3.19, with better ranking in roughly two thirds of targets "
        "(Calculating an optimal box size for ligand docking and virtual screening, 2015)."
    ),
    precondition="",
    how_to_check="",
)

_EXHAUSTIVENESS = Knob(
    id="exhaustiveness",
    label="Raise docking exhaustiveness from 8 to 32",
    lever=Lever.SAMPLING,
    delta_spearman=0.0,
    ef1_multiplier=1.0,
    added_gpu_hours=1.2e-3,
    setup_hours=0.5,
    effect=Effect.BENCHMARKED,
    source=(
        "Recorded as approximately no effect rather than as unknown, which is a claim and is sourced. "
        "ML pose sampling with DiffDock-L gave early enrichment comparable to Vina sampling when "
        "both were scored with Gnina -- BEDROC 0.33 against 0.36, EF1% 16.22 against 17.88 "
        "(Integrating Machine Learning-Based Pose Sampling with Established Scoring Functions for "
        "Virtual Screening, J Chem Inf Model, 2025). Vina benchmarks commonly run exhaustiveness 8 "
        "and evaluate the single best pose, and classic DOCK3.x pipelines rank molecules on one "
        "lowest-energy pose, both of which say the same thing: enrichment is driven by scoring "
        "rather than by how deeply the sampler looked. Raising 8 to 32 quadruples the tier's cost "
        "for an effect no benchmark separates from zero."
    ),
    precondition="",
    how_to_check=(
        "One convergence check is still worth doing: dock a few hundred molecules at 8 and at 32 "
        "and correlate the two rankings. A correlation near one means the sampler has converged on "
        "this target and the knob is settled."
    ),
)

_POSES = Knob(
    id="pose_count",
    label="Keep 30 poses per compound instead of one",
    lever=Lever.SAMPLING,
    delta_spearman=None,
    ef1_multiplier=None,
    added_gpu_hours=0.0,
    setup_hours=1.0,
    effect=Effect.ASSERTED,
    source=(
        "Asserted, not benchmarked: no effect size for screening enrichment was found. Keeping more "
        "poses matters when something downstream consumes them -- a rescorer, a pose-validity check, "
        "an MD handoff that wants a pose other than rank 0 -- and as a ranking change on its own it "
        "has no published number. Recorded so that a campaign keeping thirty poses knows it is "
        "paying storage for a downstream option rather than buying enrichment."
    ),
    precondition="Something downstream actually reads a pose other than the first.",
    how_to_check="Check whether any configured tier consumes pose_rank above 0.",
)

_QSAR = Knob(
    id="qsar_prefilter",
    label="Rank with a panel-trained QSAR model before docking",
    lever=Lever.LIBRARY,
    delta_spearman=None,
    ef1_multiplier=None,
    added_gpu_hours=1e-6,
    setup_hours=16.0,
    effect=Effect.BENCHMARKED,
    source=(
        "Across five targets with twenty fixed-budget strategies, an ML-QSAR classifier was the "
        "strongest standalone method, recovering 47.6%, 81.6% and 84.4% of actives at the 1%, 5% "
        "and 10% cutoffs, and rank fusion of QSAR with maximum common substructure gave the highest "
        "mean recall at 5% and 10% -- 83.2% and 86.4% (Beyond the Score: Fixed-Budget Benchmarking "
        "of Virtual Screening Integration Strategies, 2026). No delta-Spearman is given because "
        "recall at a cutoff and a rank correlation are different quantities and converting between "
        "them needs the active fraction."
    ),
    precondition=(
        "A panel of known actives large enough to train on and to hold out from, scaffold-split. "
        "Measured in this project: at 231 molecules the out-of-fold error on an unseen chemotype "
        "reached 3.13 pIC50 and the model was most confident exactly there."
    ),
    how_to_check=(
        "Score it with etalon.learn.calibrate on a scaffold-grouped split and read the worst-group "
        "conformal coverage beside the marginal one; findings/0003 is what happens when only the "
        "marginal figure is read."
    ),
)

_WIDEN_FILTERS = Knob(
    id="widen_property_windows",
    label="Widen the physicochemical and drug-likeness windows",
    lever=Lever.FILTERS,
    delta_spearman=None,
    ef1_multiplier=None,
    added_gpu_hours=0.0,
    setup_hours=1.0,
    effect=Effect.MEASURED_HERE,
    source=(
        "Measured in this project rather than taken from a benchmark. On the STK17B set the "
        "druglikeness tier lost 37 molecules, and the cause was maximum_rule_of_five_violations = 0 "
        "rather than the QED threshold -- a QED floor of 0.3 removed one potent molecule of 65. The "
        "shipped Lilly rules reject 17 of 77 approved oral drugs, which is the cost of that filter "
        "and not a defect of it. Widening a window recovers actives and admits decoys; the net "
        "effect on enrichment was not measured here and is target-specific."
    ),
    precondition=(
        "Know which criterion is doing the rejecting before widening anything. A window blamed for "
        "losses it did not cause gets widened while the real filter keeps rejecting."
    ),
    how_to_check="etalon and MolCascade both report per-criterion attrition; read it first.",
)

KNOBS: tuple[Knob, ...] = (
    _RESCORE,
    _CONSENSUS,
    _QSAR,
    _BOX,
    _WIDEN_FILTERS,
    _POSES,
    _EXHAUSTIVENESS,
)

BY_ID: dict[str, Knob] = {knob.id: knob for knob in KNOBS}


def by_lever(lever: Lever) -> tuple[Knob, ...]:
    return tuple(knob for knob in KNOBS if knob.lever is lever)


__all__ = ["BY_ID", "KNOBS", "Effect", "Knob", "Lever", "by_lever"]
