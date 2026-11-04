"""Optional mechanics preserve the author's decisions and withhold evaluation labels."""
import pytest

from etalon.research.method_tools import (
    bounded_acquisition, crossfit_predictions, observation_records,
)


def test_dataframe_like_input_is_read_as_rows_and_failures_survive():
    records = [{"candidate_id": "a", "protocol": "P1", "status": "FAILED", "detail": {"attempt": 2}}]

    class Frame:
        columns = ["candidate_id", "protocol", "status", "detail"]

        def __iter__(self):
            raise AssertionError("dataframe iteration loses protocol observations")

        def to_dict(self, *, orient):
            assert orient == "records"
            return records

    result = observation_records(Frame())
    assert result == observation_records(records)
    result[0]["detail"]["attempt"] = 3
    assert records[0]["detail"]["attempt"] == 2


def test_zero_budget_does_not_start_acquisition_and_sparse_pool_keeps_choice():
    def expensive_proposals():
        raise AssertionError("zero budget should not consume proposals")
        yield "unused"

    assert bounded_acquisition(expensive_proposals(), remaining=0, protocol="custom") == {
        "candidate_ids": [], "protocol": "custom"}
    result = bounded_acquisition(["b", "b", "excluded", "a", "c", "a"] * 8,
                                 remaining=32, protocol="author-chosen",
                                 available={"a", "b", "c"})
    assert result == {"candidate_ids": ["b", "a", "c"], "protocol": "author-chosen"}
    assert bounded_acquisition(["z", "y", "x"], remaining=2, protocol="P1")["candidate_ids"] == ["z", "y"]
    with pytest.raises(ValueError, match="nonnegative"):
        bounded_acquisition(["a"], remaining=-1, protocol="P0")


def test_validation_targets_cannot_change_own_fold_prediction_pipeline():
    rows = [{"x": i, "y": 2 * i + 1, "scaffold": i // 2} for i in range(6)]
    folds = [0, 0, 1, 1, 2, 2]
    seen = []

    def fitted_size_correction(train, test):
        # The entire target-dependent calibration is fitted on train here.
        intercept = sum(r["y"] - 2 * r["x"] for r in train) / len(train)
        assert all(set(r) == {"x"} for r in test)
        seen.append((len(train), len(test)))
        return [2 * r["x"] + intercept for r in test]

    original = crossfit_predictions(rows, target="y", features=["x"], folds=folds,
                                    group_key="scaffold", fit_predict=fitted_size_correction)
    changed = [dict(row) for row in rows]
    changed[0]["y"] = changed[1]["y"] = 10000
    counterfactual = crossfit_predictions(changed, target="y", features=["x"], folds=folds,
                                          group_key="scaffold", fit_predict=fitted_size_correction)
    assert original == [r["y"] for r in rows]
    assert counterfactual[:2] == original[:2]
    assert counterfactual[2:] != original[2:]
    assert seen == [(4, 2)] * 6
    with pytest.raises(ValueError, match="group spans"):
        crossfit_predictions(rows, target="y", features=["x"], folds=[0, 1, 1, 1, 2, 2],
                             group_key="scaffold", fit_predict=fitted_size_correction)


def test_training_mutation_does_not_leak_between_folds_or_back_to_source():
    rows = [{"x": i, "y": i, "meta": {"visited": False}} for i in range(4)]

    def mutate_local_training(train, test):
        assert not any(r["meta"]["visited"] for r in train)
        for row in train:
            row["meta"]["visited"] = True
        return [row["x"] for row in test]

    assert crossfit_predictions(rows, target="y", features=["x"], folds=[0, 0, 1, 1],
                                fit_predict=mutate_local_training) == [0, 1, 2, 3]
    assert not any(row["meta"]["visited"] for row in rows)
