"""Quality flags are explicit observations, never truthy/falsy input coercions."""

from __future__ import annotations

import copy

import pytest

from etalon.active.replay import from_manifest, synthetic_manifest
from etalon.active.runner import ActiveCampaign
from etalon.active.schema import CampaignSpec, Candidate, Endpoint, Evaluation
from etalon.active.store import CampaignStore
from etalon.faults.attribution import Observation
from etalon.learn.admissible import Measurement, rule


@pytest.mark.parametrize("field", ["fired", "evaluable"])
@pytest.mark.parametrize("value", [0, 1, "false", "true", None, [], {}])
def test_observation_flags_refuse_non_boolean_truthiness(field, value):
    arguments = {"code": "F_BUILD_INCOMPLETE", "fired": True, "detail": "test", "evaluable": True}
    arguments[field] = value
    with pytest.raises(ValueError, match="explicit booleans"):
        Observation(**arguments)


@pytest.mark.parametrize("code", [None, "", "   ", 123, []])
def test_observation_code_requires_an_actual_nonempty_string(code):
    with pytest.raises(ValueError, match="nonempty string"):
        Observation(code, False, "test")


@pytest.mark.parametrize("detail", [None, 123, [], {}])
def test_observation_detail_requires_text(detail):
    with pytest.raises(ValueError, match="detail.*string"):
        Observation("F_BUILD_INCOMPLETE", False, detail)


@pytest.mark.parametrize("location", ["oracle", "initial"])
@pytest.mark.parametrize("field,value", [("fired", "false"), ("evaluable", 0),
                                        ("code", []), ("detail", None)])
def test_malformed_qc_in_any_replay_row_is_rejected_before_database_creation(tmp_path, location, field, value):
    manifest = synthetic_manifest(size=8)
    check = {"code": "F_BUILD_INCOMPLETE", "fired": True, "detail": "test", "evaluable": True}
    check[field] = value
    # Modify a late row to detect accidental valid-prefix imports.
    if location == "initial":
        result = manifest["initial"][-1]["result"]
    else:
        result = manifest["oracle"][-1]
    result["checks"] = [check]
    path = tmp_path / "uncreated" / "campaign.sqlite"
    with pytest.raises(ValueError):
        from_manifest(copy.deepcopy(manifest), path)
    assert not path.exists() and not path.parent.exists()


def test_valid_unevaluable_flag_remains_an_explicit_unchecked_fact():
    check = Observation("F_BUILD_INCOMPLETE", fired=True, detail="missing diagnostic", evaluable=False)
    ruling = rule(Measurement("m", None, 1.0, (check,)), require_comparator=False)
    assert ruling.unchecked == ("F_BUILD_INCOMPLETE",)
    assert ruling.blocking == ()
    assert ruling.teaches is True  # Existing admission semantics, not a fabricated QC pass.
    assert ruling.as_dict()["unchecked"] == ["F_BUILD_INCOMPLETE"]


def test_unknown_nonempty_code_is_still_rejected_by_the_taxonomy_not_constructor():
    check = Observation("UNKNOWN_QC_CODE", False, "unknown")
    with pytest.raises(KeyError, match="not in the taxonomy"):
        rule(Measurement("m", None, 1.0, (check,)), require_comparator=False)


def test_malformed_live_result_becomes_a_paid_failed_action_with_unknown_actual_cost(tmp_path):
    store = CampaignStore(tmp_path / "campaign.sqlite")
    endpoint = Endpoint("objective", "target", "score", "u", "test/1", 3, requires_handoff=False)
    store.configure(CampaignSpec(endpoint.id, 9, "quote-units", "vector/1", batch_size=1), [endpoint])
    store.add_candidates([Candidate("m", "CCO", (1.0,))])

    def malformed_result(action, candidate, task, grant):
        # A real executor attempts to construct its returned Evaluation after work.
        check = Observation("F_BUILD_INCOMPLETE", True, "raw diagnostic", evaluable=0)
        return Evaluation(candidate.id, task.id, 1.0, task.units, 1.0, checks=(check,))

    result = ActiveCampaign(store, malformed_result).run_round()
    assert len(result["actions"]) == 1
    row = store.observations()[0]
    assert row["admitted"] is False
    assert row["result"]["status"] == "failed"
    assert row["result"]["cost"] == 3
    assert row["result"]["provenance"]["cost_basis"] == "reservation; actual cost unavailable"
    assert "explicit booleans" in row["result"]["provenance"]["error"]
    assert store.balance()["spent"] == 3
