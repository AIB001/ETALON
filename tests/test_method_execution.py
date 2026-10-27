"""Real journal/event/publication integration; synthetic numerical fixture only."""
import copy
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from etalon.active import CampaignStore
from etalon.research import MethodUse, MethodVersion, ResearchJournal
from etalon.research.continuation import read_content
from etalon.research.evidence_refs import file_identity, make_ref
from etalon.research.method_execution import bind_method_execution, collect_method_results
from etalon.runtime import service
from etalon.runtime.store import RuntimeStore
from test_research_publication import case
from test_runtime_methods import method_node, spec, until


@pytest.fixture
def bound(case, tmp_path):
    journal = ResearchJournal(CampaignStore(case["old"] / "research.sqlite"))
    method = MethodVersion("sum", ("domain_skill",), {"fixture": "sum"})
    journal.record(method)
    use = MethodUse(({"kind": "task", "id": "fixture", "scope": str(journal.store.path)},),
        ({"method_version_ref": method.id, "adoption": "partial", "used_parts": ["sum"]},),
        reason="Host selected this numerical fixture")
    journal.record(use)
    # Publish this fixture's sources only after recording the host selection.
    ws_path = case["old"] / "workspace.json"
    ws = json.loads(ws_path.read_text())
    ws["sources"][0]["identity"]["value"] = file_identity(journal.store.path)["sha256"]
    ws_path.write_text(json.dumps(ws))
    entry = case["parent"]
    use_ref = make_ref("prior", use.id, use.as_dict())
    node = bind_method_execution(method_node(tmp_path), entry=entry, branch_id="existing", use_refs=[use_ref])
    return case, node, use_ref


def finish_fixture(tmp_path, node, *, failed=False):
    """Persist a labelled test receipt through RuntimeStore, never claim science."""
    store = RuntimeStore(tmp_path / "runtime", create=True)
    store.create("job", service.plan(spec(node)))
    with store.connection(write=True) as db:
        db.execute("UPDATE nodes SET state='running',parameters=? WHERE job='job'", (json.dumps(node["arguments"]),))
    store.finish_node("job", 0, node["id"], {"fixture": True, "outputs": {"value": 6}},
        {"fixture": True}, {}, basis={}, state="failed" if failed else "verified",
        error="Numerical fixture failure" if failed else None)
    return store


def checkpoint(entry):
    value = json.loads(Path(entry).read_text())
    return value, read_content(value["workspace"], value["checkpoint_ref"])


def test_live_launcher_covers_bootstrap_but_does_not_imply_completion(tmp_path, monkeypatch):
    from etalon.runtime import native
    popen = native.subprocess.Popen
    processes = []
    def delayed(*args, **kwargs):
        child = popen([sys.executable, "-c", "import time; time.sleep(2)"], **kwargs)
        processes.append(child)
        return child
    monkeypatch.setattr(native.subprocess, "Popen", delayed)
    path = tmp_path / "slow-bootstrap"
    status = native.launch_detached_attempt(path, {"request_id": "fixture", "attempt_id": "one"},
        [sys.executable, "-c", "pass"])
    assert status["state"] == "running" and "launcher" in status["live_processes"]
    for child in processes:
        child.wait(timeout=10)
    status = native.recover_attempt(path)
    assert status["state"] == "unknown" and not (path / "receipt.json").exists()


@pytest.mark.parametrize("failed", [False, True])
def test_event_to_research_without_inventing_decision(bound, tmp_path, failed):
    case, node, use = bound
    store = finish_fixture(tmp_path, node, failed=failed)
    before = copy.deepcopy(case["checkpoint"])
    result = collect_method_results(store.workspace, "job")
    entry, cp = checkpoint(case["parent"])
    step = read_content(entry["workspace"], cp["step_refs"][-1])
    assert result["status"] == "caught_up"
    assert step["operation"] == "node"
    assert step["detail"]["state"] == ("failed" if failed else "completed")
    assert step["detail"]["use_refs"] == [use]
    assert step["method_refs"] == node["research"]["method_refs"]
    assert step["accounting_refs"] == step["detail"]["receipt_refs"]
    assert cp["branches"] == before["branches"]
    assert cp["counter_refs"] == before["counter_refs"]
    assert cp["status_guards"] == before["status_guards"]
    identity = file_identity(case["parent"])
    again = collect_method_results(store.workspace, "job")
    assert again["publications"] == [] and file_identity(case["parent"]) == identity


def test_publication_crash_replays_archival_work_only(bound, tmp_path, monkeypatch):
    from etalon.research import publication
    case, node, _ = bound
    store = finish_fixture(tmp_path, node)
    original = publication._stage
    def crash(root, phase, payload):
        original(root, phase, payload)
        if phase == "entry_replaced":
            raise RuntimeError("exit after entry update, before cursor")
    monkeypatch.setattr(publication, "_stage", crash)
    service._publish_research(store, "job")
    assert service.status(store.workspace, "job")["research_feedback"]["status"] == "pending"
    assert store.get("job")["nodes"][0]["state"] == "verified"
    published = file_identity(case["parent"])
    monkeypatch.setattr(publication, "_stage", original)
    # A separate interpreter resumes from only the durable runtime locator.
    script = "from etalon.research.method_execution import collect_method_results; import sys; print(collect_method_results(sys.argv[1], 'job')['status'])"
    done = subprocess.run([sys.executable, "-c", script, str(store.workspace)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "caught_up" in done.stdout
    assert file_identity(case["parent"]) == published
    assert len([e for e in store.get("job")["events"] if e["kind"] == "node_verified"]) == 1


def test_binding_is_not_computation_identity_and_cannot_follow_unreviewed_edit(bound):
    _, node, _ = bound
    bare = {k: v for k, v in node.items() if k != "research"}
    assert service.plan(spec(node))["plan_id"] == service.plan(spec(bare))["plan_id"]
    changed = copy.deepcopy(node)
    changed["arguments"]["inputs"]["factor"] = 2
    with pytest.raises(ValueError, match="updated research binding"):
        service.plan(spec(changed))


def test_published_results_feed_existing_evaluation_with_external_use_links(bound, tmp_path):
    import shutil
    from etalon.research import TaskSplit
    from etalon.research.composition import record_evaluation
    from etalon.research.continuation import STEP, record_content
    from etalon.research.method_execution import execution_evaluation_inputs
    case, node, use = bound
    store = finish_fixture(tmp_path, node)
    collect_method_results(store.workspace, "job")
    entry, cp = checkpoint(case["parent"])
    node_ref = cp["step_refs"][-1]
    # Fixture working journal is separate from the immutable published source.
    writer = tmp_path / "writer.sqlite"
    shutil.copyfile(case["old"] / "research.sqlite", writer)
    journal = ResearchJournal(CampaignStore(writer))
    ref = lambda rid: make_ref("writer", rid, journal.get(rid))
    bound_evidence = execution_evaluation_inputs(journal, entry=case["parent"], node_refs=[node_ref])
    base = node["research"]["method_refs"][0]["record_id"]
    candidate = journal.record(MethodVersion("unrun", ("domain_skill",), {"fixture": "unrun"}))
    evidence = ref(bound_evidence["result_refs"][0])
    split = ref(journal.record(TaskSplit(("fixture",), (), (), {"fixture": "initial"}, "Fixture diagnostic")))
    plan = dict(schema=STEP, task_id="fixture", question="Inspect normal feedback", branch_id="existing",
        operation="evaluation_plan", reason="Fixture comparison remains incomplete", input_refs=[], output_refs=[],
        method_refs=[ref(base), ref(candidate)], accounting_refs=[], source_watermarks=[], supersedes=[],
        detail=dict(target="method", subjects=[ref(base)], comparators=[ref(candidate)], protocol_ref=evidence,
            task_split_ref=split, initial_conditions_ref=evidence, use_refs=[], trigger_refs=[evidence],
            metrics=[dict(name="value", family="regression", unit="count", denominator="observed runs", missing_policy="unknown")]))
    plan_ref = ref(record_content(journal, plan, tmp_path / "content", source_id="writer", source_kind="fixture"))
    result = record_evaluation(journal, plan_ref, directory=tmp_path / "content", source_id="writer",
        source_kind="fixture", conclusion="Baseline observed; candidate unrun, no adoption", scope="fixture", **bound_evidence)
    step = json.loads(Path(journal.get(result["step_ref"]["record_id"])["path"]).read_text())
    assert step["detail"]["node_refs"] == [node_ref] and step["detail"]["use_refs"] == [use]
    assert result["promotion"] == "not_performed"
    assert execution_evaluation_inputs(journal, entry=case["parent"], node_refs=[node_ref]) == bound_evidence


@pytest.mark.parametrize("outcome", ["adopt", "retain", "rollback"])
def test_resolution_activates_exact_method_and_new_context_recovers_it(bound, tmp_path, outcome):
    from etalon.research import Artifact, MethodEvaluation, TaskSplit
    from etalon.research.method_revision import build_method_revision, record_method_revision, resolve_method_revision
    from etalon.research.meta_context import (build_task_workset, describe_resource, index_resources,
        publish_task_workset, read_task_workset)
    from etalon.research.publication import resume_publication
    from test_meta_context import setup, profile, part, review
    case, node, use_ref = bound
    journal = ResearchJournal(CampaignStore(case["old"] / "research.sqlite"))
    ref = lambda rid: make_ref("prior", rid, journal.get(rid))
    base = node["research"]["method_refs"][0]["record_id"]
    candidate = journal.record(MethodVersion("sum-v2", ("domain_skill",), {"fixture": "sum-v2"}, parents=(base,)))
    generator = journal.record(MethodVersion("improve", ("improvement_method",), {"fixture": "improve"}))
    selected = lambda version: journal.record(MethodUse(
        ({"kind": "task", "id": "fixture", "scope": str(journal.store.path)},),
        ({"method_version_ref": version},), reason="Explicit fixture selection"))
    generated_by = selected(generator)
    cost_path = tmp_path / "comparison.json"
    cost_path.write_text('{"fixture":true,"quality":[6,6],"cost":[2,1]}')
    evidence = journal.record(Artifact(str(cost_path), **file_identity(cost_path), source_kind="fixture",
        role="diagnostic", bindings={"kind": "accounting"}))
    split = journal.record(TaskSplit(("fixture",), (), (), {"fixture": "fixed"}, "Fixture diagnostic only"))
    evaluation = journal.record(MethodEvaluation((candidate,), (base,), evidence, split, evidence,
        (evidence,), (evidence,), "diagnostic", "Fixture result", "fixture"))
    revision = build_method_revision(problem_refs=[ref(evidence)], target_kind="S", base_method_ref=ref(base),
        producer_method_ref=ref(generator), producer_use_ref=ref(generated_by), candidate_version_refs=[ref(candidate)],
        change_summary="Fixture arithmetic", mechanism_hypothesis="Fixture only", scope="fixture")
    revision_id = record_method_revision(journal, revision, tmp_path / "revision", source_id="prior", source_kind="fixture")
    resolution = resolve_method_revision(journal, ref(revision_id), ref(evaluation), outcome=outcome,
        task_id="fixture", question="Choose fixture method", scope="fixture", reason="Host decision",
        directory=tmp_path / "revision", source_id="prior", source_kind="fixture", candidate_ref=ref(candidate),
        prior_use_ref=ref(selected(candidate)) if outcome == "rollback" else None,
        counterevidence_refs=[ref(evidence)] if outcome == "rollback" else [])
    ws_path = case["old"] / "workspace.json"
    ws = json.loads(ws_path.read_text())
    ws["sources"][0]["identity"]["value"] = file_identity(journal.store.path)["sha256"]
    ws_path.write_text(json.dumps(ws))
    workspace, resources = setup(case)
    resources[1] = describe_resource(resource_id="geometry", kind="method", content_ref=resolution["selected_method_ref"],
        summary="Chosen fixture method", parts=[part()])
    p = profile()
    idx = index_resources(resources, workspace=workspace)
    rv = review(p, idx, {("base", "base"), ("geometry", "base")})
    work = build_task_workset(p, idx, rv, template={"resource_id": "base", "part_id": "base"})
    intent = publish_task_workset(case["parent"], work, operation_id="choose", directory=tmp_path / "meta",
        expected_entry_identity=file_identity(case["parent"]), method_resolution=resolution)
    resume_publication(intent["intent_path"])
    later = method_node(tmp_path, "later")
    later["arguments"]["method_id"] = resolution["selected_method_ref"]["record_id"]
    new_node = bind_method_execution(later, entry=case["parent"], branch_id="existing")
    assert new_node["research"]["use_refs"] == [resolution["use_ref"]]
    assert new_node["research"]["method_refs"] == [resolution["selected_method_ref"]]
    assert resolution["default_changed"] is False
    entry, cp = checkpoint(case["parent"])
    assert read_task_workset(entry["workspace"], entry["checkpoint_ref"])["status"] == "active"
    assert cp["status_guards"] == case["checkpoint"]["status_guards"]
    identity = file_identity(case["parent"])
    replay = publish_task_workset(case["parent"], work, operation_id="choose", directory=tmp_path / "meta",
        method_resolution=resolution)
    resume_publication(replay["intent_path"])
    assert file_identity(case["parent"]) == identity
    with pytest.raises(ValueError, match="differs from the active method"):
        bind_method_execution(method_node(tmp_path, "wrong"), entry=case["parent"], branch_id="existing")
    with pytest.raises(ValueError, match="different workset"):
        publish_task_workset(case["parent"], work, operation_id="choose", directory=tmp_path / "meta",
            method_resolution={**resolution, "outcome": "retain" if outcome == "adopt" else "adopt"})


@pytest.mark.skipif(os.name != "posix", reason="native runtime requires Linux /proc")
def test_native_method_publishes_and_fresh_process_only_resumes(bound, tmp_path):
    from etalon.runtime.api import prepare_action, submit_action, read_action_result
    case, node, use = bound
    request = dict(schema="etalon-action/1", request_id="native", operation=node["operation"],
        arguments=node["arguments"], limits=node["resources"], research=node["research"], max_seconds=60)
    prepared = prepare_action(request)
    handle = submit_action(request, workspace=tmp_path / "native", plan_id=prepared["plan_id"])
    try:
        until(lambda: service.status(handle["workspace"], "native").get("research_feedback", {}).get("status") == "caught_up"
              and service.status(handle["workspace"], "native")["state"] == "succeeded")
    except AssertionError:
        state = service.status(handle["workspace"], "native")
        logs = [p.read_text() for p in (Path(handle["workspace"]) / "jobs" / "native").glob("worker-*.log")]
        pytest.fail(json.dumps({"state": state, "logs": logs}))
    result = read_action_result(handle)
    assert result["outputs"][0]["result"]["outputs"]["value"] == 6
    entry, cp = checkpoint(case["parent"])
    assert read_content(entry["workspace"], cp["step_refs"][-1])["detail"]["use_refs"] == [use]
    prior = file_identity(case["parent"])
    again = submit_action(request, workspace=handle["workspace"], plan_id=prepared["plan_id"])
    assert again["job_id"] == handle["job_id"] and file_identity(case["parent"]) == prior
