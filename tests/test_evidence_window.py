"""Ordered local engineering fixtures; no scientific validity or model-blinding claim."""
import copy
import json

import pytest

from etalon.active.schema import digest
from etalon.research import Artifact, MethodVersion, TaskSplit
from etalon.research import evidence_window as ew
from etalon.research import research_decision as rd
from etalon.research.components import record_json
from etalon.research.composition import node_step
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.method_revision import journal_ref
from test_research_continuation import case, publish, save, step


@pytest.fixture
def setup(case):
    journal, problem, _, root = case
    ref = lambda rid: journal_ref(journal, rid, source_id="research")
    artifact = lambda value: ref(record_json(journal, value, root / "fixture-inputs", source_kind="fixture"))
    protocol = artifact({"fixture": True, "comparison": "ordered-publication/1"})
    workset = artifact({"task_id": "case", "branch_id": "existing", "fixture": True})
    method = ref(journal.record(MethodVersion("fixture procedure", ("domain_skill",), {"artifact_ref": protocol["record_id"]})))
    comparator = ref(journal.record(MethodVersion("fixture comparator", ("domain_skill",), {"artifact_ref": protocol["record_id"]})))
    split = ref(journal.record(TaskSplit(("dev",), ("case",), ("transfer",),
        {k: digest(k) for k in ("dev", "case", "transfer")}, "engineering fixture only")))
    action = save(case, node_step(task_id="case", question="Do ordered calls stay ordered?",
        branch_id="existing", node_id="fixture-run", state="planned", reason="Fixture controlled operation",
        input_refs=[problem], method_refs=[method]))
    plan = step(problem, operation="evaluation_plan", detail=dict(target="method", subjects=[method],
        comparators=[comparator], protocol_ref=protocol, task_split_ref=split, initial_conditions_ref=problem,
        use_refs=[], metrics=[dict(name="ordered", family="regression", unit="boolean",
        denominator="one fixture case", missing_policy="unresolved")], trigger_refs=[problem]))
    evaluation = save(case, plan)
    slot = ew.outcome_slot(outcome_id="one", action_ref=action, protocol_ref=protocol, input_refs=[problem],
        method_refs=[method], producer_id="local-fixture/1", receipt_contract="fixture-outcome/1")
    return dict(journal=journal, problem=problem, root=root, ref=ref, artifact=artifact, protocol=protocol,
        workset=workset, method=method, action=action, evaluation=evaluation, slot=slot)


def window(case, setup, **changes):
    arguments = dict(task_id="case", branch_id="existing",
        workset_ref=setup["workset"], visible_refs=[setup["problem"]], timing_mode="controlled",
        coverage_scope="Only the explicit fixture refs; no other host reads attested",
        expected_outcomes=[setup["slot"]], evaluation_contract_ref=setup["evaluation"],
        method_refs=[setup["method"]])
    return ew.build_evidence_window(publish(case), **{**arguments, **changes})


def decision(case, setup, window_ref):
    payload = rd.build_research_decision(publish(case), task_id="case", scope="fixture publication",
        question="Does this API preserve pre-outcome order?", branch_id="existing",
        workset_ref=setup["workset"], evidence_window_ref=window_ref, problem_refs=[setup["problem"]],
        structure=dict(task_family="engineering fixture", objective_kind="event ordering", subject="local H",
            intervention="ordered API calls", readout="journal sequence", constraints=["No scientific claim"], unknowns=[]),
        attribution=dict(actor="pytest", basis="host_judgment", model_ref=None, skill_refs=[], verifier_refs=[]),
        options=[dict(option_id="a", kind="hypothesis", statement="Order is preserved", claim_refs=[]),
                 dict(option_id="u", kind="unknown", statement="Failure may leave unresolved delivery", claim_refs=[])],
        predictions=[dict(prediction_id="p", action_id="run", option_ids=["a"], observable="Committed event order",
            expected="Window before decision before outcome", distinguishes="Early release is rejected", status="testable",
            basis_refs=[setup["problem"]], unknown_reason=None)],
        actions=[dict(action_id="run", description="Run local fixture", disposition="selected", reason="Exercise real API order",
            execution_ref=setup["action"], prediction_ids=["p"])], method_refs=[setup["method"]],
        evaluation_refs=[setup["evaluation"]])
    return save(case, payload)


def deliver(setup, window_ref):
    response = ew.deliver_evidence_window(setup["journal"], window_ref, source_id="research")
    ew.acknowledge_evidence_window(setup["journal"], window_ref, token=response["token"], source_id="research")
    return response


def produced(setup, payload, *, status="failed", **binding_changes):
    path = setup["root"] / "outcome.json"
    path.write_text(json.dumps({"fixture": True, "status": status, "measurement": None}), encoding="utf-8")
    bindings = {**ew.expected_outcome_bindings(payload, "one"), **binding_changes}
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="independent_validation", bindings=bindings)
    receipt = setup["artifact"]({**bindings, "artifact_identity": file_identity(path), "status": status, "fixture": True})
    return dict(outcome_id="one", artifact=artifact, receipt_ref=receipt)


def release(setup, wref, dref, outcome):
    return ew.release_outcomes(setup["journal"], window_ref=wref, decision_ref=dref, outcomes=[outcome],
        directory=setup["root"] / "released", source_id="research", source_kind="fixture")


def test_real_ordered_calls_preserve_failure_and_idempotence(case, setup):
    payload = window(case, setup)
    wref = save(case, payload)
    delivered = deliver(setup, wref)
    assert delivered["state"] == "prepared_for_return" and delivered["consumption"] == "not_attested"
    assert delivered["materials"][0]["ref"] == setup["problem"]
    dref = decision(case, setup, wref)
    outcome = produced(setup, payload)
    released = release(setup, wref, dref, outcome)
    stored = json.loads(setup["journal"].artifact_path(Artifact(**setup["journal"].get(released["record_id"]))).read_text())
    assert ew.validate_local_links(setup["journal"], stored, "research") == stored
    events = setup["journal"].store.events()
    assert release(setup, wref, dref, outcome) == released
    assert deliver(setup, wref) == delivered
    assert setup["journal"].store.events() == events
    assert setup["journal"].store.actions() == []
    assert ew.read_evidence_window(publish(case), wref) == payload
    assert json.loads(setup["journal"].artifact_path(outcome["artifact"]).read_text())["status"] == "failed"
    with setup["journal"].store.connection() as db:
        assert ew._sequence(db, wref) < ew._sequence(db, dref) < ew._sequence(db, stored["outcomes"][0]["artifact_ref"]) < ew._sequence(db, released)


def test_decision_before_acknowledgement_is_not_prospective(case, setup):
    payload = window(case, setup)
    wref = save(case, payload)
    dref = decision(case, setup, wref)  # Actual API order is wrong.
    deliver(setup, wref)
    outcome = produced(setup, payload)
    with pytest.raises(EvidenceError, match="delivery must precede"):
        release(setup, wref, dref, outcome)
    assert setup["journal"].store.events()[-1]["kind"] == "evidence_outcome_release_failed"


def test_no_release_before_durable_decision_and_no_future_receipt_before_it(case, setup):
    payload = window(case, setup)
    wref = save(case, payload)
    deliver(setup, wref)
    outcome = produced(setup, payload)  # Real production precedes the decision: reject.
    dref = decision(case, setup, wref)
    with pytest.raises(EvidenceError, match="receipt must follow"):
        release(setup, wref, dref, outcome)
    missing = {**dref, "record_id": "artifact-" + "a" * 64}
    with pytest.raises(Exception, match="unknown research record"):
        release(setup, wref, missing, outcome)


def test_reference_substitution_and_relabelled_originals_are_rejected(case, setup):
    payload = window(case, setup)
    wref = save(case, payload)
    deliver(setup, wref)
    dref = decision(case, setup, wref)
    outcome = produced(setup, payload, producer_id="other-producer")
    with pytest.raises(EvidenceError, match="frozen producer"):
        release(setup, wref, dref, outcome)
    evaluation = outcome["artifact"]
    setup["journal"].record(evaluation)
    alias = Artifact(evaluation.path, evaluation.sha256, evaluation.bytes, "fixture", "diagnostic", {})
    alias_ref = setup["ref"](setup["journal"].record(alias))
    changed = copy.deepcopy(payload)
    changed["visible_refs"] = [alias_ref]
    changed["snapshot_digest"] = ew._snapshot(changed)
    with pytest.raises(EvidenceError, match="evaluation-only"):
        ew.validate_local_links(setup["journal"], changed, "research")
    broken = {**setup["problem"], "record_digest": "b" * 64}
    with pytest.raises(EvidenceError):
        ew.build_evidence_window(publish(case), task_id="case", branch_id="existing", workset_ref=None,
            visible_refs=[broken], timing_mode="retrospective", coverage_scope="fixture")


def test_preexisting_outcome_and_duplicate_chronology_are_rejected(case, setup):
    payload = window(case, setup)
    wref = save(case, payload)
    deliver(setup, wref)
    dref = decision(case, setup, wref)
    outcome = produced(setup, payload)
    setup["journal"].record(outcome["artifact"])
    with pytest.raises(EvidenceError, match="already recorded"):
        release(setup, wref, dref, outcome)
    with setup["journal"].store.connection(write=True) as db:
        setup["journal"].store._event(db, "research_recorded", {"id": dref["record_id"], "record": setup["journal"].get(dref["record_id"])})
    with pytest.raises(EvidenceError, match="ambiguous research chronology"):
        release(setup, wref, dref, outcome)


def test_failed_atomic_append_can_resume_without_partial_outcome(case, setup, monkeypatch):
    payload = window(case, setup)
    wref = save(case, payload)
    deliver(setup, wref)
    dref = decision(case, setup, wref)
    outcome = produced(setup, payload)
    original = setup["journal"]._put
    def fail(db, identifier, body):
        if body.get("bindings", {}).get("schema") == ew.OUTCOME_RELEASE:
            raise OSError("fixture interrupted after outcome append")
        return original(db, identifier, body)
    monkeypatch.setattr(setup["journal"], "_put", fail)
    with pytest.raises(OSError, match="interrupted"):
        release(setup, wref, dref, outcome)
    with pytest.raises(Exception, match="unknown research record"):
        setup["journal"].get(outcome["artifact"].id)
    assert list((setup["root"] / "released").glob("*.json"))  # Intact immutable orphan.
    monkeypatch.setattr(setup["journal"], "_put", original)
    assert release(setup, wref, dref, outcome)["record_id"].startswith("artifact-")


def test_large_original_is_fully_verified_but_delivery_remains_bounded(case, setup):
    large = setup["artifact"]({"padding": "x" * 40000, "hidden_ref": setup["problem"]})
    payload = ew.build_evidence_window(publish(case), task_id="case", branch_id="existing", workset_ref=None,
        visible_refs=[large], timing_mode="retrospective", coverage_scope="fixture")
    ref = save(case, payload)
    assert ew.read_evidence_window(publish(case), ref) == payload
    with pytest.raises(EvidenceError, match="complete bounded materials"):
        ew.deliver_evidence_window(setup["journal"], ref, source_id="research")
    oversized = setup["artifact"]({"padding": "x" * (1024 * 1024), "hidden_ref": setup["problem"]})
    with pytest.raises(EvidenceError, match="bounded 1 MiB"):
        ew.build_evidence_window(publish(case), task_id="case", branch_id="existing", workset_ref=None,
            visible_refs=[oversized], timing_mode="retrospective", coverage_scope="fixture")
    payload = window(case, setup)
    payload["coverage"]["scope"] = "x" * 17000
    with pytest.raises(EvidenceError, match="16 KiB"):
        ew.validate_content(payload)


def test_paged_original_follows_evaluation_reference_after_first_page(case, setup):
    path = setup["root"] / "late-evaluation.json"
    path.write_text('{"fixture":true,"value":42}', encoding="utf-8")
    original = Artifact(str(path), **file_identity(path), source_kind="fixture", role="independent_validation", bindings={})
    excluded = setup["ref"](setup["journal"].record(original))
    wrapper = setup["artifact"]({"aaa_padding": "x" * 70000, "zzz_original_ref": excluded})
    with pytest.raises(EvidenceError, match="relabelled|evaluation-only"):
        ew.build_evidence_window(publish(case), task_id="case", branch_id="existing", workset_ref=None,
            visible_refs=[wrapper], excluded_refs=[excluded], timing_mode="retrospective", coverage_scope="fixture late reference")


def test_acknowledgement_cannot_be_fabricated_before_delivery(case, setup):
    payload = window(case, setup)
    wref = save(case, payload)
    token = digest({"window_ref": wref, "snapshot_digest": payload["snapshot_digest"]})
    with pytest.raises(EvidenceError, match="prepared exactly once"):
        ew.acknowledge_evidence_window(setup["journal"], wref, token=token, source_id="research")
    response = ew.deliver_evidence_window(setup["journal"], wref, source_id="research")
    with pytest.raises(EvidenceError, match="token differs"):
        ew.acknowledge_evidence_window(setup["journal"], wref, token="bad", source_id="research")
    assert response["consumption"] == "not_attested"


def test_parent_only_evaluation_lineage_is_not_conditioning(case, setup):
    raw = setup["root"] / "evaluation-only.txt"
    raw.write_text("fixture hidden outcome", encoding="utf-8")
    evaluation = Artifact(str(raw), **file_identity(raw), source_kind="fixture", role="independent_validation", bindings={})
    excluded = setup["ref"](setup["journal"].record(evaluation))
    path = setup["root"] / "derived.txt"
    path.write_text("fixture derived summary", encoding="utf-8")
    derived = Artifact(str(path), **file_identity(path), source_kind="fixture", role="diagnostic", bindings={}, parents=(evaluation.id,))
    visible = setup["ref"](setup["journal"].record(derived))
    with pytest.raises(EvidenceError, match="relabelled|evaluation-only"):
        window(case, setup, visible_refs=[visible], excluded_refs=[excluded])


def test_generic_publication_cannot_bypass_early_receipt_check(case, setup):
    payload = window(case, setup)
    wref = save(case, payload)
    deliver(setup, wref)
    outcome = produced(setup, payload)  # Before decision, despite a later result registration.
    dref = decision(case, setup, wref)
    oref = setup["ref"](setup["journal"].record(outcome["artifact"]))
    forged = dict(schema=ew.OUTCOME_RELEASE, task_id="case", branch_id="existing", workset_ref=setup["workset"],
        method_refs=[setup["method"]], supersedes=[], boundary=ew.BOUNDARY, window_ref=wref, decision_ref=dref,
        outcomes=[dict(outcome_id="one", artifact_ref=oref, receipt_ref=outcome["receipt_ref"], slot_digest=digest(payload["expected_outcomes"][0]))],
        window_delivery="host_acknowledged", outcome_delivery="prepared_for_return", consumption="not_attested")
    forged["release_key"] = digest({k: forged[k] for k in ("window_ref", "decision_ref", "outcomes")})
    with pytest.raises(EvidenceError, match="receipt must follow"):
        save(case, forged)


def test_retained_original_freezes_exact_identity_without_claiming_no_prior_host_access(case, setup):
    path = setup["root"] / "retained.json"
    path.write_text('{"fixture": true, "status": "unknown"}', encoding="utf-8")
    original = Artifact(str(path), **file_identity(path), source_kind="fixture", role="independent_validation", bindings={})
    slot = {**setup["slot"], "kind": "retained_artifact", "expected_artifact": original.as_dict()}
    payload = window(case, setup, expected_outcomes=[slot])
    wref = save(case, payload)
    deliver(setup, wref)
    dref = decision(case, setup, wref)
    receipt = setup["artifact"]({"fixture": True, "retained_identity": file_identity(path)})
    substitute = Artifact(original.path, original.sha256, original.bytes, "fixture", "independent_validation", {"renamed": True})
    with pytest.raises(EvidenceError, match="reference substitution"):
        release(setup, wref, dref, dict(outcome_id="one", artifact=substitute, receipt_ref=receipt))
    released = release(setup, wref, dref, dict(outcome_id="one", artifact=original, receipt_ref=receipt))
    assert released["record_id"].startswith("artifact-")
    assert "not sandboxed" in payload["boundary"]


def test_distinct_members_of_one_envelope_keep_distinct_visibility(tmp_path):
    from etalon.data.artifacts import seal
    from etalon.research.evidence import query_evidence, read_evidence
    from test_evidence_query import publish as publish_manifest
    directory = tmp_path / "members"
    directory.mkdir()
    (directory / "input.txt").write_text("fixture visible conditions", encoding="utf-8")
    (directory / "evaluation.txt").write_text("fixture excluded outcome", encoding="utf-8")
    seal(directory, kind="fixture", result={}, infrastructure={})
    manifest = directory / "snapshot.json"
    ws = publish_manifest(tmp_path, [], extra_sources=[dict(source_id="members", kind="data_snapshot",
        location=str(manifest), identity=dict(kind="snapshot_sha256", value=file_identity(manifest)["sha256"]))])
    envelope = query_evidence(ws)["items"][0]["ref"]
    visible = read_evidence(ws, envelope, view="artifact", member="input.txt")["ref"]
    excluded = read_evidence(ws, envelope, view="artifact", member="evaluation.txt")["ref"]
    payload = ew.build_evidence_window(ws, task_id="case", branch_id="existing", workset_ref=None,
        visible_refs=[visible], excluded_refs=[excluded], timing_mode="host_declared", coverage_scope="two explicit fixture members")
    assert payload["visible_refs"] == [visible] and payload["excluded_refs"] == [excluded]


def test_sealed_original_is_not_silently_promoted(case, setup):
    path = setup["root"] / "sealed.json"
    path.write_text('{"fixture":true}', encoding="utf-8")
    artifact = Artifact(str(path), **file_identity(path), source_kind="fixture", role="sealed_holdout", bindings={})
    with pytest.raises(EvidenceError, match="sealed originals remain sealed"):
        ew.outcome_slot(**{k: v for k, v in setup["slot"].items() if k not in {"kind", "expected_artifact"}}, expected_artifact=artifact)


@pytest.mark.parametrize("change,message", [
    ("method_refs", "frozen methods"), ("input_refs", "frozen inputs"), ("protocol_ref", "evaluation contract")])
def test_future_slot_cannot_drop_or_substitute_frozen_execution_contract(case, setup, change, message):
    replacement = [] if change == "method_refs" else [setup["protocol"]] if change == "input_refs" else setup["problem"]
    with pytest.raises(EvidenceError, match=message):
        window(case, setup, expected_outcomes=[{**setup["slot"], change: replacement}])
