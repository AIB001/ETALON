"""Causes of a wrong number, each carrying the magnitude it could account for.

Every CADD pipeline can list the ways a free-energy estimate goes wrong. The lists
are old and good: a displaced pose, a protonation state nobody chose, an unconverged
alchemical leg, an atom mapping that failed silently. What they do not carry is a
size. So a troubleshooting checklist can tell you that six things might be wrong and
cannot tell you which of them could have produced the 4 kcal/mol you are looking at.

That is the gap this module is for. Each cause here declares a band -- the range of
|ΔΔG| error it can plausibly account for -- so a candidate can be **eliminated by
magnitude**: a cause whose band tops out at 1.5 kcal/mol is refused as the explanation
of a 6 kcal/mol divergence even when its flag fired, and a cause with no upper bound
is never eliminated that way and must be decided on its observable alone.

Elimination is the direction that matters. Confirming a cause from a flag is what a
checklist already does and it is weak: several flags usually fire at once on a real
molecule, and a list of six possibilities ranked by nothing is not a diagnosis.
Refusing a cause because the arithmetic does not reach is strong, and it is available
from numbers that are already in the contracts.

Three rules keep the bands from becoming decoration.

**A band has a provenance and it is recorded.** ``MEASURED_HERE`` means this project
measured it and names where; ``LITERATURE`` means a published value, cited;
``CONVENTION`` means a number somebody chose and nobody measured. A convention is
allowed -- some of these cannot be measured cheaply -- but it is never allowed to look
like a measurement. The engineer who reviewed the design caught exactly this: a 5.0 Å
cutoff presented as though it were a finding.

**A cause with no upper bound says so.** A ligand sitting at the molblock origin is
not wrong by an amount; it is not in the pocket, and the number computed from it is
about nothing. ``upper_kcal_mol = None`` means "cannot be eliminated by magnitude",
which is a stronger statement than a large number and an honest one.

**A check declares whether it is exact.** An exact comparison -- two strings, two
integers, a digest against a digest -- either fires or does not, and costs nothing.
An inference can be wrong in both directions. Mixing them without saying which is
which is how a diagnosis acquires false confidence, so the attribution layer weights
them differently and the field is not optional.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Phase(StrEnum):
    """When a fault can be detected, which decides what it can save.

    ``PREFLIGHT`` faults are detectable from the handoff record alone, before a
    single GPU-second is spent. They are the cheap half and the valuable half: a
    campaign that refuses eleven molecules here has saved eleven times three
    GPU-days and lost nothing, because the numbers those runs would have produced
    would have described something other than the molecule.

    ``POSTFLIGHT`` faults need the trajectory or the estimate. They cannot save the
    spend; what they do is explain a divergence, and explain it well enough that the
    next campaign does not repeat it.
    """

    PREFLIGHT = "preflight"
    POSTFLIGHT = "postflight"


class Evidence(StrEnum):
    """How a band's number was arrived at. Never omitted, never inferred."""

    #: Measured in this project. ``source`` names the run, probe or ADR.
    MEASURED_HERE = "measured_here"
    #: Measured inside a vendored asset at its pinned commit, rather than by ETALON and rather than
    #: published. ``source`` names the file in ``asset/`` that records it.
    #:
    #: This class exists because the most-quoted number in the repository did not have one. The
    #: 41-of-41 hydrogen result was measured in PRISM and labelled ``MEASURED_HERE``, which named
    #: the wrong project and is why it is the only headline figure with no ``findings/`` entry.
    #: ``LITERATURE`` was no better -- the catalogue's own rule requires a citable year, and there
    #: is no paper. The distinction is worth a class of its own for the same reason
    #: ``economics.Evidence.INFERRED`` is: ETALON can re-derive this by re-running a pinned tree,
    #: which a reader cannot do with a citation and which is a weaker claim than having run it.
    MEASURED_IN_AN_ASSET = "measured_in_an_asset"
    #: A published value. ``source`` carries the citation.
    LITERATURE = "literature"
    #: Somebody chose it and nobody has measured it. Legitimate and labelled.
    CONVENTION = "convention"


class Consequence(StrEnum):
    """What a fault does to the number, which is what decides whether to refuse it.

    This axis exists because a rule written over the magnitude band had a
    counterexample inside this very catalogue. "Fired and unbounded, therefore stop the
    spend" reads well and refuses the wrong things: an unseeded run is unbounded -- no
    band describes it -- and is not a reason to refuse a molecule. The band and the
    consequence are different questions, and one field cannot answer both.

    ``WRONG_SUBJECT`` -- the number will be about something other than the molecule it
    is filed under. A drawing instead of a structure, a species nobody chose, a pose in
    a different receptor. There is no divergence size at which this becomes tolerable,
    because the quantity itself is not the one asked for. These are the faults worth
    spending a refusal on.

    ``WRONG_SIZE`` -- the number is about the right thing and off by an amount the band
    describes. Whether that amount is acceptable is the operator's call, so this
    annotates rather than blocks.

    ``UNVERIFIABLE`` -- the number may be perfectly good; what is missing is any way to
    check a claim about it. Refusing these would refuse work that is scientifically
    fine, and reporting them as clean would let an unreproducible result be cited as a
    reproducible one. So they block a *claim*, never a spend.
    """

    WRONG_SUBJECT = "wrong_subject"
    WRONG_SIZE = "wrong_size"
    UNVERIFIABLE = "unverifiable"


class Exactness(StrEnum):
    """Whether the observable decides the question or only suggests an answer."""

    #: Two strings, two integers, a digest against a digest. Fires or does not.
    EXACT = "exact"
    #: A threshold on a continuous quantity. Has a false-positive rate.
    THRESHOLD = "threshold"
    #: Read from a tool's own diagnostic, which may itself be unavailable.
    DERIVED = "derived"


@dataclass(frozen=True, slots=True)
class Fault:
    """One cause of a wrong number, with the magnitude it could account for."""

    code: str
    phase: Phase
    summary: str
    #: What to compare, named down to the contract column where one exists. A fault
    #: whose observable is not in any contract is a fault nothing can detect, and
    #: saying so is more useful than listing it as though it were checkable.
    observable: str
    exactness: Exactness
    #: What this fault does to the number: makes it about something else, makes it the
    #: wrong size, or makes a claim about it uncheckable. Separate from the band, and
    #: the separation is load-bearing -- see :class:`Consequence`.
    consequence: Consequence
    #: The band of |ΔΔG| error in kcal/mol this cause can account for. ``upper`` of
    #: ``None`` means unbounded: the estimate is not wrong by an amount, it is about
    #: the wrong thing.
    lower_kcal_mol: float
    upper_kcal_mol: float | None
    evidence: Evidence
    source: str
    #: What to do about it. Deliberately not "fix it automatically": several of these
    #: are decisions with scientific content and a layer that made them quietly
    #: would produce a structure nobody chose.
    remedy: str

    def can_account_for(self, divergence_kcal_mol: float) -> bool:
        """Whether this cause could produce a divergence of the observed size.

        The asymmetry is the point. A bounded cause is eliminated when the
        observation exceeds its band; an unbounded one never is. Below the lower
        bound a cause is *not* eliminated, because a cause that can produce a large
        error can also produce a small one -- the band's floor describes the size at
        which the cause becomes worth naming, not a minimum it must reach.
        """

        if self.upper_kcal_mol is None:
            return True
        return abs(divergence_kcal_mol) <= self.upper_kcal_mol

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "phase": self.phase.value,
            "summary": self.summary,
            "observable": self.observable,
            "exactness": self.exactness.value,
            "consequence": self.consequence.value,
            "band_kcal_mol": [self.lower_kcal_mol, self.upper_kcal_mol],
            "evidence": self.evidence.value,
            "source": self.source,
            "remedy": self.remedy,
        }


#: Two spellings of one compound register as two molecules, each with its own
#: structure, force field and free energy, and a ±1 e error on a ligand is worth tens
#: of kcal/mol in MM-PBSA polar solvation. Unbounded because the two are not the same
#: substance: there is no error magnitude for reporting a number about a species that
#: was never there.
_PROTONATION = Fault(
    code="F_PROTONATION_UNDECIDED",
    phase=Phase.PREFLIGHT,
    summary=(
        "No step computed a protonation state, so the one being simulated is whatever "
        "the standardizer left behind"
    ),
    observable="md_system_input/v1.protonation_state_id and .formal_charge",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=5.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement and no citation: the 5.0 floor is Born arithmetic, not a "
        "published number. A unit charge on a 2 A sphere carries roughly 80 kcal/mol of "
        "solvation energy, so any part of a protonation difference is worth several, and "
        "a classical MD transfers no protons so it cannot be recovered. The floor marks "
        "where the cause becomes worth naming; nobody measured it here."
    ),
    remedy=(
        "Decide the state upstream and record which method decided it. The handoff "
        "contract requires a protonation_state_id precisely so that "
        "INHERITED_FROM_STANDARDIZER is visible rather than implied."
    ),
)

#: Embedding enforces the centres a SMILES assigns and settles the rest from a seeded
#: hash, so the molecule simulated is one isomer of the set its name designates.
_STEREOCHEMISTRY = Fault(
    code="F_STEREO_CHOSEN_BY_EMBEDDING",
    phase=Phase.PREFLIGHT,
    summary="Distance geometry chose a configuration the molecule's own name leaves open",
    observable="md_system_input/v1.stereo_smiles against .parent_smiles",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SIZE,
    lower_kcal_mol=1.0,
    upper_kcal_mol=3.0,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement: 1-3 kcal/mol is RT*ln(ratio) at 310 K for a eutomer/distomer "
        "affinity ratio of 5 to 130, and the ratio is chosen rather than observed. This "
        "is the one band in the catalogue that eliminates anything, so it is the one "
        "most in need of a real measurement on a real series."
    ),
    remedy=(
        "Enumerate the isomers and carry each as its own molecule, or fix the centre "
        "upstream. Both strings are carried in the handoff so the difference is "
        "visible without re-deriving anything."
    ),
)

#: The SDF shortlist export builds from SMILES: measured 19 heavy atoms, 0 explicit
#: hydrogens, z range 0.00 to 0.00. It parameterises, solvates and simulates, and the
#: receiving validator checks existence, size, suffix and a positive atom count.
_FLAT_GEOMETRY = Fault(
    code="F_COORDINATES_ARE_A_DEPICTION",
    phase=Phase.PREFLIGHT,
    summary="Every z is zero: the coordinates are a drawing, not a structure",
    observable="md_system_input/v1.coordinate_source == TWO_D_DEPICTION",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "MolCascade's SDF exporter on propranolol: 19 heavy atoms, 0 explicit "
        "hydrogens, z range 0.00-0.00, and PRISM's ligand validator passes it"
    ),
    remedy=(
        "Take the docked pose or the embedded conformer the run already produced. "
        "Never rebuild geometry from a name at a handoff."
    ),
)

#: A record naming heavy atoms only builds a topology with no hydrogens, and every
#: validator downstream of that accepts it.
_IMPLICIT_HYDROGENS = Fault(
    code="F_HYDROGENS_IMPLICIT",
    phase=Phase.PREFLIGHT,
    summary="The record names heavy atoms only, so the topology will have no hydrogens",
    observable="md_system_input/v1.hydrogens == IMPLICIT and .hydrogen_count == 0",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_IN_AN_ASSET,
    source=(
        "Across 82 measured gaff2 builds, the topology carried zero hydrogens in 41 of "
        "41 of those built from hydrogen-free input, with no warning at any stage, "
        "while hydrogenated input gave the right count in 35 of 41. Recorded in the "
        "vendored PRISM at asset/prism/prism/generation/handoff.py, at the commit "
        "asset/MANIFEST.json pins. Reclassified from MEASURED_HERE, which named the "
        "wrong project for the most-quoted number in this repository -- it is the one "
        "headline figure with no findings/ entry, because ETALON did not run it"
    ),
    remedy="Use the hydrogenated conformer the ligand-prep stage already wrote.",
)

#: A pose is only meaningful against the receptor bytes it was scored in. A
#: re-protonated site, a different chain or a repaired residue makes it a pose in a
#: different potential.
_RECEPTOR_MISMATCH = Fault(
    code="F_RECEPTOR_NOT_THE_ONE_SCORED",
    phase=Phase.PREFLIGHT,
    summary="The receptor about to be simulated is not the one the pose was scored in",
    observable="md_system_input/v1.receptor_id against the digest of the receptor file",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=1.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement here: the 1.0 floor is a chosen threshold for when a changed "
        "receptor is worth naming. A re-protonated hinge histidine moves an MM-PBSA "
        "number and a pose carried into a different site is unbounded, but neither "
        "figure was measured in this project."
    ),
    remedy=(
        "Compare the digests before the build, not after. The handoff carries "
        "receptor_id for this and nothing else."
    ),
)

#: A pose with no receptor names no structure at all, so the comparison above cannot
#: even be attempted. Separate from the mismatch because the remedy differs.
_POSE_WITHOUT_RECEPTOR = Fault(
    code="F_POSE_WITHOUT_RECEPTOR",
    phase=Phase.PREFLIGHT,
    summary="A docked pose that names no receptor, so nothing records where it came from",
    observable="md_system_input/v1.coordinate_source == DOCKED_POSE and .receptor_id is null",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=1.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement: the band is inherited from F_RECEPTOR_NOT_THE_ONE_SCORED "
        "because an unrecorded receptor cannot be shown to be the right one"
    ),
    remedy="Reject. A pose whose provenance is missing cannot be checked later either.",
)

#: The engine could not read the structure. Distinguished from absence because the
#: remedy is different and because a row exists either way.
_UNREADABLE = Fault(
    code="F_STRUCTURE_UNREADABLE",
    phase=Phase.PREFLIGHT,
    summary="The producer could not supply a usable structure and said so",
    observable="md_system_input/v1.status != OK",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source="6 of 2,000 generated molecules failed to parse even after kekulization",
    remedy="Reject and record. Nothing downstream can recover a structure nobody has.",
)

#: No handoff row at all. Fails closed: a molecule nothing has spoken about has not
#: been shown to be simulable.
_NO_EVIDENCE = Fault(
    code="F_HANDOFF_ABSENT",
    phase=Phase.PREFLIGHT,
    summary="No handoff record for this molecule, so nothing has declared its coordinates",
    observable="no md_system_input/v1 row for the parent_id",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement and none possible: the fault is the absence of a measurement, "
        "and treating a missing row as a pass is the failure mode the contract's own "
        "invariant forbids"
    ),
    remedy="Reject. A missing row reads downstream as a molecule that was fine.",
)

#: Relative FEP's atom mapper here is Cartesian distance with a 0.6 nm cutoff and no
#: quality gate. Two analogues out of register map almost nothing as common, and the
#: "relative" calculation becomes a near-total double annihilation.
_MAPPING_FAILED = Fault(
    code="F_FEP_MAPPING_DEGENERATE",
    phase=Phase.PREFLIGHT,
    summary=(
        "The common substructure two analogues were mapped through is too small for "
        "the calculation to be relative"
    ),
    observable="mapped-atom count against the two molecules' heavy-atom counts",
    exactness=Exactness.THRESHOLD,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=2.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement here yet. The mapper is Cartesian distance with a 0.6 nm "
        "cutoff and no quality gate, so a pseudo-symmetric pocket or a 6 A "
        "displacement can reduce the mapping to almost nothing"
    ),
    remedy=(
        "Gate the mapping before the edge is scheduled: require a mapped fraction, "
        "and overlay the analogues on a reference rather than docking each "
        "independently."
    ),
)

#: The one fault whose detection depends on a setting ETALON deliberately does not
#: change. With calc-lambda-neighbors = 1 the MBAR overlap matrix is banded by
#: construction, so the convergence observables derived from it are not available --
#: and publishing them as values would be the instrument lying about itself.
_CONVERGENCE_UNKNOWN = Fault(
    code="F_CONVERGENCE_NOT_ASSESSABLE",
    phase=Phase.POSTFLIGHT,
    summary=(
        "The estimator's own convergence diagnostics cannot be computed from what "
        "the run recorded"
    ),
    observable="the production MDP's calc-lambda-neighbors against the estimator",
    exactness=Exactness.EXACT,
    consequence=Consequence.UNVERIFIABLE,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "PRISM's FEP_PROD_MDP sets calc-lambda-neighbors = 1 while the default "
        "estimator is MBAR, which needs the full u_nk matrix. Verified: the loop at "
        "fep/gromacs/mdp_templates.py:707 writes the production MDPs from that "
        "template, and the correct -1 values at 566 and 632 are in the equilibration "
        "templates. Fixed-window FEP with no expanded ensemble, so the setting "
        "affects what is recorded and not the trajectory."
    ),
    remedy=(
        "Publish every convergence observable derived from the overlap matrix as "
        "UNAVAILABLE with this reason, rather than as a number. Do not silently "
        "rewrite the setting: it changes what the run records, which is a change to "
        "the measurement and belongs to whoever owns the protocol."
    ),
)

#: Velocity generation from the clock. Not an error in the estimate; an error in any
#: claim that the estimate is reproducible.
_UNSEEDED_VELOCITIES = Fault(
    code="F_RUN_NOT_REPRODUCIBLE",
    phase=Phase.PREFLIGHT,
    summary="Velocities or ion placement will be drawn from the clock",
    observable="the toolchain shim's presence, and gen_seed in the MDP about to run",
    exactness=Exactness.EXACT,
    consequence=Consequence.UNVERIFIABLE,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "docs/adr/0001: two genion runs from identical inputs produced different "
        "bytes, 7c3e3472a0118278 against 098089b300eae14f"
    ),
    remedy=(
        "Supply the seed from outside and record it. This does not make a result "
        "right; it makes a wrong one findable."
    ),
)

#: The build produced no topology. Postflight because only the files afterwards say so:
#: PRISM's build raises on some failures and returns on others, and the difference between
#: them is not visible from the call.
_BUILD_INCOMPLETE = Fault(
    code="F_BUILD_INCOMPLETE",
    phase=Phase.POSTFLIGHT,
    summary="The system build left no topology, so there is nothing to simulate",
    observable="topol.top and solv_ions.gro in the build's GMX_PROLIG_MD directory",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "Verified on this machine: passing protonation= to PRISMBuilder as a keyword "
        "raises TypeError after the ligand has been parameterised, and the build returns "
        "with a populated output directory and no topology in it"
    ),
    remedy=(
        "Read the captured build log, which names the failure. Nothing downstream can "
        "recover a system nobody built."
    ),
)

#: A stage of the driver left no product. Distinguished from a build failure because the
#: system was buildable and the simulation was not, and the remedy differs.
_STAGE_NEVER_RAN = Fault(
    code="F_STAGE_NEVER_RAN",
    phase=Phase.POSTFLIGHT,
    summary="A requested simulation stage left no product behind",
    observable="each stage's own product file, as localrun.sh's skip conditions define it",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "findings/0002: a re-driven directory exits 0 with em, nvt and npt all failed and "
        "23 error lines in the log, because the final block's skip branch is the last "
        "command to run. Measured on this machine."
    ),
    remedy=(
        "Judge the stage by its product, never by the driver's exit code, and read the "
        "captured output for the reason. Re-drive after fixing the cause; the script "
        "resumes on the same files."
    ),
)

#: The protonation step computed states and could not apply some of them. Unverifiable
#: rather than blocking, deliberately: PROPKA routinely fails to map a terminus, where the
#: default is almost certainly what it would have said, and refusing every such build would
#: be ADR 0002's mistake a third time. What is genuinely missing is the ability to say
#: whether the simulated state is the computed one.
_PROTONATION_NOT_APPLIED = Fault(
    code="F_PROTONATION_NOT_APPLIED",
    phase=Phase.POSTFLIGHT,
    summary=(
        "The protonation predictor reported residues it could not map, so some computed "
        "states were not applied"
    ),
    observable="unmapped-residue lines in the captured build log",
    exactness=Exactness.EXACT,
    consequence=Consequence.UNVERIFIABLE,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "Measured on a real ubiquitin build: PROPKA reported 'Residue A:1 N+ (unmapped)' "
        "and the build completed with no indication in any file that a predicted state had "
        "been dropped"
    ),
    remedy=(
        "Read which residues were unmapped. A terminus is usually the default anyway; a "
        "buried histidine near the site is not, and that one has to be decided by hand "
        "and recorded."
    ),
)

#: grompp said the system's total charge is not an integer. That is a parameterisation
#: error rather than a neutralisation question: genion adds whole ions, so a fractional
#: charge cannot be neutralised away.
_CHARGE_NOT_INTEGER = Fault(
    code="F_TOPOLOGY_CHARGE_NOT_INTEGER",
    phase=Phase.POSTFLIGHT,
    summary="The topology's total charge is not an integer, so the molecule is mis-parameterised",
    observable="grompp's non-integer-charge warning in the captured driver output",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement here: the reasoning is that genion adds whole ions, so a "
        "fractional total charge is a charge-assignment error and not something "
        "neutralisation can absorb. PRISM passes -maxwarn 999, so grompp says this and "
        "builds anyway."
    ),
    remedy=(
        "Re-derive the ligand charges and check the net formal charge passed to the "
        "parameteriser. Do not neutralise over it."
    ),
)

#: One run of an end-point free energy method is a draw from a distribution, not an estimate of
#: its mean. This is the one fault in the catalogue whose band is wide enough to explain almost
#: any divergence and is still honestly bounded -- which is informative rather than useless: a
#: 4 kcal/mol disagreement between docking and a single-replica MM-PBSA number needs no further
#: explanation, and a campaign chasing one has been chasing sampling noise.
_SINGLE_REPLICA = Fault(
    code="F_SINGLE_REPLICA_ESTIMATE",
    phase=Phase.POSTFLIGHT,
    summary=(
        "An end-point free energy came from one trajectory, so it is one sample from a wide "
        "distribution rather than an estimate of its mean"
    ),
    observable="the replica count behind the estimate, against the method's run-to-run spread",
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SIZE,
    lower_kcal_mol=1.5,
    upper_kcal_mol=12.0,
    evidence=Evidence.LITERATURE,
    source=(
        "MM-PBSA calculations initiated from the same structures varied by up to 12 kcal/mol for "
        "small molecules bound to HIV-1 protease, and individual replicas within one ensemble by "
        "up to 15 kcal/mol; the distributions are not Gaussian -- skewness and excess kurtosis "
        "definitively non-zero across 500-replica runs, with normality rejected for all nine "
        "systems tested. Wan S, Bhati AP, Zasada SJ, Coveney PV and related ensemble work; see "
        "also Ensembles Are Required to Handle Aleatoric and Parametric Uncertainty in Molecular "
        "Dynamics Simulation, 2021."
    ),
    remedy=(
        "Run an ensemble and report its median with a quantile interval rather than a mean with a "
        "standard deviation, because the distribution is skewed and heavy-tailed. Five replicas "
        "is the count the ESMACS and TIES protocols settle on, and the literature is explicit "
        "that no theoretical means establishes it: the criterion is the N at which N+1 changes "
        "nothing, which is a measurement per system."
    ),
)

#: The ligand is no longer in the site. Every number computed from such a trajectory describes a
#: solvated ligand near a protein, which is a real system and not the one anybody asked about.
_POSE_LOST = Fault(
    code="F_POSE_LEFT_THE_SITE",
    phase=Phase.POSTFLIGHT,
    summary="The ligand left the binding site during the simulation",
    observable="ligand centre-of-mass displacement from the pocket, against a cutoff",
    exactness=Exactness.THRESHOLD,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement here: the cutoff is chosen, and a displacement threshold is a weaker "
        "observable than it looks because a ligand can leave and return. What makes this "
        "unbounded is not the threshold but the consequence -- a trajectory in which the ligand "
        "is in solvent estimates the free energy of a solvated ligand."
    ),
    remedy=(
        "Record it and do not rank on the number. A pose that does not hold is a finding about "
        "the pose, and one trajectory losing it is not proof: the process is chaotic, so a "
        "single departure needs an ensemble before it means the pose was wrong."
    ),
)

#: The pose is still in the site and the average was taken over a segment that had not settled,
#: so it estimates neither the starting state nor the equilibrium one.
_NOT_EQUILIBRATED = Fault(
    code="F_POSE_NOT_EQUILIBRATED",
    phase=Phase.POSTFLIGHT,
    summary="The ligand RMSD was still drifting at the end of the run",
    observable=(
        "the trend in ligand RMSD over the final third of the trajectory, against the residual "
        "fluctuation within it"
    ),
    exactness=Exactness.THRESHOLD,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement here. The test is dimensionless -- is the trend larger than the noise -- "
        "which replaced a chosen 0.1 nm tolerance that reported a ligand creeping from 0.15 to "
        "0.40 nm over 1000 ns as settled. The reasoning rather than any number is what carries: "
        "an average over a non-stationary segment estimates a time-dependent quantity, which is "
        "neither the initial state nor the equilibrium one, so there is no error magnitude to "
        "assign it. The frames are serially correlated, so the ratio has no null distribution "
        "and the factor of one is a convention."
    ),
    remedy=(
        "Extend the run, or discard more of the beginning and re-check. Do not average over the "
        "drift and call the result an equilibrium property."
    ),
)

#: The ligand stayed and the interactions did not. Unverifiable rather than blocking: the number
#: is about a real bound state, and what is lost is the ability to compare it with the cheap score.
_POSE_CHANGED = Fault(
    code="F_POSE_NOT_THE_SCORED_ONE",
    phase=Phase.POSTFLIGHT,
    summary=(
        "The ligand held the site but lost the contacts the docked pose was scored for, so the "
        "simulation and the screen are describing different poses"
    ),
    observable="persistence of the docked pose's key contacts across the trajectory",
    exactness=Exactness.THRESHOLD,
    consequence=Consequence.UNVERIFIABLE,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.CONVENTION,
    source=(
        "No measurement here: the persistence threshold is chosen. The classification is the "
        "substantive part -- this does not make the free energy wrong, it makes the comparison "
        "between it and the docking score a comparison between two different poses, which "
        "invalidates a calibration rather than a number."
    ),
    remedy=(
        "Keep the free energy and withhold the pair from any cheap-versus-expensive calibration. "
        "A simulation that found a better pose than the docking did is a result, and using it to "
        "tell the screen it was wrong about this molecule would be teaching it from a different "
        "molecule's geometry."
    ),
)

#: The number was computed before the system it is filed under. Postflight because only the
#: files afterwards say so, and WRONG_SUBJECT because the energy is a real measurement of a
#: different system -- not a badly-sized measurement of this one.
_RESULT_PREDATES_THE_SYSTEM = Fault(
    code="F_RESULT_PREDATES_THE_SYSTEM",
    phase=Phase.POSTFLIGHT,
    summary=(
        "The binding energy is older than the topology it is attributed to, so it describes a "
        "system this directory no longer holds"
    ),
    observable=(
        "modification time of GMX_PROLIG_MMPBSA/FINAL_RESULTS_MMPBSA.dat against "
        "GMX_PROLIG_MD/topol.top in the same run directory"
    ),
    exactness=Exactness.EXACT,
    consequence=Consequence.WRONG_SUBJECT,
    lower_kcal_mol=0.0,
    upper_kcal_mol=None,
    evidence=Evidence.MEASURED_HERE,
    source=(
        "Measured on this project's own expensive stage: a FINAL_RESULTS_MMPBSA.dat written one "
        "minute before the topol.top beside it was admitted as expensive_value = -42.0 with no "
        "observation and no withheld reason, because nothing compared the two. gmx_MMPBSA is run "
        "by the operator and not by ETALON, so the energy is always older than the drive that "
        "reads it and freshness relative to the drive cannot be the test"
    ),
    remedy=(
        "Re-run mmpbsa_run.sh against the rebuilt system, or delete the stale result. Do not "
        "reconcile the dates by touching the file: the energy would then be labelled with a "
        "system nobody computed it from, which is the same fault with its evidence removed."
    ),
)

#: The catalogue. Ordered by phase then by how cheaply the observable decides, so a
#: caller walking it in order spends the least before it has an answer.
FAULTS: tuple[Fault, ...] = (
    _NO_EVIDENCE,
    _UNREADABLE,
    _FLAT_GEOMETRY,
    _IMPLICIT_HYDROGENS,
    _POSE_WITHOUT_RECEPTOR,
    _RECEPTOR_MISMATCH,
    _PROTONATION,
    _STEREOCHEMISTRY,
    _UNSEEDED_VELOCITIES,
    _MAPPING_FAILED,
    _BUILD_INCOMPLETE,
    _STAGE_NEVER_RAN,
    _RESULT_PREDATES_THE_SYSTEM,
    _CHARGE_NOT_INTEGER,
    _PROTONATION_NOT_APPLIED,
    _POSE_LOST,
    _NOT_EQUILIBRATED,
    _POSE_CHANGED,
    _SINGLE_REPLICA,
    _CONVERGENCE_UNKNOWN,
)

BY_CODE: dict[str, Fault] = {fault.code: fault for fault in FAULTS}


def preflight_faults() -> tuple[Fault, ...]:
    """Faults detectable before a GPU-second is spent.

    Named ``..._faults`` rather than ``preflight`` because ``etalon.faults.preflight`` is
    also a module, and a package cannot hold both under one name: importing the submodule
    silently rebinds the attribute, so the same expression meant a function or a module
    depending on what had been imported first. Measured, not theorised -- it was shipped
    that way for two commits.
    """

    return tuple(fault for fault in FAULTS if fault.phase is Phase.PREFLIGHT)


def postflight_faults() -> tuple[Fault, ...]:
    """Faults that need the run to have happened. See :func:`preflight_faults` on the name."""

    return tuple(fault for fault in FAULTS if fault.phase is Phase.POSTFLIGHT)


def wrong_subject() -> tuple[Fault, ...]:
    """Faults that make the number be about something other than the molecule.

    Deliberately not the same set as :func:`unbounded`, and a caller deciding whether to
    spend should use this one. Every fault here is unbounded, but not every unbounded
    fault is here -- and the two that are not, the run that cannot be reproduced and the
    convergence that cannot be assessed, are exactly the ones a magnitude-based refusal
    rule rejects by mistake.
    """

    return tuple(
        fault for fault in FAULTS if fault.consequence is Consequence.WRONG_SUBJECT
    )


def unbounded() -> tuple[Fault, ...]:
    """Faults that magnitude cannot eliminate, and which therefore need an observable.

    Worth being able to ask for: these are the causes where a diagnosis rests entirely
    on the exact comparison, so a campaign with no observable for one of them has no
    way to rule it out at all.
    """

    return tuple(fault for fault in FAULTS if fault.upper_kcal_mol is None)


__all__ = [
    "BY_CODE",
    "FAULTS",
    "Consequence",
    "Evidence",
    "Exactness",
    "Fault",
    "Phase",
    "postflight_faults",
    "preflight_faults",
    "unbounded",
    "wrong_subject",
]
