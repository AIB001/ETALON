"""Software-contract fixtures only; vendor facts require fresh retained sources."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "source_qualified_retrosynthesis",
    Path(__file__).parents[1] / "examples/source_qualified_retrosynthesis.py",
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.fixture
def entry(tmp_path):
    key = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    body = tmp_path / "catalog.fixture"
    body.write_text("product FIXTURE-1 " + key, encoding="utf-8")
    return {"smiles": "CCO", "inchikey": key, "catalog_listing_supported": True,
            "source": {"kind": "manufacturer_catalog", "url": "https://example.test/FIXTURE-1",
                       "path": str(body), "sha256": hashlib.sha256(body.read_bytes()).hexdigest(),
                       "product_id": "FIXTURE-1", "identity_token": key}}


def test_overlay_preserves_scope_and_refuses_overwrite(tmp_path, entry):
    path = tmp_path / "overlay.txt"
    manifest = helper.write_catalog_stock([entry], path)
    assert path.read_text().strip() == entry["inchikey"]
    assert manifest["baseline_stock_modified"] is False
    assert manifest["reaction_viability_validated"] is False
    assert manifest["live_inventory_confirmed"] is False
    with pytest.raises(FileExistsError):
        helper.write_catalog_stock([entry], path)


@pytest.mark.parametrize("defect", ["unknown", "identity", "source_bytes"])
def test_unqualified_source_cannot_enter_stock(tmp_path, entry, defect):
    if defect == "unknown":
        entry["catalog_listing_supported"] = None
    elif defect == "identity":
        entry["smiles"] = "CO"
    else:
        Path(entry["source"]["path"]).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError):
        helper.write_catalog_stock([entry], tmp_path / "overlay.txt")
    assert not (tmp_path / "overlay.txt").exists()


def test_stock_closure_does_not_claim_reaction_success():
    result = helper.summarize_native_record({"is_solved": True, "search_time": 3.,
        "profiling": {"iterations": 9}, "trees": [{"type": "mol", "in_stock": True,
                                                   "smiles": "CCO"}]})
    assert result["stock_closed"] is True
    assert result["lab_synthesis_validated"] is False
    assert result["reaction_viability_unknown"] is True


@pytest.mark.parametrize("invalid", [None, "false", "true", 0, 1, [], {}])
def test_invalid_native_closure_is_not_coerced_into_measurement(invalid):
    with pytest.raises(ValueError, match="JSON boolean"):
        helper.summarize_native_record({"is_solved": invalid})
