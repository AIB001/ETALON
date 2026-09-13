"""Which generator was worth running, and the stage at which that stops being answerable.

Generation is the one stage of a modern CADD pipeline with no feedback on it: the campaign runs the
models it has, screens the union, reports the survivors. Where feedback is attempted it uses the
final hit counts, and the arithmetic of small samples says those counts cannot support it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from etalon.generate.audit import GeneratorYield, audit, compare, wilson

STAGES = ("qc_passed", "docking_top", "mmgbsa", "final")


def _yield(model: str, counts: tuple[int, int, int, int], known: int = 0) -> GeneratorYield:
    return GeneratorYield(
        model, 150_000, dict(zip(STAGES, counts, strict=True)), known_scaffolds=known
    )


def test_the_interval_that_decides_everything() -> None:
    """Four of ten and one of ten overlap, so the stage everybody reports cannot rank models."""

    four = wilson(4, 10)
    one = wilson(1, 10)

    assert four == pytest.approx((0.168, 0.687), abs=0.002)
    assert one == pytest.approx((0.018, 0.404), abs=0.002)
    assert four[0] < one[1], "these intervals must overlap or the finding is wrong"


def test_the_interval_is_tight_where_the_counts_are_large() -> None:
    low, high = wilson(91_000, 150_000)

    assert high - low < 0.01


def test_wilson_stays_inside_zero_and_one() -> None:
    """The normal approximation does not, which is why this one is used."""

    assert wilson(0, 10)[0] == 0.0
    assert wilson(10, 10)[1] == 1.0
    with pytest.raises(ValueError, match="not a rate"):
        wilson(11, 10)
    with pytest.raises(ValueError, match="at least one trial"):
        wilson(0, 0)


def test_qc_rates_separate_models_and_final_counts_do_not() -> None:
    """The central result: feedback belongs where n is large."""

    models = [
        _yield("diffsbdd", (91_000, 420, 41, 4)),
        _yield("pocket2mol", (128_000, 510, 49, 3)),
        _yield("targetdiff", (88_000, 400, 38, 2)),
    ]

    report = audit(models, STAGES)

    assert all(c.decidable for c in report.comparisons("qc_passed"))
    assert not any(c.decidable for c in report.comparisons("final"))
    assert report.deepest_decidable_stage() in ("qc_passed", "docking_top")


def test_an_undecidable_comparison_names_no_winner() -> None:
    left, right = _yield("a", (100_000, 450, 42, 4)), _yield("b", (100_000, 450, 42, 1))

    comparison = compare(left, right, "final")

    assert comparison.decidable is False
    assert comparison.better is None
    assert "reallocating on noise" in str(comparison.as_dict()["note"])


def test_a_decidable_comparison_names_the_better_one() -> None:
    left, right = _yield("a", (128_000, 450, 42, 4)), _yield("b", (88_000, 450, 42, 4))

    comparison = compare(left, right, "qc_passed")

    assert comparison.decidable is True
    assert comparison.better == "a"


def test_a_generator_rediscovering_the_panel_is_flagged() -> None:
    """Its survival rate is high for the wrong reason."""

    models = [
        _yield("novel", (91_000, 420, 41, 4), known=20_000),
        _yield("rediscoverer", (128_000, 510, 49, 3), known=96_000),
    ]

    report = audit(models, STAGES)

    assert any("share a scaffold with the training panel" in note for note in report.notes)
    assert report.yields[1].novel_fraction == pytest.approx(0.36)


def test_a_small_final_count_is_called_out() -> None:
    models = [_yield("a", (91_000, 420, 41, 4)), _yield("b", (88_000, 400, 38, 0))]

    report = audit(models, STAGES)

    assert any("reallocated on noise" in note for note in report.notes)


def test_a_missing_count_is_refused_rather_than_read_as_zero() -> None:
    """Filling a gap with zero reports a model as having failed where it was not counted."""

    complete = _yield("a", (91_000, 420, 41, 4))
    partial = GeneratorYield("b", 150_000, {"qc_passed": 88_000})

    with pytest.raises(KeyError, match="no counts at some stages"):
        audit([complete, partial], STAGES)


def test_one_generator_is_not_an_audit() -> None:
    with pytest.raises(ValueError, match="nothing to compare"):
        audit([_yield("a", (91_000, 420, 41, 4))], STAGES)


def test_a_funnel_that_decides_nothing_says_so() -> None:
    """The honest conclusion is that the campaign has not learned which model to favour."""

    models = [_yield("a", (100_000, 450, 42, 3)), _yield("b", (100_050, 451, 42, 3))]

    report = audit(models, STAGES)

    assert report.deepest_decidable_stage() is None
    assert "has not learned which model to favour" in str(report.as_dict()["how_to_use_this"])
