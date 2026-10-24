"""Synthetic engineering fixtures; no independent science or method benefit."""
import copy
import json

import pytest

from etalon.research import MethodVersion
from etalon.research import candidate_brief as cb
from etalon.research import improvement as imp
from etalon.research.components import record_json
from etalon.research.composition import node_step
from etalon.research.evidence_refs import EvidenceError
from etalon.research.method_revision import journal_ref
from test_research_continuation import case, publish, save


@pytest.fixture
def prepared(case):
    journal, problem, _, root = case
    ref = lambda rid: journal_ref(journal, rid, source_id="research")
    artifact = lambda value: ref(record_json(journal, value, root / "artifacts", source_kind="fixture"))
    before = artifact({"read": "typed", "preserve_negative": True})
    after = artifact({"read": "generic", "preserve_negative": True})
    base = ref(journal.record(MethodVersion("fixture base", ("usage_method",), {"artifact_ref": before["record_id"]})))

    def episode(*, method=base, scope="fixture readers", evidence_refs=()):
        context = imp.build_revision_context(publish(case), task_id="fixture-case", scope=scope,
            conditions={"endpoint": "released-evidence/1"}, target_ref=method,
            problem_refs=[problem], evidence_refs=evidence_refs)
        context_ref = save(case, context)
        opened = imp.register_opportunity(journal, context_ref, root / "content",
                                         source_id="research", source_kind="fixture")
        normal = save(case, node_step(task_id="fixture-case", question="Read the original evidence?",
            branch_id="ordinary-use", node_id="read", state="planned", reason="Fixture existing normal execution",
            input_refs=[problem], method_refs=[method]))
        return context_ref, opened["opportunity_ref"], normal

    context, opportunity, normal = episode()
    arguments = dict(context_ref=context, opportunity_ref=opportunity, base_method_ref=base,
        title="Fixture generic reader proposal", rationale="Generic records need their original reader",
        target_part="/read", change={"kind": "method_content", "before_ref": before, "after_ref": after},
        expected_effect={"metric": "read-errors", "unit": "count", "direction": "decrease",
                         "hypothesis": "Verified generic reads avoid schema routing errors"},
        frozen_invariants=["Preserve original record identities", "Preserve negative observations"],
        quality_criteria=["Original bytes verify", "No unsupported measurement claims"],
        normal_execution={"execution_ref": normal, "description": "Use the existing normal reader node",
                          "discriminator": "Compare read failures on the same explicit originals",
                          "metric": "read-errors", "unit": "count", "success_criterion": "Fewer read failures"},
        applicability={"applies_when": ["Released original artifacts are available"],
                       "does_not_apply_when": ["Evidence is sealed or unavailable"]},
        support_refs=[problem])
    return dict(journal=journal, root=root, ref=ref, artifact=artifact, before=before, after=after,
                base=base, episode=episode, args=arguments)


def build(case, prepared, **updates):
    return cb.build_candidate_brief(publish(case), **{**prepared["args"], **updates})


def store(prepared, payload):
    # Exercise semantic publication check even before root's shared dispatch wiring.
    cb.validate_local_links(prepared["journal"], payload, "research")
    return prepared["ref"](record_json(prepared["journal"], payload, prepared["root"] / "briefs",
        source_kind="fixture", bindings={"schema": cb.CANDIDATE_BRIEF}))


def test_complete_proposal_is_source_backed_without_execution_or_evaluation(case, prepared):
    journal = prepared["journal"]
    events = list(journal.store.events())
    payload = build(case, prepared)
    assert payload["status"] == "ready_for_normal_execution"
    assert payload["evaluation_refs"] == [] and payload["revision_ref"] is None
    assert (payload["claim_status"], payload["independent_benefit"], payload["adoption"]) == (
        "hypothesis", "not_established", "not_performed")
    assert journal.store.events() == events and journal.store.actions() == []
    saved = store(prepared, payload)
    assert cb.read_candidate_brief(publish(case), saved) == payload
    assert cb.validate_links(publish(case), payload) == payload
    ordinary = save(case, payload)
    count = len(journal.store.events())
    assert save(case, payload) == ordinary and len(journal.store.events()) == count
    assert cb.read_candidate_brief(publish(case), ordinary) == payload
    with pytest.raises(EvidenceError, match="hypothesis"):
        save(case, {**payload, "adoption": "adopted"})
    payload["frozen_invariants"].append("Caller-local edit")
    assert cb.read_candidate_brief(publish(case), saved)["frozen_invariants"] != payload["frozen_invariants"]


def test_title_rationale_and_verified_content_aliases_do_not_invent_a_candidate(case, prepared):
    first = build(case, prepared)
    ref = store(prepared, first)
    alias = copy.deepcopy(prepared["args"]["change"])
    for key in ("before_ref", "after_ref"):
        record = prepared["journal"].get(alias[key]["record_id"])
        alias[key].update(selector="bytes", sha256=record["sha256"], bytes=record["bytes"])
    renamed = build(case, prepared, title="Renamed hypothesis", rationale="Longer explanation, same intended edit",
                    change=alias, duplicate_refs=[ref])
    assert renamed["candidate_key"] == first["candidate_key"]
    assert renamed["episode_key"] == first["episode_key"]
    assert renamed["same_candidate_refs"] == [ref]
    # Another artifact locator wrapping the same intended JSON is also the same proposal.
    other = prepared["ref"](record_json(prepared["journal"], {"preserve_negative": True, "read": "generic"},
        prepared["root"] / "another-location", source_kind="fixture"))
    assert other["record_id"] != prepared["after"]["record_id"]
    alias["after_ref"] = other
    assert build(case, prepared, change=alias)["candidate_key"] == first["candidate_key"]


def test_source_aliases_verified_against_the_same_original_do_not_split_identity(case, prepared):
    first = build(case, prepared)
    ws = publish(case)
    manifest = json.loads(ws.read_text(encoding="utf-8"))
    manifest["sources"].append({**manifest["sources"][0], "source_id": "alias"})
    ws.write_text(json.dumps(manifest), encoding="utf-8")
    arguments = copy.deepcopy(prepared["args"])
    arguments["base_method_ref"]["source_id"] = "alias"
    same = cb.build_candidate_brief(ws, **arguments)
    assert same["candidate_key"] == first["candidate_key"]
    arguments["base_method_ref"]["source_id"] = "unpublished"
    with pytest.raises(EvidenceError):
        cb.build_candidate_brief(ws, **arguments)


def test_changed_evidence_episode_preserves_content_identity_and_negative_refs(case, prepared):
    negative = prepared["artifact"]({"fixture": True, "observation": "Earlier generic reader dropped nulls"})
    first = build(case, prepared, counter_refs=[negative])
    earlier = store(prepared, first)
    revised = prepared["artifact"]({"fixture": True, "observation": "Null-preserving receipt now available"})
    context, opportunity, normal = prepared["episode"](evidence_refs=[revised])
    later = build(case, prepared, context_ref=context, opportunity_ref=opportunity,
        normal_execution={**prepared["args"]["normal_execution"], "execution_ref": normal},
        duplicate_refs=[earlier], counter_refs=[], supersedes=[earlier])
    assert later["candidate_key"] == first["candidate_key"]
    assert later["episode_key"] != first["episode_key"]
    assert later["counter_refs"] == [negative]
    assert later["same_candidate_refs"] == [earlier]
    forged = {**later, "counter_refs": []}
    cb.validate_content(forged)  # Shape alone cannot prove historical retention.
    with pytest.raises(EvidenceError, match="retained counterevidence"):
        cb.validate_links(publish(case), forged)


@pytest.mark.parametrize("change_kind", ["base", "scope", "content", "target_part"])
def test_actual_base_scope_part_or_content_change_changes_identity(case, prepared, change_kind):
    first = build(case, prepared)
    updates = {}
    if change_kind in {"base", "scope"}:
        method = prepared["base"]
        if change_kind == "base":
            method = prepared["ref"](prepared["journal"].record(MethodVersion("second exact base", ("usage_method",),
                {"artifact_ref": prepared["before"]["record_id"]})))
        context, opportunity, normal = prepared["episode"](method=method,
            scope="other exact scope" if change_kind == "scope" else "fixture readers")
        updates = dict(base_method_ref=method, context_ref=context, opportunity_ref=opportunity,
                       normal_execution={**prepared["args"]["normal_execution"], "execution_ref": normal})
    elif change_kind == "target_part":
        updates["target_part"] = "/other-part"
    else:
        updates["change"] = {**prepared["args"]["change"], "after_ref": prepared["artifact"]({"read": "bounded"})}
    assert build(case, prepared, **updates)["candidate_key"] != first["candidate_key"]


def test_incomplete_proposal_and_absent_normal_execution_stay_incomplete(case, prepared):
    missing = build(case, prepared, missing_evidence=["No observed same-conditions receipt"])
    assert missing["status"] == "incomplete"
    absent = build(case, prepared, normal_execution={**prepared["args"]["normal_execution"], "execution_ref": None})
    assert absent["status"] == "incomplete" and absent["missing"]
    with pytest.raises(EvidenceError, match="readiness"):
        cb.validate_content({**missing, "status": "ready_for_normal_execution"})


def test_no_change_is_not_a_candidate_and_no_prose_can_create_one(case, prepared):
    with pytest.raises(EvidenceError, match="no content change"):
        build(case, prepared, title="New proposal name", rationale="Extra prose only",
              change={**prepared["args"]["change"], "after_ref": prepared["before"]})


@pytest.mark.parametrize("field,value", [("adoption", "adopted"), ("claim_status", "measured"),
                                         ("independent_benefit", "established")])
def test_candidate_cannot_be_relabelled_as_evaluation_or_adoption(case, prepared, field, value):
    with pytest.raises(EvidenceError, match="hypothesis"):
        cb.validate_content({**build(case, prepared), field: value})


def test_forged_digest_and_corrupt_originals_fail_despite_valid_schema(case, prepared):
    payload = build(case, prepared)
    forged = copy.deepcopy(payload)
    forged["change_identity"]["after_digest"] = "0" * 64
    forged["candidate_key"] = cb._candidate_key(forged)
    forged["episode_key"] = cb._episode_key(forged)
    cb.validate_content(forged)
    with pytest.raises(EvidenceError, match="verified originals"):
        cb.validate_links(publish(case), forged)
    record = prepared["journal"].get(prepared["after"]["record_id"])
    from pathlib import Path
    Path(record["path"]).write_text('{"read":"tampered"}', encoding="utf-8")
    with pytest.raises(EvidenceError):
        cb.validate_links(publish(case), payload)


def test_duplicate_detection_is_bounded_and_requires_actual_verified_briefs(case, prepared):
    with pytest.raises(EvidenceError, match="bounded"):
        build(case, prepared, duplicate_refs=[case[1]] * (cb.MAX_REVIEWED + 1))
    with pytest.raises(EvidenceError, match="schema"):
        build(case, prepared, duplicate_refs=[case[1]])
    payload = build(case, prepared)
    saved = store(prepared, payload)
    unavailable = {**saved, "source_id": "missing"}
    with pytest.raises(EvidenceError):
        build(case, prepared, duplicate_refs=[unavailable])


def test_existing_execution_must_actually_bind_the_base_and_metric(case, prepared):
    wrong = save(case, node_step(task_id="fixture-case", question="Other work", branch_id="other", node_id="other",
                                state="planned", reason="Explicit unrelated fixture", method_refs=[]))
    with pytest.raises(EvidenceError, match="comparator/base"):
        build(case, prepared, normal_execution={**prepared["args"]["normal_execution"], "execution_ref": wrong})
    with pytest.raises(EvidenceError, match="measurable effect"):
        build(case, prepared, normal_execution={**prepared["args"]["normal_execution"], "metric": "unrelated"})


def test_external_publication_requires_original_verification_workspace(case, prepared):
    payload = build(case, prepared)
    # A new journal publication source cannot label old originals local.
    with pytest.raises(EvidenceError, match="explicit verification workspace"):
        cb.validate_local_links(prepared["journal"], payload, "new-diagnostic-publication")
    assert cb.validate_local_links(prepared["journal"], payload, "new-diagnostic-publication",
                                   external_workspace=publish(case)) == payload


def test_workflow_change_reuses_existing_workflow_schema(case, prepared):
    from etalon.research.composition import WORKFLOW
    before = {"schema": WORKFLOW, "name": "fixture reader", "scope": "fixture readers", "nodes": [
        {"node_id": "read", "capability_id": "etalon.evidence", "method_refs": [prepared["base"]], "views": ["E"],
         "purpose": "Read originals", "skip_when": "Already read"}], "edges": [],
        "method_refs": [prepared["base"]], "supersedes": []}
    before_ref = save(case, before)
    after = copy.deepcopy(before)
    after["nodes"][0]["skip_when"] = "Already read and original identity still verified"
    after["supersedes"] = [before_ref]
    after_ref = save(case, after)
    brief = build(case, prepared, target_part="nodes/read/skip_when",
                  change={"kind": "workflow", "before_ref": before_ref, "after_ref": after_ref})
    assert brief["change_identity"]["kind"] == "workflow"
    assert cb.validate_local_links(prepared["journal"], brief, "research") == brief
    renamed = save(case, {**before, "name": "Only a display rename", "supersedes": [before_ref]})
    with pytest.raises(EvidenceError, match="no content change"):
        build(case, prepared, change={"kind": "workflow", "before_ref": before_ref, "after_ref": renamed})


def test_large_originals_are_byte_verified_without_requiring_complete_json(case, prepared):
    large = prepared["artifact"]({"fixture": True, "large_original": "x" * 40000})
    brief = build(case, prepared, counter_refs=[large])
    assert brief["counter_refs"] == [large]
    assert cb.validate_links(publish(case), brief) == brief
    assert cb.validate_local_links(prepared["journal"], brief, "research") == brief
    from pathlib import Path
    record = prepared["journal"].get(large["record_id"])
    Path(record["path"]).write_text("corrupt large original", encoding="utf-8")
    with pytest.raises(EvidenceError):
        cb.validate_links(publish(case), brief)


def test_optional_existing_patch_and_negative_experience_are_attributed_not_adopted(case, prepared):
    p = prepared
    candidate = p["ref"](p["journal"].record(MethodVersion("fixture candidate", ("usage_method",),
        {"artifact_ref": p["after"]["record_id"]}, parents=(p["base"]["record_id"],))))
    patch = imp.build_method_patch(p["journal"], context_ref=p["args"]["context_ref"],
        opportunity_ref=p["args"]["opportunity_ref"], base_method_ref=p["base"], candidate_method_ref=candidate,
        expected_change="Proposed read failure reduction", source_id="research")["patch"]
    patch_ref = save(case, patch)
    counter = p["artifact"]({"fixture": True, "negative": "Earlier failure remains unresolved"})
    experience = imp.record_experience(p["journal"], patch_ref=patch_ref, outcome="unresolved",
        lesson="Need normal use to resolve the fixture hypothesis", invalid_when="Different reader protocol",
        capability_id="etalon.evidence", support_refs=[case[1]], counter_refs=[counter],
        directory=p["root"] / "content", source_id="research", source_kind="fixture")
    brief = build(case, p, patch_ref=patch_ref, experience_refs=[experience])
    assert brief["counter_refs"] == [counter]
    assert brief["evaluation_refs"] == [] and brief["adoption"] == "not_performed"
    assert cb.validate_links(publish(case), brief) == brief
    with pytest.raises(EvidenceError, match="exact candidate patch"):
        build(case, p, experience_refs=[experience])


def test_unrelated_opportunity_is_not_accepted_as_matching_by_prose(case, prepared):
    _, other_opportunity, _ = prepared["episode"](scope="different exact boundary")
    with pytest.raises(EvidenceError, match="exact opportunity"):
        build(case, prepared, opportunity_ref=other_opportunity)
