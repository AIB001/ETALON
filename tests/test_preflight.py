"""Refusing a handoff before the spend, and the two ways that refusal goes wrong.

The checker itself is arithmetic over columns, so these tests spend almost all their
attention on the judgement calls around it.

The first is over-refusal. A checker that blocks too much gets switched off, and then it
protects nothing. The bug this file exists to pin was exactly that: ``blocking`` once
read "fired and unbounded", which blocked every molecule in an unseeded campaign even
though an unseeded run is scientifically fine and merely uncheckable.

The second is under-reporting. An observable that could not be evaluated must not be
counted as clean, because "0 blocking" printed over four unevaluated checks tells the
operator the opposite of the truth.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.faults import (
    BY_CODE,
    Consequence,
    Observation,
    attribute,
    blocking,
    check_population,
    check_record,
    preflight_faults,
    unbounded,
    unchecked,
    unverifiable,
    wrong_subject,
)

#: A record that should clear every check: a docked pose with explicit hydrogens, a
#: receptor it names, a protonation state something decided, and a configuration that
#: matches the name it is filed under.
CLEAN = {
    "parent_id": "parent:0001",
    "method_id": "dock:vina@1.2.5",
    "molblock": "(a real molblock)",
    "coordinate_source": "DOCKED_POSE",
    "hydrogens": "EXPLICIT_ALL",
    "heavy_atom_count": 19,
    "hydrogen_count": 21,
    "formal_charge": 0,
    "stereo_smiles": "CC(C)NC[C@H](O)c1ccccc1",
    "parent_smiles": "CC(C)NC[C@H](O)c1ccccc1",
    "protonation_state_id": "propka:ph7.4",
    "receptor_id": "receptor:sha256:0" * 1,
    "status": "OK",
}

#: What MolCascade's SDF shortlist exporter actually produces, as measured: geometry
#: rebuilt from the name, no explicit hydrogens, nothing having decided a species.
FROM_A_DRAWING = {
    **CLEAN,
    "parent_id": "parent:0002",
    "coordinate_source": "TWO_D_DEPICTION",
    "hydrogens": "IMPLICIT",
    "hydrogen_count": 0,
    "stereo_smiles": None,
    "protonation_state_id": "INHERITED_FROM_STANDARDIZER",
    "receptor_id": None,
}


def test_a_clean_record_fires_nothing() -> None:
    """Otherwise every later test is measuring the checker's own noise."""

    fired = [o for o in check_record(CLEAN, toolchain_active=True) if o.evaluable and o.fired]

    assert fired == [], [o.code for o in fired]


def test_every_preflight_fault_has_an_observation() -> None:
    """A fault the checker never speaks about is a fault nothing detects.

    ``F_HANDOFF_ABSENT`` is the one exclusion and it is structural: a row cannot report
    its own absence, so it is evaluated against the expected population instead.
    """

    codes = {o.code for o in check_record(CLEAN)} | {
        o.code for o in check_population(["a"], {"a": CLEAN})
    }

    assert {fault.code for fault in preflight_faults()} == codes


def test_a_record_built_from_a_drawing_is_refused() -> None:
    observations = check_record(FROM_A_DRAWING, toolchain_active=True)
    refused = {o.code for o in blocking(observations)}

    assert "F_COORDINATES_ARE_A_DEPICTION" in refused
    assert "F_HYDROGENS_IMPLICIT" in refused
    assert "F_PROTONATION_UNDECIDED" in refused
    # And the pose check must not fire on it: these coordinates never claimed to be one.
    assert "F_POSE_WITHOUT_RECEPTOR" not in refused


def test_an_unseeded_toolchain_does_not_block_a_good_molecule() -> None:
    """The bug this file was written for.

    ``blocking`` read "fired and unbounded". ``F_RUN_NOT_REPRODUCIBLE`` is unbounded, so
    a campaign run without the seed shim had every one of its molecules refused -- for a
    reason that is about the claim "this is reproducible" and not about the molecule.
    """

    observations = check_record(CLEAN, toolchain_active=False)

    assert blocking(observations) == ()
    assert [o.code for o in unverifiable(observations)] == ["F_RUN_NOT_REPRODUCIBLE"]


def test_the_two_axes_are_not_the_same_set() -> None:
    """If they were, the field would be redundant and the old rule would have been right.

    Pinned by name rather than by count, and it has already earned that: adding the
    expensive stage's faults introduced a third member and this test made the addition a
    decision instead of a side effect. Each of these three is unbounded -- no band
    describes it -- and none is a reason to refuse a molecule:

    ``F_RUN_NOT_REPRODUCIBLE``  no seed, so no re-run confirms the number.
    ``F_CONVERGENCE_NOT_ASSESSABLE``  the diagnostics cannot be computed from what was
    recorded, which invalidates the diagnostics and not the free energy.
    ``F_PROTONATION_NOT_APPLIED``  the predictor could not map a residue. Usually a
    terminus, where the default is what it would have said anyway; refusing every such
    build would be ADR 0002's mistake a third time, and what is really missing is the
    ability to say whether the simulated state is the computed one.

    Moving any of them into WRONG_SUBJECT would re-introduce the refusal bug silently.
    """

    only_unbounded = {f.code for f in unbounded()} - {f.code for f in wrong_subject()}

    assert only_unbounded == {
        "F_RUN_NOT_REPRODUCIBLE",
        "F_CONVERGENCE_NOT_ASSESSABLE",
        "F_PROTONATION_NOT_APPLIED",
        # The ligand held the site and lost the contacts the docked pose was scored for. The free
        # energy is about a real bound state, so refusing the molecule would discard a result;
        # what is lost is the ability to compare that number with the screen's, which invalidates
        # a calibration rather than a measurement.
        "F_POSE_NOT_THE_SCORED_ONE",
    }
    # The containment still holds in the other direction: a number about something else
    # has no error size, so nothing in WRONG_SUBJECT may carry an upper bound.
    for fault in wrong_subject():
        assert fault.upper_kcal_mol is None, fault.code


def test_a_bounded_fault_annotates_rather_than_blocks() -> None:
    """A wrong-size result is the operator's call, not this layer's."""

    differing = {**CLEAN, "stereo_smiles": "CC(C)NC[C@@H](O)c1ccccc1"}
    observations = check_record(differing, toolchain_active=True)
    fired = [o.code for o in observations if o.evaluable and o.fired]

    assert fired == ["F_STEREO_CHOSEN_BY_EMBEDDING"]
    assert blocking(observations) == ()
    assert BY_CODE["F_STEREO_CHOSEN_BY_EMBEDDING"].consequence is Consequence.WRONG_SIZE


def test_an_unevaluable_check_is_not_a_pass() -> None:
    """Without the receptor file the digest comparison cannot be made, and says so."""

    observations = check_record(CLEAN)  # no receptor_path, no toolchain answer
    by_code = {o.code: o for o in observations}

    assert by_code["F_RECEPTOR_NOT_THE_ONE_SCORED"].evaluable is False
    assert by_code["F_RUN_NOT_REPRODUCIBLE"].evaluable is False
    assert {o.code for o in unchecked(observations)} >= {
        "F_RECEPTOR_NOT_THE_ONE_SCORED",
        "F_RUN_NOT_REPRODUCIBLE",
    }
    # Crucially, not reported as clean: nothing fired, and three checks did not run.
    assert blocking(observations) == ()
    assert unchecked(observations)


def test_the_receptor_digest_is_compared_against_the_file_on_disk(tmp_path: Path) -> None:
    """The point of the check is the file about to be used, not a recorded claim."""

    receptor = tmp_path / "receptor.pdb"
    receptor.write_bytes(b"ATOM      1  N   MET A   1       0.000   0.000   0.000\n")
    digest = hashlib.sha256(receptor.read_bytes()).hexdigest()

    matching = check_record(
        {**CLEAN, "receptor_id": f"receptor:sha256:{digest}"}, receptor_path=receptor
    )
    by_code = {o.code: o for o in matching}
    assert by_code["F_RECEPTOR_NOT_THE_ONE_SCORED"].fired is False
    assert by_code["F_RECEPTOR_NOT_THE_ONE_SCORED"].evaluable is True

    # Re-protonated between docking and the build: same filename, different bytes.
    receptor.write_bytes(b"ATOM      1  N   MET A   1       0.000   0.000   0.001\n")
    stale = check_record(
        {**CLEAN, "receptor_id": f"receptor:sha256:{digest}"}, receptor_path=receptor
    )
    assert {o.code for o in blocking(stale)} == {"F_RECEPTOR_NOT_THE_ONE_SCORED"}


def test_an_unreadable_receptor_file_is_unevaluable_not_clean(tmp_path: Path) -> None:
    missing = tmp_path / "gone.pdb"

    observations = check_record(CLEAN, receptor_path=missing)
    entry = next(o for o in observations if o.code == "F_RECEPTOR_NOT_THE_ONE_SCORED")

    assert entry.evaluable is False
    assert "could not read" in entry.detail


def test_a_missing_record_fails_closed() -> None:
    observations = check_population(["a", "b", "c"], {"a": CLEAN, "b": FROM_A_DRAWING})

    assert len(observations) == 1
    assert observations[0].fired is True
    assert "c" in observations[0].detail
    assert blocking(observations)


def test_a_complete_population_does_not_fire() -> None:
    observations = check_population(["a"], {"a": CLEAN})

    assert observations[0].fired is False
    assert "all 1 expected molecules have a record" in observations[0].detail


def test_the_observations_feed_the_attribution_layer_unchanged() -> None:
    """The two halves must compose: preflight observations are attribution input.

    If they did not, the magnitude layer would need its own parallel evaluation of the
    same columns, and the two would drift.
    """

    observations = check_record(FROM_A_DRAWING, toolchain_active=True)
    result = attribute(6.0, observations)

    assert result.verdict.value in {"attributed", "ambiguous"}
    assert {fault.code for fault in result.candidates} >= {"F_COORDINATES_ARE_A_DEPICTION"}


def test_the_evidence_for_each_finding_reaches_the_record() -> None:
    """It did not, once: the verdict named the fault and dropped the observation."""

    observations = check_record(FROM_A_DRAWING, toolchain_active=True)
    payload = attribute(6.0, observations).as_dict()

    depiction = next(
        entry for entry in payload["candidates"] if entry["code"] == "F_COORDINATES_ARE_A_DEPICTION"
    )
    assert depiction["detail"] == "coordinate_source='TWO_D_DEPICTION'"
    assert all(entry["detail"] for entry in payload["candidates"])


def test_a_cause_below_its_own_floor_is_separated_from_one_expressing_fully() -> None:
    """The only use the catalogue makes of a lower bound."""

    flag = (Observation("F_STEREO_CHOSEN_BY_EMBEDDING", True, "built the other centre"),)

    faint = attribute(0.4, flag)
    full = attribute(2.0, flag)

    assert [f.code for f in faint.below_their_floor] == ["F_STEREO_CHOSEN_BY_EMBEDDING"]
    assert faint.candidates == full.candidates  # still a candidate, not eliminated
    assert full.below_their_floor == ()
    assert any("check the divergence itself" in note for note in faint.notes)


# -- the postflight half ---------------------------------------------------


def test_a_stage_that_never_ran_blocks_on_a_driver_that_reported_success() -> None:
    """findings/0002, read through the fault layer.

    The driver's exit code is 0 and em, nvt and npt all failed. A campaign reading the
    exit code would admit whatever the analysis produced from a trajectory that is not
    there.
    """

    from etalon.faults import postflight

    manifest = {
        "build": {"built": True},
        "drive": {
            "exit_code": 0,
            "timed_out": False,
            "requested": ["em", "nvt", "npt"],
            "stages": [
                {"stage": "em", "state": "NEVER_STARTED"},
                {"stage": "nvt", "state": "NEVER_STARTED"},
                {"stage": "npt", "state": "NEVER_STARTED"},
                {"stage": "prod", "state": "FINISHED"},
            ],
            "warnings": {"grompp_warnings": 0, "by_kind": {}},
        },
    }

    observed = postflight.check_run(manifest)
    blocked = {entry.code for entry in postflight.blocking(observed)}

    assert "F_STAGE_NEVER_RAN" in blocked
    detail = next(e for e in observed if e.code == "F_STAGE_NEVER_RAN").detail
    assert "em=NEVER_STARTED" in detail and "exit 0" in detail


def test_a_campaign_wanting_equilibration_is_not_failed_by_production() -> None:
    from etalon.faults import postflight

    manifest = {
        "build": {"built": True},
        "drive": {
            "exit_code": None,
            "timed_out": True,
            "requested": ["em", "nvt", "npt"],
            "stages": [
                {"stage": "em", "state": "FINISHED"},
                {"stage": "nvt", "state": "FINISHED"},
                {"stage": "npt", "state": "FINISHED"},
                {"stage": "prod", "state": "STARTED_NOT_FINISHED"},
            ],
            "warnings": {"grompp_warnings": 2, "by_kind": {}},
        },
    }

    assert postflight.blocking(postflight.check_run(manifest)) == ()


def test_a_fractional_charge_grompp_was_told_to_ignore_still_blocks() -> None:
    """Every grompp in the driver carries -maxwarn 999, so it printed this and built."""

    from etalon.faults import postflight

    observed = postflight.check_drive(
        {
            "exit_code": 0,
            "requested": ["em"],
            "stages": [{"stage": "em", "state": "FINISHED"}],
            "warnings": {"grompp_warnings": 3, "by_kind": {"non_integer_charge": 1}},
        }
    )

    blocked = {entry.code for entry in postflight.blocking(observed)}
    assert blocked == {"F_TOPOLOGY_CHARGE_NOT_INTEGER"}
    assert "-maxwarn 999" in next(
        e for e in observed if e.code == "F_TOPOLOGY_CHARGE_NOT_INTEGER"
    ).detail


def test_a_build_never_driven_leaves_its_stages_unevaluable_not_failed() -> None:
    """Nothing ran, which is different from something having failed."""

    from etalon.faults import postflight

    observed = postflight.check_run({"build": {"built": True}})
    by_code = {entry.code: entry for entry in observed}

    assert by_code["F_STAGE_NEVER_RAN"].evaluable is False
    assert by_code["F_TOPOLOGY_CHARGE_NOT_INTEGER"].evaluable is False
    assert postflight.blocking(observed) == ()


def test_an_unmapped_terminus_is_told_apart_from_an_unmapped_residue() -> None:
    """The one distinction this check exists to draw, and it was inverted once.

    The real line is ``Residue A:1 N+: N+ (unmapped)``. Capturing only the first token
    after "Residue" yields ``A:1``, which contains no terminus marker, so a routine
    terminus was reported as an interior residue worth deciding by hand.
    """

    from etalon.faults import postflight

    log = (
        "WARNING:prism.utils.protonation:Residue A:1 N+: N+ (unmapped)\n"
        "WARNING:prism.utils.protonation:Residue A:34 HIS: HIS (unmapped)\n"
    )

    entry = next(
        e for e in postflight.check_build({"built": True}, log)
        if e.code == "F_PROTONATION_NOT_APPLIED"
    )

    assert entry.fired is True
    assert "1 of them a terminus" in entry.detail
    assert "and 1 not" in entry.detail
    assert "HIS" in entry.detail


def test_no_build_log_means_the_protonation_check_did_not_run() -> None:
    """The unmapped lines exist only in the log; an absent log is an unasked question."""

    from etalon.faults import postflight

    entry = next(
        e for e in postflight.check_build({"built": True})
        if e.code == "F_PROTONATION_NOT_APPLIED"
    )

    assert entry.evaluable is False
    assert entry.fired is False


def test_a_failed_build_blocks_and_carries_its_reason() -> None:
    from etalon.faults import postflight

    observed = postflight.check_build(
        {"built": False, "detail": "no topol.top and solv_ions.gro in GMX_PROLIG_MD"}
    )

    assert {e.code for e in postflight.blocking(observed)} == {"F_BUILD_INCOMPLETE"}
    assert "topol.top" in observed[0].detail
