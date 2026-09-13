"""The starter cascade offered to a first-time user.

The job this cascade is designed for is the one MolCascade sits in the middle
of: a generator such as PRISM proposes millions of molecules, and something has
to decide which twenty or thirty thousand are worth docking, rescoring and
running free-energy calculations on.  Everything below is arranged around that.

**Cheapest and most decisive first.**  Staged screening funnels work because
each tier is allowed to be cruder than the next, provided it is also far
cheaper: run the fast, coarse filters on everything and reserve the expensive,
accurate ones for what survives.  So the order here is parse -> descriptor
arithmetic -> substructure matching -> learned synthesizability, with anything
requiring a neural network or a route search placed after all of them.

The ordering is measured, not assumed.  On one core: RDKit parse and sanitize
~850 molecules/s, the RDKit property panel ~270, the alert catalogues ~85, the
ChEMBL structure checker ~100.  The spread is an order of magnitude, so tier
position is worth more than any amount of tuning inside a tier -- the same
check is three hours per million at the top of the funnel and minutes at the
bottom.  Those numbers are recorded on each catalogue option as
``throughput_per_second``, and the builder warns when a slow one is dropped
into the first tier.

**Two toolkits, not one, at the points where being wrong is expensive.**  A
cascade built entirely on one library cannot tell a molecule that is fine from
a molecule that library happens to be wrong about.  The alert tier already
takes a union of independently curated catalogues; the last tier here adds the
ChEMBL curation checker, whose verdicts come from the IUPAC InChI library, so
what leaves for docking has been read by two toolkits rather than by RDKit
twice.

**Terminal rejects only where the chemistry is actually wrong.**  The recurring
failure of screening funnels is over-strict early filtering: if the filters are
too aggressive, real hits are eliminated before anything has looked at them, and
nothing downstream can recover them.  Tier one therefore rejects only molecules
that are broken -- unparseable, bad valence, disallowed elements -- and every
heuristic further down is either a soft window or an explicit, editable gate.

**PAINS is a hypothesis, not a verdict.**  Baell and Holloway's filters were
derived from one assay technology and one library, and the follow-up literature
(Capuzzi and colleagues' "Phantom PAINS", among others) documents how badly they
travel.  The default warns on PAINS and rejects on Brenk, whose list is about
reactive, unstable and toxicophoric groups -- chemistry that is wrong rather
than chemistry that is suspicious.

**Synthesizability heuristics are not route searches.**  Thakkar and colleagues
(Chem. Sci. 2021) checked directly whether SAscore, SCScore or SYBA predict
whether AiZynthFinder can find a route, and found no threshold on any of them
that separates solvable from unsolvable; they warn explicitly about misuse when
filtering large virtual libraries.  The honest response is not to drop the
scores -- they are the only thing that runs at this scale -- but to stop using
one of them as a hard cut.  The synthesizability tier here is therefore a
*union*: a molecule survives if either an occurrence-frequency score (SA) or a
reaction-trained score (SCScore) calls it tractable, and is dropped only when
two methods that disagree by construction both say it is hard.

**Predicted liabilities are joined, not trusted individually.**  One ADMET
endpoint is a default: hERG, asked of two models trained by different people on
different labels, and acted on only where they agree.  ADMET-AI predicts the
TDC binary blocker label and answers with a probability; OpenADMET regresses
pIC50 from ChEMBL and answers with a potency.  Neither is strong enough to
delete on by itself -- 0.84 AUROC for the first, no published metric at all for
the second -- so the tier is a union for the same reason the synthesizability
tier above it is, and its arms appear only when the machine can really answer
them.  A tier with no arms is no tier, which is what keeps this a bonus on a
host that has the models rather than a failure on a host that does not.

**What is deliberately absent.**  Similarity to reference leads needs a lead
file only the project has, so it cannot ship as a default; the remaining ADMET
endpoints -- absorption, DILI, AMES, the CYP panel -- are computed and recorded
but gate nothing, because a predicted liability is not a measured one and this
funnel has no exposure model to weigh it against; and AiZynthFinder route
search waits for an environment of its own, because installing it beside this
one would silently re-score every tier above it.  All three are one click away
in the builder, and belong at the bottom of the funnel where the population is
small enough to afford them -- route search most of all, at seconds to minutes
per molecule.

References

- Scior, T. et al. Recognizing pitfalls in virtual screening.
  *J. Chem. Inf. Model.* 2012. DOI: 10.1021/ci200528d
- Kimber, T. B. et al. Deep learning in virtual screening: principles and
  architectures. *Int. J. Mol. Sci.* 2021. DOI: 10.3390/ijms22094435
- Baell, J. B.; Holloway, G. A. New substructure filters for removal of pan
  assay interference compounds. *J. Med. Chem.* 2010. DOI: 10.1021/jm901137j
- Capuzzi, S. J. et al. Phantom PAINS: problems with the utility of alerts for
  pan-assay interference compounds. *J. Chem. Inf. Model.* 2017.
  DOI: 10.1021/acs.jcim.7b00465
- Brenk, R. et al. Lessons learnt from assembling screening libraries for drug
  discovery for neglected diseases. *ChemMedChem* 2008.
  DOI: 10.1002/cmdc.200700139
- Ertl, P.; Schuffenhauer, A. Estimation of synthetic accessibility score.
  *J. Cheminform.* 2009. DOI: 10.1186/1758-2946-1-8
- Coley, C. W. et al. SCScore: synthetic complexity learned from a reaction
  corpus. *J. Chem. Inf. Model.* 2018. DOI: 10.1021/acs.jcim.7b00622
- Thakkar, A. et al. Retrosynthetic accessibility score (RAscore).
  *Chem. Sci.* 2021. DOI: 10.1039/D0SC05401A
- Bickerton, G. R. et al. Quantifying the chemical beauty of drugs.
  *Nat. Chem.* 2012. DOI: 10.1038/nchem.1243
- Veber, D. F. et al. Molecular properties that influence oral bioavailability.
  *J. Med. Chem.* 2002. DOI: 10.1021/jm020017n
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from molcascade.cascade.availability import option_availability
from molcascade.cascade.catalog import CRITERIA_BY_ID, BackendOption, CriterionSpec
from molcascade.cascade.introspect import with_schema_version
from molcascade.cascade.models import (
    DEFAULT_TARGET_COUNT,
    CascadeConfig,
    CriterionConfig,
    FinalizeConfig,
    GateConfig,
    LibraryConfig,
    StepConfig,
    TierConfig,
    TierMode,
)
from molcascade.contracts.schemas import DOCKING_SCORE_V1
from molcascade.errors import PluginError
from molcascade.plugins.builtin._machine_paths import fill_machine_paths
from molcascade.plugins.registry import PluginRegistry, create_builtin_registry

DEFAULT_SEED = 20_260_823

#: Which SCScore variant the default uses when the asset has been provisioned.
#: The 1024-bit binary model is the one the paper's figures were produced with.
SCSCORE_ASSET_MEMBER = "models/full_reaxys_model_1024bool/model.ckpt-10654.as_numpy.json.gz"

#: Where the ADMET-AI arm of the hERG tier cuts, in the blocking probability
#: its head reports -- and deliberately not the 0.95 the same option carries as
#: a standalone gate in ``catalog.py``.  Two numbers because the arm is asked a
#: different question here, and the panel says the answer differs.
#:
#: Measured on the 77 approved oral drugs plus the four drugs withdrawn or
#: restricted for QT prolongation, with the OpenADMET arm at its own cut:
#:
#:     ADMET-AI cut    this arm alone      inside the 'any' join
#:                     appr'd  withdrawn   appr'd  withdrawn   missed
#:         0.90        13/77      4/4       3/77      4/4      --
#:         0.92        13/77      3/4       3/77      3/4      dofetilide
#:         0.95         6/77      3/4       1/77      3/4      dofetilide
#:         0.96         5/77      3/4       1/77      3/4      dofetilide
#:
#: 0.90 is wrong for a filter standing alone -- 13/77 is one approved drug in
#: six -- and that is why the catalogue default stays 0.95.  Inside the join it
#: cannot cost that, because ``any`` deletes only what *both* arms object to and
#: the other arm objects to three drugs in the whole panel.  3/77 is therefore
#: the ceiling on this arm's collateral however far it loosens, which is what
#: makes the fourth detection nearly free: the two extra deletions are
#: haloperidol (p 0.946, pIC50 7.23) and risperidone (p 0.928, pIC50 6.54),
#: both antipsychotics carrying QT labelling and both flagged independently by
#: the other model.
#:
#: What it buys back is dofetilide, at p 0.919 the one withdrawn drug a 0.95 cut
#: misses.  Dofetilide is a class III antiarrhythmic whose mechanism *is* hERG
#: blockade and whose label requires in-hospital initiation under continuous
#: ECG; of everything on this panel it is the least defensible thing for a hERG
#: screen to pass.  Trading two antipsychotics that both models call blockers
#: for the one detection this arm most obviously owes is the trade this cut
#: makes.
ADMET_AI_HERG_JOIN_MAX = 0.90

#: Where the OpenADMET arm of the hERG tier cuts, in the pIC50 the released
#: baseline regresses.  Measured on the same 77 approved oral drugs every other
#: default in this file answers to, plus the same four withdrawn QT drugs.
#:
#: The literature anchor is 5.0 -- IC50 above 10 uM, the conventional line -- and
#: it is wrong here by a log and a half, which is the whole reason this number
#: was measured instead of quoted.  A 5.0 cut deletes **30 of the 77 approved
#: drugs**.  The anchor is about a *measured* IC50; this model regresses pchembl
#: values out of ChEMBL, where hERG is assayed mostly on compounds somebody
#: already suspected, so its output sits high and a threshold read off the
#: literature scale lands in the middle of the panel rather than at its edge.
#:
#: Measured (77 approved: min 3.77, p25 4.55, median 4.88, p75 5.29, max 7.23;
#: withdrawn: astemizole 8.19, dofetilide 7.25, cisapride 7.23, terfenadine
#: 6.85):
#:
#:     cut    approved deleted    withdrawn caught
#:     5.0        30/77                4/4
#:     6.0         7/77                4/4
#:     6.5         3/77                4/4
#:     7.0         1/77                3/4
#:
#: 6.5 is the knee: every lower cut is strictly dominated -- 6.0 catches the
#: same four and deletes four more approved drugs for it -- and 7.0 buys two
#: false positives by dropping terfenadine, which is a drug withdrawn for
#: torsades and so exactly the detection this arm exists to make.
#:
#: The three it does delete are haloperidol 7.23, verapamil 6.77 and risperidone
#: 6.54, all three genuine hERG binders carrying QT labelling.  Verapamil is the
#: classic case of a strong hERG blocker that is not torsadogenic, because it
#: blocks calcium channels too -- so a hERG model is right about it and a
#: cardiac safety conclusion drawn from that alone would be wrong.  That is the
#: distinction the tier note refuses to blur, standing here as a measurement.
#:
#: These three are also, by the ``any`` join, the only approved drugs the tier
#: can ever delete: see :data:`ADMET_AI_HERG_JOIN_MAX`.
OPENADMET_HERG_PIC50_MAX = 6.5


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    """Write ``value`` at a dotted path, creating intermediate objects."""

    keys = path.split(".")
    cursor = target
    for key in keys[:-1]:
        nested = cursor.get(key)
        if not isinstance(nested, dict):
            nested = {}
            cursor[key] = nested
        cursor = nested
    cursor[keys[-1]] = value


def criterion_defaults(
    option: BackendOption,
    *,
    registry: PluginRegistry | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the producer and gate settings a fresh criterion starts with."""

    if option.plugin_ref is None:
        return dict(option.defaults), dict(option.gate_defaults)
    settings = with_schema_version(option.plugin_ref, option.defaults, registry=registry)
    if option.gate_plugin is None:
        return settings, dict(option.gate_defaults)
    gate_settings = with_schema_version(option.gate_plugin, option.gate_defaults, registry=registry)
    return settings, gate_settings


def build_criterion(
    spec: CriterionSpec,
    option: BackendOption,
    *,
    criterion_id: str | None = None,
    label: str | None = None,
    settings: dict[str, Any] | None = None,
    gate_settings: dict[str, Any] | None = None,
    evidence_from: dict[str, str] | None = None,
    registry: PluginRegistry | None = None,
) -> CriterionConfig:
    """Create a criterion bound to one executable option.

    ``evidence_from`` names the stage that should supply a contract this
    criterion consumes; see :class:`CriterionConfig` for when it is needed.
    """

    if option.plugin_ref is None:
        raise ValueError(
            f"option {option.id!r} for criterion {spec.id!r} has no executable adapter"
        )
    base_settings, base_gate = criterion_defaults(option, registry=registry)
    if settings:
        for key, value in settings.items():
            _set_path(base_settings, key, value)
    if gate_settings:
        for key, value in gate_settings.items():
            _set_path(base_gate, key, value)
    gate = (
        GateConfig(backend=option.gate_plugin, settings=base_gate)
        if option.gate_plugin is not None
        else None
    )
    return CriterionConfig(
        id=criterion_id or spec.id,
        criterion=spec.id,
        backend=option.plugin_ref,
        label=label or f"{spec.label} · {option.engine}",
        settings=base_settings,
        gate=gate,
        evidence_from=dict(evidence_from or {}),
    )


def _runnable(option: BackendOption | None, registry: PluginRegistry) -> bool:
    """Whether this installation can really execute ``option`` end to end.

    Adapter, threshold gate *and* the third-party packages the option declares.
    The last of those is easy to forget because the adapters register
    unconditionally and import lazily, so a missing package looks exactly like
    an installed one until the stage runs.
    """

    if option is None or option.plugin_ref is None:
        return False
    return option_availability(option, registry).runnable


def _criterion_if_available(
    criterion_id: str,
    registry: PluginRegistry,
    *,
    option_id: str | None = None,
    config_id: str | None = None,
    label: str | None = None,
    settings: dict[str, Any] | None = None,
    gate_settings: dict[str, Any] | None = None,
    evidence_from: dict[str, str] | None = None,
) -> CriterionConfig | None:
    """Build one criterion, or return ``None`` if this machine cannot run it.

    The default cascade is offered to whoever is sitting in front of the
    installation, so it may only contain steps that installation can actually
    execute.  A cascade that names a missing adapter would fail at compile time
    with an error the user did not cause.
    """

    spec = CRITERIA_BY_ID.get(criterion_id)
    if spec is None:
        return None
    if option_id is None:
        option = spec.default_option
    else:
        option = next((entry for entry in spec.options if entry.id == option_id), None)
    if not _runnable(option, registry):
        return None
    assert option is not None  # narrowed by _runnable
    return build_criterion(
        spec,
        option,
        criterion_id=config_id,
        label=label,
        settings=settings,
        gate_settings=gate_settings,
        evidence_from=evidence_from,
        registry=registry,
    )


def _step_if_available(
    step_id: str,
    backend: str,
    registry: PluginRegistry,
    *,
    settings: dict[str, Any] | None = None,
) -> StepConfig | None:
    if backend not in registry:
        return None
    return StepConfig(
        id=step_id,
        backend=backend,
        settings=with_schema_version(backend, settings or {}, registry=registry),
    )


def _scscore_criterion(registry: PluginRegistry) -> CriterionConfig | None:
    """Add the SCScore arm only when its published weights are on this machine.

    SCScore is worth having in the default because it disagrees with SA score
    for principled reasons -- one counts fragment frequency, the other learns
    from reaction records -- and the union of two such scores is a far more
    defensible filter than either alone.  But it reads a 6 MB weight file that
    ``molcascade assets fetch scscore`` provides, and offering a default that
    stops on a missing file would be a worse first experience than offering one
    tier with a single arm.
    """

    try:
        from molcascade.assets import asset_spec, asset_status
    except ImportError:  # pragma: no cover - assets ship with the package
        return None
    try:
        spec = asset_spec("scscore")
        if not asset_status(spec).ready:
            return None
        digest = spec.file(SCSCORE_ASSET_MEMBER).sha256
    except (KeyError, OSError):
        return None
    return _criterion_if_available(
        "synthesizability",
        registry,
        option_id="scscore",
        config_id="synthesis_scscore",
        label="Synthesizability · SCScore (reaction-trained)",
        settings={
            "weights_path": f"asset:{spec.id}/{SCSCORE_ASSET_MEMBER}",
            "expected_weights_sha256": digest,
            "fingerprint": "bits",
        },
        # SCScore is a *relative* score -- it was trained to rank a reaction's
        # product above its reactants, not to place an absolute line -- so a
        # threshold on it has to be read off the population it will judge.  On
        # the 77-drug approved oral panel the distribution is p25 2.81, median
        # 3.41, p75 4.20, p90 4.65.  The 3.5 the catalogue still offers sits on
        # the median and rejects 49% of marketed drugs, a coin flip dressed as
        # a filter; 4.5 sat above p75 and rejected 12%.
        #
        # 4.0 is between the two and rejects 24 of 77 (31%), which is the
        # largest single cost in this funnel and is a deliberate one: it removes
        # every kinase inhibitor on the panel -- sorafenib 4.18, erlotinib 4.22,
        # sunitinib 4.20, gefitinib 4.35, palbociclib 4.61, lapatinib 4.82,
        # ibrutinib 4.93, imatinib 5.00.  Marketed kinase drugs are elaborate,
        # and a cut here says this project would rather have simpler chemistry
        # than chemistry shaped like the ones that already exist.  Raise it to
        # 4.5 to get them back.
        gate_settings={"expected_direction": "HIGHER_HARDER", "maximum": 4.0},
    )


def _chemistry_tier(registry: PluginRegistry) -> TierConfig | None:
    criteria = [
        criterion
        for criterion in (
            _criterion_if_available(
                "structure_validity",
                registry,
                # PAINS is handled two tiers down, where the verdict is a warning
                # the user can read rather than a rejection nothing can undo.
                settings={"policy.flag_pains": False},
            ),
        )
        if criterion is not None
    ]
    if not criteria:
        return None
    return TierConfig(
        id="t1_chemistry",
        title="Chemistry triage",
        mode="all",
        criteria=tuple(criteria),
        note=(
            "Terminal rejects: unparseable strings, valence errors, fragment-only "
            "records and disallowed elements. Nothing removed here can be recovered "
            "by a later score, which is why only broken chemistry is removed here."
        ),
    )


def _property_tier(registry: PluginRegistry) -> TierConfig | None:
    criteria = [
        criterion
        for criterion in (
            _criterion_if_available(
                "physchem_window",
                registry,
                config_id="physchem_window",
                settings={
                    # Deliberately an *envelope*, not a preference.  These bounds
                    # were set by running them against 71 approved oral drugs
                    # (tests/fixtures/approved_drugs.py) and widening until the
                    # only rejections left were ones with a chemical argument
                    # behind them.  A tighter window costs real chemistry for no
                    # gain the later tiers cannot deliver better: this tier sees
                    # every molecule, so its mistakes are unrecoverable, while
                    # ADMET, alerts and synthesizability all discriminate harder
                    # on the population that reaches them.
                    #
                    # 200-550 rejected 21% of that panel -- aspirin at 180 Da,
                    # caffeine at 194, theophylline at cLogP -1.04 -- which is
                    # arithmetic, not chemistry.  The bounds below reject 5.6%,
                    # and still reject every one of the six drugs the panel marks
                    # as genuinely outside oral small-molecule space.
                    "mw_min": 150.0,  # below Ghose's 160; keeps aspirin, caffeine
                    "mw_max": 650.0,  # atorvastatin is 559 and sells by the tonne
                    "clogp_min": -3.0,  # metformin and the nucleosides live here
                    "clogp_max": 6.0,  # Egan's ceiling is 5.88; montelukast (8.9) still goes
                    "tpsa_max": 180.0,  # Veber's 140 is a soft optimum, not a cliff
                    "hbd_max": 5,  # Lipinski, unmodified: nothing on the panel hits it
                    "hba_max": 10,  # likewise
                    "rotatable_bonds_max": 12,  # Veber's 10 costs atorvastatin and montelukast
                    "absolute_formal_charge_max": 2,
                },
            ),
            _criterion_if_available(
                "ring_topology",
                registry,
                settings={
                    # Ceilings on *shape*.  They exist because a generated
                    # library is the one input where shape goes wrong in ways
                    # every other tier in this funnel scores as excellent: a
                    # seven-ring flat polycyclic with no rotatable bond passes
                    # Ro5, QED, the alert catalogues and the ADMET panel, and is
                    # not a molecule anyone would make.
                    #
                    # These are tighter than any published drug-likeness
                    # calibration, and deliberately so -- "at most two fused
                    # rings per system, four across all of them" is a house rule
                    # about what is worth making, not a claim about what is
                    # drug-like.  FAF-Drugs4's own values (6 rings, 18 atoms per
                    # system) pass up to 90% of 916 approved oral drugs; the
                    # numbers below do not, and the difference is the point.
                    #
                    # Measured on the approved oral panel, on the same standard
                    # as the window above: 10 of 77 rejected, up from 2.  Every
                    # one of the 10 is rejected by 'max_fused_rings' alone --
                    # amitriptyline, carbamazepine, codeine, dexamethasone,
                    # levofloxacin, loratadine, morphine, olanzapine,
                    # prednisolone, quetiapine -- and the other three ceilings
                    # cost nothing beyond it on this panel.  A project screening
                    # a purchasable library rather than a generated one should
                    # raise 'max_fused_rings' back to 4 first: it is the one
                    # number here that is doing all of the rejecting.
                    "max_rings": 5,  # free on the panel; a ceiling on total count
                    "max_fused_rings": 2,  # at most a bicyclic per system
                    "max_fused_ring_total": 4,  # two bicyclics, not three
                    "max_ring_system_size": 16,  # atoms in one fused system
                },
            ),
            _criterion_if_available(
                "physchem_window",
                registry,
                option_id="mordred_descriptor_window",
                config_id="aromatic_ring_count",
                label="Aromatic ring count",
                settings={
                    # The one thing in this tier that is not a restatement of
                    # size.  Ritchie and Macdonald showed developability falling
                    # off above three aromatic rings across ~3,000 oral drugs and
                    # candidates; four leaves a ring of slack, because the
                    # boundary is a trend and this tier's mistakes cannot be
                    # recovered later.
                    #
                    # Measured before shipping, on the same standard as the
                    # window above: of the approved oral panel it rejects one
                    # molecule -- lapatinib, at five rings, which that panel
                    # already carries as a deliberate edge case.  Ritchie's own
                    # boundary of three would reject six.
                    "descriptor": "naRing",
                    "maximum": 4,
                },
            ),
        )
        if criterion is not None
    ]
    if not criteria:
        return None
    return TierConfig(
        id="t2_physchem",
        title="Physicochemical window",
        mode="all",
        criteria=tuple(criteria),
        note=(
            "Descriptor arithmetic on every surviving molecule: cheap, and the first "
            "place a funnel can lose something it will never get back. The window is "
            "wide on purpose -- it is meant to exclude molecules that are not oral "
            "small molecules at all, not to express a preference among ones that are. "
            "Narrow it to a project window once you "
            "know what you are looking for. Measured against the approved oral panel "
            "the size window rejects 4, the aromatic ring cap 1 and the ring topology "
            "ceilings 10 -- the last of those is a house rule about fused systems, not "
            "a drug-likeness calibration, and is the first thing to loosen on a "
            "purchasable library."
        ),
    )


def _druglikeness_tier(registry: PluginRegistry) -> TierConfig | None:
    criteria = [
        criterion
        for criterion in (
            _criterion_if_available(
                "drug_likeness",
                registry,
                config_id="druglike_ro5_qed",
                label="Rule of Five and QED · RDKit",
                settings={
                    # Zero violations, not Lipinski's own "no more than one".
                    # On the approved oral panel that is 8 of 77 rather than 3:
                    # it adds levothyroxine, sertraline, sorafenib, tamoxifen and
                    # verapamil to atorvastatin, lapatinib and montelukast.  The
                    # argument for it is the same as the ring ceilings above --
                    # a generated library has no scarcity of molecules that
                    # violate nothing, so the cheapest thing to spend here is
                    # slack.  Raise it to 1 to get Lipinski's actual rule back.
                    "maximum_rule_of_five_violations": 0,
                    # QED's own paper puts approved oral drugs around 0.5; a floor of
                    # 0.3 removes the clearly undesirable without pretending the
                    # score is sharp enough to rank the survivors.
                    "minimum_qed": 0.3,
                    "failure_action": "reject",
                },
            ),
            _criterion_if_available(
                "drug_likeness",
                registry,
                option_id="medchem_rules",
                config_id="druglike_oral_consensus",
                label="Oral-absorption consensus · medchem",
                settings={
                    # Three independent published rules for oral absorption, and a
                    # molecule needs two of them.  The quorum is the point.  Run
                    # alone against 71 approved oral drugs these reject 9.9%
                    # (Lipinski), 5.6% (Veber) and 8.5% (Egan); requiring two of
                    # the three rejects 5.6%, because the drugs each rule dislikes
                    # are largely different drugs.  No single rule set gets to
                    # delete a molecule on its own idiosyncrasy.
                    #
                    # medchem ships 22 rules and the tempting ones are traps.
                    # rule_of_generative_design_strict -- which this tier used to
                    # apply as a hard reject -- removes 49% of that panel,
                    # including ibuprofen, imatinib, morphine and olanzapine: it
                    # asks for a ring-heavy, chain-light, TPSA >= 40 shape that
                    # much of pharmacology simply does not have. It is a fine
                    # *preference* for generated matter and a bad gate. Select it
                    # in the builder if you want it; it is not a default.
                    "rules": "rule_of_five,rule_of_veber,rule_of_egan",
                    "combination": "at_least",
                    "minimum_passes": 2,
                },
            ),
        )
        if criterion is not None
    ]
    if not criteria:
        return None
    return TierConfig(
        id="t3_druglike",
        title="Drug-likeness",
        mode="all",
        criteria=tuple(criteria),
        note=(
            "Drug-likeness is a fuzzy judgement, so both arms here are consensus "
            "rather than verdict: an aggregate score with a low floor, and a vote "
            "among three oral-absorption rules. That is the opposite of how the "
            "alerts tier is built, and deliberately -- a rule disliking a molecule "
            "is an opinion, whereas a reactive group is a fact. Switch the tier to "
            "Serial if you would rather chain them."
        ),
    )


def _alerts_tier(registry: PluginRegistry) -> TierConfig | None:
    criteria = [
        criterion
        for criterion in (
            _criterion_if_available(
                "structural_alerts",
                registry,
                config_id="alerts_rdkit",
                label="Reactivity and interference · RDKit catalogues",
                settings={
                    # Which catalogue deletes and which annotates was decided by
                    # measurement against 71 approved oral drugs, and the answer
                    # was not the one this tier originally shipped.
                    #
                    # NIH flags 3 molecules in the whole 77-drug panel and is right
                    # about all three: ibrutinib's acrylamide, amoxicillin's
                    # beta-lactam, levothyroxine's four iodines. Two covalent
                    # warheads and a polyiodide -- exactly the matter that should
                    # not enter a funnel whose downstream is non-covalent docking
                    # and MM-PBSA, where a scored pose for a covalent binder is
                    # meaningless. 2.8% false-positive rate, every hit defensible.
                    "nih_action": "reject",
                    # Brenk flags 31% of the same panel, including aspirin,
                    # paracetamol, morphine, warfarin, gefitinib and omeprazole.
                    # Nine of those hits are "Aliphatic_long_chain" -- four sp3
                    # atoms in a row. Rejecting on this was deleting a third of
                    # known drug space to catch a handful of genuine liabilities
                    # the NIH set catches anyway.
                    #
                    # Measuring it also turned up a separate bug, which is fixed
                    # rather than tolerated: RDKit's standardizer rewrites every
                    # neutral sulfoxide S(=O) into the charge-separated [S+][O-]
                    # form, and RDKit's own Brenk catalogue then flags that form
                    # as "charged_oxygen_or_sulfur_atoms" -- so every
                    # proton-pump inhibitor, sulindac and modafinil left the run
                    # for a reason that was purely how the molecule got written
                    # down. molcascade.chemistry.alerts now hands the catalogues
                    # the neutral depiction they were authored against, and
                    # test_shipped_defaults.py holds that shut.
                    "brenk_action": "warn",
                    # PAINS is an assay-interference hypothesis, and this funnel
                    # ends in physics rather than a biochemical assay. Low false
                    # positives here (2.8%), but the claim does not apply, so the
                    # match is a note for whoever reads the report.
                    "pains_action": "warn",
                    # ZINC's hits on the panel are all "Non-Hydrogen_atoms", a size
                    # cap the physicochemical tier already enforces with a number
                    # the user can see and edit.
                    "zinc_action": "ignore",
                },
            ),
            _criterion_if_available(
                "structural_alerts",
                registry,
                option_id="medchem_common_alerts",
                config_id="alerts_medchem",
                label="Screening-deck liabilities · medchem",
                settings={
                    # Dundee used to be in this list and is now not. It is the same
                    # rule set as the Brenk catalogue in the arm above -- Brenk et
                    # al. published it from Dundee -- so listing it here rejected a
                    # third of the approved-drug panel a second time, under a
                    # different name, from a tier note that claimed two independent
                    # opinions were being taken. Two arms agreeing because they are
                    # the same data is not agreement.
                    #
                    # BMS and Glaxo are the reactive-chemistry sets, measured at 8%
                    # and 4% on that panel, and the molecules they flag there are
                    # ones a screening deck should genuinely exclude.
                    "alert_sets": "BMS,Glaxo",
                    "use_nibr": True,
                    "nibr_reject_at_severity": 10,
                    "exclude_action": "reject",
                    "flag_action": "warn",
                    "annotation_action": "ignore",
                    # NIBR's exclusions come in two kinds and only one of them
                    # is about the molecule.  67 of its 444 rules fire on
                    # compound-class membership -- steroid, peptide,
                    # nucleoside, glycoside, retinoid, fatty acid -- because a
                    # physical screening deck does not want promiscuous or
                    # assay-confounding matter taking up wells.  That is an
                    # argument about a plate, not about whether a molecule can
                    # bind the target this cascade is aimed at, and applied as
                    # a reject it costs dexamethasone and prednisolone off the
                    # approved-drug panel and every steroid PRISM generates.
                    # The liability rules are untouched: ranitidine still
                    # rejects on its nitroalkane, at the same severity.
                    "nibr_compound_class_action": "warn",
                },
            ),
            _criterion_if_available(
                "structural_alerts",
                registry,
                option_id="lilly_medchem",
                config_id="alerts_lilly_demerits",
                label="Graded demerits · Lilly Medchem Rules",
                settings={
                    # The only graded arm in this tier. The other two answer
                    # "does it match"; this one answers "how much is wrong with
                    # it", summing demerits across motifs so that a molecule
                    # with no single disqualifying group can still be flagged for
                    # accumulating blemishes. That total is published as
                    # derived_metric evidence and travels to the report.
                    #
                    # Every action warns, and the panel is why. These 275 rules
                    # reject 17 of the 77 approved oral drugs -- amoxicillin,
                    # aspirin, dexamethasone, metformin, ranitidine, and
                    # ibrutinib, lapatinib and sunitinib, which are marketed
                    # kinase inhibitors. A default that deletes a fifth of known
                    # drug space, concentrated in the chemotype most cascades are
                    # aimed at, is not a default.
                    #
                    # The rules are still the best-enriching thing in the
                    # catalogue once a project measures them on its own actives:
                    # against 231 molecules with measured STK17B affinity they
                    # reject 7.7% of those below 100 nM and 80.2% of the
                    # generated library screened beside them. That is an argument
                    # for reading the demerit total and choosing a cutoff, not
                    # for inheriting one.
                    "mode": "relaxed",
                    "rejection_action": "warn",
                    "demerit_action": "warn",
                    "unreadable_action": "warn",
                },
            ),
        )
        if criterion is not None
    ]
    if not criteria:
        return None
    return TierConfig(
        id="t4_alerts",
        title="Alerts and liabilities",
        mode="all",
        criteria=tuple(criteria),
        note=(
            "Only reactive chemistry deletes a molecule here: covalent warheads, "
            "unstable groups, known assay-destroying functionality. Everything else "
            "-- interference hypotheses, screening-deck hygiene, medicinal-chemistry "
            "taste -- is recorded as a warning and travels with the molecule to the "
            "report. The arms use union rather than a vote, which is the right way "
            "round for hazards: one catalogue seeing a beta-lactam is enough, and "
            "waiting for a second opinion would only let it through.\n\n"
            "The third arm answers a different question from the other two. They ask "
            "whether a molecule matches something; it asks how much is wrong with the "
            "molecule, summing graded demerits across motifs and publishing the total "
            "as evidence. Nothing is rejected on that total by default -- on the "
            "approved-drug panel it would cost a fifth of them, kinase inhibitors "
            "included -- so it arrives as a number to threshold rather than a verdict "
            "already taken."
        ),
    )


def _synthesis_tier(registry: PluginRegistry) -> TierConfig | None:
    criteria = [
        criterion
        for criterion in (
            _criterion_if_available(
                "synthesizability",
                registry,
                config_id="synthesis_sa_score",
                label="Synthesizability · SA score (fragment frequency)",
                # SA score on 71 approved oral drugs: median 2.65, p95 4.53,
                # p99 5.20.  A cutoff of 4.5 sat right on the 95th percentile and
                # the four drugs above it were morphine, codeine, simvastatin and
                # dexamethasone -- i.e. the loss was not spread evenly, it fell
                # entirely on natural-product-derived polycyclics, which is a
                # chemotype bias rather than a difficulty threshold.  5.0 keeps
                # them and still rejects the genuinely intractable.
                gate_settings={"expected_direction": "HIGHER_HARDER", "maximum": 5.0},
            ),
            _scscore_criterion(registry),
        )
        if criterion is not None
    ]
    if not criteria:
        return None
    union = len(criteria) > 1
    return TierConfig(
        id="t5_synthesis",
        title="Synthesizability",
        # "any" is the point: drop a molecule only when two scores built on
        # different evidence agree that it is hard.  With one arm available the
        # mode is immaterial, and "all" reads more honestly in the report.
        mode="any" if union else "all",
        criteria=tuple(criteria),
        note=(
            "Two scores that disagree by construction -- SA counts how common a "
            "molecule's fragments are, SCScore learns complexity from twelve "
            "million reactions -- and a molecule needs only one of them to call it "
            "tractable. Neither predicts whether a route exists: no threshold on "
            "either cleanly separates molecules a retrosynthesis planner can solve "
            "from ones it cannot, so treat this as coarse triage and put a route "
            "search below it once the population is small enough."
            if union
            else "SA score is a fragment-frequency proxy. It is not a route and it "
            "is not a step count. Run 'molcascade assets fetch scscore' to add a "
            "second, reaction-trained opinion alongside it."
        ),
    )


# There is deliberately no rules-based ADMET tier in the starter cascade.
#
# There used to be one, applying Egan's egg alone as a hard gate.  It was
# removed because Egan is already one of the three rules the drug-likeness tier
# votes on, and a tier that re-applies a vote's losing member as a verdict
# silently overrides the vote.  Measured on the approved-oral-drug panel the
# damage was exact: caffeine, theophylline, metformin and amoxicillin each pass
# Lipinski and Veber, win the 2-of-3 quorum, and were then deleted downstream on
# the single rule they lost.  Panel rejection was 10/77 with the tier and is
# 6/77 without it, and the vote's stated promise -- that no single rule set gets
# to delete a molecule on its own idiosyncrasy -- is only true without it.
#
# The wider point is that this backend has no warning outcome, so any rule put
# here is a rule the run deletes on, and no rule medchem ships is permissive
# enough for that job: measured alone on the same panel the most lenient are
# Veber 6/77 and Lipinski 10/77, and the rest run from Ghose at 28/77 to
# druglike_soft at 51/77.  Absorption is asked once, as a quorum, one tier up.
#
# A predictive ADMET tier is a different thing, and it is now the default one:
# :func:`_admet_tier` below fills the t6 slot with hERG liability, asked of two
# models that were trained by different people on different labels.  It deletes
# only where they agree, which is the same construction and the same sentence as
# the synthesizability tier above it.
#
# What changed is not the trust argument but where the argument is settled.  A
# tier that needs a checkpoint fetched, or a licence accepted, must not be a
# default that fails closed on a fresh install -- so neither arm asserts
# anything it cannot check first, and a tier with no arms is no tier at all.


def _installed_version(distribution: str) -> str | None:
    """The installed release of one distribution, or ``None`` if it is absent.

    Reads ``dist-info`` metadata and nothing else.  That matters because this
    runs inside :func:`default_cascade`, which the tests, the builder and every
    ``molcascade`` invocation call: importing the package to ask its version
    would drag torch in to answer a question about a text file.
    """

    import importlib.metadata

    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _admet_ai_arm(registry: PluginRegistry) -> CriterionConfig | None:
    """ADMET-AI's hERG head, but only on the release the digests were taken from.

    This arm is the one that needs a trust acknowledgement, and the default
    supplies it rather than asking -- which is only defensible because of what
    the flag actually governs.  It does not mean "load whatever bytes are
    there".  ``catalog.py`` pins two digests measured from admet-ai
    ``PINNED_ADMET_AI_VERSION``, and the stage rehashes the installed package
    tree and model tree in the *parent* and refuses to hand a shard any path
    whose bytes differ, before a child calls ``torch.load``.  So the assertion
    being made here is narrow and checkable: this is that release, and the
    digests still match.

    The version is checked here because the digests are only informative while
    they belong to the release installed; a different release means the pins
    describe something else, and then the honest default is no arm at all rather
    than a trusted one.  What is deliberately *not* checked here is the digests
    themselves -- that means hashing gigabytes of weights, and this function
    runs on every ``default_cascade()`` call.  Tampering is still caught, one
    layer down, when the stage starts and reports a digest mismatch: the right
    diagnosis, rather than a missing tier that says nothing.

    The catalogue default stays ``False``, so the builder goes on asking the
    operator the question this answers for the starter cascade.
    """

    from molcascade.plugins.builtin.admet import PINNED_ADMET_AI_VERSION

    if _installed_version("admet-ai") != PINNED_ADMET_AI_VERSION:
        return None
    return _criterion_if_available(
        "admet_endpoint",
        registry,
        option_id="admet_ai_v2",
        config_id="admet_herg_ai",
        label="hERG liability · ADMET-AI (blocking probability)",
        settings={"allow_unsafe_model_deserialization": True},
        # Well above the 0.5 an unexamined "it's a probability" reading
        # suggests, because on this panel half the approved drugs score above
        # 0.5 -- the model predicts hERG *binding*, and verapamil, diltiazem and
        # imatinib genuinely bind it.  Why this arm cuts lower than the same
        # option's standalone gate is measured in ADMET_AI_HERG_JOIN_MAX: the
        # join bounds what a looser cut can cost.
        gate_settings={"endpoint_id": "herg_blocking", "maximum": ADMET_AI_HERG_JOIN_MAX},
    )


def _openadmet_paths_resolve(registry: PluginRegistry) -> bool:
    """Would this machine answer both of OpenADMET's installation paths?

    Asked through the plugin's own config model, so there is one resolution
    order rather than two that can disagree: ``fill_machine_paths`` is handed an
    empty mapping and reports what it can supply from the environment and from
    the layout the bootstrap script writes.  A field it cannot answer comes back
    absent, and absent means no arm.
    """

    reference = "prediction.openadmet@0.1.0"
    if reference not in registry:
        return False
    try:
        # Trust is part of the question, and the membership test above
        # deliberately does not ask it: ``entry`` looks up registry metadata
        # "without making a trust decision", so ``in`` is true of a registration
        # this project has not vouched for.  Such a registration is not one to
        # build a default arm on, so resolve it properly and let the refusal
        # answer here.
        registry.get(reference)
        # Named as a class rather than read off the resolved plugin, because the
        # ``StagePlugin`` boundary declares ``descriptor`` and ``execute`` and
        # nothing else -- a config model is not part of it, and reaching for one
        # there would be an attribute that happens to exist on the concrete
        # class.  The import is a module-cache hit: registering the plugin above
        # is what imported the module in the first place.
        from molcascade.plugins.builtin.openadmet import OpenADMETConfig

        specs = OpenADMETConfig.installed_paths()
        filled = fill_machine_paths({}, engine_id=OpenADMETConfig.engine_id, paths=specs)
    except (ImportError, OSError, PluginError, TypeError):
        # A plugin that does not declare machine paths is not this plugin, and an
        # untrusted registration is not one to build a default on.  Anything
        # unexpected means "cannot confirm", which is not the same as "installed".
        return False
    if not isinstance(filled, Mapping):  # pragma: no cover - defensive
        return False
    return all(str(filled.get(spec.field, "")).strip() for spec in specs)


def _openadmet_arm(registry: PluginRegistry) -> CriterionConfig | None:
    """OpenADMET's released hERG baseline, when its two paths resolve.

    No trust flag, and none is needed: this is an isolated CLI, so the
    checkpoint is deserialized inside OpenADMET's own interpreter and
    MolCascade never calls ``torch.load`` on it.  What decides whether the arm
    appears is whether the executable and the model directory are really on
    this machine.

    That question has to be asked *here*, which is a departure from every other
    isolated engine in this file.  :func:`option_availability` deliberately
    does not look at the filesystem -- it answers about an installation, not a
    host -- so an uninstalled engine still produces a runnable option, and the
    preflight refuses the run on the machine that was going to do the work.
    For the docking engines that is correct: a host with no docking engine
    cannot screen, and saying so before the first molecule is the useful
    behaviour.  For an optional ADMET arm it would be exactly wrong.  The tier
    is a bonus, and a bonus that turns a fresh install's starter cascade into a
    preflight failure is worse than no tier -- so this arm is only offered
    where it can actually run.

    The check reuses the plugin's own resolution rather than reimplementing it,
    so the arm appears if and only if the config validator would have filled
    the same two fields in: a value from the environment first
    (``MOLCASCADE_OPENADMET_EXECUTABLE`` and ``..._MODEL_DIR``), then the layout
    ``envs/bootstrap.sh`` writes.  Both are existence-only tests.  Whether the
    executable really is OpenADMET, and whether the directory holds a
    checkpoint rather than an unpulled pointer, stays where it belongs -- in
    the preflight, on the host, where a wrong answer can be reported.
    """

    if not _openadmet_paths_resolve(registry):
        return None
    return _criterion_if_available(
        "admet_endpoint",
        registry,
        option_id="openadmet",
        config_id="admet_herg_openadmet",
        label="hERG liability · OpenADMET (predicted pIC50)",
        gate_settings={"endpoint_id": "herg_pic50", "maximum": OPENADMET_HERG_PIC50_MAX},
    )


def _admet_tier(registry: PluginRegistry) -> TierConfig | None:
    """hERG liability, asked of two models and acted on only when both agree.

    Placed at t6 -- after synthesizability, before structure QC -- because that
    is the last point at which a molecule is still cheap.  On the 103,515-molecule
    reference run 8,782 molecules reached this position, and conformer generation
    plus two docking engines then cost 3,020 s of the 4,607 s spent getting to
    the affinity tier: 65.6% of the work, 60.3% of it docking alone.  Every
    molecule deleted here is one that does not pay that.

    ``any`` is the deliberate choice and it is the permissive one: a molecule
    survives unless *both* models object.  Neither is strong enough to be given
    the verdict alone -- ADMET-AI's hERG head reports AUROC 0.84, and
    OpenADMET's released baseline is trained on its full dataset with no
    held-out split and publishes no metric at all.  Two independent objections
    is a claim worth deleting on; one is a reason to look.

    What that costs and buys is measured, on the 77 approved oral drugs and the
    four withdrawn or restricted QT drugs, with both arms at the cuts above:
    the tier deletes **3 of 77 approved** and catches **4 of 4 withdrawn**.
    The three are haloperidol, verapamil and risperidone -- every one a real
    hERG binder carrying QT labelling, and every one flagged independently by
    both models, which is the join working rather than failing.  Each arm alone
    is worse in one direction or the other: ADMET-AI at its own cut deletes
    13/77, OpenADMET catches the same four but on a model with no held-out
    split at all.

    So this tier is not where a hERG problem gets caught.  It is where the
    molecules that two unrelated models both call cardiotoxic stop consuming
    docking time.  A programme that cares about QT owes the endpoint a
    measurement, not a screen -- and verapamil is the standing reminder of why:
    it is a strong hERG blocker that is not torsadogenic, because it blocks
    calcium channels too.  Both models are right about its affinity and a
    cardiac safety conclusion drawn from that alone would be wrong.
    """

    criteria = [
        criterion
        for criterion in (_admet_ai_arm(registry), _openadmet_arm(registry))
        if criterion is not None
    ]
    if not criteria:
        return None
    union = len(criteria) > 1
    return TierConfig(
        id="t6_admet",
        title="hERG liability",
        # The enum rather than the bare strings its neighbours use, because a
        # conditional is the one place the literal loses its type: mypy widens
        # ``"any" if union else "all"`` to ``str``.  ``TierMode`` is a StrEnum,
        # so this is the same value in memory and the same token in JSON.
        mode=TierMode.ANY if union else TierMode.ALL,
        criteria=tuple(criteria),
        note=(
            "Two models on the same endpoint and different labels -- ADMET-AI "
            "predicts the TDC binary blocker label, so its number is a probability; "
            "OpenADMET regresses pIC50 from ChEMBL -- joined so that a molecule is "
            "deleted only when both object. They are recorded as two endpoints "
            "because one is a probability and the other a potency, and neither is a "
            "rescaling of the other. Predicted hERG affinity is not a cardiac "
            "safety assessment: approved drugs bind hERG and are approved anyway, "
            "on an affinity-to-exposure margin this stage cannot see."
            if union
            else "One model's opinion on hERG, used to stop the clearest liabilities "
            "before conformers and docking rather than to assess cardiac safety. "
            "Install the second arm -- 'bash envs/bootstrap.sh openadmet' -- and a "
            "molecule then needs two independent objections before it is deleted."
        ),
    )


def _structure_qc_tier(registry: PluginRegistry) -> TierConfig | None:
    """A second toolkit's opinion on the structures, taken at the handoff.

    The reason this is a tier of its own at the bottom rather than a second arm
    of tier one is cost.  Measured on one core: RDKit parse and sanitize runs at
    ~850 molecules/s, the property panel at ~270, the alert catalogues at ~85
    and this checker at ~100 -- so against two million molecules it is roughly
    six hours in tier one and a few minutes here, and threading does not change
    that because the InChI call holds the GIL for its whole duration.

    Placing it last costs nothing scientifically, because nothing between here
    and there can rescue a molecule it rejects, and the alternative -- leaving
    it switched off by default because it is too slow to run first -- would mean
    the structures handed to docking never get a second toolkit's opinion at
    all.  It is also how the check is used upstream: EBI runs it when compounds
    are *registered* into ChEMBL, not as a bulk pre-filter.
    """

    criterion = _criterion_if_available(
        "structure_validity",
        registry,
        option_id="chembl_structure_pipeline",
        # Tier one already claims the bare criterion id, and every stage id in a
        # cascade has to be unique.
        config_id="structure_qc_chembl",
        label="Structure quality · ChEMBL checker (InChI second opinion)",
    )
    if criterion is None:
        return None
    return TierConfig(
        id="t7_structure_qc",
        title="Structure quality",
        mode="all",
        criteria=(criterion,),
        note=(
            "The last thing that happens before the shortlist leaves for docking. "
            "RDKit decided in tier one that these strings parse; this asks whether "
            "the IUPAC InChI library agrees they are molecules somebody meant to "
            "draw, and rejects at penalty 6 -- radicals off the known list, "
            "polymers, embedded 3D. Undefined stereochemistry scores 2 and is kept "
            "and counted, because in a generated library it describes almost "
            "everything. Around 100 molecules/s, which is why it is here and not "
            "at the top."
        ),
    )


def _conformer_tier(registry: PluginRegistry) -> TierConfig | None:
    """One embedded, minimised conformer per survivor, for the docking tier below.

    It is a tier of its own rather than a step inside each engine because the
    engines that *search* a box -- Uni-Dock and GNINA -- must search the same
    starting geometry, or a disagreement between them is a question about which
    conformer rather than about which scoring function.  Embedding once also
    pays ETKDG plus the MMFF minimisation once instead of once per engine, and
    that is most of this level's CPU.

    KarmaDock is not one of those engines, and since the shipped docking tier
    pairs it with Uni-Dock this has to be said plainly rather than left to be
    discovered: it takes SMILES on its command line and embeds its own geometry
    internally, which is why its adapter binds ``parent/v1`` rather than this
    table (see ``docking/ligand_prep.py``).  So the consensus one tier down is
    between two engines that agreed about a *molecule*, not about a geometry.
    For a molecule whose stereocentres its SMILES leaves undefined -- which in
    a generated library is most of them, as ``_structure_qc_tier`` says -- the
    two embeddings can settle those centres differently, and then the two
    engines are scoring different stereoisomers.  ``molcascade stereo`` reports
    which molecules had a configuration chosen for them this way.
    """

    criterion = _criterion_if_available(
        "ligand_conformers",
        registry,
        settings={
            # One conformer, not an ensemble.  Both engines below do their own
            # conformational search from whatever they are given, so a second
            # input conformer buys a second copy of that search rather than
            # more coverage -- and at this position in the funnel that is the
            # single most expensive thing this file could ask for.
            "conformers_per_molecule": 1,
            "embed_attempts": 3,
            "minimize": True,
            "max_minimize_iterations": 500,
            "prune_rms_threshold": 0.5,
        },
    )
    if criterion is None:
        return None
    return TierConfig(
        id="t8_ligand_conformers",
        title="3D conformer generation",
        mode="serial",
        criteria=(criterion,),
        note=(
            "The handoff from 2D to 3D. Everything above this line is a property of "
            "the graph; everything below needs coordinates. A molecule that will not "
            "embed leaves here, and the run records why rather than letting it reach "
            "an engine that would report it as a bad score."
        ),
    )


def _docking_tier(registry: PluginRegistry) -> TierConfig | None:
    """Two engines, in parallel, against the same receptor and the same conformer.

    ``all`` rather than ``serial`` is the whole design of this tier.  Run in
    series, Uni-Dock's rejects never reach KarmaDock and the run can only say
    what the first engine thought; run in parallel, both score every molecule
    that arrives and the survivors are the ones *both* engines liked -- the
    same set series would have produced, but with the per-engine numbers for
    everything, so a disagreement is visible instead of invisible.  Consensus
    between an empirical function and a learned one is the cheapest real
    protection there is against a pose that is an artefact of one of them.

    The cost of parallel is that KarmaDock sees the whole tier input rather
    than Uni-Dock's survivors.  KarmaDock is a GPU screening model at
    thousands of ligands a minute, so on the populations that reach here that
    is minutes, and it buys the second opinion on every molecule rather than
    on the ones the first engine already approved.

    The thresholds are deliberately strict.  This tier is the last one before
    the shortlist, so it is the only place where being wrong costs nothing but
    compute: a molecule it rejects was going to be rejected by a chemist.
    """

    criteria = [
        criterion
        for criterion in (
            _criterion_if_available(
                "docking_score",
                registry,
                option_id="unidock",
                config_id="docking_score",
                label="Docking score · Uni-Dock",
                # A shortlist threshold, not a binding prediction: Vina's own
                # error bar is wider than the gap between -9.5 and -8.5, so what
                # this number really fixes is how many molecules reach a human.
                #
                # It was -9.5 while the tiers above admitted three- and
                # four-ring fused systems.  They no longer do, and a docking
                # score is not scale-free: replaying the 103,515-molecule
                # STK17B run through the ceilings above leaves 3,923 of the
                # 21,873 molecules that used to dock, and they are smaller
                # (heavy-atom median 22 against 26), so they score worse
                # (Uni-Dock median -8.77 -> -7.85).  Holding -9.5 against that
                # population would pass 15 molecules out of the whole library,
                # which is a filter that has stopped measuring anything.
                #
                # -8.5 keeps the tier as selective as it was -- 23.7% of what
                # arrives, against 21.7% before -- rather than as absolute as it
                # was.  Post-docking metrics one tier down read the same score
                # normalized by size, which is the honest way to compare across
                # a population this much smaller.
                gate_settings={"maximum": -8.5},
            ),
            _criterion_if_available(
                "docking_score",
                registry,
                option_id="karmadock",
                # A second criterion on the same slot needs its own id: the
                # config id is what the trace, the score table and the pose SDF
                # are named after, and two stages cannot share one.
                config_id="docking_score_2",
                label="Docking score · KarmaDock",
                # KarmaDock's MDN score is not a kcal/mol and does not convert
                # to one -- higher is better, and 40 is its own scale.  Pairing
                # it with the Vina cut is the point: two scales that cannot be
                # averaged, so agreement means something.
                #
                # Relaxed from 45 for the same reason and by the same measure as
                # the Uni-Dock cut above: on the smaller molecules the tightened
                # ceilings admit, the KarmaDock median falls 38.1 -> 32.1, and
                # 40 passes 13.6% of them against 16.2% at 45 before.  The two
                # cuts together put 246 molecules through this tier.
                gate_settings={"minimum": 40.0},
            ),
        )
        if criterion is not None
    ]
    if not criteria:
        return None
    return TierConfig(
        id="t9_docking",
        title="Docking",
        mode="all",
        criteria=tuple(criteria),
        note=(
            "The only tier that needs a protein: a run that reaches it without "
            "'--receptor' and a binding site is refused before it reads its first "
            "molecule. Both engines score every molecule that arrives and both must "
            "pass, so what leaves here is what two independent functions agreed on. "
            "Poses are kept and checked -- PoseBusters runs inside each engine -- so "
            "it is a structure, not just a number. Screening without a target means "
            "deleting this tier and the post-docking metrics that read it."
        ),
    )


#: Adapters whose docking scores are interaction energies in kcal/mol.  The
#: post-docking metrics below are only defined on that scale -- ligand
#: efficiency divides a score by heavy atoms, and dividing KarmaDock's MDN
#: score by heavy atoms produces a ratio with no name -- so the tier is built
#: only when one of these is present, rather than built and then refused at
#: run time on a machine that happens to have only KarmaDock.
_KCAL_DOCKING_ADAPTERS = ("docking.unidock@",)


def _kcal_docking_source(docking: TierConfig | None) -> str | None:
    """The stage id of the first kcal/mol docking criterion in ``docking``."""

    if docking is None:
        return None
    return next(
        (
            criterion.id
            for criterion in docking.criteria
            if criterion.enabled and criterion.backend.startswith(_KCAL_DOCKING_ADAPTERS)
        ),
        None,
    )


def _docking_metrics_tier(
    registry: PluginRegistry, docking: TierConfig | None
) -> TierConfig | None:
    """What the docking score looks like once size and strain are paid for.

    A docking score is extensive and charges nothing for internal strain, so it
    rewards mass and rewards a bent conformer.  Both criteria here read the
    score and the pose that earned it and turn them into something a threshold
    can be set on honestly.  Neither is a rescoring function: they re-express
    the number the engine already produced.

    Both must sit *below* the docking tier rather than inside it.  Criteria
    within a tier are parallel branches over the same input population and
    cannot see each other's outputs, so a criterion placed beside Uni-Dock
    would fail to compile with ``CASCADE_CRITERION_EVIDENCE_UNAVAILABLE``.
    That is also why this returns ``None`` when the docking tier is absent.

    ``evidence_from`` pins Uni-Dock explicitly.  The default docking tier has
    two engines and both emit ``docking_score/v1``, so lowering refuses to
    guess -- and the choice is a real one: KarmaDock scores a pose it has
    already relaxed through a force field (see ``karmadock.py`` on raw poses
    reaching six-figure MMFF energies), so its strain has been flattened by its
    own pipeline before this tier can measure it.  Uni-Dock hands over the raw
    Vina pose, which is where strain still carries information.  Writing the
    choice here rather than relying on tier order puts it in the exported plan.

    ``all`` rather than ``serial``: the two metrics are independent readings of
    the same pose and every molecule that arrives should have both, so a
    molecule rejected for strain still carries its efficiency number into the
    trace instead of a blank.
    """

    source = _kcal_docking_source(docking)
    if source is None:
        return None
    evidence_from = {DOCKING_SCORE_V1.id: source}
    criteria = [
        criterion
        for criterion in (
            _criterion_if_available(
                "pose_strain",
                registry,
                evidence_from=evidence_from,
            ),
            _criterion_if_available(
                "size_normalized_docking_score",
                registry,
                evidence_from=evidence_from,
            ),
        )
        if criterion is not None
    ]
    if not criteria:
        return None
    return TierConfig(
        id="t10_docking_metrics",
        title="Post-docking metrics",
        # Spelled as the enum rather than "all" like the tiers above: the
        # string form is accepted by a validator but is a type error, and
        # there is no reason for new code to inherit that.
        mode=TierMode.ALL,
        criteria=tuple(criteria),
        note=(
            "Reads the docking tier above it -- Uni-Dock specifically, named in each "
            "criterion's 'evidence_from' -- and asks what the score cost. Strain is "
            "the MMFF94s penalty the molecule pays to hold the docked conformer, in "
            "kcal/mol and not in the TEU of the torsion-library literature, so 8.0 "
            "here is not the 6.5 of a TEU filter; it keeps about two thirds of a "
            "kinase shortlist. Ligand efficiency is the score per heavy atom, and "
            "0.30 is the classic guideline rather than a tight cut -- it keeps close "
            "to nine molecules in ten. Neither one is a ring filter: strain "
            "correlates with fused-ring count at r = -0.014, and flat fused systems "
            "are the most heavy-atom-efficient class there is. Delete this tier if "
            "you want the raw engine numbers and nothing else."
        ),
    )


def _affinity_tier(
    registry: PluginRegistry, *, boltz2_settings: Mapping[str, Any] | None
) -> TierConfig | None:
    """Boltz-2 on whatever survived everything above, and nothing else.

    Every other tier in this cascade reads the molecule, or reads what the tier
    before it measured.  This one reads a *sequence*: it folds the complex from
    the target's residues and a SMILES, so it consumes none of the poses the
    docking tiers produced and its opinion is about the molecule rather than
    about our geometry.  That independence is the reason to have it and the
    reason it goes last -- a co-folded affinity that disagrees with the docking
    score is informative precisely because it did not see the docking score.

    Nothing about the compiler forces the position.  The plugin declares only
    ``parent/v1`` as input, so lowering would happily run it first; only the
    cost says otherwise, and twenty seconds a molecule is a different quantity
    at the top of a funnel than at the bottom.  Hence ``max_molecules`` is a
    budget the stage enforces before folding anything rather than a guard it
    checks on the way past.

    Off unless asked for, and by a path rather than a flag.  The target FASTA
    cannot be discovered: not from this installation, and deliberately not from
    the receptor PDB either, because reading residues off ATOM records closes
    every unresolved gap without saying so -- the STK17B structure this was
    developed against is missing nine residues in the activation segment, and a
    sequence derived from it would be a protein that does not exist.  So the
    caller supplies ``boltz2_settings`` with at least ``target_fasta_path``,
    and without it this returns ``None`` and the starter cascade is unchanged.
    """

    if not boltz2_settings:
        return None
    settings = dict(boltz2_settings)
    if not str(settings.get("target_fasta_path", "")).strip():
        return None
    criterion = _criterion_if_available("binding_affinity", registry, settings=settings)
    if criterion is None:
        return None
    return TierConfig(
        id="t12_affinity",
        title="Co-folded affinity",
        mode=TierMode.ALL,
        criteria=(criterion,),
        note=(
            "Boltz-2 folds the complex itself from the target sequence and the SMILES, "
            "so this is the one tier that never looks at a pose -- it is an independent "
            "second opinion, not a rescoring of the docking result. The number is log10 "
            "of an IC50 in micromolar and lower is stronger, so the default gate at 0.0 "
            "keeps molecules predicted at or below 1 uM. Read it as a ranking: the paper "
            "presents these values for ordering compounds and nothing here calibrates "
            "them against an assay. Measured on one RTX 4090 it costs about 20 s a "
            "molecule on top of a fixed 40 s of weight loading, which is why it is last "
            "and why 'max_molecules' stops the stage before folding rather than during."
        ),
    )


def default_cascade(
    *,
    registry: PluginRegistry | None = None,
    target_count: int = DEFAULT_TARGET_COUNT,
    boltz2_settings: Mapping[str, Any] | None = None,
) -> CascadeConfig:
    """Build the starter cascade against the plugins this installation has."""

    active = registry or create_builtin_registry()
    # Held in a local because the tier below it needs to name a stage inside it.
    docking = _docking_tier(active)

    tiers = [
        tier
        for tier in (
            _chemistry_tier(active),
            _property_tier(active),
            _druglikeness_tier(active),
            _alerts_tier(active),
            _synthesis_tier(active),
            _admet_tier(active),
            _structure_qc_tier(active),
            _conformer_tier(active),
            docking,
            _docking_metrics_tier(active, docking),
            _affinity_tier(active, boltz2_settings=boltz2_settings),
        )
        if tier is not None
    ]

    finalize_steps = [
        step
        for step in (
            _step_if_available(
                "properties",
                "features.rdkit_properties@0.1.0",
                active,
                settings={"include_sa_score": True},
            ),
            _step_if_available("scaffolds", "scaffold.rdkit_murcko@0.1.0", active),
            _step_if_available("diversity_groups", "cluster.rdkit_scaffold_groups@0.1.0", active),
            _step_if_available(
                "shortlist_select",
                "select.native_scaffold_round_robin@0.1.0",
                active,
                # A generated library is full of near-neighbours, and docking
                # budget spent on the twenty-sixth analogue of one scaffold buys
                # nothing that the first twenty-five did not.
                settings={"max_per_scaffold": 25},
            ),
            _step_if_available(
                "shortlist",
                "export.native_smiles_shortlist@0.1.0",
                active,
                settings={"filename": "shortlist.smi"},
            ),
        )
        if step is not None
    ]

    ingest = StepConfig(
        id="library",
        backend="source.delimited_smiles@0.1.0",
        settings=with_schema_version("source.delimited_smiles@0.1.0", {}, registry=active),
    )
    standardize = _step_if_available(
        "standardize",
        "chemistry.rdkit_standardize@0.1.0",
        active,
        settings={
            "identity_policy": {
                "schema_version": 1,
                # Not RDKit's default of "insensitive", and this is the one
                # place in the starter cascade that overrides an identity
                # default, so it is worth saying why.
                #
                # Under "insensitive" two tautomers register as one parent, and
                # that parent is depicted as RDKit's canonical tautomer.  The
                # canonical form is chosen by a score that pays a large bonus
                # per aromatic ring, so wherever a lactam sits next to an
                # aromatic ring the lactim wins, and every substituent that has
                # to move to pay for it moves.
                #
                # On the approved-oral-drug panel that transform rewrites 15 of
                # 77 structures, and the rewrites are not cosmetic:
                #
                #   * clopidogrel, valsartan, levothyroxine and methotrexate
                #     each lose a defined stereocentre; amoxicillin loses three;
                #   * sunitinib's oxindole aromatises to the lactim and takes
                #     the defined Z-alkene with it;
                #   * ranitidine's nitroalkene becomes a nitroalkane, which is
                #     a functional group two alert catalogues reject on sight;
                #   * dexamethasone and prednisolone have the A-ring enone
                #     shifted out of conjugation.
                #
                # The alert hits are the visible damage.  The real damage is
                # that the shortlist handed to docking carries the invented
                # structure, stereochemistry and all.
                #
                # What "insensitive" buys back is cross-tautomer deduplication,
                # and for a generated library -- one writer, one spelling
                # convention, millions of distinct molecules -- that is close to
                # nothing, while the depiction is what every downstream stage
                # and the docking handoff actually consume.  "preserve" keeps
                # the structure as drawn, and is just as deterministic.
                #
                # A curated deck assembled from several vendors is the opposite
                # trade, so this stays a per-cascade setting the builder
                # exposes rather than something hard-coded.
                "tautomer_policy": "preserve",
            }
        },
    )

    return CascadeConfig(
        name="molcascade_screen",
        description=(
            "Cost-ordered triage of a generated library: chemistry, physicochemical "
            "window, drug-likeness, liability alerts, synthesizability and a "
            "second-toolkit structure check, then a scaffold-diverse shortlist "
            "sized for docking."
        ),
        library=LibraryConfig(smiles_column="smiles"),
        ingest=ingest,
        standardize=standardize,
        tiers=tuple(tiers),
        finalize=FinalizeConfig(
            target_count=target_count,
            seed=DEFAULT_SEED,
            steps=tuple(finalize_steps),
        ),
        metadata={"created_by": "MolCascade cascade builder"},
    )


__all__ = [
    "DEFAULT_SEED",
    "DEFAULT_TARGET_COUNT",
    "SCSCORE_ASSET_MEMBER",
    "build_criterion",
    "criterion_defaults",
    "default_cascade",
]
