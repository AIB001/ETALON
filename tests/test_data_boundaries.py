"""Admission, packaging and resource-budget failures that must stay explicit."""

from __future__ import annotations

import concurrent.futures
import json
import threading
from pathlib import Path

import httpx
import pytest
from test_data_integration import assay_fixture
from test_data_integration import library as _library_fixture

from etalon.__main__ import main
from etalon.active import CampaignSpec, CampaignStore, Candidate, Endpoint
from etalon.active.adapters import MOLECULAR_REPRESENTATION
from etalon.boundary.quarry import DataBudget, Quarry
from etalon.data.artifacts import new_run, read_snapshot, seal, status, write_json
from etalon.data.ingress import import_assays, review_template
from etalon.data.library import import_candidates, prepare_library
from etalon.data.service import plan_data, run_data

library = _library_fixture


def test_request_limit_preserves_already_authorized_concurrent_responses(tmp_path):
    received, release = threading.Event(), threading.Event()
    lock, calls = threading.Lock(), []

    def transport(request):
        with lock:
            calls.append(str(request.url))
            if len(calls) == 2:
                received.set()
        assert release.wait(5)
        return httpx.Response(200, json={"record": "retained"})

    with Quarry(tmp_path, DataBudget(max_requests=2), transport=httpx.MockTransport(transport)) as quarry:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(quarry.client.http.client.get, "https://example.invalid/data")
                       for _ in range(2)]
            try:
                assert received.wait(5)
                with pytest.raises(Exception, match="max_requests"):
                    quarry.client.http.client.get("https://example.invalid/third")
            finally:
                release.set()
            assert all(future.result().json()["record"] == "retained" for future in futures)
        assert quarry.usage()["requests"] == len(calls) == 2


def test_download_uses_provider_validation_and_records_the_original_bytes(tmp_path):
    body = b"fixture\n  RDKit\n\n  0  0  0  0  0  0            999 V2000\nM  END\n$$$$\n"
    result = run_data({"kind": "download", "source": "chembl", "operation": "sdf",
                       "parameters": {"chembl_id": "CHEMBL25"}}, tmp_path, run_id="download",
                      transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(body))))
    assert result["result"]["status"] == "downloaded"
    assert result["usage"]["requests"] == 1
    assert Path(result["result"]["download"]["path"]).read_bytes() == body
    read_snapshot(Path(result["snapshot"]))


def test_failed_download_keeps_actual_byte_overrun_and_is_never_a_sealed_snapshot(tmp_path):
    with pytest.raises(Exception, match="max_bytes"):
        run_data({"kind": "download", "source": "chembl", "operation": "sdf",
                  "parameters": {"chembl_id": "CHEMBL25"}}, tmp_path, run_id="too-large",
                 budget=DataBudget(max_bytes=10),
                 transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=httpx.ByteStream(b"x" * 20))))
    report = status(tmp_path / "data/too-large")
    assert report["state"] == "failed"
    assert report["failure"]["usage"]["response_bytes"] == 20
    assert not (tmp_path / "data/too-large/snapshot.json").exists()


def test_collect_budget_exhaustion_is_visible_even_if_upstream_returns_ok(tmp_path):
    result = run_data({"kind": "collect", "config": {"targets": ["P00533"], "max_pages": 1,
                       "max_details": 0}}, tmp_path, run_id="collect", budget=DataBudget(max_requests=0))
    assert result["result"]["status"] == "partial"
    assert result["result"]["upstream_status"] == "needs_target_resolution"
    assert result["usage"]["requests"] == 0
    read_snapshot(Path(result["snapshot"]))


def test_query_failure_after_a_page_keeps_the_successful_page_and_continuation(tmp_path):
    def transport(request):
        return httpx.Response(200, json={"molecules": [{"id": "one", "smiles": "CCO"}],
            "page_meta": {"total_count": 2, "offset": 0, "limit": 1, "next": "more"}})

    result = run_data({"kind": "query", "source": "chembl", "operation": "search_molecules",
                       "parameters": {"query": "fixture", "limit": 1}}, tmp_path, run_id="partial",
                      budget=DataBudget(max_requests=1), transport=httpx.MockTransport(transport))
    assert result["result"]["returned"] == 1
    assert result["result"]["errors"][0]["code"] == "data_budget_exhausted"
    assert result["result"]["next_parameters"]["offset"] == 1


@pytest.mark.parametrize("selection", [{"limt": 1}, {"field": "id"}, {"snapshot_id": "injected"}])
def test_catalog_selection_is_validated_before_local_import(tmp_path, selection):
    path = tmp_path / "input.csv"
    path.write_text("id,smiles\na,CCO\n")
    with pytest.raises(ValueError):
        plan_data({"kind": "import_catalog", "source": "chembl", "path": str(path),
                   "options": {"source_version": "fixture"}, "search": selection})
    assert not (tmp_path / ".molquarry").exists()


def test_active_pool_limit_is_checked_before_writing_resources(tmp_path, library):
    store = CampaignStore(tmp_path / "small.sqlite")
    endpoint = Endpoint("mw", "T", "mw", "Da", "fixture", 1, requires_handoff=False)
    store.configure(CampaignSpec("mw", 5, "quotes", MOLECULAR_REPRESENTATION, max_candidates=1), [endpoint])
    events = store.events()
    with pytest.raises(ValueError, match="pool limit"):
        import_candidates(store, library[0])
    assert store.candidates() == {} and store.events() == events
    identities = json.loads((library[0] / "identity-map.json").read_text())
    selected = identities[0]["parent_id"]
    report = import_candidates(store, library[0], candidate_ids=[selected])
    assert report["selection"] == "explicit_subset" and report["added"] == 1


def test_concurrent_candidate_writers_cannot_overfill_the_declared_pool(tmp_path):
    store = CampaignStore(tmp_path / "one.sqlite")
    endpoint = Endpoint("mw", "T", "mw", "Da", "fixture", 1, requires_handoff=False)
    store.configure(CampaignSpec("mw", 2, "q", "test", max_candidates=1), [endpoint])
    barrier = threading.Barrier(2)

    def add(identifier):
        barrier.wait(timeout=5)
        try:
            return store.add_candidates([Candidate(identifier, "CCO", (1.0,))])
        except ValueError as error:
            assert "pool limit" in str(error)
            return 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(add, ["a", "b"])) == 1
    assert len(store.candidates()) == 1


def test_legacy_endpoint_defaults_resume_without_rewriting_configuration_history(tmp_path):
    store = CampaignStore(tmp_path / "legacy.sqlite")
    endpoint = Endpoint("mw", "T", "mw", "Da", "fixture", 1, requires_handoff=False)
    spec = CampaignSpec("mw", 2, "q", "test")
    store.configure(spec, [endpoint])
    with store.connection(write=True) as db:
        original = json.loads(db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()[0])
        original["endpoints"][0].pop("queryable")
        legacy = json.dumps(original, sort_keys=True)
        db.execute("UPDATE metadata SET body=? WHERE key='configuration'", (legacy,))
    events = store.events()
    store.configure(spec, [endpoint])
    assert store.configuration()[1]["mw"].queryable is True
    assert store.events() == events
    with store.connection() as db:
        assert db.execute("SELECT body FROM metadata WHERE key='configuration'").fetchone()[0] == legacy
    store.register_endpoints([endpoint], rationale="resume legacy definition")
    assert store.events() == events


def test_confirmation_reservation_uses_geometry_attached_after_candidate_registration(tmp_path):
    store = CampaignStore(tmp_path / "confirmation.sqlite")
    high = Endpoint("high", "T", "energy", "kcal/mol", "fixture-high", 2)
    low = Endpoint("low", "T", "score", "au", "fixture-low", 1, requires_handoff=False)
    store.configure(CampaignSpec("high", 3, "q", "test", policy="decision_aware"), [high, low])
    store.add_candidates([Candidate("a", "CCO", (1.0,))])
    store.bind_handoffs([{"parent_id": "a", "parent_smiles": "CCO", "status": "OK", "molblock": "fixture"}],
                        source={"fixture": True}, rationale="late geometry")
    round_id = store.start_round({})
    action = store.reserve(round_id, "a", "low", {})
    assert action.reserved_cost == 1 and store.balance()["remaining"] == 2


@pytest.mark.parametrize("relation", ["<", ">", "<=", ">=", "~"])
def test_censored_inventory_rows_cannot_be_admitted_as_exact_values(tmp_path, relation):
    store, library, review = assay_fixture(tmp_path)
    # Re-seal a fixture to exercise semantic admission, not byte-tampering rejection.
    original = read_snapshot(library)
    inventory = json.loads((library / "inventory.json").read_text())
    inventory["measurements"][0]["relation"] = relation
    (library / "snapshot.json").unlink()
    (library / "inventory.json").unlink()
    write_json(library / "inventory.json", inventory)
    updated = seal(library, kind="library", result=original["result"],
                   infrastructure=original["infrastructure"])
    review["library_snapshot_id"] = updated["snapshot_id"]
    with pytest.raises(ValueError, match="censored"):
        import_assays(store, library, review)
    assert store.observations() == []


@pytest.mark.parametrize("value", ["1e-999", "1e999"])
def test_concentration_conversion_cannot_silently_underflow_or_overflow(tmp_path, value):
    _, library, review = assay_fixture(tmp_path)
    original = read_snapshot(library)
    inventory = json.loads((library / "inventory.json").read_text())
    inventory["measurements"][0]["value"] = value
    (library / "snapshot.json").unlink()
    (library / "inventory.json").unlink()
    write_json(library / "inventory.json", inventory)
    updated = seal(library, kind="library", result=original["result"],
                   infrastructure=original["infrastructure"])
    review["library_snapshot_id"] = updated["snapshot_id"]
    store = CampaignStore(tmp_path / "concentration.sqlite")
    endpoint = Endpoint("kd", "P12345", "Kd", "nM", "fixture-assay/1", 0,
                        queryable=False, requires_handoff=False)
    store.configure(CampaignSpec("kd", 0, "q", "test"), [endpoint])
    identities = json.loads((library / "identity-map.json").read_text())
    store.add_candidates([Candidate(identities[0]["parent_id"], identities[0]["parent_smiles"], (1.0,))])
    with pytest.raises(ValueError, match="underflows|finite model range"):
        import_assays(store, library, review)
    assert store.observations() == [] and store.actions() == []


def test_cli_review_template_is_directly_editable_json(tmp_path, capsys):
    _, library, review = assay_fixture(tmp_path)
    assert main(["data", "review-template", "--snapshot", str(library),
                 "--endpoint", "kd", "--protocol", "fixture-assay/1"]) == 0
    template = json.loads(capsys.readouterr().out)
    assert set(template) == set(review)
    assert template["observations"][0]["decision"] == "withhold"


def test_seal_refuses_extra_files_and_symlinked_directories(tmp_path, library):
    root = library[0]
    extra = root / "extra.json"
    extra.write_text("{}")
    with pytest.raises(ValueError, match="inventory"):
        read_snapshot(root)
    extra.unlink()
    (root / "redirect").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        read_snapshot(root)


def test_json_publication_never_overwrites_previous_evidence(tmp_path):
    destination = tmp_path / "record.json"
    write_json(destination, {"value": 1})
    with pytest.raises(FileExistsError):
        write_json(destination, {"value": 2})
    assert json.loads(destination.read_text()) == {"value": 1}
    assert list(tmp_path.iterdir()) == [destination]


def test_equal_standard_inchikey_does_not_hide_tautomer_state_change(tmp_path):
    root = new_run(tmp_path, "tautomer", {"fixture": True})
    (root / "payload").mkdir()
    write_json(root / "payload/records.json", [{"id": "hydroxypyridine", "smiles": "Oc1ccccn1"}])
    seal(root, kind="query", result={"status": "complete"}, infrastructure={"fixture": True})
    result = prepare_library(root, tmp_path, run_id="canonical", id_field="id", smiles_field="smiles",
                             identity_policy={"tautomer_policy": "canonicalize"})
    row = json.loads((Path(result["snapshot"]) / "identity-map.json").read_text())[0]
    assert row["source_inchikey"] == row["parent_inchikey"]
    assert row["source_canonical_smiles"] != row["parent_smiles"]
    assert row["chemical_state_changed"] is True


def test_dossier_measurements_keep_their_own_state_after_inchikey_inventory_grouping(tmp_path):
    molecules = [("CHEMBL1", "Oc1ccccn1"), ("CHEMBL2", "O=c1cccc[nH]1")]
    dossier = {"targets": [{"input": "T", "accession": "P12345", "taxon": 9606}],
        "chembl_details": {"molecule": [{"molecule_chembl_id": identifier,
            "molecule_structures": {"canonical_smiles": smiles}} for identifier, smiles in molecules],
            "document": []}, "bindingdb": [],
        "activities": [{"input_target": "T", "record": {"activity_id": index,
            "molecule_chembl_id": identifier, "canonical_smiles": smiles, "standard_type": "Kd",
            "standard_relation": "=", "standard_value": str(10 * (index + 1)), "standard_units": "nM"}}
            for index, (identifier, smiles) in enumerate(molecules)]}
    root = new_run(tmp_path, "dossier", {"fixture": True})
    (root / "payload").mkdir()
    write_json(root / "payload/dossier.json", dossier)
    seal(root, kind="collect", result={"status": "collected_requires_review"}, infrastructure={"fixture": True})
    result = prepare_library(root, tmp_path, run_id="library")
    library = Path(result["snapshot"])
    inventory = json.loads((library / "inventory.json").read_text())
    identities = json.loads((library / "identity-map.json").read_text())
    assert len(inventory["compounds"]) == 1  # Standard InChI groups these tautomers upstream.
    assert result["result"]["candidates"] == 2
    assert len({row["etalon_source_record_id"] for row in inventory["measurements"]}) == 2
    assert all(row["chemical_state_changed"] is False for row in identities)
    store = CampaignStore(tmp_path / "campaign.sqlite")
    endpoint = Endpoint("kd", "P12345", "Kd", "nM", "fixture-assay", 0,
                        queryable=False, requires_handoff=False)
    store.configure(CampaignSpec("kd", 0, "q", MOLECULAR_REPRESENTATION), [endpoint])
    import_candidates(store, library)
    review = review_template(library, endpoint_id="kd", protocol=endpoint.protocol)
    review.update(reviewer="fixture", reviewed_at="2026-09-21",
                  target={"accession": "P12345", "taxon": 9606, "construct": "fixture construct"})
    for index, observation in enumerate(review["observations"]):
        observation.update(decision="accept", experiment_id=f"fixture:{index}", rationale="offline fixture")
    assert import_assays(store, library, review)["added"] == 2
    observed = {row["result"]["candidate_id"]: row["result"]["value"] for row in store.observations()}
    assert observed == {identities[0]["parent_id"]: 10, identities[1]["parent_id"]: 20}


def test_changed_chemistry_environment_cannot_silently_reuse_a_library(tmp_path, library, monkeypatch):
    from molcascade.chemistry import identity

    original = identity.rdkit_identity_metadata
    monkeypatch.setattr(identity, "rdkit_identity_metadata", lambda policy: {
        **original(policy), "rdkit_version": "different-implementation"})
    store = CampaignStore(tmp_path / "mismatch.sqlite")
    endpoint = Endpoint("mw", "T", "mw", "Da", "fixture", 1, requires_handoff=False)
    store.configure(CampaignSpec("mw", 5, "quotes", MOLECULAR_REPRESENTATION), [endpoint])
    events = store.events()
    with pytest.raises(ValueError, match="identity implementation"):
        import_candidates(store, library[0])
    assert store.events() == events and store.candidates() == {}


def test_campaign_setup_validates_before_creation_and_refuses_sqlite_sidecars(tmp_path):
    from etalon.active.setup import create_campaign

    database = tmp_path / "campaign.sqlite"
    spec = {"objective": "kd", "budget": 0, "cost_unit": "q", "representation": "test"}
    endpoint = {"id": "kd", "target": "T", "quantity": "Kd", "units": "nM", "protocol": "p",
                "cost": 0, "queryable": False, "requires_handoff": False}
    with pytest.raises(ValueError, match="include the objective"):
        create_campaign(database, spec, [])
    assert not database.exists()
    sidecar = Path(str(database) + "-wal")
    sidecar.write_bytes(b"previous work")
    with pytest.raises(FileExistsError, match="sidecar"):
        create_campaign(database, spec, [endpoint])
    sidecar.unlink()
    assert create_campaign(database, spec, [endpoint])["executed_actions"] == 0
    with pytest.raises(FileExistsError):
        create_campaign(database, spec, [endpoint])


def test_bundle_reuses_review_and_preserves_af3_prepared_state_without_network(tmp_path):
    pytest.importorskip("gemmi")
    pytest.importorskip("Bio")
    dossier = {"config": {"mode": "ligand"}, "targets": [{"input": "T", "gene": "T",
        "accession": "P00533", "taxon": 9606, "sequence": "ACDEFGHIK"}],
        "papers": [], "fulltexts": {}, "chembl_details": {"assay": [], "document": [], "molecule": []},
        "activities": [], "bindingdb": [], "structures": [], "structure_hits": {},
        "polymer_entities": [], "ccd_ligands": [], "review_queue": [], "coverage": [],
        "incomplete_steps": []}
    root = new_run(tmp_path, "dossier", {"fixture": True})
    (root / "payload").mkdir()
    write_json(root / "payload/dossier.json", dossier)
    seal(root, kind="collect", result={"status": "collected_requires_review"},
         infrastructure={"fixture": True})
    curation = tmp_path / "curation.json"
    write_json(curation, {"reviewed_at": "2026-09-21", "candidates": [],
                         "papers_reviewed": [], "limitations": ["offline empty fixture"]})
    result = run_data({"kind": "bundle", "snapshot": str(root), "curation_path": str(curation)},
                      tmp_path, run_id="bundle", budget=DataBudget(max_requests=0))
    assert result["usage"]["requests"] == 0
    assert result["result"]["af3"]["status"] == "prepared_not_submitted"
    assert result["result"]["af3"]["models_returned"] == 0
    assert (Path(result["snapshot"]) / "payload/tables/compounds.xlsx").is_file()
    read_snapshot(Path(result["snapshot"]))
