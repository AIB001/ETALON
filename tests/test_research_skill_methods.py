"""Behavior checks for portable synthetic research methods; no native science."""
from copy import deepcopy
from pathlib import Path
import json
import re
import runpy

import pytest

from etalon.research.fixed_pool import subset_rank_outcome
from etalon.research.transfer import checked_rows, evaluate_policies

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "etalon-research"
EXAMPLE = runpy.run_path(str(SKILL / "scripts" / "research_methods.py"))


def test_transfer_calibration_is_new_version_and_no_test_release_is_invented():
    result = EXAMPLE["transfer_example"]()
    assert result["training_policy_sha256"] != result["calibrated_policy_sha256"]
    assert result["executed"] is False and result["test_labels_read"] is False
    assert result["independent_gain"] is None
    policy, rows = EXAMPLE["train_fixture"]()
    with pytest.raises(ValueError, match="independent release"):
        evaluate_policies(policy, rows[6:], role="test")


def test_visible_composition_pays_terminal_resources_once_and_order_is_visible():
    result = EXAMPLE["visible_example"]()
    prediction = result["prediction"]
    assert prediction["decision"] == "fixed_pool_vinardo"
    assert prediction["actual_path_cost"] == pytest.approx(.3)
    assert prediction["labels_accessed"] is False
    acquisitions = [e["resource_id"] for e in result["events"] if e["kind"] == "fixture_acquire"]
    assert acquisitions == ["r0", "r1", "r2"]
    events = result["events"]
    assert events.index({"kind": "visible_receipt", "probe_id": "q1"}) < next(
        i for i, e in enumerate(events) if e.get("resource_id") == "r2")
    assert result["native_calls"] == 0 and result["evidence_admitted"] is False
    assert result["measured_scientific_cost"] is None


def test_missing_paid_score_stops_without_other_chunk_or_imputed_loss():
    result = EXAMPLE["visible_example"](missing=True)
    prediction = result["prediction"]
    assert prediction["decision"] == "abstain"
    assert prediction["controller_status"] == "unsupported_history"
    assert set(prediction["resources"]) == {"r0", "r1"}
    assert prediction["actual_path_cost"] == pytest.approx(.2)
    assert all(v is None for v in prediction["candidate_scores"].values())
    assert prediction["query_history"][0]["outcome"] == "failed"


def test_partial_subset_use_needs_no_policy_and_rejects_unpaid_information():
    result = subset_rank_outcome(candidate_ids=("a", "b"),
                                vina={"a": -2., "b": -1.}, vinardo={"a": -2., "b": None})
    assert result["outcome"] == "failed" and result["missing_candidates"] == ["b"]
    with pytest.raises(ValueError, match="exact paid subset"):
        subset_rank_outcome(candidate_ids=("a", "b"), vina={"a": -2., "b": -1.},
                            vinardo={"a": -2., "b": -1., "unpaid": -5.})
    split, catalog, rows = EXAMPLE["synthetic_inputs"]()
    bad = deepcopy(rows[:4]); bad[0]["resource_costs"].pop("r2")
    with pytest.raises(ValueError, match="unknown is not zero"):
        checked_rows(bad, split, "train", catalog)


def test_documented_pose_and_governance_snippets_execute():
    references = [SKILL / "references" / "pose-preparation.md",
                  ROOT / ".claude" / "skills" / "etalon-campaign" / "references" / "governance-and-fep.md"]
    for path in references:
        snippet = re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.S)[0]
        exec(compile(snippet, str(path), "exec"), {})


def test_portable_script_only_writes_requested_new_output(tmp_path):
    output = tmp_path / "report.json"
    report = EXAMPLE["main"](["--output", str(output)])
    # JSON converts immutable tuple sequences returned by the SDK to arrays.
    assert json.loads(output.read_text()) == json.loads(json.dumps(report))
    assert report["unknown_loss"]["value"] is None and report["scientific_gain"] is None
    assert report["pose"]["attempted"] == 3 and report["pose"]["admitted"] == 2
    with pytest.raises(FileExistsError):
        EXAMPLE["main"](["--output", str(output)])
    assert list(tmp_path.iterdir()) == [output]
