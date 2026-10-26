import json

import pytest

from etalon.research.method_reuse import execute_or_reuse, request_identity


def request(**parameters):
    return request_identity(operation="real-tool", implementation={"sha256": "a"},
        environment={"rdkit": "fixed"}, inputs={"all_atoms_sha256": "b"}, parameters=parameters)


def test_reuse_verifies_output_without_executing(tmp_path):
    artifact = tmp_path / "result.dat"
    calls = []
    def run():
        calls.append(1)
        artifact.write_text("actual output")
        return {"result": {"objective": 2.5}, "artifacts": [str(artifact)]}
    first = execute_or_reuse(tmp_path / "receipts", request(), run)
    repeat = execute_or_reuse(tmp_path / "receipts", request(), run)
    assert first["reused"] is False and repeat["reused"] is True
    assert len(calls) == 1 and first["receipt"] == repeat["receipt"]
    artifact.write_text("tampered")
    with pytest.raises(ValueError, match="artifact identity"):
        execute_or_reuse(tmp_path / "receipts", request(), run)
    assert len(calls) == 1


def test_unresolved_request_never_retries(tmp_path):
    req = request(seed=1)
    root = tmp_path / req["request_digest"]
    root.mkdir()
    (root / "request.json").write_text(json.dumps(req))
    with pytest.raises(RuntimeError, match="unresolved"):
        execute_or_reuse(tmp_path, req, lambda: pytest.fail("must not run"))


def test_failure_is_charged_and_reused(tmp_path):
    def failure():
        raise ValueError("native failed")
    first = execute_or_reuse(tmp_path, request(), failure)
    second = execute_or_reuse(tmp_path, request(), lambda: pytest.fail("must not rerun"))
    assert first["receipt"]["status"] == "failed" and second["reused"]
    assert first["receipt"]["execution_cost"]["wall_seconds"] > 0


def test_distinct_chemistry_coordinates_environment_and_options_are_not_reused():
    a = request(seed=1)
    assert a != request(seed=2)
    args = dict(operation="real-tool", implementation={"sha256": "a"},
                environment={"rdkit": "fixed"}, inputs={"all_atoms_sha256": "b"}, parameters={"seed": 1})
    for field, value in [("inputs", {"all_atoms_sha256": "hydrogen-moved"}),
                         ("environment", {"rdkit": "new"}),
                         ("implementation", {"sha256": "new"})]:
        assert request_identity(**{**args, field: value})["request_digest"] != a["request_digest"]
