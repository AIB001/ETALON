"""Optional mechanics for host-authored methods; no scoring policy or research loop.

The host chooses inputs, features, folds, model, acquisition and protocol. These
helpers remove input-shape and accounting mistakes without selecting a method.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from numbers import Integral


def observation_records(observations):
    """Copy row records from an iterable or a dataframe without importing pandas.

    A dataframe's iterator yields column names, not observations. Protocol,
    failure and attempt fields stay intact; this does not aggregate or dedupe.
    """
    if observations is None:
        return []
    if hasattr(observations, "columns") and callable(getattr(observations, "to_dict", None)):
        observations = observations.to_dict(orient="records")
    if isinstance(observations, (str, bytes, Mapping)):
        raise ValueError("observations must be row records or a dataframe")
    rows = list(observations)
    if any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("each observation must be a row object")
    return deepcopy([dict(row) for row in rows])


def bounded_acquisition(candidate_ids, *, remaining, protocol, batch_size=32, available=None):
    """Keep the author's order and protocol, within unique candidate/budget bounds.

    No new candidate is chosen. `available`, when supplied, is the author's
    eligible set; omitting it imposes no framework chemistry or quality filter.
    """
    if any(isinstance(v, bool) or not isinstance(v, Integral) for v in (remaining, batch_size)):
        raise ValueError("remaining and batch_size must be integers")
    if remaining < 0 or batch_size <= 0:
        raise ValueError("remaining must be nonnegative and batch_size positive")
    if not isinstance(protocol, str) or not protocol:
        raise ValueError("protocol must be an explicit nonempty name")
    cap = min(int(remaining), int(batch_size))
    if cap == 0:
        return {"candidate_ids": [], "protocol": protocol}
    if isinstance(candidate_ids, (str, bytes)):
        raise ValueError("candidate_ids must be an ordered iterable of IDs")
    allowed = None if available is None else set(available)
    seen, selected = set(), []
    for candidate in candidate_ids:
        if not isinstance(candidate, str) or not candidate:
            raise ValueError("candidate IDs must be nonempty strings")
        if candidate in seen or (allowed is not None and candidate not in allowed):
            continue
        seen.add(candidate)
        selected.append(candidate)
        if len(selected) == cap:
            break
    return {"candidate_ids": selected, "protocol": protocol}


def crossfit_predictions(observations, *, target, features, folds, fit_predict, group_key=None):
    """Run a caller-selected prediction pipeline with held-out labels withheld.

    `fit_predict(training_rows, prediction_features)` must fit ALL label-dependent
    preprocessing/model selection/calibration from training_rows. It returns one
    prediction per feature row. Folds and scientific interpretation are chosen
    by the host. This cannot detect hidden data or prefit state in a closure.
    """
    rows = observation_records(observations)
    names, assignments = tuple(features), list(folds)
    if not isinstance(target, str) or not target or not names or target in names:
        raise ValueError("declare a target and separate prediction feature names")
    if len(set(names)) != len(names) or any(not isinstance(name, str) for name in names):
        raise ValueError("feature names must be unique strings")
    if len(assignments) != len(rows):
        raise ValueError("one fold assignment is required for each observation")
    for fold in assignments:
        try:
            hash(fold)
            present = fold is not None and bool(fold == fold)
        except (TypeError, ValueError) as exc:
            raise ValueError("fold assignments must be nonmissing, comparable identifiers") from exc
        if not present:
            raise ValueError("fold assignments must not be missing or NaN")
    unique_folds = list(dict.fromkeys(assignments))
    if len(unique_folds) < 2:
        raise ValueError("cross-fitting requires at least two nonempty folds")
    groups = {}
    for row, fold in zip(rows, assignments, strict=True):
        if target not in row or any(name not in row for name in names):
            raise ValueError("observation is missing the target or a declared feature")
        if group_key is not None:
            if group_key not in row:
                raise ValueError("observation is missing its group")
            group = row[group_key]
            if group in groups and groups[group] != fold:
                raise ValueError("a group spans training and validation folds")
            groups[group] = fold
    predictions = [None] * len(rows)
    for fold in unique_folds:
        indexes = [i for i, assignment in enumerate(assignments) if assignment == fold]
        train = deepcopy([row for row, assignment in zip(rows, assignments, strict=True) if assignment != fold])
        inputs = deepcopy([{name: rows[i][name] for name in names} for i in indexes])
        values = list(fit_predict(train, inputs))
        if len(values) != len(indexes):
            raise ValueError("predictor must return one value per held-out row")
        for i, value in zip(indexes, values, strict=True):
            predictions[i] = value
    return predictions
