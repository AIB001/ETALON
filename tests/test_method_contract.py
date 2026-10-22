"""Contract/comparison counterexamples and real MolCascade compile-only tests.

Receipts in the comparison tests are explicit fixtures; real compilation is
not an execution, affinity or method-benefit experiment.
"""
import copy
import json

import pytest

from etalon.active.schema import digest
from etalon.boundary.screen import Screen
from etalon.campaign.design import component, compose
from etalon.research import method_contract as mc
from etalon.research.evidence_refs import EvidenceError, make_ref


def parameter(path="/library/batch_size", *, category="execution", immutable=False):
    return {"path": path, "category": category, "type": "integer", "unit": "rows", "default": 65536,
            "minimum": 1, "maximum": 1000000, "choices": [], "required": True,
            "immutable": immutable, "depends_on": [], "expected_effect": "reduce batching overhead; untested hypothesis"}


def contract():
    return {"schema": mc.CONTRACT, "name": "fixture batch tuning", "parameters": [parameter()],
            "required_checks": ["chemical-identity", "properties"],
            "evaluation": {"quality": "identical molecule rows/properties", "cost": "elapsed_seconds", "missing": "unknown"},
            "supersedes": []}


@pytest.fixture
def prepared(tmp_path):
    configuration = compose("compile-only", [{"id": "properties", "title": "Properties",
        "criteria": [component("properties", "features.rdkit_properties@0.1.0"),
                     component("other-properties", "features.rdkit_properties@0.1.0")]}])
    path = tmp_path / "config.json"
    path.write_text(json.dumps(configuration, indent=2), encoding="utf-8", newline="\n")
    library = tmp_path / "library.csv"
    library.write_text("id,smiles\nethanol,CCO\nbenzene,c1ccccc1\n", encoding="utf-8")
    screen = Screen.open_existing(tmp_path)
    kwargs = dict(contract=contract(), input_identities={"library": "fixture-two-molecules"},
                  conditions={"endpoint": "physicochemical-properties/1"}, library=library,
                  parameter_bindings=[{"parameter": "/library/batch_size", "stage_id": "library", "path": "/batch_size"}],
                  stage_checks={"standardize": ["chemical-identity"], "properties": ["properties"]})
    plan = mc.compile_screen_revision(screen, path, **kwargs)
    return configuration, path, screen, kwargs, plan


def coverage(plan, *, reuse=False):
    return [{"check_id": check, "state": "reused" if reuse else "passed",
             "receipt_ref": make_ref("fixture", "artifact-receipt-" + check, {"fixture": check}),
             "reuse_basis_ref": make_ref("fixture", "artifact-equivalence", {"same_inputs": True}) if reuse else None,
             "input_digest": digest(plan["input_identities"]), "conditions_digest": digest(plan["conditions"])}
            for check in plan["contract"]["required_checks"]]


def assess(base, candidate, *, mode="same_protocol", candidate_coverage=None, verifier=lambda row, side: True):
    return mc.assess_comparability(base, candidate, mode=mode, base_coverage=coverage(base),
        candidate_coverage=coverage(candidate) if candidate_coverage is None else candidate_coverage, verify_receipt=verifier)


def test_real_compilation_preserves_authored_effective_and_module_identities_without_run(prepared):
    configuration, path, screen, kwargs, plan = prepared
    assert plan["status"] == "compiled_not_executed"
    assert plan["missing_checks"] == []
    assert {s["stage_id"] for s in plan["compiled_stages"]} >= {"library", "standardize", "properties"}
    assert plan["declaration_digest"] == digest(configuration)
    assert plan["modules"]["etalon.boundary.screen"]["sha256"]
    assert plan["parameters"]["/library/batch_size"]["origin"] == "declared"
    assert plan["effective_parameters"][0]["status"] == "matches"
    assert not (path.parent / "etalon-screen-jobs.sqlite").exists()
    assert not (path.parent / "runs").exists()


def test_exact_file_byte_identity_distinguishes_lf_crlf_without_changing_semantics(prepared):
    _, path, screen, kwargs, lf = prepared
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    crlf = mc.compile_screen_revision(screen, path, **kwargs)
    assert crlf["authored_file"] != lf["authored_file"]
    assert crlf["declaration_digest"] == lf["declaration_digest"]
    assert crlf["compiled_digest"] == lf["compiled_digest"]
    assert crlf["revision_id"] == lf["revision_id"]


def test_real_parameter_change_is_compiled_and_no_unverified_benefit_claim(prepared):
    configuration, path, screen, kwargs, base = prepared
    configuration["library"]["batch_size"] = 128
    path.write_text(json.dumps(configuration), encoding="utf-8")
    candidate = mc.compile_screen_revision(screen, path, **kwargs)
    assert candidate["revision_id"] != base["revision_id"]
    assert assess(base, candidate)["status"] == "ready_for_evaluation"
    assert assess(base, candidate)["benefit_established"] is False
    assert assess(base, candidate, verifier=None)["status"] == "awaiting_evidence"


def test_workflow_can_reuse_check_while_accidentally_missing_check_is_not_speedup(prepared):
    configuration, path, screen, kwargs, base = prepared
    configuration["tiers"][0]["criteria"][0]["enabled"] = False
    path.write_text(json.dumps(configuration), encoding="utf-8")
    candidate = mc.compile_screen_revision(screen, path, **kwargs)
    assert candidate["missing_checks"] == ["properties"]
    missing = assess(base, candidate, mode="workflow_change")
    assert any("no compiled implementation" in m for m in missing["missing"])
    reused = coverage(candidate)
    reused[1] = coverage(candidate, reuse=True)[1]
    allowed = assess(base, candidate, mode="workflow_change", candidate_coverage=reused)
    assert allowed["status"] == "ready_for_evaluation"
    assert assess(base, candidate, mode="same_protocol", candidate_coverage=reused)["status"] == "awaiting_evidence"
    reused[1]["reuse_basis_ref"] = None
    assert assess(base, candidate, mode="workflow_change", candidate_coverage=reused)["status"] == "awaiting_evidence"


@pytest.mark.parametrize("key", ["input_identities", "conditions", "target"])
def test_input_or_conditions_mismatch_is_not_comparable(prepared, key):
    base = prepared[-1]
    candidate = copy.deepcopy(base)
    candidate[key] = {"different": "yes"}
    result = assess(base, candidate)
    assert result["status"] == "awaiting_evidence"
    assert key in result["differences"]


def test_changed_evaluation_contract_never_claims_same_round_benefit(prepared):
    base = prepared[-1]
    candidate = copy.deepcopy(base)
    candidate["contract"]["evaluation"]["quality"] = "relaxed threshold"
    candidate["contract_digest"] = digest(candidate["contract"])
    result = assess(base, candidate, mode="method_change")
    assert any("evaluation standard changed" in m for m in result["missing"])
    assert result["benefit_established"] is False


def test_parameter_type_bounds_dependency_and_hard_constraints_are_enforced():
    spec = contract()
    for value in (True, 0, 2000000):
        with pytest.raises(EvidenceError):
            mc.parameter_values(spec, {"library": {"batch_size": value}})
    spec["parameters"][0]["immutable"] = True
    with pytest.raises(EvidenceError, match="hard constraint"):
        mc.compare_parameters(spec, {"library": {"batch_size": 10}}, {"library": {"batch_size": 20}})
    dep = parameter("/passes", category="method")
    dep["depends_on"] = [{"path": "/library/batch_size", "equals": 10}]
    spec["parameters"].append(dep)
    with pytest.raises(EvidenceError, match="dependency"):
        mc.parameter_values(spec, {"library": {"batch_size": 20}, "passes": 1})


def test_method_change_allowed_and_evaluation_change_disclosed():
    spec = contract()
    spec["parameters"][0]["category"] = "method"
    result = mc.compare_parameters(spec, {"library": {"batch_size": 10}}, {"library": {"batch_size": 20}})
    assert result["changes"][0]["category"] == "method"
    assert not result["evaluation_changed"]
    spec["parameters"][0]["category"] = "evaluation"
    assert mc.compare_parameters(spec, {"library": {"batch_size": 10}}, {"library": {"batch_size": 20}})["evaluation_changed"]


def test_corrupted_compilation_mapping_and_digest_rejected(prepared):
    broken = copy.deepcopy(prepared[-1])
    broken["compiled_stages"][0]["config"]["invented"] = True
    with pytest.raises(EvidenceError, match="identity"):
        mc.validate_content(broken)
    broken = copy.deepcopy(prepared[-1])
    broken["stage_checks"][0]["compiled"] = False
    with pytest.raises(EvidenceError, match="map"):
        mc.validate_content(broken)


def test_edited_input_without_effective_binding_is_not_a_method_improvement(prepared):
    configuration, path, screen, kwargs, base = prepared
    configuration["library"]["batch_size"] = 128
    path.write_text(json.dumps(configuration), encoding="utf-8")
    kwargs["parameter_bindings"] = []
    candidate = mc.compile_screen_revision(screen, path, **kwargs)
    assert candidate["unbound_parameters"] == ["/library/batch_size"]
    assert any("consumption not established" in m for m in assess(base, candidate)["missing"])


def test_backend_failure_propagates_instead_of_manufacturing_ready_plan(prepared, monkeypatch):
    _, path, screen, kwargs, _ = prepared
    def fail(*args, **kw):
        raise RuntimeError("fixture BACKEND_PREFLIGHT_FAILED")
    monkeypatch.setattr(screen, "plan", fail)
    with pytest.raises(RuntimeError, match="BACKEND_PREFLIGHT_FAILED"):
        mc.compile_screen_revision(screen, path, **kwargs)


@pytest.mark.parametrize("tamper", ["empty-coverage", "missing-receipt", "context", "verifier", "digest"])
def test_comparison_publication_cannot_claim_ready_with_inconsistent_receipts(prepared, tamper):
    payload = assess(prepared[-1], prepared[-1])
    assert payload["verification_scope"] == "caller_attested_not_kernel_certified"
    if tamper == "empty-coverage":
        payload["coverage"]["candidate"] = []
    elif tamper == "missing-receipt":
        payload["coverage"]["candidate"][0]["receipt_ref"] = None
    elif tamper == "context":
        payload["comparison_contexts"]["candidate"]["input_digest"] = "a" * 64
    elif tamper == "verifier":
        payload["verifier_used"] = False
    else:
        payload["candidate_digest"] = "made-up"
    with pytest.raises(EvidenceError):
        mc.validate_content(payload)
