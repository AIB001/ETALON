"""Exercise QC contracts through public MCP and legitimate numerical producers."""

from __future__ import annotations

import json

import numpy as np
import pytest

from etalon.faults.attribution import Observation
from etalon.faults.stability import Trajectory, check_stability
from etalon.mcp.governance import register


class Collector:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def record(function):
            self.tools[function.__name__] = function
            return function
        return record


def _admission(check):
    collector = Collector()
    register(collector)
    row = {"parent_id": "m", "cheap_value": -1.0, "expensive_value": -2.0,
           "observations": [check]}
    return json.loads(collector.tools["etalon_rule_admissible"](json.dumps([row])))


@pytest.mark.parametrize("field", ["fired", "evaluable"])
@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_actual_mcp_admission_does_not_precoerce_external_quality_flags(field, value):
    check = {"code": "F_BUILD_INCOMPLETE", "fired": True, "evaluable": True, "detail": "failed topology"}
    check[field] = value
    result = _admission(check)
    assert result["ok"] is False
    assert result["error"]["code"] == "ValueError"
    assert "explicit booleans" in result["error"]["message"]
    assert "report" not in result  # No successful-looking admission rate from malformed QC.


@pytest.mark.parametrize("field,value", [("code", 123), ("code", None), ("code", []),
                                        ("detail", None), ("detail", 123), ("detail", {})])
def test_actual_mcp_admission_does_not_stringify_malformed_observation_fields(field, value):
    check = {"code": "F_BUILD_INCOMPLETE", "fired": True, "detail": "failed topology"}
    check[field] = value
    result = _admission(check)
    assert result["ok"] is False
    assert result["error"]["code"] == "ValueError"
    assert "observation" in result["error"]["message"]


def test_actual_mcp_preserves_blocking_versus_legitimately_unevaluable_facts():
    check = {"code": "F_BUILD_INCOMPLETE", "fired": True, "detail": "diagnostic"}
    blocked = _admission(check)  # The declared omitted-evaluable default remains true.
    unchecked = _admission({**check, "evaluable": False})
    assert blocked["ok"] is True and blocked["report"]["admitted"] == 0
    assert blocked["report"]["rulings"][0]["blocking"] == ["F_BUILD_INCOMPLETE"]
    assert unchecked["ok"] is True and unchecked["report"]["admitted"] == 1
    assert unchecked["report"]["rulings"][0]["unchecked"] == ["F_BUILD_INCOMPLETE"]


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("values", [(0.1, 0.2, 0.1), (0.1, 0.7, 0.1),
                                    (0.1, 0.2, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)])
@pytest.mark.parametrize("replicas", [1, 2])
def test_numpy_trajectory_comparisons_are_normalized_inside_the_producer(dtype, values, replicas):
    array = np.asarray(values, dtype=dtype)
    numeric = Trajectory(tuple(array), contacts={"ASP1": dtype(0.8)}, scored_contacts=("ASP1",),
                         replicas=np.int64(replicas))
    ordinary = Trajectory(tuple(float(value) for value in array), contacts={"ASP1": float(dtype(0.8))},
                          scored_contacts=("ASP1",), replicas=replicas)
    actual, expected = check_stability(numeric), check_stability(ordinary)
    assert type(numeric.settled()) is bool
    assert [(row.code, row.fired, row.evaluable) for row in actual] == [
        (row.code, row.fired, row.evaluable) for row in expected]
    assert all(type(row.fired) is bool and type(row.evaluable) is bool for row in actual)


def test_external_numpy_quality_flag_is_not_implicitly_coerced_by_the_schema():
    # Only internal calculated predicates are normalized. The public contract remains
    # exact bool, so Python callers must explicitly express a scientific QC decision.
    with pytest.raises(ValueError, match="explicit booleans"):
        Observation("F_BUILD_INCOMPLETE", np.bool_(True), "external flag")
