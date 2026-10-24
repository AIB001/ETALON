"""Synthetic contract counterexamples, not a scientific validation panel."""
import copy
from pathlib import Path

import pytest

from etalon.research import Artifact, MethodUse
from etalon.research import research_decision as rd
from etalon.research.components import record_json
from etalon.research.composition import node_step, record_evaluation
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.method_revision import journal_ref
from test_research_continuation import case, publish, save
from test_research_composition import evaluation_plan
from test_research_publication import case as publication_case


@pytest.fixture
def prepared(case):
    journal, problem, _, root = case
    ref = lambda rid: journal_ref(journal, rid, source_id="research")
    artifact = lambda value, **kw: ref(record_json(journal, value, root / "artifacts", source_kind="fixture", **kw))
    plan = evaluation_plan(case)
    plan_ref = save(case, plan)
    methods = plan["method_refs"]
    planned = save(case, node_step(task_id="case", question="Fixture discrimination", branch_id="normal",
        node_id="read", state="planned", reason="Existing normal execution", method_refs=methods, input_refs=[problem]))
    arguments = dict(task_id="case", scope="fixture only", branch_id="normal", question="Does the normal read preserve evidence?",
        problem_refs=[problem], structure={"task_family": "reader", "objective_kind": "retention", "subject": "released artifact",
            "intervention": None, "readout": "preserved evidence", "constraints": ["existing normal use"], "unknowns": ["intervention"]},
        attribution={"actor": "fixture host", "basis": "host_judgment", "model_ref": None, "skill_refs": [], "verifier_refs": []},
        options=[{"option_id": "preserve", "kind": "option", "statement": "Existing reader preserves evidence", "claim_refs": []},
                 {"option_id": "unknown", "kind": "unknown", "statement": "Unspecified or combined failure remains possible", "claim_refs": []}],
        predictions=[{"prediction_id": "p1", "action_id": "read", "option_ids": ["preserve"], "observable": "retained count",
            "expected": "All fixture evidence preserved", "distinguishes": "A missing item contradicts preservation", "status": "testable",
            "basis_refs": [problem], "unknown_reason": None}],
        actions=[{"action_id": "read", "description": "Use normal reader", "disposition": "selected", "reason": "Smallest existing discriminator",
            "execution_ref": planned, "prediction_ids": ["p1"]},
            {"action_id": "repeat", "description": "Repeat expensive work", "disposition": "skipped", "reason": "No additional discrimination",
             "execution_ref": None, "prediction_ids": []}], method_refs=methods, evaluation_refs=[plan_ref])
    return dict(journal=journal, root=root, ref=ref, artifact=artifact, args=arguments, plan=plan_ref, planned=planned)


def build(case, prepared, **changes):
    return rd.build_research_decision(publish(case), **{**prepared["args"], **changes})


def store(prepared, content):
    rd.validate_local_links(prepared["journal"], content, "research")
    return prepared["ref"](record_json(prepared["journal"], content, prepared["root"] / "decisions",
        source_kind="fixture", bindings={"schema": content["schema"]}))


def observed(case, prepared, decision_ref, status="supported"):
    p = prepared
    output = p["artifact"]({"fixture": True, "retained": status == "supported"})
    use = p["ref"](p["journal"].record(MethodUse(({"kind": "task", "id": "case", "scope": str(p["journal"].store.path)},),
        tuple({"method_version_ref": r["record_id"]} for r in p["args"]["method_refs"]), reason="Fixture actual normal use")))
    node = save(case, node_step(task_id="case", question="Fixture discrimination", branch_id="normal", node_id="read",
        state="completed", reason="Fixture receipt only", input_refs=[case[1]], output_refs=[output], receipt_refs=[output],
        method_refs=p["args"]["method_refs"], use_refs=[use], supersedes=[p["planned"]]))
    evaluation = record_evaluation(p["journal"], p["plan"], directory=p["root"] / "content", source_id="research",
        source_kind="fixture", result_refs=[output["record_id"]], conclusion="Host fixture judgment", scope="fixture only",
        use_refs=[use], node_refs=[node])["step_ref"]
    args = dict(decision_ref=decision_ref, execution_refs=[node], evaluation_refs=[evaluation],
        judgments=[{"prediction_id": "p1", "status": status, "observation_refs": [output], "evaluation_refs": [evaluation],
                    "reason": "Attributable fixture interpretation"}], disposition="no_change", summary="No scientific benefit claim")
    return args, output, node, evaluation


def test_decision_is_optional_ordinary_artifact_with_exact_readonly_links(case, prepared):
    events = prepared["journal"].store.events()
    decision = build(case, prepared)
    assert decision["scientific_truth"] == "not_certified" and decision["adoption"] == "not_performed"
    assert prepared["journal"].store.events() == events
    saved = store(prepared, decision)
    assert rd.read_research_decision(publish(case), saved) == decision
    assert rd.validate_local_links(prepared["journal"], decision, "research") == decision
    assert prepared["journal"].store.actions() == []


@pytest.mark.parametrize("field,value", [("record_digest", "0" * 64), ("sha256", "0" * 64)])
def test_forged_original_identities_rejected(case, prepared, field, value):
    args = copy.deepcopy(prepared["args"])
    args["problem_refs"][0][field] = value
    if field == "sha256":
        args["problem_refs"][0]["bytes"] = prepared["journal"].get(case[1]["record_id"])["bytes"]
    with pytest.raises(EvidenceError):
        rd.build_research_decision(publish(case), **args)


def test_changed_original_bytes_fail_readback(case, prepared):
    saved = store(prepared, build(case, prepared))
    Path(prepared["journal"].get(case[1]["record_id"])["path"]).write_text("changed fixture")
    with pytest.raises((EvidenceError, ValueError)):
        rd.read_research_decision(publish(case), saved)


@pytest.mark.parametrize("change", ["task", "scope", "input"])
def test_task_scope_and_input_binding_cannot_be_repaired_with_prose(case, prepared, change):
    updates = {}
    if change == "task":
        updates["task_id"] = "other-task"
    elif change == "scope":
        scientific = prepared["artifact"]({"task_id": "case", "scope": "other scope"})
        updates["scientific_inputs"] = [{"ref": scientific, "use": "conditioning", "reason": "It looks relevant"}]
    else:
        updates["problem_refs"] = [prepared["artifact"]({"different": "input"})]
        predictions = copy.deepcopy(prepared["args"]["predictions"])
        predictions[0]["basis_refs"] = []
        updates["predictions"] = predictions
    with pytest.raises(EvidenceError, match="task/scope|inputs"):
        build(case, prepared, **updates)


@pytest.mark.parametrize("lineage", ["direct", "binding", "parent", "envelope"])
def test_evaluation_only_data_cannot_enter_declared_conditioning(case, prepared, lineage):
    p = prepared
    path = p["root"] / "labels.json"
    path.write_text('{"fixture":true,"answer":7}')
    label = p["ref"](p["journal"].record(Artifact(str(path), **file_identity(path), source_kind="fixture",
        role="independent_validation", bindings={})))
    target = label
    if lineage == "binding":
        target = p["artifact"]({"derived": True}, bindings={"evidence_refs": [label]})
    elif lineage == "parent":
        target = p["ref"](p["journal"].record(Artifact(str(path), **file_identity(path), source_kind="fixture",
            role="diagnostic", bindings={}, parents=(label["record_id"],))))
    elif lineage == "envelope":
        target = p["artifact"]({"schema": "etalon-scientific-input/1", "task_id": "case", "scope": "fixture only", "subject": "fixture",
            "sources": [{"id": "sealed", "role": "evaluation_only", "scope": "fixture only", "path": str(path),
                         "locator": "fixture labels", **file_identity(path)}], "observations": [], "interpretations": [], "bindings": []})
    with pytest.raises((EvidenceError, ValueError), match="evaluation-only|unavailable"):
        build(case, p, scientific_inputs=[{"ref": target, "use": "conditioning", "reason": "Declared use"}])


def test_unknown_prediction_and_unrun_action_are_not_success(case, prepared):
    predictions = copy.deepcopy(prepared["args"]["predictions"])
    predictions[0].update(status="unknown", expected=None, distinguishes=None, unknown_reason="No discriminating expectation yet")
    decision = store(prepared, build(case, prepared, predictions=predictions))
    pending = rd.build_decision_update(publish(case), decision_ref=decision, judgments=[{"prediction_id": "p1",
        "status": "not_tested", "observation_refs": [], "evaluation_refs": [], "reason": "Not run"}],
        disposition="defer", summary="Unknown remains unknown")
    assert pending["outcome"] == "pending"
    with pytest.raises(EvidenceError, match="unsupported predictions"):
        rd.validate_content({**pending, "outcome": "supported"})
    args, _, _, _ = observed(case, prepared, decision)
    with pytest.raises(EvidenceError, match="unknown or skipped"):
        rd.build_decision_update(publish(case), **args)
    known = store(prepared, build(case, prepared))
    args["decision_ref"] = known
    args["execution_refs"] = [prepared["planned"]]
    with pytest.raises(EvidenceError, match="executed action"):
        rd.build_decision_update(publish(case), **args)


def test_negative_unchanged_update_retains_exact_counterevidence_and_gaps(case, prepared):
    decision = store(prepared, build(case, prepared, gaps=["Missing independent repeat"]))
    args, negative, _, _ = observed(case, prepared, decision, "contradicted")
    update = rd.build_decision_update(publish(case), **args)
    assert update["disposition"] == "no_change" and update["outcome"] == "unresolved"
    assert negative in update["counter_refs"] and update["gaps"] == ["Missing independent repeat"]
    first = store(prepared, update)
    again = rd.build_decision_update(publish(case), **args, previous_update_ref=first)
    assert again["counter_refs"] == update["counter_refs"] and again["supersedes"] == [first]
    forged = {**again, "counter_refs": []}
    with pytest.raises(EvidenceError, match="counterevidence"):
        rd.validate_links(publish(case), forged)
    forged = {**again, "gaps": [], "outcome": "contradicted"}
    with pytest.raises(EvidenceError, match="prior gaps"):
        rd.validate_links(publish(case), forged)
    assert rd.read_research_decision(publish(case), first) == update


def test_updates_require_exact_immutable_plan_node_and_observation_links(case, prepared):
    decision = store(prepared, build(case, prepared))
    args, output, node, evaluation = observed(case, prepared, decision)
    success = rd.build_decision_update(publish(case), **args)
    assert success["outcome"] == "supported" and success["scientific_truth"] == "not_certified"
    unrelated = prepared["artifact"]({"fixture": True, "retained": True, "unrelated": True})
    forged = copy.deepcopy(args)
    forged["judgments"][0]["observation_refs"] = [unrelated]
    with pytest.raises(EvidenceError, match="exact outputs"):
        rd.build_decision_update(publish(case), **forged)
    other_plan = evaluation_plan(case)
    other_plan["reason"] = "Different immutable plan"
    other_ref = save(case, other_plan)
    changed = build(case, prepared, evaluation_refs=[other_ref])
    forged["decision_ref"] = store(prepared, changed)
    forged["judgments"][0]["observation_refs"] = [output]
    with pytest.raises(EvidenceError, match="exact evaluation plan"):
        rd.build_decision_update(publish(case), **forged)


def test_update_cannot_attach_another_decisions_previous_judgment(case, prepared):
    first = store(prepared, build(case, prepared))
    args, _, _, _ = observed(case, prepared, first, "contradicted")
    previous = store(prepared, rd.build_decision_update(publish(case), **args))
    another = store(prepared, build(case, prepared, question="Different attributable decision"))
    with pytest.raises(EvidenceError, match="different exact decision"):
        rd.build_decision_update(publish(case), **{**args, "decision_ref": another}, previous_update_ref=previous)


def test_nonmechanistic_unknown_and_combined_options_do_not_require_two_hypotheses(case, prepared):
    actions = copy.deepcopy(prepared["args"]["actions"])
    actions[0].update(disposition="deferred", prediction_ids=[])
    decision = build(case, prepared, options=[{"option_id": "combined", "kind": "combined",
        "statement": "Descriptive curation; causal decomposition unknown", "claim_refs": []}], predictions=[], actions=actions)
    saved = store(prepared, decision)
    update = rd.build_decision_update(publish(case), decision_ref=saved, judgments=[], disposition="retain", summary="No new evidence")
    assert update["outcome"] == "pending"


def test_payload_and_reference_closure_are_bounded(case, prepared, monkeypatch):
    with pytest.raises(EvidenceError, match="16 KiB"):
        build(case, prepared, question="x" * 17000)
    monkeypatch.setattr(rd, "MAX_CLOSURE", 1)
    with pytest.raises(EvidenceError, match="bounded budget"):
        build(case, prepared)


def test_external_alias_requires_explicit_verification_workspace(case, prepared):
    decision = build(case, prepared)
    with pytest.raises(EvidenceError, match="explicit verification workspace"):
        rd.validate_local_links(prepared["journal"], decision, "other")
    assert rd.validate_local_links(prepared["journal"], decision, "other", publish(case)) == decision


@pytest.mark.parametrize("error,status", [(0.001, "supported"), (1, "contradicted"), (None, "unresolved")])
def test_frozen_rules_accept_later_observations_but_not_unknown_success(case, prepared, error, status):
    from etalon.research.evaluation_contract import evaluate_contract, record_evaluation_artifact
    from test_evaluation_contract import setup
    contract, _ = setup(case)
    contract.update(scope="fixture only", protocol_ref=case[1], initial_conditions_ref=case[1])
    frozen = prepared["ref"](record_evaluation_artifact(prepared["journal"], contract,
        prepared["root"] / "evaluation", source_kind="fixture"))
    decision = store(prepared, build(case, prepared, evaluation_refs=[frozen]))
    output = prepared["artifact"]({"status": "completed", "protocol": "paired/1", "attempted": 1, "max_error": error})
    node = save(case, node_step(task_id="case", question="Fixture discrimination", branch_id="normal", node_id="read",
        state="completed", reason="New fixture result", input_refs=[case[1]], output_refs=[output], receipt_refs=[output],
        method_refs=prepared["args"]["method_refs"], supersedes=[prepared["planned"]]))
    later = copy.deepcopy(contract)
    later["inputs"][0]["ref"] = output
    report = evaluate_contract(publish(case), later)
    report_ref = prepared["ref"](record_evaluation_artifact(prepared["journal"], report,
        prepared["root"] / "evaluation", source_kind="fixture", workspace=publish(case)))
    args = dict(decision_ref=decision, execution_refs=[node], evaluation_refs=[report_ref], disposition="retain",
        summary="Host interpretation of exact rule replay", judgments=[{"prediction_id": "p1", "status": status,
            "observation_refs": [output], "evaluation_refs": [report_ref], "reason": "Fixture only"}])
    update = rd.build_decision_update(publish(case), **args)
    assert update["outcome"] == status
    assert store(prepared, update)
    if status == "unresolved":
        args["judgments"][0]["status"] = "supported"
        with pytest.raises(EvidenceError, match="verified evaluation status"):
            rd.build_decision_update(publish(case), **args)
    else:
        report["status"] = "criteria_failed" if status == "supported" else "criteria_passed"
        forged = prepared["artifact"](report)
        args["evaluation_refs"] = [forged]
        args["judgments"][0]["evaluation_refs"] = [forged]
        with pytest.raises(EvidenceError, match="verified replay"):
            rd.build_decision_update(publish(case), **args)


def test_local_hash_alias_cannot_relabel_protected_original(case, prepared):
    decision = build(case, prepared)
    original = prepared["journal"].get(case[1]["record_id"])
    prepared["journal"].record(Artifact(**{**original, "role": "independent_validation"}))
    with pytest.raises(EvidenceError, match="evaluation-only"):
        rd.validate_local_links(prepared["journal"], decision, "research")


def test_superseded_decision_preserves_negative_evidence(case, prepared):
    negative = prepared["artifact"]({"fixture": True, "counterexample": "Prior read dropped a value"})
    previous = store(prepared, build(case, prepared, counter_refs=[negative]))
    with pytest.raises(EvidenceError, match="prior counterevidence"):
        build(case, prepared, supersedes=[previous])
    retained = build(case, prepared, supersedes=[previous], counter_refs=[negative])
    assert retained["counter_refs"] == [negative]


def test_superseding_execution_cannot_change_the_selected_action(case, prepared):
    decision = store(prepared, build(case, prepared))
    args, _, original, _ = observed(case, prepared, decision)
    body = rd.read_material(publish(case), original)["content"]
    body["detail"]["node_id"] = "different-action"
    wrong = save(case, body)
    with pytest.raises(EvidenceError, match="changed selected action identity"):
        rd.build_decision_update(publish(case), **{**args, "execution_refs": [wrong]})


def test_raw_method_evaluation_ids_cannot_change_sources(case, prepared):
    import json
    decision = store(prepared, build(case, prepared))
    args, _, _, evaluation = observed(case, prepared, decision)
    content = rd.read_material(publish(case), evaluation)["content"]
    content["detail"]["evaluation_ref"]["source_id"] = "alias"
    swapped = prepared["artifact"](content)
    args["evaluation_refs"] = [swapped]
    args["judgments"][0]["evaluation_refs"] = [swapped]
    workspace = publish(case)
    manifest = json.loads(workspace.read_text(encoding="utf-8"))
    manifest["sources"].append({**manifest["sources"][0], "source_id": "alias"})
    workspace.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvidenceError, match="exact local source"):
        rd.build_decision_update(workspace, **args)


@pytest.mark.parametrize("container", ["object", "array"])
def test_large_json_original_is_fully_inspected_and_late_label_lineage_rejected(case, prepared, container):
    large = prepared["artifact"]({"fixture": True, "padding": "x" * 50000, "released": True})
    decision = build(case, prepared, support_refs=[large])
    assert rd.validate_local_links(prepared["journal"], decision, "research") == decision
    separate = prepared["artifact"]({"fixture": True, "evaluation_only_value": 7})
    original = prepared["journal"].get(separate["record_id"])
    held = prepared["ref"](prepared["journal"].record(Artifact(**{**original, "role": "independent_validation"})))
    wrapper = {"padding": "x" * 50000, "evidence_refs": [held]} if container == "object" else ["x" * 50000, held]
    late = prepared["artifact"](wrapper)
    with pytest.raises(EvidenceError, match="evaluation-only|unavailable"):
        build(case, prepared, support_refs=[late])
    too_large = prepared["artifact"]({"padding": "x" * (1024 * 1024)})
    with pytest.raises(EvidenceError, match="1 MiB"):
        build(case, prepared, support_refs=[too_large])


def test_real_publication_preflight_and_commit_accept_typed_decision_update(publication_case):
    import json
    from etalon.active import CampaignStore
    from etalon.research import ResearchJournal
    from etalon.research.continuation import record_content
    from etalon.research.publication import prepare_publication, resume_publication, _PreflightJournal
    c = publication_case
    journal = ResearchJournal(CampaignStore(c["old"] / "research.sqlite"))
    problem = c["checkpoint"]["branches"][0]["basis_refs"][0]
    workspace = c["old"] / "workspace.json"
    decision = rd.build_research_decision(workspace, task_id="fixture", scope="fixture only", branch_id="existing",
        question="Retain existing evidence?", problem_refs=[problem],
        structure={**{k: None for k in rd.DIMENSIONS}, "unknowns": list(rd.DIMENSIONS), "constraints": []},
        attribution={"actor": "fixture host", "basis": "host_judgment", "model_ref": None, "skill_refs": [], "verifier_refs": []},
        options=[{"option_id": "retain", "kind": "option", "statement": "Retain published evidence", "claim_refs": []}],
        predictions=[], actions=[{"action_id": "retain", "description": "Retain", "disposition": "selected",
            "reason": "No new discriminating observation", "execution_ref": None, "prediction_ids": []}])
    rid = record_content(journal, decision, c["old"] / "content", source_id="prior", source_kind="fixture")
    original = journal_ref(journal, rid, source_id="prior")
    manifest = json.loads(workspace.read_text(encoding="utf-8"))
    manifest["sources"][0]["identity"]["value"] = file_identity(journal.store.path)["sha256"]
    workspace.write_text(json.dumps(manifest), encoding="utf-8")
    update = rd.build_decision_update(workspace, decision_ref=original, judgments=[], disposition="no_change", summary="Still pending")
    # The real publication adapter deliberately has no .store. Its exact local
    # content view must work without constructing a second journal.
    adapter = _PreflightJournal()
    for ref in (problem, original):
        adapter.records[ref["record_id"]] = journal.get(ref["record_id"])
        adapter.paths[ref["record_id"]] = Path(adapter.records[ref["record_id"]]["path"])
    adapter.contents[original["record_id"]] = decision
    assert rd.validate_local_links(adapter, update, "prior") == update
    results = []
    for name, value in (("decision", decision), ("update", update)):
        path = c["old"].parent / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        results.append(dict(name=name, path=str(path), identity=file_identity(path), source_kind="fixture",
            role="diagnostic", bindings={"schema": value["schema"]}))
    request = copy.deepcopy(c["request"])
    request["results"] = results
    request["steps"][0]["content"]["output_refs"] = [{"publication_ref": "decision"}, {"publication_ref": "update"}]
    before = {str(p): file_identity(p) for p in c["old"].rglob("*") if p.is_file()}
    intent = prepare_publication(**request)
    published = resume_publication(intent["intent_path"])
    assert published["status"] == "published"
    assert resume_publication(intent["intent_path"]) == published
    entry = json.loads(c["entry"].read_text(encoding="utf-8"))
    assert rd.read_research_decision(entry["workspace"], original) == decision
    assert {str(p): file_identity(p) for p in c["old"].rglob("*") if p.is_file()} == before
    assert CampaignStore(Path(intent["intent_path"]).parent / "research.sqlite", read_only=True).actions() == []


def test_scientific_input_checks_validation_copy_through_exact_relocation(case, prepared):
    import json
    source_path = prepared["root"] / "relocated-input.json"
    source_path.write_text('{"fixture":true,"input":"released"}', encoding="utf-8")
    foreign = "/historical-linux-producer/clean-input.json"
    envelope = {"schema": "etalon-scientific-input/1", "task_id": "case", "scope": "fixture only", "subject": "fixture",
        "sources": [{"id": "clean", "role": "conditioning", "scope": "fixture only", "path": foreign,
                     "locator": "exact historical source", **file_identity(source_path)}],
        "observations": [], "interpretations": [], "bindings": []}
    scientific = prepared["artifact"](envelope)
    workspace = publish(case)
    manifest = json.loads(workspace.read_text(encoding="utf-8"))
    manifest["location_map"] = [{"source_id": "research", "original": foreign, "location": source_path.name,
                                 **file_identity(source_path)}]
    workspace.write_text(json.dumps(manifest), encoding="utf-8")
    args = {**prepared["args"], "scientific_inputs": [{"ref": scientific, "use": "conditioning", "reason": "Explicit relocated clean source"}]}
    decision = rd.build_research_decision(workspace, **args)
    assert rd.validate_local_links(prepared["journal"], decision, "research", workspace) == decision
    assert rd.read_material(workspace, scientific)["content"] == envelope
    assert rd.read_material(workspace, scientific)["content"]["sources"][0]["path"] == foreign
    with pytest.raises((EvidenceError, ValueError, OSError)):
        rd.validate_local_links(prepared["journal"], decision, "research")
    manifest["location_map"][0]["sha256"] = "0" * 64
    workspace.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvidenceError, match="identity conflicts"):
        rd.build_research_decision(workspace, **args)
