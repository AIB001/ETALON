"""Explicit card fast path preserves continuation/counter/original contracts."""
from pathlib import Path

import pytest

from etalon.research.evidence_refs import EvidenceError
from etalon.research.research_context import build_research_context
from etalon.research.graph_memory import build_research_graph
from etalon.research.continuation_card import _inventory, continuation_card
from etalon.research.entry import make_read_context, continuation_status
from etalon.research.read_scope import verified_read
from test_research_graph_memory import graph_case
from test_declared_relations import nested_counter
from test_research_publication import case


def test_explicit_card_does_not_construct_display_graph_but_summary_still_does(graph_case, monkeypatch):
    import etalon.research.research_context as context
    calls = []
    def graph(*args, **kwargs):
        calls.append(True)
        return build_research_graph(*args, **kwargs)
    monkeypatch.setattr(context, "build_research_graph", graph)
    card = build_research_context(graph_case["entry"], view="card")
    assert not calls
    assert card["counterevidence"]["reference_coverage_complete"]
    assert not card["counterevidence"]["coverage_complete"]
    assert not card["counterevidence"]["graph_counter_context_complete"]
    build_research_context(graph_case["entry"], view="summary")
    assert calls == [True]


def test_direct_card_matches_old_graph_first_authority_refs_and_counter_inventory(graph_case):
    from etalon.research.research_context import _publication_context
    from etalon.research.meta_context import workset_summary
    c = graph_case
    nested_counter(c)
    with verified_read():
        graph = build_research_graph(c["workspace"], checkpoint_ref=c["checkpoint"], max_nodes=128, max_bytes=131072)
        pinned = make_read_context(c["workspace"], c["checkpoint"], entry_path=str(c["entry"]))
        handoff = {"read_context": pinned, "entry_status": continuation_status(pinned),
                   "task_workset": workset_summary(c["workspace"], c["checkpoint"])}
        pubs = _publication_context(c["entry"])
        inventory = _inventory(c["workspace"], c["checkpoint"], policy=None, purpose="research",
                               publications=pubs, handoff=handoff, graph=graph,
                               neutral_workset=handoff["task_workset"])
        old = continuation_card(inventory, c["checkpoint"], handoff, publications=pubs, purpose="research")
        new = build_research_context(c["entry"], view="card")
    for key in ("checkpoint_ref", "read_context", "entry_status", "continuation", "authority", "branches",
                "checkpoint_progress", "publications", "source_watermarks", "watermark", "source_issues",
                "manifest", "resume_frontier", "execution_permission", "automatic_retry", "task_workset"):
        assert old[key] == new[key], key
    for key in ("reference_coverage_complete", "endpoint_count", "relationship_count", "status",
                "original_bodies_read_by_host", "declared_counter_count"):
        assert old["counterevidence"][key] == new["counterevidence"][key], key


@pytest.mark.parametrize("original", ["evidence", "counter", "claim", "decision", "checkpoint"])
def test_direct_card_still_checks_every_checkpoint_original(graph_case, original):
    c = graph_case
    path = Path(c["journal"].get(c[original]["record_id"])["path"])
    path.write_bytes(path.read_bytes() + b"X")
    with pytest.raises(EvidenceError, match="identity|hash|unavailable|projection"):
        build_research_context(c["entry"], view="card")


@pytest.mark.parametrize("overrides", [[], {"id": "wrong"}, {1: "Deep"}])
def test_card_preserves_home_override_validation(graph_case, overrides):
    with pytest.raises(EvidenceError, match="home overrides"):
        build_research_context(graph_case["entry"], view="card", home_overrides=overrides)


def test_uncheckpointed_progress_cannot_hide_corrupt_raw_input_after_skipping_graph(graph_case):
    c = graph_case
    orphan = c["save"]({**c["step"], "reason": "uncheckpointed fixture progress", "input_refs": [c["unrelated"]]})
    c["publish"]()
    before = build_research_context(c["entry"], view="card")
    assert "uncheckpointed_task_step_requires_review" in before["continuation"]["issue_codes"]
    assert before["checkpoint_ref"] == c["checkpoint"]
    Path(c["journal"].get(c["unrelated"]["record_id"])["path"]).write_text("tampered")
    with pytest.raises(EvidenceError, match="identity|hash|unavailable"):
        build_research_context(c["entry"], view="card")


def test_real_published_workset_card_preserves_activation_versions_for_hook_transition(case, tmp_path):
    from test_meta_context import workset
    from etalon.research.meta_context import publish_task_workset
    from etalon.research.publication import resume_publication
    from etalon.research_hooks import _delivery_payload
    from etalon.research.evidence_refs import file_identity
    selected = workset(case)
    intent = publish_task_workset(case["entry"], selected, parent_entry=case["parent"],
                                 operation_id="card-selected", directory=tmp_path / "selected")
    resume_publication(intent["intent_path"])
    first = build_research_context(case["entry"], view="card")
    summary = build_research_context(case["entry"], view="summary")
    assert first["task_workset"]["enabled_versions"] == summary["task_workset"]["enabled_versions"]
    assert first["task_workset"]["enabled"] == selected["enabled"]
    second_workset = workset(case, target="other", chosen={("base", "base"), ("geometry", "parser")},
                            parent=first["task_workset"]["ref"])
    intent = publish_task_workset(case["entry"], second_workset, operation_id="card-switch",
                                 directory=tmp_path / "switch", expected_entry_identity=file_identity(case["entry"]))
    resume_publication(intent["intent_path"])
    second = build_research_context(case["entry"], view="card")
    config = {"policy": None, "max_bytes": 16384, "conditions": None}
    payload = _delivery_payload(second, {"usable": True, "config": config, "context": first,
                                         "receipt_path": "synthetic-first-receipt"},
                                event_name="PreToolUse", entry=str(case["entry"]), config=config)
    transition = payload["delivery"]["workset_transition"]
    assert payload["delivery"]["mode"] == "baseline"
    assert [part["part_id"] for part in transition["deactivated"]] == ["fixed-core"]
    assert all(part["resource_digest"] for part in transition["deactivated"])
    assert not transition["activated"] and transition["context_erasure"] is False
    changed = build_research_context(case["entry"], view="card", conditions={"target": "unexpected"})
    assert changed["task_workset"]["status"] == "review_required"
    assert changed["task_workset"]["enabled_versions"] == []
    assert changed["task_workset"]["enabled"] == []
