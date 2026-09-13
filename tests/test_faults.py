"""Elimination by magnitude, and the honesty rules that keep the bands meaningful.

The mechanism is small and the ways it can become theatre are specific, so most of
these tests are about those rather than about the arithmetic.

A band that nobody measured must not look like a measurement. A cause that cannot be
eliminated by magnitude must say so rather than carrying a large number that implies
a bound. A verdict reached while some observable could not be evaluated must be marked
provisional. And the case that matters most -- a divergence too large for anything
that fired -- must come back UNEXPLAINED rather than blaming the nearest candidate,
because a confident wrong answer is worse here than an admitted gap.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.faults import (
    BY_CODE,
    FAULTS,
    Consequence,
    Evidence,
    Observation,
    Phase,
    Verdict,
    attribute,
    postflight_faults,
    preflight_faults,
    unbounded,
)


def test_the_catalogue_is_internally_consistent() -> None:
    codes = [fault.code for fault in FAULTS]
    assert len(codes) == len(set(codes)), "a fault code is duplicated"
    assert set(BY_CODE) == set(codes)
    assert len(preflight_faults()) + len(postflight_faults()) == len(FAULTS)
    for fault in FAULTS:
        assert fault.code.startswith("F_")
        assert fault.lower_kcal_mol >= 0.0
        if fault.upper_kcal_mol is not None:
            assert fault.upper_kcal_mol >= fault.lower_kcal_mol


def test_every_band_says_where_its_number_came_from() -> None:
    """A convention is allowed. A convention dressed as a measurement is not.

    This is the rule a reviewer caught the design breaking once already: a chosen
    cutoff presented as though it were a finding. Every band here declares its
    provenance, and a CONVENTION must say in its source that nothing measured it.
    """

    for fault in FAULTS:
        assert fault.source.strip(), f"{fault.code} has a band with no stated source"
        if fault.evidence is Evidence.CONVENTION:
            lowered = fault.source.lower()
            assert "no measurement" in lowered or "inherited" in lowered, (
                f"{fault.code} is a convention but its source does not admit that "
                "nothing measured it"
            )


def test_a_literature_band_carries_a_citation_and_not_an_argument() -> None:
    """The hole the previous rule left, and how it was found.

    Three bands were labelled LITERATURE and their sources were arguments -- true
    statements about solvation energies and eutomer ratios, with no paper behind them.
    The old rule only required a non-empty string, so it passed all three. A reader
    following the label would have gone looking for a reference that was never there.

    They are now CONVENTION, which is what they always were. This rule is what stops the
    relabelling from quietly reverting: a LITERATURE band must carry something a reader
    can go and find, and a year is the cheapest test for that.
    """

    for fault in FAULTS:
        if fault.evidence is not Evidence.LITERATURE:
            continue
        assert re.search(r"\b(19|20)\d{2}\b", fault.source), (
            f"{fault.code} claims LITERATURE but its source carries no year, so it is "
            f"an argument rather than a citation: {fault.source!r}"
        )


def test_the_literature_rule_is_no_longer_vacuous() -> None:
    """It used to be, and the test that said so has been deleted by its own instructions.

    For two commits this file asserted the catalogue held *zero* LITERATURE bands, so that the
    citation rule above could not pass trivially without somebody noticing. Its docstring said
    that adding a real citation should make it fail and that the author should then read why and
    delete it. F_SINGLE_REPLICA_ESTIMATE is that citation -- the run-to-run spread of
    single-trajectory MM-PBSA, up to 12 kcal/mol from identical structures -- so the rule now has
    something to rule on and the placeholder is gone.
    """

    cited = [f for f in FAULTS if f.evidence is Evidence.LITERATURE]

    assert cited, "the citation rule above is vacuous again; restore a state assertion"
    assert all(re.search(r"\b(19|20)\d{2}\b", fault.source) for fault in cited)


def test_the_consequence_of_every_fault_is_stated_and_coherent() -> None:
    """A number about something else has no error size. The two must agree."""

    for fault in FAULTS:
        assert isinstance(fault.consequence, Consequence)
        if fault.consequence is Consequence.WRONG_SUBJECT:
            assert fault.upper_kcal_mol is None, (
                f"{fault.code} says the number is about something else but carries an "
                "upper band, which would let magnitude eliminate it"
            )
        if fault.consequence is Consequence.WRONG_SIZE:
            assert fault.upper_kcal_mol is not None, (
                f"{fault.code} says the number is the wrong size but names no size"
            )


def test_every_fault_names_an_observable_and_its_exactness() -> None:
    """A fault with no observable is a fault nothing can detect."""

    for fault in FAULTS:
        assert fault.observable.strip()
        assert fault.exactness is not None
        assert fault.remedy.strip()
        # The remedy must not be "repair it silently": several of these are decisions
        # with scientific content.
        assert "automatically" not in fault.remedy.lower()


def test_an_unbounded_cause_is_never_eliminated_by_size() -> None:
    """Unbounded means "the estimate is about the wrong thing", not "a big error"."""

    for fault in unbounded():
        assert fault.upper_kcal_mol is None
        assert fault.can_account_for(0.1)
        assert fault.can_account_for(500.0)


def test_a_bounded_cause_is_refused_above_its_band() -> None:
    stereo = BY_CODE["F_STEREO_CHOSEN_BY_EMBEDDING"]

    assert stereo.upper_kcal_mol == 3.0
    assert stereo.can_account_for(1.5)
    assert stereo.can_account_for(3.0)
    assert not stereo.can_account_for(3.01)
    # Below the floor a cause is still a candidate: a cause that can produce a large
    # error can produce a small one, so the floor marks where it becomes worth naming.
    assert stereo.can_account_for(0.2)


def test_sign_is_recorded_and_ignored_in_the_arithmetic() -> None:
    """A band describes a magnitude; a cause shifts an estimate either way."""

    flag = (Observation("F_STEREO_CHOSEN_BY_EMBEDDING", True, "chose [C@H]"),)

    positive = attribute(2.0, flag)
    negative = attribute(-2.0, flag)

    assert positive.verdict is negative.verdict is Verdict.ATTRIBUTED
    assert positive.divergence_kcal_mol == 2.0
    assert negative.divergence_kcal_mol == -2.0


def test_one_surviving_cause_is_attributed() -> None:
    result = attribute(
        1.5,
        (
            Observation("F_STEREO_CHOSEN_BY_EMBEDDING", True, "stereo differs from name"),
            Observation("F_HYDROGENS_IMPLICIT", False, "21 hydrogens"),
            Observation("F_COORDINATES_ARE_A_DEPICTION", False, "DOCKED_POSE"),
        ),
    )

    assert result.verdict is Verdict.ATTRIBUTED
    assert [fault.code for fault in result.candidates] == ["F_STEREO_CHOSEN_BY_EMBEDDING"]
    assert result.magnitude_was_available is True
    assert result.magnitude_eliminated is False


def test_the_same_flag_at_a_larger_divergence_is_unexplained() -> None:
    """The whole point. The flag is identical; only the size changed."""

    flag = (Observation("F_STEREO_CHOSEN_BY_EMBEDDING", True, "stereo differs from name"),)

    small = attribute(1.5, flag)
    large = attribute(6.0, flag)

    assert small.verdict is Verdict.ATTRIBUTED
    assert large.verdict is Verdict.UNEXPLAINED
    assert [fault.code for fault in large.eliminated_by_magnitude] == [
        "F_STEREO_CHOSEN_BY_EMBEDDING"
    ]
    assert not large.candidates
    assert any("outside this taxonomy" in note for note in large.notes)


def test_several_survivors_are_reported_as_ambiguous_rather_than_ranked() -> None:
    result = attribute(
        8.0,
        (
            Observation("F_PROTONATION_UNDECIDED", True, "INHERITED_FROM_STANDARDIZER"),
            Observation("F_RECEPTOR_NOT_THE_ONE_SCORED", True, "digests differ"),
        ),
    )

    assert result.verdict is Verdict.AMBIGUOUS
    assert len(result.candidates) == 2
    assert any("Narrowing further needs an observable" in note for note in result.notes)


def test_candidates_are_ordered_so_the_decidable_ones_come_first() -> None:
    result = attribute(
        10.0,
        (
            Observation("F_FEP_MAPPING_DEGENERATE", True, "12 of 31 atoms mapped"),
            Observation("F_PROTONATION_UNDECIDED", True, "no method decided"),
        ),
    )

    assert result.verdict is Verdict.AMBIGUOUS
    # The exact comparison ranks above the threshold, because a reader looking at an
    # ambiguous list should see what can be settled first.
    assert result.candidates[0].code == "F_PROTONATION_UNDECIDED"
    assert result.candidates[-1].code == "F_FEP_MAPPING_DEGENERATE"


def test_nothing_firing_is_not_the_same_as_nothing_explaining() -> None:
    """Two methods within their own error bars is not a fault, and this says so."""

    result = attribute(
        2.1,
        (
            Observation("F_STEREO_CHOSEN_BY_EMBEDDING", False, "stereo matches the name"),
            Observation("F_HYDROGENS_IMPLICIT", False, "EXPLICIT_ALL"),
        ),
    )

    assert result.verdict is Verdict.NO_FAULT_OBSERVED
    assert any("within their own reported uncertainties" in note for note in result.notes)


def test_an_unevaluable_observable_leaves_its_cause_standing() -> None:
    """Neither a candidate nor cleared, and the verdict is marked provisional.

    This is the state PRISM's FEP configuration actually produces: with
    calc-lambda-neighbors = 1 the MBAR overlap matrix is banded by construction, so
    the convergence diagnostics derived from it cannot be computed. Reporting them as
    values would be the instrument lying about itself; reporting the check as done
    would be worse.
    """

    result = attribute(
        4.0,
        (
            Observation("F_STEREO_CHOSEN_BY_EMBEDDING", False, "matches"),
            Observation(
                "F_CONVERGENCE_NOT_ASSESSABLE",
                False,
                "overlap matrix is banded by construction",
                evaluable=False,
            ),
        ),
    )

    assert [fault.code for fault in result.not_assessable] == ["F_CONVERGENCE_NOT_ASSESSABLE"]
    assert not result.candidates
    assert any("provisional" in note for note in result.notes)


def test_only_unbounded_causes_firing_is_reported_as_such() -> None:
    """Otherwise a verdict resting on no magnitude reasoning looks like one that did."""

    result = attribute(12.0, (Observation("F_HYDROGENS_IMPLICIT", True, "0 hydrogens"),))

    assert result.verdict is Verdict.ATTRIBUTED
    assert result.magnitude_was_available is False
    assert any("No cause that fired carries a band" in note for note in result.notes)


def test_a_bounded_survivor_does_not_trigger_the_no_band_note() -> None:
    """The note was wrong once: it fired whenever nothing was eliminated."""

    result = attribute(1.5, (Observation("F_STEREO_CHOSEN_BY_EMBEDDING", True, "differs"),))

    assert result.magnitude_was_available is True
    assert not any("No cause that fired carries a band" in note for note in result.notes)


def test_an_unknown_code_is_refused_rather_than_ignored() -> None:
    with pytest.raises(KeyError, match="not in the taxonomy"):
        attribute(1.0, (Observation("F_SOMETHING_INVENTED", True, "made up"),))


def test_the_preflight_half_needs_no_simulation() -> None:
    """The cheap half is the valuable half: it refuses before the spend.

    Every preflight observable must name a column of the handoff contract or the
    toolchain, because anything needing a trajectory cannot run before one exists.
    """

    for fault in preflight_faults():
        observable = fault.observable
        assert (
            "md_system_input/v1" in observable
            or "toolchain" in observable
            or "mapped-atom" in observable
            or "no md_system_input/v1 row" in observable
        ), f"{fault.code} claims PREFLIGHT but its observable needs more than the handoff"


def test_the_record_round_trips_as_json() -> None:
    import json

    result = attribute(
        6.0,
        (
            Observation("F_STEREO_CHOSEN_BY_EMBEDDING", True, "differs"),
            Observation("F_CONVERGENCE_NOT_ASSESSABLE", False, "banded", evaluable=False),
        ),
    )

    payload = json.loads(json.dumps(result.as_dict()))

    assert payload["verdict"] == "unexplained"
    assert payload["eliminated_by_magnitude"][0]["code"] == "F_STEREO_CHOSEN_BY_EMBEDDING"
    assert "cannot reach the observed 6.0" in payload["eliminated_by_magnitude"][0]["why"]
    assert payload["not_assessable"][0]["code"] == "F_CONVERGENCE_NOT_ASSESSABLE"


def test_faults_measured_here_cite_a_measurement() -> None:
    """A MEASURED_HERE band must point at something a reader can go and check.

    The rule was first written as a hand-kept list of substrings, which failed on the
    fourth source added to the catalogue -- for capitalising "Measured". A whitelist of
    phrasings tests the phrasing, so the rule is now what it was always meant to be: the
    source names a recorded artefact, or a count, or says in words that somebody measured
    it. Case-insensitive, because the distinction between a finding and a sentence is not
    a matter of capitalisation.
    """

    for fault in FAULTS:
        if fault.evidence is not Evidence.MEASURED_HERE:
            continue
        lowered = fault.source.lower()
        points_at_a_record = "adr/" in lowered or "findings/" in lowered
        says_it_was_measured = any(
            word in lowered for word in ("measured", "verified", "observed")
        )
        # Two or more numbers, as a proxy for "reports quantities rather than reasoning
        # about them". It is a proxy and not a proof: what it actually catches is a source
        # with no recorded artefact, no claim of measurement and no numbers in it, which is
        # an argument wearing a measurement's label. The three bands that were exactly that
        # are CONVENTION now, which is how the proxy was calibrated.
        quantities = len(re.findall(r"\d+(?:[.,]\d+)*", fault.source))
        assert points_at_a_record or says_it_was_measured or quantities >= 2, (
            f"{fault.code} claims a measurement without pointing at one. Name the ADR or "
            f"findings file, give the counts, or say what was measured: {fault.source!r}"
        )


def test_the_convergence_fault_is_postflight_and_names_the_real_setting() -> None:
    """It is the one fault whose detection depends on a setting ETALON leaves alone."""

    fault = BY_CODE["F_CONVERGENCE_NOT_ASSESSABLE"]

    assert fault.phase is Phase.POSTFLIGHT
    assert "calc-lambda-neighbors" in fault.observable
    assert "UNAVAILABLE" in fault.remedy
    # And it must not propose rewriting the setting, because that changes what the run
    # records rather than how it is read.
    assert "Do not silently rewrite" in fault.remedy


def test_a_number_measured_in_an_asset_says_so_and_names_the_file() -> None:
    """The most-quoted number in the repository had the wrong label on it.

    "41 of 41 gaff2 builds carried zero hydrogens" is the opening evidence of the README and it was
    labelled MEASURED_HERE, which named the wrong project: it was measured in the vendored PRISM,
    which is why it is the only headline figure with no findings/ entry. LITERATURE was no better --
    the citation rule above requires a year and there is no paper. So the class exists, and what it
    demands is the one thing that makes the claim checkable: a path into asset/ that somebody can go
    and read at the pinned commit.
    """

    from_assets = [f for f in FAULTS if f.evidence is Evidence.MEASURED_IN_AN_ASSET]

    assert from_assets, "the rule below is vacuous; a reclassification has been reverted"
    for fault in from_assets:
        assert "asset/" in fault.source, (
            f"{fault.code} claims a measurement inside a vendored asset and does not name the file "
            f"it is recorded in, so a reader cannot check it: {fault.source!r}"
        )
        assert "MANIFEST" in fault.source or "pin" in fault.source.lower(), (
            f"{fault.code} names an asset file without saying the copy is pinned, and an asset that "
            "moves makes this number a claim about whatever is on disk today"
        )


def test_no_fault_claims_this_project_measured_the_hydrogen_result() -> None:
    """The specific regression: ETALON did not run those builds and must not say it did."""

    hydrogens = BY_CODE["F_HYDROGENS_IMPLICIT"]

    assert hydrogens.evidence is Evidence.MEASURED_IN_AN_ASSET
    assert hydrogens.evidence is not Evidence.MEASURED_HERE
