"""Contract fixtures; these do not establish real retrosynthesis performance."""
import hashlib
import json
import subprocess
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from molcascade.artifacts import ArtifactRef, ArtifactProducer, LocalArtifactStore
from molcascade.contracts import PARENT_V1, DEFAULT_CONTRACT_REGISTRY, get_contract
from molcascade.errors import PluginError
from molcascade.plugins.api import StageContext, StageInput, StageRequest
from molcascade.plugins.builtin.aizynthfinder import AiZynthFinderRoutePlugin
from molcascade.plugins.builtin.aizynth_evidence import ROUTES_PATH, SEARCH_PATH
from molcascade.pipeline.compiler import CompiledStage
from molcascade.runtime.validation import validate_stage_response


@pytest.fixture
def invocation(tmp_path):
    executable = tmp_path / "aizynthcli"
    executable.write_text("fixture", encoding="utf-8")
    executable.chmod(0o755)
    config = tmp_path / "config.yml"
    config.write_text("stock: {}\n", encoding="utf-8")
    dependency = tmp_path / "stock.fixture"
    dependency.write_bytes(b"fixture-stock")
    source = tmp_path / "input"
    source.mkdir()
    pq.write_table(pa.Table.from_pylist([
        {"parent_id": "ethanol", "identity_policy_id": "fixture", "parent_smiles": "CCO",
         "registration_key": "ethanol", "duplicate_count": 1},
        {"parent_id": "benzene", "identity_policy_id": "fixture", "parent_smiles": "c1ccccc1",
         "registration_key": "benzene", "duplicate_count": 1},
    ], schema=PARENT_V1.schema), source / "parents.parquet")
    stage_input = StageInput(
        ArtifactRef(artifact_id="artifact:sha256:" + "a" * 64, kind=PARENT_V1.id),
        source, PARENT_V1.id, file_paths=("parents.parquet",),
    )
    request = StageRequest("route-test", {"primary": stage_input}, {
        "executable": str(executable), "config_path": str(config),
        "backend_version": "4.4.1", "dependency_files": {"stock": str(dependency)},
        "failure_evidence_dir": str(tmp_path / "retained-failures"),
    })
    records = [
        {"target": "CCO", "is_solved": True, "number_of_steps": 1,
         "trees": [{"smiles": "CCO", "children": [{"type": "reaction", "metadata":
                    {"template": "fixture"}, "children": [{"smiles": "CCBr"}]}]}]},
        {"target": "c1ccccc1", "is_solved": False, "trees": [], "search_time": 0.1},
    ]
    return request, records, dependency


def native_fixture(monkeypatch, records, *, returncode=0, timeout=False, mutate=None):
    payload = json.dumps({"data": records}, indent=2).encode()
    def run(command, **kwargs):
        Path(command[command.index("--output") + 1]).write_bytes(payload)
        if mutate:
            mutate()
        if timeout:
            raise subprocess.TimeoutExpired(command, 1, output=b"partial log")
        return subprocess.CompletedProcess(command, returncode, stdout=b"native log\n")
    monkeypatch.setattr(subprocess, "run", run)
    return payload


def test_full_native_bytes_and_routes_survive_atomic_artifact_commit(tmp_path, monkeypatch, invocation):
    request, records, _ = invocation
    payload = native_fixture(monkeypatch, records)
    store = LocalArtifactStore(tmp_path / "store")
    with store.staging_area() as staging:
        response = AiZynthFinderRoutePlugin().execute(request, StageContext(staging))
        stage = CompiledStage("route-test", "synthesis", "synthesis.aizynthfinder@0.2.0",
                              AiZynthFinderRoutePlugin.descriptor, request.config,
                              PARENT_V1.id, PARENT_V1.id, "primary")
        validated = validate_stage_response(stage, response, staging, DEFAULT_CONTRACT_REGISTRY)
        assert validated.row_counts["synthesis_routes"] == 2
        for output in response.outputs.values():
            for path in output.file_paths:
                get_contract(output.contract_id).validate(pq.read_table(staging / path))
        manifest = store.commit(staging, kind="route-search", outputs=response.artifact_outputs,
                                producer=ArtifactProducer(plugin_id="synthesis.aizynthfinder",
                                                          plugin_version="0.2.0", api_version="1"))
    reopened = LocalArtifactStore(tmp_path / "store")
    ref = manifest.dataset_ref("synthesis_routes")
    root = reopened.resolve_dataset(ref, verify=True)
    rows = pq.read_table(root / ROUTES_PATH).to_pylist()
    assert [r["status"] for r in rows] == ["SOLVED", "NOT_FOUND_WITHIN_BUDGET"]
    assert json.loads(rows[0]["record_json"]) == records[0]
    archive = pq.read_table(root / SEARCH_PATH).to_pylist()[0]
    assert archive["output_bytes"] == payload
    assert archive["output_sha256"] == hashlib.sha256(payload).hexdigest()
    assert pq.read_table(root / "datasets/synthesis_scores/part-00000.parquet")["score"].to_pylist() == [1., 99.]


@pytest.mark.parametrize("failure", ["timeout", "exit", "misaligned", "dependency"])
def test_failure_preserves_raw_evidence_and_stays_failed(tmp_path, monkeypatch, invocation, failure):
    request, records, dependency = invocation
    if failure == "misaligned":
        records.reverse()
    mutate = (lambda: dependency.write_bytes(b"changed-stock")) if failure == "dependency" else None
    payload = native_fixture(monkeypatch, records, returncode=2 if failure == "exit" else 0,
                             timeout=failure == "timeout", mutate=mutate)
    store = LocalArtifactStore(tmp_path / "store")
    with pytest.raises(PluginError) as captured:
        with store.staging_area() as staging:
            AiZynthFinderRoutePlugin().execute(request, StageContext(staging))
    locator = captured.value.context["route_evidence"]
    retained = Path(locator["directory"])
    assert locator["published_artifact"] is False
    assert locator["retention"] == "caller_owned"
    assert not staging.exists()
    archive = pq.read_table(retained / SEARCH_PATH).to_pylist()[0]
    assert archive["output_bytes"] == payload
    assert archive["status"] == ("TIMEOUT" if failure == "timeout" else "FAILED")
    assert all(row["record_json"] is None for row in pq.read_table(retained / ROUTES_PATH).to_pylist())
    assert not store.list_refs()
