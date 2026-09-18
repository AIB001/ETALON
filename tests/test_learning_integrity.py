"""Legacy numerical assessments cannot turn malformed data into accepted changes."""

from __future__ import annotations

import json

import pytest

from etalon.learn.acquire import acquire
from etalon.learn.calibrate import (
    Score,
    Split,
    Verdict,
    auc,
    decide,
    hanley_mcneil_se,
    scaffold_groups,
)
from etalon.learn.conformal import Calibration, Interval, _quantile_index, calibrate, intervals
from etalon.learn.surrogate import Features, Surrogate, pic50


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "0.7", -0.1, 1.1])
def test_auc_summary_requires_a_real_probability(value):
    with pytest.raises(ValueError):
        Score(value, 0.01, 20, 20)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, True, "0.1"])
def test_auc_summary_requires_finite_nonnegative_uncertainty(value):
    with pytest.raises(ValueError):
        Score(0.7, value, 20, 20)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "10"])
def test_auc_summary_requires_actual_class_counts(value):
    with pytest.raises(ValueError):
        Score(0.7, 0.01, value, 20)
    with pytest.raises(ValueError):
        Score(0.7, 0.01, 20, value)


@pytest.mark.parametrize("value", ["scaffold", "whole_panel", None])
def test_split_claim_must_use_the_declared_enum(value):
    with pytest.raises(ValueError):
        Score(0.7, 0.01, 20, 20, value)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, "1.0"])
def test_auc_refuses_invalid_scores_instead_of_counting_them_as_losses(bad):
    with pytest.raises(ValueError):
        auc([bad, 0.0], [1, 0])


@pytest.mark.parametrize("bad", [-1, 2, None, "1", float("nan"), 0.5])
def test_auc_refuses_nonbinary_labels_instead_of_using_truthiness(bad):
    with pytest.raises(ValueError):
        auc([1.0, 0.0], [bad, 0])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_se_refuses_invalid_auc(value):
    with pytest.raises(ValueError):
        hanley_mcneil_se(value, 20, 20)


@pytest.mark.parametrize("bad", [True, 0, -1, 1.5])
def test_decision_requires_a_positive_integer_power_floor(bad):
    with pytest.raises(ValueError):
        decide(Score(0.6, 0.02, 20, 20), Score(0.9, 0.01, 20, 20), minimum_positives=bad)


def test_identical_perfect_scores_are_no_improvement_not_a_worsening():
    perfect = Score(1.0, 0.0, 20, 20)
    result = decide(perfect, perfect)
    assert result.verdict is Verdict.WITHIN_NOISE and not result.accepted
    assert result.delta == result.bound == 0


@pytest.mark.parametrize("after", [Score(0.9, 0.01, 19, 20), Score(0.9, 0.01, 20, 21),
                                   Score(0.9, 0.01, 20, 20, Split.SCAFFOLD_GROUPED)])
def test_different_panel_counts_or_split_cannot_claim_a_paired_change(after):
    with pytest.raises(ValueError, match="(same|match)"):
        decide(Score(0.6, 0.02, 20, 20), after)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, False, "1.0", -1.0, 0.0])
def test_log_concentration_requires_a_real_positive_finite_measurement(bad):
    with pytest.raises(ValueError):
        pic50(bad)


def test_binary_boolean_labels_are_valid_and_ties_get_half_credit():
    assert auc([0.5, 0.5], [True, False]) == 0.5


def test_acyclic_identity_does_not_depend_on_row_position_or_smiles_spelling():
    ethanol, propane = scaffold_groups(["CCO", "CCC"])
    propane_again, ethanol_again = scaffold_groups(["CCC", "OCC"])
    assert ethanol == ethanol_again
    assert propane == propane_again
    assert ethanol != propane
    assert scaffold_groups(["CCO", "OCC"]) == [ethanol, ethanol]


@pytest.mark.parametrize("fields", [
    {"mean": float("nan")}, {"spread": -0.1}, {"lower": 2.0}, {"upper": -2.0},
    {"mean": True}, {"lower": -float("inf")}, {"upper": float("inf")},
    {"parent_id": ""}, {"parent_id": 1},
])
def test_intervals_require_real_finite_ordered_predictions(fields):
    with pytest.raises(ValueError):
        Interval(**{"parent_id": "a", "mean": 0.0, "spread": 0.1, "lower": -1.0, "upper": 1.0, **fields})


@pytest.mark.parametrize("fields", [
    {"alpha": True}, {"alpha": 0}, {"q": -1}, {"q": float("inf")},
    {"calibration_count": 0}, {"groups_measured": True}, {"marginal_coverage": 1.1},
    {"worst_group_coverage": float("nan")}, {"median_half_width": -0.1},
])
def test_calibration_summary_cannot_certify_invalid_intervals(fields):
    with pytest.raises(ValueError):
        Calibration(**{"alpha": 0.1, "q": 1.0, "calibration_count": 10, "marginal_coverage": 0.9,
                       "worst_group_coverage": 0.8, "worst_group": "g", "groups_measured": 1,
                       "median_half_width": 1.0, **fields})


def test_interval_overflow_and_duplicate_identity_do_not_enter_selection():
    calibration = Calibration(0.1, 1.0, 10, 0.9, 0.8, "g", 1, 1.0)
    with pytest.raises(ValueError, match="unique"):
        intervals(["a", "a"], [0.0, 0.0], [0.1, 0.1], calibration)
    with pytest.raises(ValueError, match="finite"):
        intervals(["a"], [1e308], [1e308], calibration)
    large = Interval("a", 0.0, 0.1, -1e308, 1e308)
    assert large.half_width == 1e308  # Intermediate subtraction must not overflow.
    with pytest.raises(ValueError, match="distinct"):
        acquire([large, large], budget=2)


@pytest.mark.parametrize("controls", [
    {"budget": True}, {"budget": 1.5}, {"kappa": float("inf")}, {"kappa": -1},
    {"novelty_bonus": float("nan")}, {"explore_fraction": True}, {"explore_fraction": 2},
])
def test_acquisition_validates_controls_even_for_an_empty_pool(controls):
    with pytest.raises(ValueError):
        acquire([], **{"budget": 1, **controls})


def test_unattainable_residual_rank_is_explicit_not_a_coverage_guarantee():
    calibration = Calibration(0.01, 1.0, 5, 1.0, 1.0, "g", 1, 1.0)
    assert calibration.as_dict()["quantile_rank_clipped"] is True
    assert "NOT independent" in calibration.as_dict()["guarantee"]
    with pytest.raises(ValueError):
        _quantile_index(0, 0.1)


def test_surrogate_refuses_same_column_names_but_different_representations():
    import numpy as np

    matrix = np.asarray([[0.0], [1.0], [2.0], [3.0]])
    features = Features(matrix, ("f00000",), representation_json='{"feature":"A"}')
    model = Surrogate(trees=3).fit(features, [0.0, 1.0, 2.0, 3.0])
    assert model.representation_json == features.representation_json
    with pytest.raises(ValueError, match="representation differs"):
        model.predict(Features(matrix, features.names, representation_json='{"feature":"B"}'))
    with pytest.raises(ValueError, match="representation differs"):
        model.predict(Features(matrix, features.names))


@pytest.mark.parametrize("controls", [{"folds": True}, {"folds": 1}, {"min_group": 0},
                                     {"min_group": 1.5}, {"alpha": True}])
def test_calibration_controls_fail_before_model_fitting(controls, monkeypatch):
    import numpy as np

    def forbidden_fit(*_args, **_kwargs):
        pytest.fail("invalid calibration must be refused before fitting")

    monkeypatch.setattr(Surrogate, "fit", forbidden_fit)
    with pytest.raises(ValueError):
        calibrate(Features(np.asarray([[0.0], [1.0]]), ("f00000",)), [0.0, 1.0], ["a", "b"], **controls)


@pytest.mark.parametrize("dtype_name", ["float16", "float32", "float64"])
@pytest.mark.parametrize("report_kind", ["score", "decision", "calibration", "batch"])
def test_valid_numpy_scalars_produce_native_json_reports(dtype_name, report_kind):
    import numpy as np

    scalar = getattr(np, dtype_name)
    before = Score(scalar(.6), scalar(.02), np.int64(20), np.int64(30))
    if report_kind == "score":
        report = before.as_dict()
    elif report_kind == "decision":
        report = decide(before, Score(scalar(.8), scalar(.02), np.int64(20), np.int64(30))).as_dict()
    elif report_kind == "calibration":
        report = Calibration(scalar(.1), scalar(.8), 10, scalar(.9), scalar(.8),
                             "group", 1, scalar(.4)).as_dict()
    else:
        interval = Interval("m", scalar(3), scalar(.5), scalar(2), scalar(4))
        report = acquire([interval], budget=1).as_dict()
    encoded = json.dumps(report, allow_nan=False)
    assert json.loads(encoded) == report


def test_numpy_interval_overlap_returns_a_native_boolean():
    import numpy as np

    interval = Interval("m", np.float32(3), np.float32(.5), np.float32(2), np.float32(4))
    assert interval.overlaps(interval) is True


def test_score_export_cannot_emit_infinite_derived_resolution():
    with pytest.raises(ValueError, match="finite"):
        Score(.5, 1e308, 20, 30).as_dict()
