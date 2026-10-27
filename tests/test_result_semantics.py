"""Explicit synthetic profiles: not scientific results or performance evidence."""
from copy import deepcopy

import pytest

from etalon.research.result_semantics import (
    bind_scientific_result, build_result_profile, compare_result_context,
    profile_definitions, summarize_result_binding, validate_result_binding,
    validate_result_profile,
)


@pytest.fixture(params=["coordinate_diagnostic", "screening", "evidence_query"])
def profile(request):
    definitions = {
        "coordinate_diagnostic": ("Unweighted all-atom RMSD to reference, including H", "angstrom", {"atoms": [0, 1, 2, 3]}),
        "screening": ("Fraction of eligible compounds with measured score below threshold", "fraction", {"threshold": -7, "cohort": "fixture-only"}),
        "evidence_query": ("Number of explicitly supplied records matching query predicates", "record_count", {"query": "fixture binding", "sources": ["synthetic-corpus"]}),
    }
    definition, unit, context = definitions[request.param]
    payload = build_result_profile(profile_id=request.param,
        method={"implementation": "fixture-only", "name": request.param},
        metrics={"score": {"definition": definition, "unit": unit}},
        conditions={"task": "Actual domain-specific task conditions"},
        extensions={"fixture": True})
    return payload, context


def binding(profile, conditions=None):
    return bind_scientific_result(profile, subjects=[{"id": "fixture-subject"}],
        operation={"performed": "fixture-only"},
        conditions={"task": {"fixture": True}} if conditions is None else conditions,
        observations={"score": {"value": None, "status": "not_measured"}},
        assessment={"status": "unknown"},
        host_interpretation={"proposal": "retain current method"})


def test_three_domains_share_profile_binding_and_keep_unknowns(profile):
    definition, task = profile
    result = binding(definition, {"task": task})
    assert "method" not in result  # definitions published once, not copied per run
    assert validate_result_binding(result, definition) == result
    summary = summarize_result_binding(result, definition)
    assert summary["metric_definitions"]["score"] == definition["metrics"]["score"]
    assert summary["conditions"]["task"] == task
    assert summary["observations"]["score"]["value"] is None
    assert summary["assessment"] == {"status": "unknown"}
    assert summary["host_interpretation"] == {"proposal": "retain current method"}
    assert summary["profile_extensions"] == {"fixture": True}
    assert summarize_result_binding(result)["definition_status"] == "unresolved"


def test_metric_name_never_substitutes_for_definition(profile):
    original, _ = profile
    changed = build_result_profile(profile_id=original["profile_id"], method=original["method"],
        metrics={"score": {"definition": "A different score with the same short name", "unit": "count"}},
        conditions=original["conditions"])
    comparison = compare_result_context(binding(original), binding(changed))
    assert not comparison["same_definitions"]
    assert "metric:score" in comparison["definition_differences"]
    with pytest.raises(ValueError, match="profile binding"):
        validate_result_binding(binding(original), changed)


def test_method_changes_can_keep_the_same_external_measure(profile):
    original, _ = profile
    changed = build_result_profile(profile_id="changed-method",
        method={"implementation": "different fixture-only algorithm"},
        metrics=original["metrics"], conditions=original["conditions"])
    assert original["profile_digest"] != changed["profile_digest"]
    assert profile_definitions(original) == profile_definitions(changed)
    result = compare_result_context(binding(original), binding(changed))
    assert result["same_definitions"]
    assert result["scientific_comparability"] == "not_assessed"


def test_actual_condition_changes_and_unknowns_are_not_an_exact_context_match(profile):
    original, _ = profile
    current = binding(original, {"task": {"selection": "heavy-only"}})
    changed = binding(original, {"task": {"selection": "all-atoms"}})
    assert compare_result_context(current, changed)["differences"] == ["conditions"]
    unknown = binding(original, {"task": None})
    comparison = compare_result_context(unknown, unknown)
    assert comparison["unknown_context"]
    assert not comparison["same_declared_context"]


def test_profile_and_bound_definition_tampering_is_detected(profile):
    original, _ = profile
    damaged = deepcopy(original)
    damaged["metrics"]["score"]["definition"] = "edited after binding"
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_result_profile(damaged)
    damaged = binding(original)
    damaged["definitions"]["metric:score"] = "f" * 64
    with pytest.raises(ValueError, match="definition binding"):
        validate_result_binding(damaged, original)


def test_optional_observations_remain_absent_and_input_is_not_mutated(profile):
    original, _ = profile
    result = binding(original)
    result["observations"] = {}
    checked = validate_result_binding(result, original)
    assert checked["observations"] == {}
    checked["subjects"][0]["id"] = "other"
    assert result["subjects"][0]["id"] == "fixture-subject"


def test_nonfinite_values_unknown_metrics_and_missing_conditions_are_rejected(profile):
    original, _ = profile
    result = binding(original)
    result["observations"]["score"] = float("nan")
    with pytest.raises(ValueError, match="finite JSON"):
        validate_result_binding(result, original)
    result = binding(original)
    result["observations"]["unscoped"] = 1
    with pytest.raises(ValueError, match="metric definition"):
        validate_result_binding(result, original)
    with pytest.raises(ValueError, match="conditions missing"):
        binding(original, {})


def test_condition_definition_identity_is_independent_from_actual_values(profile):
    original, _ = profile
    changed = build_result_profile(profile_id=original["profile_id"], method=original["method"],
        metrics=original["metrics"], conditions={"task": "Different selection definition"})
    assert profile_definitions(original)["metric:score"] == profile_definitions(changed)["metric:score"]
    assert profile_definitions(original)["condition:task"] != profile_definitions(changed)["condition:task"]


def test_unresolved_results_with_conflicting_definition_bindings_never_exact_match(profile):
    original, _ = profile
    left = binding(original)
    right = deepcopy(left)
    right["definitions"]["metric:score"] = "e" * 64
    compared = compare_result_context(left, right)
    assert not compared["same_declared_context"]
    assert compared["definition_differences"] == ["metric:score"]
