"""Invalid offline inputs are rejected before a journal or imported action is written."""

from __future__ import annotations

import copy
import json

import pytest

from etalon.active.replay import from_manifest, synthetic_manifest
from etalon.mcp.active import register


@pytest.mark.parametrize("defect", [
    "boolean_schema", "duplicate_endpoint", "missing_objective", "mixed_targets", "mixed_widths",
    "oversized_pool", "boolean_replicate", "extra_replicate", "empty_source", "numeric_source", "nonfinite_metadata",
    "initial_unknown_qc", "oracle_unknown_qc", "kg_pool_limit",
])
def test_bad_replay_contract_never_creates_a_database(tmp_path, defect):
    manifest = synthetic_manifest(size=8)
    if defect == "boolean_schema":
        manifest["schema_version"] = True
    elif defect == "duplicate_endpoint":
        manifest["endpoints"].append(copy.deepcopy(manifest["endpoints"][0]))
    elif defect == "missing_objective":
        manifest["spec"]["objective"] = "absent"
    elif defect == "mixed_targets":
        manifest["endpoints"][0]["target"] = "another-target"
    elif defect == "mixed_widths":
        manifest["candidates"][0]["features"] = [1.0]
    elif defect == "oversized_pool":
        manifest["spec"]["max_candidates"] = 7
    elif defect == "boolean_replicate":
        manifest["oracle"][0]["replicate"] = False
    elif defect == "extra_replicate":
        manifest["oracle"].append({**manifest["oracle"][0], "replicate": 1})
    elif defect == "empty_source":
        manifest["initial"][0]["source_id"] = "  "
    elif defect == "numeric_source":
        manifest["initial"][0]["source_id"] = 123
    elif defect in {"initial_unknown_qc", "oracle_unknown_qc"}:
        row = (manifest["initial"][1]["result"] if defect == "initial_unknown_qc" else manifest["oracle"][-1])
        row["checks"] = [{"code": "UNKNOWN_QC_CODE", "fired": True, "detail": "not in taxonomy"}]
    elif defect == "kg_pool_limit":
        manifest["spec"].update({"policy": "mf_kg", "max_kg_candidates": 7})
    else:
        manifest["metadata"] = {"unsupported": float("nan")}
    path = tmp_path / "not-created" / "journal.sqlite"
    with pytest.raises(ValueError):
        from_manifest(manifest, path)
    assert not path.exists() and not path.parent.exists()


@pytest.mark.parametrize("rounds", [True, False, 0, -1, 1.5, 101, "2"])
def test_mcp_rejects_invalid_rounds_before_loading_or_bootstrapping(tmp_path, rounds):
    class Collector:
        def __init__(self):
            self.functions = {}

        def tool(self):
            def record(function):
                self.functions[function.__name__] = function
                return function
            return record

    collector = Collector()
    register(collector)
    manifest = tmp_path / "intentionally-missing.json"
    database = tmp_path / "journal.sqlite"
    result = json.loads(collector.functions["etalon_active_replay"](str(manifest), str(database), rounds))
    assert result["ok"] is False
    assert result["error"]["code"] == "ValueError"
    assert "rounds" in result["error"]["message"]
    assert not database.exists()
