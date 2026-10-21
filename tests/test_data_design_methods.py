"""Behavior checks for the optional source example, using actual local SDKs."""
import importlib.util
from pathlib import Path

import pytest

from etalon.data.artifacts import status


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("data_design_methods", ROOT / "examples/data_design_methods.py")
example = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(example)


def test_local_composition_stops_when_catalog_has_no_assays(tmp_path):
    result = example.local_catalog_example(tmp_path / "local")
    assert result["acquisition"]["usage"]["requests"] == 0
    assert result["prepared"]["result"]["source_records"] == 3
    assert result["import"]["added"] == 2 and result["replay"]["added"] == 0
    assert result["assay"]["state"] == "not_applicable"
    assert result["observations"] == []
    assert result["scientific_balance"]["spent"] == 0
    assert status(Path(result["prepared"]["snapshot"]))["state"] == "sealed"
    with pytest.raises(FileExistsError):
        example.local_catalog_example(tmp_path / "local")


def test_design_example_uses_only_index_and_keeps_failed_slots(tmp_path):
    result = example.design_index_example(ROOT / "tests/fixtures/prism_design_bundle_v1/bundle.json",
                                           tmp_path / "design")
    assert result["import"]["indexed_slots"] == 4
    assert result["index"]["counts"]["pose"] == 2
    assert result["candidates"] == 0 and result["observations"] == []
    assert result["replay"]["imported"] is False
    assert result["verification"] == result["import"]
    rows = [r["candidate"] for k, r in result["index"]["records"].items()
            if k.startswith("occurrence:")]
    assert any(r["raw_status"] != "DECODED" for r in rows)


def test_independent_helpers_expose_stop_without_execution():
    result = example.numerical_advice_example()
    assert result["allocation"]["retire"]["spent-loop"]
    assert result["allocation"]["median_unique_per_hour"] is None
    assert result["scientific_execution"] == "not_run"
    assert result["separation"][0]["best_inactive"] < result["separation"][0]["best_active"]


def test_configuration_stops_at_caller_selected_plan(tmp_path):
    library = tmp_path / "library.csv"
    library.write_text("id,smiles\nfixture,CCO\n", encoding="utf-8")
    result = example.configuration_example(tmp_path / "configuration", library)
    assert result["execution"] == "not_run"
    assert result["plan"]["plan_id"]
    assert result["plan"]["request"]["library"] == str(library)
    assert not list(tmp_path.rglob("etalon-screen-jobs.sqlite"))
