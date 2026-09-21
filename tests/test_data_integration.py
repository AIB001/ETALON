"""Real pinned SDK/chemistry integration with fixture HTTP and no live services."""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import httpx
import pytest

from etalon.active import (
    ActiveCampaign,
    CampaignSpec,
    CampaignStore,
    Candidate,
    Endpoint,
    Evaluation,
)
from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.active.store import StateError
from etalon.boundary.quarry import DataBudget, Quarry
from etalon.data.artifacts import new_run, read_snapshot, seal, write_json
from etalon.data.ingress import import_assays, review_template
from etalon.data.library import import_candidates, prepare_library
from etalon.data.service import plan_data, run_data


def no_http(request):
    raise AssertionError(f"offline test tried HTTP: {request.url}")


@pytest.fixture
def library(tmp_path):
    path = tmp_path / "fixture.csv"
    path.write_text("id,smiles\nethanol,CCO\nethanol-again,OCC\nacetate,CC(=O)[O-].[Na+]\nbad,not-a-smiles\n")
    acquired = run_data({"kind": "import_catalog", "source": "chembl", "path": str(path),
                        "options": {"source_version": "OFFLINE-FIXTURE-NOT-CHEMBL-DATA"},
                        "search": {"limit": 100}}, tmp_path, run_id="catalog",
                        budget=DataBudget(max_requests=0), transport=httpx.MockTransport(no_http))
    prepared = prepare_library(Path(acquired["snapshot"]), tmp_path, run_id="library",
                               id_field="fields.id", smiles_field="fields.smiles")
    return Path(prepared["snapshot"]), acquired, prepared


def test_local_catalog_to_molcascade_identity_keeps_source_rows(library):
    root, acquired, prepared = library
    assert acquired["usage"]["requests"] == 0
    assert acquired["result"]["status"] == "complete"
    assert prepared["result"]["candidates"] == 2
    assert prepared["result"]["rejected_records"] == 1
    mappings = json.loads((root / "identity-map.json").read_text())
    assert len(mappings) == 4
    assert mappings[0]["parent_id"] == mappings[1]["parent_id"]
    assert mappings[2]["chemical_state_changed"] is True
    with (root / "library.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2 and all(row["id"] for row in rows)
    read_snapshot(root)


def test_candidate_import_is_idempotent_and_preserves_multiple_library_origins(tmp_path, library):
    root, _, prepared = library
    store = CampaignStore(tmp_path / "campaign.sqlite")
    endpoint = Endpoint("mw", "T", "mw", "Da", "fixture", 1, requires_handoff=False)
    store.configure(CampaignSpec("mw", 3, "quotes", MOLECULAR_REPRESENTATION), [endpoint])
    assert import_candidates(store, root)["added"] == 2
    assert import_candidates(store, root)["added"] == 0
    assert all(c.source == "snapshot:" + prepared["snapshot_id"] for c in store.candidates().values())
    again = prepare_library(Path(library[1]["snapshot"]), tmp_path, run_id="library-2",
                            id_field="fields.id", smiles_field="fields.smiles")
    assert import_candidates(store, Path(again["snapshot"]))["added"] == 0


def test_seal_rejects_changed_files_and_manifest(library):
    root = library[0]
    path = root / "library.csv"
    old = path.read_bytes()
    path.write_bytes(old + b"injected,CCN\n")
    with pytest.raises(ValueError, match="file changed"):
        read_snapshot(root)
    path.write_bytes(old)
    manifest = json.loads((root / "snapshot.json").read_text())
    manifest["result"]["candidates"] += 1
    (root / "snapshot.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="seal"):
        read_snapshot(root)


def test_plan_refuses_changed_input_before_creating_a_run(tmp_path):
    path = tmp_path / "catalog.csv"
    path.write_text("id,smiles\na,CCO\n")
    request = {"kind": "import_catalog", "source": "chembl", "path": str(path),
               "options": {"source_version": "fixture"}}
    plan = plan_data(request)
    path.write_text("id,smiles\na,CCN\n")
    with pytest.raises(ValueError, match="changed since planning"):
        run_data(request, tmp_path, run_id="changed", expected_plan_id=plan["plan_id"])
    assert not (tmp_path / "data/changed").exists()


@pytest.mark.parametrize("requests", [0, 1])
def test_http_allowance_counts_retries_and_never_exceeds_bound(tmp_path, requests):
    calls = []

    def upstream(request):
        calls.append(str(request.url))
        return httpx.Response(503, json={"error": "temporary"})

    request = {"kind": "query", "source": "chembl", "operation": "molecule",
               "parameters": {"chembl_id": "CHEMBL25"}}
    result = run_data(request, tmp_path, run_id="bounded", budget=DataBudget(max_requests=requests),
                       transport=httpx.MockTransport(upstream))
    assert len(calls) == requests == result["usage"]["requests"]
    assert result["usage"]["exhausted"] == "max_requests"
    assert result["result"]["status"] == "partial"
    read_snapshot(Path(result["snapshot"]))


def test_pagination_limit_is_visible_and_partial_library_requires_explicit_choice(tmp_path):
    def upstream(request):
        return httpx.Response(200, json={"molecules": [{"id": "m", "smiles": "CCO"}],
            "page_meta": {"total_count": 200, "offset": 0, "limit": 1, "next": "more"}})

    result = run_data({"kind": "query", "source": "chembl", "operation": "search_molecules",
                       "parameters": {"query": "fixture", "limit": 1}, "max_pages": 1},
                      tmp_path, run_id="page", transport=httpx.MockTransport(upstream))
    assert result["result"]["status"] == "partial"
    assert result["result"]["next_parameters"]["offset"] == 1
    assert result["usage"]["response_bytes"] > 0
    with pytest.raises(ValueError, match="incomplete"):
        prepare_library(Path(result["snapshot"]), tmp_path, run_id="refused",
                        id_field="id", smiles_field="smiles")


def test_byte_allowance_is_shared_across_responses(tmp_path):
    with Quarry(tmp_path, DataBudget(max_bytes=10), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"123456"))) as quarry:
        quarry.client.http.client.get("https://example.invalid/one")
        with pytest.raises(Exception, match="max_bytes"):
            quarry.client.http.client.get("https://example.invalid/two")
        assert quarry.usage()["requests"] == 2
        assert quarry.usage()["response_bytes"] == 12  # received overrun stays visible
        assert quarry.usage()["exhausted"] == "max_bytes"


@pytest.mark.parametrize("policy", ["cost_aware", "cost_only", "random", "greedy", "ucb", "mf_kg", "decision_aware"])
def test_historical_endpoint_trains_but_is_never_selected_or_confirmable(tmp_path, policy):
    store = CampaignStore(tmp_path / "historical.sqlite")
    historical = Endpoint("exp", "P12345", "Kd", "nM", "assay", 0, requires_handoff=False,
                          queryable=False)
    proxy = Endpoint("proxy", "P12345", "score", "au", "dock", 1, requires_handoff=False)
    store.configure(CampaignSpec("exp", 10, "quotes", "test", policy=policy), [historical, proxy])
    store.add_candidates([Candidate(str(i), "CCO", (float(i),)) for i in range(4)])
    store.import_evaluation(Evaluation("0", "exp", 3.0, "nM", 0), source_id="fixture")
    run = ActiveCampaign(store)
    model, choices, _ = run.plan()
    assert model.snapshot()["training_size"] == 1
    assert all(choice.endpoint_id == "proxy" for choice in choices)
    recommendation = run.recommend()
    assert recommendation["evidence_backed"]["candidate_id"] == "0"
    assert recommendation["provisional"]["confirmation_eligible"] is False
    round_id = store.start_round({})
    with pytest.raises(StateError, match="historical-only"):
        store.reserve(round_id, "1", "exp", {})


def test_historical_only_campaign_has_explicit_stop_reason(tmp_path):
    store = CampaignStore(tmp_path / "history.sqlite")
    endpoint = Endpoint("exp", "T", "Kd", "nM", "assay", 0, queryable=False, requires_handoff=False)
    store.configure(CampaignSpec("exp", 0, "quotes", "test"), [endpoint])
    store.add_candidates([Candidate("a", "CCO", (1.0,))])
    assert ActiveCampaign(store).plan()[2] == "no_executable_endpoints"


def assay_fixture(tmp_path):
    """Synthetic dossier bytes exercise accession/assay admission, not empirical activity."""
    source = new_run(tmp_path, "dossier", {"fixture": True})
    (source / "payload").mkdir()
    dossier = {"targets": [{"input": "T", "accession": "P12345", "taxon": 9606}],
               "chembl_details": {"molecule": [{"molecule_chembl_id": "CHEMBL1",
                   "molecule_structures": {"canonical_smiles": "CCO"}}], "document": []},
               "bindingdb": [], "activities": [{"input_target": "T", "record": {
                   "activity_id": 123, "molecule_chembl_id": "CHEMBL1", "standard_type": "Kd",
                   "standard_relation": "=", "standard_value": "25", "standard_units": "nM"}}]}
    write_json(source / "payload/dossier.json", dossier)
    seal(source, kind="collect", result={"status": "collected_requires_review"},
         infrastructure={"fixture": "not live database evidence"})
    prepared = prepare_library(source, tmp_path, run_id="assay-library")
    library = Path(prepared["snapshot"])
    store = CampaignStore(tmp_path / "assays.sqlite")
    endpoint = Endpoint("kd", "P12345", "pKd", "-log10(M)", "fixture-assay/1", 0,
                        queryable=False, requires_handoff=False, direction="maximize")
    store.configure(CampaignSpec("kd", 2, "quotes", MOLECULAR_REPRESENTATION), [endpoint])
    import_candidates(store, library)
    review = review_template(library, endpoint_id="kd", protocol=endpoint.protocol)
    review.update(reviewer="offline-test", reviewed_at="2026-09-21",
                  target={"accession": "P12345", "taxon": 9606, "construct": "fixture construct"})
    review["observations"][0].update(decision="accept", experiment_id="fixture-paper:assay:123",
                                      rationale="fixture review tests plumbing only")
    return store, library, review


def test_reviewed_exact_assay_conversion_and_idempotent_experiment_import(tmp_path):
    store, library, review = assay_fixture(tmp_path)
    assert import_assays(store, library, review)["added"] == 1
    assert import_assays(store, library, review)["added"] == 0
    observations = store.observations()
    assert len(observations) == 1 and observations[0]["admitted"]
    assert observations[0]["result"]["value"] == pytest.approx(7.6020599913)
    assert store.balance()["spent"] == 0
    assert store.actions()[0]["round_id"] == 0
    changed = copy.deepcopy(review)
    changed["target"]["construct"] = "different mutant"
    with pytest.raises(StateError, match="construct changed"):
        import_assays(store, library, changed)


@pytest.mark.parametrize("field,value", [("accession", "Q99999"), ("taxon", 10090)])
def test_target_mismatch_cannot_seed_a_label(tmp_path, field, value):
    store, library, review = assay_fixture(tmp_path)
    review["target"][field] = value
    with pytest.raises(ValueError):
        import_assays(store, library, review)
    assert store.observations() == []


def test_batch_assay_import_rolls_back_on_experiment_collision(tmp_path):
    store, _, _ = assay_fixture(tmp_path)
    candidate = next(iter(store.candidates()))
    first = Evaluation(candidate, "kd", 5.0, "-log10(M)", 0)
    changed = Evaluation(candidate, "kd", 6.0, "-log10(M)", 0)
    with pytest.raises(StateError, match="experiment id"):
        store.import_reviewed_evaluations([("one", first), ("one", changed)], review={"fixture": True})
    assert store.observations() == []
    assert store.actions() == []


def test_handoff_is_an_immutable_late_binding_and_updates_snapshot(tmp_path):
    store = CampaignStore(tmp_path / "geometry.sqlite")
    endpoint = Endpoint("md", "T", "energy", "kcal/mol", "p", 1)
    store.configure(CampaignSpec("md", 10, "quotes", "test"), [endpoint])
    candidate = Candidate("a", "CCO", (1.0,))
    store.add_candidates([candidate])
    record = {"parent_id": "a", "parent_smiles": "CCO", "molblock": "fixture", "status": "OK"}
    assert store.bind_handoffs([record], source={"fixture": True}, rationale="test resource binding") == 1
    assert store.snapshot()["candidates"]["a"].handoff == record
    assert store.add_candidates([candidate]) == 0  # base identity remains unchanged
    assert store.bind_handoffs([record], source={"fixture": True}, rationale="retry") == 0
    with pytest.raises(StateError, match="different geometry"):
        store.bind_handoffs([{**record, "molblock": "changed"}], source={"fixture": True}, rationale="bad")
    store.start_round({})
    with pytest.raises(StateError, match="pending"):
        store.bind_handoffs([record], source={"fixture": True}, rationale="while running")
