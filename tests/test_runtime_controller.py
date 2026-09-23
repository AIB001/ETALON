"""A model selects permitted work; only verified state can establish completion."""

from __future__ import annotations

import copy
import json

import pytest

from etalon.judgment.advisor import AdvisorError, Scripted
from etalon.runtime.controller import ControllerError, decide, observation


def node(key, *, state="waiting", dependencies=(), result=None, arguments=None):
    return {"id": key, "definition": {"operation": "data.query", "depends_on": list(dependencies),
                                     "arguments": arguments or {}},
            "state": state, "result": result}


def observed(nodes, sequence=1):
    return observation({"objective": "Prepare and verify a bounded molecular library."}, nodes, sequence)


def proposal(state, key, **extra):
    return {"observation_id": state["observation_id"], "node_id": key,
            "reason": "Select a permitted next step.", **extra}


def test_ready_graph_preserves_dependencies_and_only_unblocks_verified_results():
    nodes = [node("source", state="verified", result={"snapshot": "/tmp/data/source", "snapshot_id": "abc"}),
             node("prepare", dependencies=("source",)),
             node("screen", dependencies=("prepare",)), node("other", state="failed")]
    before = copy.deepcopy(nodes)
    state = observed(nodes)
    assert nodes == before
    assert [row["id"] for row in state["ready"]] == ["prepare"]
    assert [row["state"] for row in state["nodes"]] == ["verified", "waiting", "waiting", "failed"]
    assert state["nodes"][2]["depends_on"] == ["prepare"]
    evidence = state["verified_results"][0]
    assert evidence["node_id"] == "source" and len(evidence["result_sha256"]) == 64
    assert {row["field"] for row in evidence["references"]} == {"snapshot", "snapshot_id"}
    assert state["all_verified"] is False
    assert decide(state)["node_id"] == "prepare"


@pytest.mark.parametrize("dependency_state", ["waiting", "running", "failed", "reconciliation_required"])
def test_failed_or_unresolved_dependency_cannot_be_selected(dependency_state):
    state = observed([node("source", state=dependency_state), node("prepare", dependencies=("source",))])
    with pytest.raises(ControllerError, match="not ready"):
        decide(state, proposal=proposal(state, "prepare"))
    assert state["all_verified"] is False


def test_ordered_controller_finishes_only_after_every_node_is_verified():
    state = observed([node("first"), node("second")])
    assert decide(state)["node_id"] == "first"
    assert decide(state)["source"] == "ordered"
    state = observed([node("first", state="verified"), node("second", state="verified")])
    assert decide(state)["node_id"] == "finish"
    assert decide(state, proposal=proposal(state, "finish"))["source"] == "external"


def test_empty_or_unresolved_work_pauses_without_claiming_success():
    for nodes in ([], [node("first", state="running")], [node("first", state="failed")]):
        state = observed(nodes)
        assert decide(state)["node_id"] == "pause"
        assert state["all_verified"] is False
    state = observed([node("first")])
    assert decide(state, proposal=proposal(state, "pause"))["node_id"] == "pause"


def test_scripted_transport_selects_ready_work_through_one_exact_call():
    state = observed([node("first"), node("second")])
    transport = Scripted({state["observation_id"]: json.dumps(proposal(state, "second"))})
    result = decide(state, transport=transport)
    assert result["node_id"] == "second" and result["source"] == "scripted"
    assert len(transport.asked) == 1
    assert "untrusted data" in transport.asked[0]


def test_transport_errors_are_not_retried_or_swallowed():
    class Unavailable:
        name = "unavailable"
        calls = 0

        def ask(self, prompt):
            self.calls += 1
            raise AdvisorError("unavailable", retryable=True)

    transport = Unavailable()
    with pytest.raises(AdvisorError, match="unavailable"):
        decide(observed([node("first")]), transport=transport)
    assert transport.calls == 1


@pytest.mark.parametrize("key", ["finish", "unknown"])
def test_model_cannot_finish_unverified_work_or_invent_a_node(key):
    state = observed([node("first")])
    transport = Scripted({state["observation_id"]: json.dumps(proposal(state, key))})
    with pytest.raises(ControllerError):
        decide(state, transport=transport)


@pytest.mark.parametrize("extra", [{"score": 1.0}, {"source": "person"}, {"approved": True}])
def test_external_proposal_cannot_add_fields_or_impersonate_a_source(extra):
    state = observed([node("first")])
    with pytest.raises(ControllerError, match="only"):
        decide(state, proposal=proposal(state, "first", **extra))


@pytest.mark.parametrize("raw", [
    '{"observation_id":"x","node_id":"first","node_id":"finish","reason":"x"}',
    '{"observation_id":"x","node_id":"first","reason":NaN}',
    '{"observation_id":"x","node_id":"first","reason":1e999}',
    '```json\n{"observation_id":"x","node_id":"first","reason":"x"}\n```',
    '{"observation_id":"x","node_id":"first","reason":"x","extra":true}',
    '[]',
])
def test_malformed_model_json_is_refused_without_repair(raw):
    state = observed([node("first")])
    transport = Scripted({state["observation_id"]: raw})
    with pytest.raises(ControllerError):
        decide(state, transport=transport)
    assert len(transport.asked) == 1


@pytest.mark.parametrize("field,value", [("reason", True), ("reason", " "), ("node_id", 1),
                                         ("reason", float("nan")), ("reason", "a" * 2049)])
def test_external_proposal_types_are_never_coerced(field, value):
    state = observed([node("first")])
    offered = proposal(state, "first")
    offered[field] = value
    with pytest.raises(ControllerError):
        decide(state, proposal=offered)


def test_stale_and_mutated_observations_are_rejected_before_transport_calls():
    prior = observed([node("first")], sequence=1)
    state = observed([node("first")], sequence=2)
    with pytest.raises(ControllerError, match="stale"):
        decide(state, proposal=proposal(prior, "first"))
    state["all_verified"] = True
    transport = Scripted({"anything": "{}"})
    with pytest.raises(ControllerError, match="changed"):
        decide(state, transport=transport)
    assert not transport.asked


def test_context_previews_are_bounded_but_hidden_changes_invalidate_decisions():
    arguments = {"records": "a" * 20_000}
    nodes = [node("first", state="verified", result={"records": "b" * 20_000}),
             node("second", dependencies=("first",), arguments=arguments)]
    first = observed(nodes)
    assert len(first["ready"][0]["arguments_summary"]) <= 768
    assert len(first["verified_results"][0]["summary"]) <= 384
    assert first["nodes"][1]["depends_on"] == ["first"]
    nodes[1]["definition"]["arguments"]["records"] += "hidden change"
    second = observed(nodes)
    assert first["ready"] == second["ready"]
    assert first["observation_id"] != second["observation_id"]
    with pytest.raises(ControllerError, match="stale"):
        decide(second, proposal=proposal(first, "second"))


@pytest.mark.parametrize("nodes", [
    [node("duplicate"), node("duplicate")], [node("finish")], [node("pause")],
    [node("first", dependencies=("unknown",))], [node("first", dependencies=("first",))],
    [node("first", state="ok")],
])
def test_invalid_graph_state_is_refused(nodes):
    with pytest.raises(ControllerError):
        observed(nodes)


def test_oversized_graph_is_refused_instead_of_hiding_dependency_ids():
    ids = [f"node_{index:03d}_" + "x" * 70 for index in range(100)]
    nodes = [node(key, dependencies=ids[:index]) for index, key in enumerate(ids)]
    with pytest.raises(ControllerError, match="context"):
        observed(nodes)


def test_cannot_supply_two_decision_sources():
    state = observed([node("first")])
    with pytest.raises(ControllerError, match="either"):
        decide(state, transport=Scripted({}), proposal=proposal(state, "first"))
