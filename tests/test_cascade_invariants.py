"""A campaign can declare steps its conclusions depend on, and a revision cannot drop them.

Free composition is the premise of ``campaign.design``: a one-component cascade is a legal
design and nothing is required by default. That premise has a cost which this module is about.
Two different mechanisms can remove a step without anybody saying so:

* a proposed revision -- including one an automated improvement loop writes -- can simply omit
  a tier and still compile, because the compiler checks interfaces rather than intent;
* the shipped default cascade is assembled against *the installed registry*. Every tier goes
  through ``_criterion_if_available``, so a cascade described as twelve tiers is seven in an
  environment missing the packages behind the other five, and ``include_redock=True`` does not
  make redock present. Measured in this repository's own review environment: the default
  cascade yielded ``t1_chemistry t2_physchem t3_druglike t4_alerts t5_synthesis
  t8_ligand_conformers t9_docking`` -- no redock, no affinity, no ADMET, no structure QC.

Both are silent, and for an affinity campaign both change what the numbers mean. The
invariant here is deliberately about *presence*, not adequacy: it cannot say whether a docking
box was right or a redock threshold sensible, which stays the gate calibration's question.
"""

from __future__ import annotations

import pytest

from etalon.campaign.design import (
    CAPABILITY_BACKENDS,
    check_required,
    component,
    compose,
    provided_capabilities,
)

#: What an affinity loop's conclusions rest on: a pose, an independent check of that pose, and
#: an affinity opinion that is not the docking score again.
AFFINITY_LOOP = ("docking", "redock", "affinity")


def _tier(tier_id: str, *backends: str) -> dict:
    return {
        "id": tier_id,
        "title": tier_id,
        "criteria": [component(f"{tier_id}_{index}", backend)
                     for index, backend in enumerate(backends)],
    }


def test_nothing_is_required_by_default() -> None:
    """The opt-in property, pinned. A cascade of one component stays legal."""

    assert compose("one", [_tier("properties", "features.rdkit_properties@0.1.0")])
    check_required([_tier("properties", "features.rdkit_properties@0.1.0")], ())


def test_a_revision_that_drops_a_declared_step_is_refused() -> None:
    """The case this exists for: an improvement loop proposes a cheaper funnel."""

    tiers = [_tier("properties", "features.rdkit_properties@0.1.0")]
    with pytest.raises(ValueError, match="indispensable") as refusal:
        compose("loop", tiers, require=AFFINITY_LOOP)
    message = str(refusal.value)
    # The refusal has to name what is missing and what would satisfy it, or the next actor
    # cannot act on it -- the failure mode this project keeps finding in its own messages.
    for name in AFFINITY_LOOP:
        assert name in message
    assert "docking.unidock" in message


def test_a_typo_cannot_read_as_a_satisfied_requirement() -> None:
    with pytest.raises(ValueError, match="unknown required capability"):
        check_required([_tier("t", "docking.unidock@0.4.0")], ("dokcing",))


def test_an_explicit_affinity_composition_satisfies_the_invariant() -> None:
    """Stated against exact backends, so the result does not depend on what is installed."""

    tiers = [
        _tier("conformers", "docking.rdkit_conformers@0.1.0"),
        _tier("dock", "docking.unidock@0.4.0"),
        _tier("redock", "docking.redock_consistency@0.1.0"),
        _tier("affinity", "prediction.boltz2@0.1.0"),
    ]
    provided = provided_capabilities(tiers)
    assert {"docking", "redock", "affinity", "conformers"} <= provided
    check_required(tiers, AFFINITY_LOOP)


def test_any_registered_docking_engine_satisfies_the_docking_step() -> None:
    """Which engine runs stays the host's scientific choice; that one runs does not."""

    for backend in CAPABILITY_BACKENDS["docking"]:
        check_required([_tier("dock", backend + "@0.9.9")], ("docking",))


def test_the_installed_default_cascade_is_checked_rather_than_assumed() -> None:
    """Whatever the registry yields, the invariant reports it rather than trusting the name.

    This test deliberately asserts no particular tier count: the point is that the answer is
    environment-dependent and must therefore be *measured*. What it does assert is that the
    two silent-removal paths are visible through this check.
    """

    from molcascade.cascade.defaults import default_cascade

    shipped = default_cascade().model_dump(mode="json")
    provided = provided_capabilities(shipped["tiers"])

    # Docking is the one tier the starter cascade cannot be useful without, and the review
    # environment does have an engine for it.
    assert "docking" in provided, provided

    # include_redock=False is a legal call that removes a step an affinity loop depends on.
    without = default_cascade(include_redock=False).model_dump(mode="json")
    assert "redock" not in provided_capabilities(without["tiers"])
    with pytest.raises(ValueError, match="redock"):
        check_required(without["tiers"], ("docking", "redock"))

    # And when redock *is* present, the same call passes -- so the check is not vacuous.
    if "redock" in provided:
        check_required(shipped["tiers"], ("docking", "redock"))


def test_boltz2_is_omitted_without_its_settings_and_the_invariant_says_so() -> None:
    """``_affinity_tier`` returns None when ``boltz2_settings`` is empty, with no error.

    For a campaign whose final ranking is an affinity opinion, that is the difference between
    a cascade that answers the question and one that stops at the docking score.
    """

    from molcascade.cascade.defaults import default_cascade

    bare = default_cascade().model_dump(mode="json")
    assert "affinity" not in provided_capabilities(bare["tiers"])
    with pytest.raises(ValueError, match="affinity"):
        check_required(bare["tiers"], ("affinity",))
