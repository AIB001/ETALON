"""Resource attribution contracts, not evidence of scientific improvement."""
import copy
import json

import pytest

from etalon.research import MethodUse
from etalon.research.components import record_json
from etalon.research.composition import record_evaluation
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.improvement import build_revision_context
from etalon.research.meta_context import (
    WORKSET, _dependencies, build_task_workset, describe_resource, index_resources,
    make_task_profile, prepare_adaptation_context, review_adaptation,
)
from etalon.research.method_links import method_links
from etalon.research.method_revision import journal_ref
from etalon.research.resource_usage import (
    build_resource_usage, read_resource_usage, record_resource_usage,
)
from test_research_continuation import case, checkpoint, publish, save, step
from test_research_composition import evaluation_plan, node, ref


def artifact(case, content, bindings=None):
    j, _, _, root = case
    rid = record_json(j, content, root / "json", source_kind="fixture", bindings=bindings)
    return journal_ref(j, rid, source_id="research")


def chain(case, *, task="case", target="SND1", actual_target="SND1"):
    j, evidence, _, root = case
    plan = evaluation_plan(case)
    method = plan["detail"]["subjects"][0]
    p = make_task_profile(task_id=task, question="Attribution fixture", conditions={"target": actual_target},
                          critical_conditions=["target"], available_stages=["input"])
    def part(name, conditions, irrelevant=()):
        return dict(part_id=name, conditions=conditions, requires=[], irrelevant=list(irrelevant),
                    available_at="input", dependencies=[])
    resources = [describe_resource(resource_id="base", kind="template", content_ref=evidence,
        summary="Domain-neutral evidence template", parts=[part("base", {}, ["target"])]),
        describe_resource(resource_id="method", kind="method", content_ref=method, summary="Scoped method",
            parts=[part("identity-check", {"target": target})])]
    idx = index_resources(resources, workspace=publish(case))
    decisions = [dict(resource_id=c["resource_id"], part_id=c["part_id"],
        disposition="reuse" if c["applicability"] == "match" else "exclude",
        reason="Explicit condition match", reconsider_when="New evidence")
        for c in prepare_adaptation_context(p, idx)["candidates"]]
    review = review_adaptation(p, idx, decisions=decisions, reviewer="fixture", rationale="Scoped test")
    workset = build_task_workset(p, idx, review, template={"resource_id": "base", "part_id": "base"})
    wr = artifact(case, workset, dict(schema=WORKSET, task_id=task, evidence_refs=_dependencies(workset)))
    use = ref(case, MethodUse(({"kind": "task", "id": "case", "scope": str(j.store.path)},),
        ({"method_version_ref": method["record_id"], "adoption": "partial", "used_parts": ["identity-check"]},),
        host_conditions={"task_workset_ref": wr}, reason="Host reports this part only"))
    nr = save(case, node(case, method_refs=[method], use_refs=[use]))
    pr = save(case, plan)
    evaluated = record_evaluation(j, pr, directory=root / "content", source_id="research", source_kind="fixture",
        result_refs=[evidence["record_id"]], conclusion="Fixture comparison; benefit unknown", scope="fixture",
        use_refs=[use], node_refs=[nr])
    decision = step(evidence)
    decision["detail"]["basis_refs"] = [evaluated["evaluation_ref"]]
    selected = save(case, decision)
    payload = build_resource_usage(task_id=task, workset_ref=wr, resource_id="method", part_id="identity-check",
        method_ref=method, use_ref=use, node_ref=nr, evaluation_ref=evaluated["step_ref"], selection_ref=selected,
        provenance=dict(mode="contemporaneous", note="Synthetic attribution fixture", source_refs=[evidence]))
    return payload


def record(case, payload, **kwargs):
    j, _, _, root = case
    return record_resource_usage(j, payload, root / "usage", source_id="research", source_kind="fixture", **kwargs)


def test_full_chain_reads_and_improvement_reuses_artifact_without_claiming_consumption(case):
    payload = chain(case)
    rid = record(case, payload)
    events = case[0].store.events()
    assert record(case, payload) == rid and case[0].store.events() == events
    ur = journal_ref(case[0], rid, source_id="research")
    ws = publish(case)
    before = file_identity(case[0].store.path)
    result = read_resource_usage(ws, ur)
    assert result["chain_complete"] and result["selected"]
    assert result["activation"] == "unknown"
    assert result["part_attribution"] == "explicit"
    assert result["method_use_basis"] == "host_reported"
    assert result["causal_benefit"] == "not_established"
    assert all(v["status"] == "unknown" for v in result["observations"].values())
    assert result["node_state"] == "reused" and result["selection_choice"] == "reuse"
    context = build_revision_context(ws, task_id="case", scope="fixture", conditions={"target": "SND1"},
        target_ref=payload["method_ref"], problem_refs=[ur])
    assert context["status"] == "ready_for_revision"
    assert context["observations"][-1]["schema"] in {"etalon-method-version/1", "etalon-resource-usage/1"}
    links = method_links(case[0].get(rid), "research", {})
    assert (payload["use_ref"], "declared_attributed_use") in links
    assert file_identity(case[0].store.path) == before and not case[0].store.actions()


def test_host_reports_require_exact_workset_parts_and_still_are_not_telemetry(case):
    payload = chain(case)
    for phase in payload["observations"]:
        payload["observations"][phase] = dict(status="host_reported", note="Explicit host report, not tool telemetry",
            evidence_refs=[payload["use_ref"], payload["node_ref"]])
    rid = record(case, payload)
    out = read_resource_usage(publish(case), journal_ref(case[0], rid, source_id="research"))
    assert all(v["status"] == "host_reported" for v in out["observations"].values())
    payload["observations"]["consumed"]["status"] = "tool_observed"
    with pytest.raises(EvidenceError, match="telemetry"):
        record(case, payload)


@pytest.mark.parametrize("mutation,match", [
    (lambda p: p.update(task_id="other"), "another task"),
    (lambda p: p.update(part_id="missing"), "outside"),
    (lambda p: p.update(method_ref=p["use_ref"]), "MethodVersion"),
    (lambda p: p.update(node_ref=p["selection_ref"]), "wrong operation"),
    (lambda p: p.update(evaluation_ref=p["node_ref"]), "wrong operation"),
    (lambda p: p["use_ref"].update(source_id="other"), "external references"),
    (lambda p: p["observations"]["executed"].update(status="host_reported"), "references"),
])
def test_false_joins_rejected_without_writes(case, mutation, match):
    payload = chain(case)
    mutation(payload)
    before = case[0].store.events()
    with pytest.raises(EvidenceError, match=match):
        record(case, payload)
    assert case[0].store.events() == before


def test_retrospective_and_excluded_parts_do_not_gain_use_or_activation(case):
    payload = chain(case, actual_target="PoseBusters")
    payload["provenance"]["mode"] = "retrospective"
    rid = record(case, payload)
    result = read_resource_usage(publish(case), journal_ref(case[0], rid, source_id="research"))
    assert not result["selected"] and result["applicability"] == "conflict"
    assert result["chain_complete"] and result["observations"]["executed"]["status"] == "unknown"
    payload["observations"]["consumed"].update(status="host_reported", evidence_refs=[payload["use_ref"]])
    with pytest.raises(EvidenceError, match="backfill"):
        record(case, payload)
    payload["provenance"]["mode"] = "contemporaneous"
    with pytest.raises(EvidenceError, match="unselected"):
        record(case, payload)


def test_evaluation_and_decision_need_direct_attribution(case):
    payload = chain(case)
    from etalon.research.improvement import _local
    content = _local(case[0], payload["evaluation_ref"], "research")
    content["detail"].pop("use_refs")
    content["detail"].pop("node_refs")
    payload["evaluation_ref"] = save(case, content)
    with pytest.raises(EvidenceError, match="explicitly attribute"):
        record(case, payload)
    payload = chain(case)
    payload["selection_ref"] = save(case, step(case[1]))
    with pytest.raises(EvidenceError, match="does not cite"):
        record(case, payload)


def test_reader_rejects_changed_bytes_and_missing_dependency_bindings(case):
    payload = chain(case)
    rid = record(case, payload)
    raw = case[0].get(rid)
    forged = artifact(case, payload, {"schema": payload["schema"], "task_id": "case"})
    with pytest.raises(EvidenceError, match="complete dependencies"):
        read_resource_usage(publish(case), forged)
    from pathlib import Path
    Path(raw["path"]).write_text(json.dumps({**payload, "task_id": "other"}), encoding="utf-8")
    with pytest.raises((EvidenceError, ValueError)):
        read_resource_usage(publish(case), journal_ref(case[0], rid, source_id="research"))


def test_arbitrary_graph_metadata_cannot_claim_verified_use(case):
    evidence = case[1]
    value = {"bindings": {"schema": "etalon-resource-usage/1", "evidence_refs": [evidence],
        "resource_usage_links": [{"ref": evidence, "relation": "verified_execution"},
                                 {"ref": evidence, "relation": "declared_attributed_use"}]}}
    assert method_links(value, "research", {}) == [(evidence, "declared_attributed_use")]
    value["bindings"]["evidence_refs"] = []
    assert method_links(value, "research", {}) == []
    value["bindings"]["resource_usage_links"] = None
    assert method_links(value, "research", {}) == []


def test_json_artifact_cannot_impersonate_methoduse(case):
    payload = chain(case)
    payload["use_ref"] = artifact(case, case[0].get(payload["use_ref"]["record_id"]))
    with pytest.raises(EvidenceError, match="MethodUse"):
        record(case, payload)


def test_selection_via_exact_experience_preserves_task_and_method_scope(case):
    payload = chain(case)
    from etalon.research.improvement import _local
    evaluation = _local(case[0], payload["evaluation_ref"], "research")["detail"]["evaluation_ref"]
    experience = dict(schema="etalon-method-experience/1", task_id="case", scope="fixture", conditions={"target": "SND1"},
        method_refs=[payload["method_ref"]], supersedes=[], patch_ref=case[1], evaluation_ref=evaluation,
        resolution_ref=payload["selection_ref"], outcome="retain", lesson="No improvement", invalid_when="Scope changes",
        support_refs=[case[1]], counter_refs=[], capability_id="molquarry.data")
    er = artifact(case, experience)
    choice = step(case[1]); choice["detail"]["basis_refs"] = [er]
    payload["selection_ref"] = save(case, choice)
    rid = record(case, payload)
    result = read_resource_usage(publish(case), journal_ref(case[0], rid, source_id="research"))
    assert result["selection_via_experience_refs"] == [er]
    experience["task_id"] = "another"
    choice["detail"]["basis_refs"] = [artifact(case, experience)]
    payload["selection_ref"] = save(case, choice)
    with pytest.raises(EvidenceError, match="does not cite"):
        record(case, payload)


def test_supplied_checkpoint_checks_activation_instead_of_guessing_latest(case):
    payload = chain(case)
    from etalon.research.improvement import _local
    workset = _local(case[0], payload["workset_ref"], "research")
    wr = artifact(case, workset, dict(schema=WORKSET, meta_activation=WORKSET,
        task_id="case", evidence_refs=_dependencies(workset)))
    activation = save(case, step(case[1], output_refs=[wr]))
    cp = save(case, checkpoint(case, activation))
    payload.update(workset_ref=wr, checkpoint_ref=cp)
    rid = record(case, payload, workspace=publish(case))
    result = read_resource_usage(publish(case), journal_ref(case[0], rid, source_id="research"))
    assert result["activation"] == "verified_at_supplied_checkpoint"
    payload["checkpoint_ref"] = save(case, checkpoint(case, save(case, step(case[1]))))
    with pytest.raises(EvidenceError, match="not active"):
        record(case, payload, workspace=publish(case))
