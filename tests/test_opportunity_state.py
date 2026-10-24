"""Lifecycle engineering fixtures only; no model/science execution or adoption."""
import copy
import json

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.schema import digest
from etalon.active.store import StateError
from etalon.research import Artifact, MethodVersion, ResearchJournal
from etalon.research import improvement as imp
from etalon.research import opportunity_state as state
from etalon.research.components import record_json
from etalon.research.continuation import record_content
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.method_revision import journal_ref
from test_method_revision import case as revision_case

SID = "fixture"
CONDITION = {"condition_id": "new-receipt", "condition": "A newly released receipt changes the bounded failure diagnosis"}


@pytest.fixture
def case(tmp_path):
    store = CampaignStore(tmp_path / "research.sqlite")
    store.configure(CampaignSpec("lifecycle", 0, "wall_second", "fixture"),
                    [Endpoint("lifecycle", "fixture", "unmeasured", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    ref = lambda rid: journal_ref(journal, rid, source_id=SID)
    artifact = lambda payload: ref(record_json(journal, payload, tmp_path / "originals", source_kind="fixture"))
    problem, negative = artifact({"failure": "one unresolved observation"}), artifact({"counterexample": "previous idea failed"})
    method = ref(journal.record(MethodVersion("base", ("usage_method",), {"artifact_ref": artifact({"method": "base"})["record_id"]})))
    context = dict(schema=imp.CONTEXT, task_id="fixture-task", scope="fixture-scope", conditions={"endpoint": "fixture/1"},
                   method_refs=[method], target_ref=method, problem_refs=[problem], evidence_refs=[], observations=[],
                   missing=[], status="ready_for_revision", supersedes=[])
    context["opportunity_key"] = digest({k: context[k] for k in ("scope", "conditions", "target_ref", "problem_refs", "evidence_refs")})
    context_ref = ref(record_content(journal, context, tmp_path / "notes", source_id=SID, source_kind="fixture"))
    opened = imp.register_opportunity(journal, context_ref, tmp_path / "notes", source_id=SID, source_kind="fixture")
    return dict(j=journal, root=tmp_path, ref=ref, artifact=artifact, problem=problem, negative=negative,
                method=method, context=context, context_ref=context_ref, opportunity=opened["opportunity_ref"], opened=opened)


def close(case, **changes):
    args = dict(opportunity_ref=case["opportunity"], outcome="no_change", reason="Existing evidence does not justify a candidate",
                reviewer="fixture-host", support_refs=[case["problem"]], counter_refs=[case["negative"]],
                reconsider_when=[CONDITION], source_id=SID)
    return state.build_opportunity_disposition(case["j"], **{**args, **changes})


def save(case, content, **kwargs):
    return case["ref"](record_content(case["j"], content, case["root"] / "notes", source_id=SID,
                                     source_kind="fixture", **kwargs))


def resolve(case, **kwargs):
    return state.resolve_opportunity_state(case["j"], case["opportunity"], source_id=SID, **kwargs)


def reopen(case, terminal, evidence, **changes):
    args = dict(opportunity_ref=case["opportunity"], previous_terminal_ref=terminal, new_evidence_refs=[evidence],
                matched_condition=CONDITION, reviewer="fixture-host", reason="The new receipt contradicts the prior missing-input diagnosis",
                source_id=SID)
    return state.build_opportunity_reopen(case["j"], **{**args, **changes})


def test_no_change_without_patch_is_terminal_and_exact_replay_is_idempotent(case):
    assert resolve(case)["state"] == "open"
    payload = close(case)
    assert payload["candidate_ref"] is payload["evaluation_ref"] is payload["resolution_ref"] is None
    terminal = save(case, payload)
    events = case["j"].store.events()
    assert save(case, payload) == terminal
    assert close(case) == payload
    assert case["j"].store.events() == events
    result = resolve(case)
    assert result["terminal"] and result["outcome"] == "no_change"
    assert result["request_id"] == case["opened"]["request_id"]
    assert result["retained_counter_refs"] == [case["negative"]]
    assert result["terminal_refs"] == result["history_refs"] == [terminal]
    assert case["j"].store.actions() == []
    with pytest.raises(EvidenceError, match="current open episode"):
        close(case, reason="A second conflicting closure")


@pytest.mark.parametrize("outcome", ["waiting", "awaiting_evidence"])
def test_waiting_is_not_success_and_can_later_close(case, outcome):
    pending = save(case, close(case, outcome=outcome))
    result = resolve(case)
    assert result["state"] == outcome and not result["terminal"] and result["terminal_ref"] is None
    terminal = save(case, close(case, outcome="retain", previous_state_ref=pending))
    assert resolve(case)["terminal_ref"] == terminal


def test_genuine_conditional_reopen_preserves_negatives_and_changes_only_episode_id(case):
    terminal = save(case, close(case))
    fresh = case["artifact"]({"receipt": "new independent raw fixture result"})
    body = reopen(case, terminal, fresh)
    reopened = save(case, body)
    count = len(case["j"].store.events())
    assert save(case, body) == reopened and len(case["j"].store.events()) == count
    assert reopen(case, terminal, fresh) == body
    result = resolve(case)
    assert result["state"] == "reopened" and not result["terminal"]
    assert result["episode_ref"] == reopened and result["terminal_ref"] == terminal
    assert result["request_id"].startswith(case["opened"]["request_id"] + "-reopen-")
    assert result["retained_counter_refs"] == [case["negative"]]
    assert fresh in result["seen_evidence_refs"]
    next_terminal = save(case, close(case, previous_state_ref=reopened, counter_refs=[]))
    assert resolve(case)["terminal_refs"] == [terminal, next_terminal]
    with pytest.raises(EvidenceError, match="unseen original"):
        reopen(case, next_terminal, fresh)


def test_same_original_with_new_artifact_id_or_json_wrapper_is_not_novel(case):
    terminal = save(case, close(case))
    old = case["j"].get(case["problem"]["record_id"])
    wrapped_id = case["j"].record(Artifact(**{**old, "bindings": {"renamed": "new ID, same original"}}))
    wrapped = case["ref"](wrapped_id)
    assert wrapped != case["problem"]
    with pytest.raises(EvidenceError, match="unseen original"):
        reopen(case, terminal, wrapped)
    envelope = case["artifact"]({"title": "new metadata", "original_ref": case["problem"]})
    with pytest.raises(EvidenceError, match="unseen original"):
        reopen(case, terminal, envelope)
    malformed = case["artifact"]({"title": "new metadata", "original_ref": {k: v for k, v in case["problem"].items() if k != "schema"}})
    with pytest.raises(EvidenceError, match="evidence ref"):
        reopen(case, terminal, malformed)


def test_metadata_record_does_not_supply_new_original_evidence(case):
    terminal = save(case, close(case))
    with pytest.raises(EvidenceError, match="bounded explicit list|unseen original"):
        reopen(case, terminal, case["method"])


def test_forged_ref_identity_and_mismatched_condition_are_rejected(case):
    terminal = save(case, close(case))
    fresh = case["artifact"]({"receipt": "new"})
    with pytest.raises(EvidenceError, match="specific prior"):
        reopen(case, terminal, fresh, matched_condition={"condition_id": "new-receipt", "condition": "Changed title only"})
    with pytest.raises(EvidenceError, match="digest"):
        reopen(case, terminal, {**fresh, "record_digest": "0" * 64})
    body = reopen(case, terminal, fresh)
    body["new_original_identities"][0]["sha256"] = "0" * 64
    with pytest.raises(EvidenceError, match="verified bytes"):
        save(case, body)
    with pytest.raises(EvidenceError, match="text"):
        reopen(case, terminal, fresh, reason=" ")


def test_direct_hand_built_reopen_cannot_drop_negative_evidence(case):
    terminal = save(case, close(case))
    body = reopen(case, terminal, case["artifact"]({"receipt": "new"}))
    body["counter_refs"] = []
    with pytest.raises(EvidenceError, match="preserve prior counter_refs"):
        save(case, body)
    body = reopen(case, terminal, case["artifact"]({"receipt": "new"}))
    body["original_evidence_refs"] = [case["problem"]]
    with pytest.raises(EvidenceError, match="preserve prior original"):
        save(case, body)


def test_concurrent_sibling_heads_fail_closed_even_if_raw_artifacts_bypass_helper(case):
    first, second = close(case), close(case, outcome="retain")
    save(case, first)
    with pytest.raises(EvidenceError, match="current open episode"):
        save(case, second)
    record_json(case["j"], second, case["root"] / "concurrent", source_kind="fixture",
                bindings={"schema": state.DISPOSITION, "source_id": SID})
    with pytest.raises(EvidenceError, match="ambiguous concurrent"):
        resolve(case)


def test_history_overflow_and_missing_original_never_become_open(case):
    terminal = save(case, close(case))
    save(case, reopen(case, terminal, case["artifact"]({"new": "receipt"})))
    with pytest.raises(EvidenceError, match="bounded coverage"):
        resolve(case, max_history=1)
    from pathlib import Path
    Path(case["j"].get(case["negative"]["record_id"])["path"]).write_text("corrupted fixture", encoding="utf-8")
    with pytest.raises(StateError, match="hash/size"):
        resolve(case)


def test_adoption_cannot_be_fabricated_by_disposition(case):
    with pytest.raises(EvidenceError, match="existing evaluation"):
        close(case, outcome="adopt")
    with pytest.raises(EvidenceError, match="existing evaluation"):
        close(case, outcome="rollback")
    with pytest.raises(EvidenceError, match="paired"):
        close(case, evaluation_ref=case["problem"])
    with pytest.raises(EvidenceError, match="binding schema"):
        close(case, outcome="adopt", evaluation_ref=case["problem"], resolution_ref=case["problem"])


@pytest.mark.parametrize("outcome,without_use", [("adopt",False), ("retain",False), ("rollback",False), ("retain",True)])
def test_measured_terminal_uses_existing_evaluation_resolution_authority(revision_case, outcome, without_use):
    from test_research_rsi import evaluation, resolve as resolve_revision
    journal, revision, directory, ref, _ = revision_case
    candidate = revision["candidate_version_refs"][0]
    scope = "fixture/local-next-use"
    context = dict(schema=imp.CONTEXT, task_id="next-use", scope=scope, conditions={"endpoint": "fixture/1"},
                   method_refs=[revision["base_method_ref"]], target_ref=revision["base_method_ref"],
                   problem_refs=revision["problem_refs"], evidence_refs=[], observations=[], missing=[],
                   status="ready_for_revision", supersedes=[])
    context["opportunity_key"] = digest({k: context[k] for k in ("scope", "conditions", "target_ref", "problem_refs", "evidence_refs")})
    context_ref = ref(record_content(journal, context, directory, source_id=SID, source_kind="fixture"))
    opportunity = imp.register_opportunity(journal, context_ref, directory, source_id=SID, source_kind="fixture")["opportunity_ref"]
    measured = evaluation(revision_case)
    options = {"candidate_ref": candidate} if outcome == "adopt" else {}
    if outcome == "rollback":
        prior = resolve_revision(revision_case, outcome="adopt", candidate_ref=candidate, evaluation_ref=measured)
        options = {"prior_use_ref": prior["use_ref"], "counterevidence_refs": revision["problem_refs"]}
    resolution = resolve_revision(revision_case, outcome=outcome, evaluation_ref=measured, **options)
    if without_use:
        from etalon.research.schema import MethodEvaluation
        evaluated=journal.get(measured['record_id'])
        evaluated['scope']=scope
        measured=ref(journal.record(MethodEvaluation(**{k:v for k,v in evaluated.items() if k!='schema'})))
        step=imp._local(journal,resolution['step_ref'],SID)
        step['output_refs']=[]
        step['input_refs']=[measured if r['record_id'].startswith('methodevaluation-') else r for r in step['input_refs']]
        resolution['step_ref']=ref(record_content(journal,step,directory,source_id=SID,source_kind='fixture'))
    before_uses = sum(e["kind"] == "research_recorded" and e.get("payload", {}).get("record", {}).get("schema") == "etalon-method-use/1"
                      for e in journal.store.events())
    payload = state.build_opportunity_disposition(journal, opportunity_ref=opportunity, outcome=outcome,
        reason="Existing fixture evaluation and scoped selection", reviewer="fixture-host",
        support_refs=revision["problem_refs"], counter_refs=revision["problem_refs"] if outcome == "rollback" else [],
        reconsider_when=[CONDITION], source_id=SID, candidate_ref=candidate,
        evaluation_ref=measured, resolution_ref=resolution["step_ref"])
    record_content(journal, payload, directory, source_id=SID, source_kind="fixture")
    result = state.resolve_opportunity_state(journal, opportunity, source_id=SID)
    assert result["terminal"] and result["outcome"] == outcome
    after_uses = sum(e["kind"] == "research_recorded" and e.get("payload", {}).get("record", {}).get("schema") == "etalon-method-use/1"
                     for e in journal.store.events())
    assert after_uses == before_uses and journal.store.actions() == []


def test_preflight_reader_validates_exact_chain_without_database_interface(case):
    terminal = save(case, close(case))
    payload = reopen(case, terminal, case["artifact"]({"new": "receipt"}))
    class Reader:
        get = case["j"].get
        artifact_path = case["j"].artifact_path
    state.validate_local_links(Reader(), payload, SID)
    bad = copy.deepcopy(payload)
    bad["counter_refs"] = []
    with pytest.raises(EvidenceError, match="preserve prior"):
        state.validate_local_links(Reader(), bad, SID)


def test_external_frozen_opportunity_can_have_local_lifecycle_without_mutation(case, tmp_path):
    original_hash = file_identity(case["j"].store.path)["sha256"]
    workspace = case["root"] / "external-workspace.json"
    workspace.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "original-fixture",
        "sources": [{"source_id": SID, "kind": "journal", "location": str(case["j"].store.path),
                     "identity": {"kind": "snapshot_sha256", "value": original_hash}}],
        "status_refs": [], "location_map": []}), encoding="utf-8")
    local = CampaignStore(tmp_path / "diagnostic.sqlite")
    local.configure(CampaignSpec("diagnostic", 0, "wall_second", "fixture"),
                    [Endpoint("diagnostic", "fixture", "unmeasured", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(local)
    args = dict(opportunity_ref=case["opportunity"], outcome="no_change", reason="Retrospective bounded judgment",
                reviewer="fixture-host", support_refs=[case["problem"]], counter_refs=[case["negative"]],
                reconsider_when=[CONDITION], source_id="new-diagnostic")
    with pytest.raises(EvidenceError, match="external_workspace"):
        state.build_opportunity_disposition(journal, **args)
    payload = state.build_opportunity_disposition(journal, **args, external_workspace=workspace)
    rid = record_content(journal, payload, tmp_path / "diagnostic-notes", source_id="new-diagnostic",
                         source_kind="fixture", external_workspace=workspace)
    result = state.resolve_opportunity_state(journal, case["opportunity"], source_id="new-diagnostic", external_workspace=workspace)
    assert result["terminal"] and result["head_ref"]["record_id"] == rid
    assert result["retained_counter_refs"] == [case["negative"]]
    assert file_identity(case["j"].store.path)["sha256"] == original_hash
