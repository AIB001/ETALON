"""Side-advice software contracts; native handles and evaluations are fixtures."""
import asyncio
import json
import subprocess
import sys

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import ResearchJournal
from etalon.research.advice import advice
from etalon.research.handoff import export_research_handoff, plan_research_handoff
from etalon.research.notes import (
    entry_value, find_notes, original, patch_progress, read_current, read_entry,
    read_note, save_progress,
)


TOPIC = "screen-efficiency"
KEY = "measured-bottleneck-v1"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    store = CampaignStore(root / "research.sqlite")
    store.configure(CampaignSpec("fixture", 0, "wall_second", "fixture"), [
        Endpoint("fixture", "diagnostic", "recorded", "count", "fixture/1", 0, queryable=False)
    ])
    journal = ResearchJournal(store)
    negative = root / "negative.json"
    negative.write_text('{"scope":"software fixture","benefit_established":false}', encoding="utf-8")
    support = root / "timing.json"
    support.write_text('{"scope":"software fixture","repeated_setup":3}', encoding="utf-8")
    note = {
        "task_id": "advice-fixture", "branch_id": "main", "question": "Useful local change?",
        "summary": "Main calculation continues", "conditions": {"target": "fixture"},
        "authorization": {"owner": "main", "work": "existing main calculation"},
        "budget": {"wall_second": 30},
        "controls": {"execution_owner": "main"},
        "pending_handles": [{"job_id": "main-job", "owner": "main"}],
        "constraints": ["preserve original results"], "other_pauses": ["R4b"],
        "knowledge": {"hypothesis": "Main-only working hypothesis"},
        "resources": {"baseline": {"kind": "experience", "summary": "Measured negative retained"}},
        "evidence": [], "counterevidence": [original(negative)],
    }
    entry = root / "MAIN.json"
    first = save_progress(journal, entry, note, publication_id="initial")
    request = {
        "question": "Can repeated setup be reduced without changing results?",
        "brief": {"observed_issue": "Repeated setup in the selected timing original"},
        "execution_scope": {"work": "Inspect selected results; prepare a local candidate",
                            "read_paths": [str(support)], "write_paths": [str(root / "candidate")]},
    }
    report = {
        "outcome": "suggestion", "summary": "Try reusing setup for this fixture",
        "proposed_change": "Reuse one setup within the same input batch",
        "conditions": {"target": "fixture"},
        "comparison": {"status": "not_yet_evaluated"},
        "evidence": [str(support)], "counterevidence": [str(negative)],
    }
    return dict(root=root, journal=journal, entry=entry, note=note, first=first,
                negative=negative, support=support, request=request, report=report)


def prepare(project, key=KEY):
    return advice(project["entry"], "prepare", TOPIC, key, project["request"])


def slot(project):
    return read_entry(project["entry"])["note"]["advisories"][TOPIC]


def mcp_call(server, action, entry, payload=None, *, key=KEY):
    raw = asyncio.run(server.call_tool("etalon_research_advice", {
        "entry": str(entry), "action": action, "topic": TOPIC,
        "review_key": key, "payload": payload,
    }))
    blocks = raw[0] if isinstance(raw, tuple) else raw
    result = json.loads(blocks[0].text)
    assert result["ok"], result
    return result


def cli_call(tmp_path, action, entry, payload=None, *, key=KEY):
    command = [sys.executable, "-m", "etalon", "research", "advice",
               "--entry", str(entry), "--action", action, "--topic", TOPIC, "--review-key", key]
    if payload is not None:
        path = tmp_path / (action + "-payload.json")
        path.write_text(json.dumps(payload), encoding="utf-8")
        command.extend(["--payload", str(path)])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


def test_prepare_isolates_working_memory_and_execution_authority(project):
    prepared = prepare(project)
    assert prepared["launch_required"] and not prepared["execution_performed"]
    advisor = prepared["advisor_entry"]
    private = read_entry(advisor)
    assert private["memory_scope"] != "shared"
    assert entry_value(advisor)["database"] == entry_value(project["entry"])["database"]
    assert private["note"]["shared_basis"]["record_id"] == project["first"]
    assert private["note"]["knowledge"] == {}
    assert not {"authorization", "budget", "pending_handles", "controls", "resources"} & private["note"].keys()
    assert private["note"]["other_pauses"] == ["R4b"]
    assert private["note"]["constraints"] == project["note"]["constraints"]
    assert private["note"]["advice_request"]["execution_scope"] == project["request"]["execution_scope"]
    assert private["execution_permission"] == "not_evaluated_by_reader"
    patch_progress(advisor, {"knowledge": {"hypothesis": "Private candidate, not a main finding"}})
    assert read_entry(project["entry"])["note"]["knowledge"] == project["note"]["knowledge"]
    shared = find_notes(project["journal"], task_id=project["note"]["task_id"],
                        conditions=project["note"]["conditions"])
    assert all(row["note"].get("memory_scope", "shared") == "shared" for row in shared["matches"])
    assert not project["journal"].store.actions()


@pytest.mark.parametrize(("evaluator", "outcome"), [("main", "retain"), ("delegated", "adopt")])
def test_final_report_trial_feedback_and_retries_preserve_main_progress(project, evaluator, outcome):
    main = project["entry"]
    prepared = prepare(project)
    advisor = prepared["advisor_entry"]
    handle = {"host": "native-fixture", "agent_id": "advisor-1"}
    bound = advice(main, "bind", TOPIC, KEY, {"handle": handle})
    assert bound["handle"] == handle and not bound["execution_performed"]
    events = project["journal"].store.events()
    assert prepare(project)["handle"] == handle
    assert advice(main, "bind", TOPIC, KEY, {"handle": handle})["reused"]
    assert not advice(main, "collect", TOPIC, KEY)["deliver"]
    assert project["journal"].store.events() == events

    patch_progress(advisor, {"summary": "Private intermediate idea; no final advice yet"})
    patch_progress(main, {"summary": "Main completed the next batch", "next_action": "Read its result"})
    reported = advice(advisor, "report", TOPIC, KEY, project["report"])
    assert reported["notify_main"]
    private_head = entry_value(advisor)["record_id"]
    repeated = advice(advisor, "report", TOPIC, KEY, project["report"])
    assert repeated["reused"] and not repeated["notify_main"]
    assert entry_value(advisor)["record_id"] == private_head
    # Only explicit collection releases the final report into main memory.
    assert "report" not in slot(project)
    collected = advice(main, "collect", TOPIC, KEY)
    assert collected["deliver"] and collected["needs_decision"]
    assert collected["report"]["evidence"] == [original(project["support"])]
    assert collected["report"]["counterevidence"] == [original(project["negative"])]
    shared_head = entry_value(main)["record_id"]
    assert not advice(main, "collect", TOPIC, KEY)["deliver"]
    assert entry_value(main)["record_id"] == shared_head
    current = read_entry(main)["note"]
    assert current["summary"] == "Main completed the next batch"
    assert current["pending_handles"] == project["note"]["pending_handles"]
    assert current["counterevidence"] == project["note"]["counterevidence"]
    assert current["resources"] == project["note"]["resources"]

    trial = {"decision_id": "trial-1", "outcome": "try", "reason": "Evaluate on the declared fixture",
             "evaluation": {"owner": evaluator, "status": "pending"}}
    if evaluator == "delegated":
        trial["handles"] = [{"host": "native-fixture", "agent_id": "evaluator-1"}]
    assert advice(main, "decide", TOPIC, KEY, trial)["state"] == "try"
    private = read_entry(advisor)["note"]
    assert private["status"] == "evaluating" and private["advice_feedback"]["decision"] == trial
    trial_head = entry_value(main)["record_id"]
    evaluation = project["root"] / "evaluation.json"
    evaluation.write_text(json.dumps({"scope": "software fixture", "owner": evaluator,
                                      "outcome": outcome}), encoding="utf-8")
    patch_progress(main, {"summary": "Main completed another batch"}, evidence=[str(evaluation)])
    before_retry = entry_value(main)["record_id"]
    assert advice(main, "decide", TOPIC, KEY, trial)["reused"]
    assert entry_value(main)["record_id"] == before_retry
    feedback = {"decision_id": "feedback-1", "outcome": outcome,
                "reason": "Record the fixture evaluation without inferring scientific efficacy",
                "evaluation": {"owner": evaluator, "result": original(evaluation)}}
    decision = advice(main, "decide", TOPIC, KEY, feedback)
    assert decision["state"] == outcome and not decision["execution_performed"]
    private = read_entry(advisor)["note"]
    assert private["status"] == "closed" and private["advice_feedback"]["decision"] == feedback
    assert read_note(project["journal"], trial_head)["note"]["advisories"][TOPIC]["decision"] == trial
    patch_progress(main, {"summary": "Main advanced after feedback"})
    events = project["journal"].store.events()
    assert advice(main, "decide", TOPIC, KEY, feedback)["reused"]
    assert not advice(main, "collect", TOPIC, KEY)["deliver"]
    recovered = prepare(project)
    assert recovered["state"] == outcome and not recovered["launch_required"]
    assert project["journal"].store.events() == events
    assert read_current(main)["knowledge"]["summary"] == "Main advanced after feedback"
    assert read_entry(main)["note"]["counterevidence"] == project["note"]["counterevidence"]
    assert not project["journal"].store.actions()


@pytest.mark.parametrize("state", ["pending", "reported", "try"])
def test_active_topic_defers_duplicate_work_even_with_a_new_key(project, state):
    prepared = prepare(project)
    if state != "pending":
        advice(prepared["advisor_entry"], "report", TOPIC, KEY, project["report"])
        advice(project["entry"], "collect", TOPIC, KEY)
    if state == "try":
        advice(project["entry"], "decide", TOPIC, KEY,
               {"decision_id": "trial", "outcome": "try", "reason": "Trial still underway"})
    head = entry_value(project["entry"])["record_id"]
    blocked = prepare(project, "new-result-v2")
    assert blocked["review_key"] == KEY and blocked["state"] == state
    assert blocked["deferred_review_key"] == "new-result-v2"
    assert not blocked["launch_required"]
    assert entry_value(project["entry"])["record_id"] == head
    assert len(list((project["root"] / "advisors").glob("*/ENTRY.json"))) == 1


def test_no_change_stays_current_until_material_new_review_and_passes_feedback(project):
    prepared = prepare(project)
    unchanged = {"outcome": "no_change", "summary": "No useful change under the current evidence",
                 "counterevidence": [str(project["negative"])]}
    report = advice(prepared["advisor_entry"], "report", TOPIC, KEY, unchanged)
    assert not report["notify_main"]
    advice(project["entry"], "collect", TOPIC, KEY)
    feedback = {"decision_id": "unchanged", "outcome": "no_change", "reason": "Keep the current method"}
    advice(project["entry"], "decide", TOPIC, KEY, feedback)
    events = project["journal"].store.events()
    recovered = prepare(project)
    assert recovered["state"] == "no_change" and not recovered["launch_required"]
    assert project["journal"].store.events() == events

    # The host supplies a new key after an actual new result, not on a timer.
    newer = project["root"] / "new-result.json"
    newer.write_text('{"scope":"software fixture","changed_input_size":100}', encoding="utf-8")
    patch_progress(project["entry"], {"summary": "A larger-input result now exists"}, evidence=[str(newer)])
    new_request = {**project["request"], "brief": {"changed_evidence": str(newer)}}
    next_review = advice(project["entry"], "prepare", TOPIC, "larger-input-v2", new_request)
    assert next_review["launch_required"] and next_review["advisor_entry"] != prepared["advisor_entry"]
    assert read_entry(next_review["advisor_entry"])["note"]["advice_request"]["previous_feedback"] == feedback
    advice(project["entry"], "decide", TOPIC, "larger-input-v2",
           {"decision_id": "defer-v2", "outcome": "defer", "reason": "Use the result after current work"})
    head = entry_value(project["entry"])["record_id"]
    retired = prepare(project)
    assert retired["state"] == "archived" and not retired["launch_required"]
    assert entry_value(project["entry"])["record_id"] == head
    assert slot(project)["review_key"] == "larger-input-v2"


def test_report_scope_paths_and_finality_are_enforced(project):
    prepared = prepare(project)
    advisor = prepared["advisor_entry"]
    with pytest.raises(ValueError, match="private"):
        advice(project["entry"], "report", TOPIC, KEY, project["report"])
    with pytest.raises(ValueError, match="matching"):
        advice(advisor, "report", TOPIC, "another-key", project["report"])
    with pytest.raises(ValueError, match="file paths"):
        advice(advisor, "report", TOPIC, KEY,
               {**project["report"], "evidence": [original(project["support"])]})
    advice(advisor, "report", TOPIC, KEY, project["report"])
    head = entry_value(advisor)["record_id"]
    with pytest.raises(ValueError, match="final report already saved"):
        advice(advisor, "report", TOPIC, KEY, {**project["report"], "summary": "Different final advice"})
    assert entry_value(advisor)["record_id"] == head
    with pytest.raises(ValueError, match="collect"):
        advice(project["entry"], "decide", TOPIC, KEY,
               {"decision_id": "premature", "outcome": "adopt", "reason": "Uncollected report"})


def test_collect_rechecks_counterevidence_and_never_replaces_its_identity(project):
    prepared = prepare(project)
    negative = project["root"] / "advisor-negative.json"
    negative.write_text('{"scope":"software fixture","candidate_regression":true}', encoding="utf-8")
    advice(prepared["advisor_entry"], "report", TOPIC, KEY,
           {**project["report"], "counterevidence": [str(negative)]})
    saved = negative.read_bytes()
    expected = original(negative)
    head = entry_value(project["entry"])["record_id"]
    negative.write_text('{"scope":"changed fixture"}', encoding="utf-8")
    with pytest.raises(ValueError, match="original changed"):
        advice(project["entry"], "collect", TOPIC, KEY)
    assert entry_value(project["entry"])["record_id"] == head
    assert read_entry(project["entry"])["note"]["counterevidence"] == project["note"]["counterevidence"]
    negative.write_bytes(saved)
    collected = advice(project["entry"], "collect", TOPIC, KEY)
    assert collected["report"]["counterevidence"] == [expected]
    assert read_entry(project["entry"])["note"]["counterevidence"] == [*project["note"]["counterevidence"], expected]


def test_main_handoff_carries_released_advice_but_does_not_traverse_private_drafts(project, tmp_path):
    prepared = prepare(project)
    private_file = project["root"] / "private-draft.json"
    private_file.write_text('{"hypothesis":"unreleased draft only"}', encoding="utf-8")
    draft = patch_progress(prepared["advisor_entry"], {"summary": "Unreleased private draft"},
                           evidence=[str(private_file)])
    final = advice(prepared["advisor_entry"], "report", TOPIC, KEY, project["report"])
    advice(project["entry"], "collect", TOPIC, KEY)
    # Private draft originals must not become required main-handoff inputs.
    private_file.unlink()
    plan = plan_research_handoff(project["entry"])
    assert draft["record_id"] not in plan["note_records"]
    assert final["record_id"] not in plan["note_records"]
    assert str(private_file) not in {row["source"] for row in plan["files"]}
    assert str(project["negative"]) in {row["source"] for row in plan["files"]}
    target = tmp_path / "handoff"
    assert export_research_handoff(plan, target)["status"] == "verified"
    project["root"].rename(tmp_path / "original-unavailable")
    copied = read_entry(target / "entry.json")
    assert copied["note"]["advisories"][TOPIC]["report"]["summary"] == project["report"]["summary"]
    assert copied["note"]["counterevidence"] == project["note"]["counterevidence"]


def test_failed_native_handle_can_be_explicitly_replaced_without_erasing_history(project):
    prepare(project)
    old = {"host": "native-fixture", "agent_id": "failed-agent"}
    new = {"host": "native-fixture", "agent_id": "replacement-agent"}
    advice(project["entry"], "bind", TOPIC, KEY, {"handle": old})
    bound_head = entry_value(project["entry"])["record_id"]
    with pytest.raises(ValueError):
        advice(project["entry"], "bind", TOPIC, KEY, {"handle": new})
    recovery = {"handle": new, "replaces": old, "reason": "Native host confirmed the original agent failed"}
    replacement = advice(project["entry"], "bind", TOPIC, KEY, recovery)
    assert replacement["handle"] == new and not replacement["launch_required"]
    assert read_note(project["journal"], bound_head)["note"]["advisories"][TOPIC]["handle"] == old
    events = project["journal"].store.events()
    assert advice(project["entry"], "bind", TOPIC, KEY, recovery)["reused"]
    assert project["journal"].store.events() == events
    assert prepare(project)["handle"] == new


@pytest.mark.parametrize(("previous_closed", "failure_stage"), [
    (False, "before_ticket"), (True, "before_ticket"), (True, "before_entry_advance"),
])
def test_interrupted_prepare_recovers_same_key_without_losing_work(project, monkeypatch,
                                                                  previous_closed, failure_stage):
    import etalon.research.advice as advisory_module
    import etalon.research.notes as notes_module

    main = project["entry"]
    feedback = {"decision_id": "close-old", "outcome": "no_change", "reason": "Earlier scope complete"}
    if previous_closed:
        prepare(project, "earlier-closed-key")
        advice(main, "decide", TOPIC, "earlier-closed-key", feedback)
    before = entry_value(main)["record_id"]
    unpublished = []
    publish = notes_module.publish_note

    def fail_ticket(*args, **kwargs):
        raise RuntimeError("interrupted shared ticket")

    def fail_entry_advance(entry, journal, record_id, **kwargs):
        if entry == main:
            unpublished.append(record_id)
            raise RuntimeError("interrupted shared ticket")
        return publish(entry, journal, record_id, **kwargs)

    with monkeypatch.context() as interrupted:
        if failure_stage == "before_ticket":
            interrupted.setattr(advisory_module, "_write", fail_ticket)
        else:
            interrupted.setattr(notes_module, "publish_note", fail_entry_advance)
        with pytest.raises(RuntimeError, match="interrupted shared ticket"):
            prepare(project)
    assert entry_value(main)["record_id"] == before
    if unpublished:
        assert read_note(project["journal"], unpublished[0])["note"]["advisories"][TOPIC]["review_key"] == KEY

    private = next(path for path in (project["root"] / "advisors").glob("*/ENTRY.json")
                   if read_entry(path)["note"]["advice_request"]["review_key"] == KEY)
    request = read_entry(private)["note"]["advice_request"]
    assert request["question"] == project["request"]["question"]
    if previous_closed:
        assert request["previous_feedback"] == feedback
    patch_progress(private, {"summary": "Private candidate survived the interrupted call",
                             "knowledge": {"candidate": "saved-private-work"}})
    private_head = entry_value(private)["record_id"]
    patch_progress(main, {"summary": "Main progressed across the prepare failure"})
    recovered = prepare(project)
    assert recovered["state"] == "pending" and recovered["review_key"] == KEY
    assert recovered["advisor_entry"] == str(private)
    assert recovered["reused"] and not recovered["launch_required"]
    assert "native host" in recovered["next_action"]
    assert recovered["dispatch"]["question"] == request["question"]
    assert entry_value(private)["record_id"] == private_head
    assert read_entry(private)["note"]["knowledge"] == {"candidate": "saved-private-work"}
    assert read_entry(main)["note"]["summary"] == "Main progressed across the prepare failure"
    assert read_entry(main)["note"]["counterevidence"] == project["note"]["counterevidence"]
    assert len(list((project["root"] / "advisors").glob("*/ENTRY.json"))) == 1 + previous_closed


def test_collect_retries_interrupted_main_publication_after_later_progress(project, monkeypatch):
    import etalon.research.notes as notes_module

    main = project["entry"]
    prepared = prepare(project)
    advisor = prepared["advisor_entry"]
    advice(advisor, "report", TOPIC, KEY, project["report"])
    private_head = entry_value(advisor)["record_id"]
    main_head = entry_value(main)["record_id"]
    publish = notes_module.publish_note
    unpublished = []

    def fail_entry_advance(entry, journal, record_id, **kwargs):
        if entry == main:
            unpublished.append(record_id)
            raise RuntimeError("interrupted collection")
        return publish(entry, journal, record_id, **kwargs)

    with monkeypatch.context() as interrupted:
        interrupted.setattr(notes_module, "publish_note", fail_entry_advance)
        with pytest.raises(RuntimeError, match="interrupted collection"):
            advice(main, "collect", TOPIC, KEY)
    assert unpublished
    assert entry_value(main)["record_id"] == main_head and "report" not in slot(project)
    patch_progress(main, {"summary": "Main made progress before collect retry"})
    recovered = advice(main, "collect", TOPIC, KEY)
    assert recovered["deliver"] and recovered["state"] == "reported"
    assert entry_value(advisor)["record_id"] == private_head
    current = read_entry(main)["note"]
    assert current["summary"] == "Main made progress before collect retry"
    assert current["evidence"] == [original(project["support"])]
    assert current["counterevidence"] == project["note"]["counterevidence"]
    events = project["journal"].store.events()
    assert not advice(main, "collect", TOPIC, KEY)["deliver"]
    assert project["journal"].store.events() == events


@pytest.mark.parametrize("failure_stage", ["before_private_write", "before_private_entry_advance"])
def test_decision_retry_repairs_private_feedback_without_erasing_later_progress(project, monkeypatch, failure_stage):
    import etalon.research.advice as advisory_module
    import etalon.research.notes as notes_module

    main = project["entry"]
    prepared = prepare(project)
    advisor = prepared["advisor_entry"]
    advice(advisor, "report", TOPIC, KEY, project["report"])
    advice(main, "collect", TOPIC, KEY)
    private_head = entry_value(advisor)["record_id"]
    decision = {"decision_id": "return-feedback", "outcome": "retain",
                "reason": "Keep the existing method after the fixture evaluation"}
    publish = notes_module.publish_note

    def fail_feedback(*args, **kwargs):
        raise RuntimeError("interrupted private feedback")

    def fail_private_entry(entry, journal, record_id, **kwargs):
        if str(entry) == advisor:
            raise RuntimeError("interrupted private feedback")
        return publish(entry, journal, record_id, **kwargs)

    with monkeypatch.context() as interrupted:
        if failure_stage == "before_private_write":
            interrupted.setattr(advisory_module, "_feedback", fail_feedback)
        else:
            interrupted.setattr(notes_module, "publish_note", fail_private_entry)
        with pytest.raises(RuntimeError, match="interrupted private feedback"):
            advice(main, "decide", TOPIC, KEY, decision)
    assert slot(project)["decision"] == decision and slot(project)["state"] == "retain"
    assert entry_value(advisor)["record_id"] == private_head
    assert "advice_feedback" not in read_entry(advisor)["note"]

    patch_progress(main, {"summary": "Main completed a later batch",
                          "knowledge": {"finding": "main-only later result"}})
    patch_progress(advisor, {"summary": "Advisor kept a later local finding",
                             "knowledge": {"finding": "private-only later result"}})
    main_before_retry = read_entry(main)
    repaired = advice(main, "decide", TOPIC, KEY, decision)
    assert repaired["reused"] and not repaired["execution_performed"] and not repaired["launch_required"]
    assert read_entry(main) == main_before_retry
    private = read_entry(advisor)["note"]
    assert private["advice_feedback"] == {
        "shared_entry": str(main), "topic": TOPIC, "review_key": KEY, "decision": decision,
    }
    assert private["status"] == "closed"
    assert private["summary"] == "Advisor kept a later local finding"
    assert private["knowledge"] == {"finding": "private-only later result"}
    assert "main-only later result" not in json.dumps(private)
    assert private["advice_report"]["summary"] == project["report"]["summary"]
    events = project["journal"].store.events()
    head = entry_value(advisor)["record_id"]
    assert advice(main, "decide", TOPIC, KEY, decision)["reused"]
    assert entry_value(advisor)["record_id"] == head
    assert project["journal"].store.events() == events
    assert not project["journal"].store.actions()

    prepare(project, "later-evidence-v2")
    assert read_entry(advisor)["note"]["advice_feedback"]["decision"] == decision


def test_fresh_cli_and_mcp_resume_one_advice_loop(project, tmp_path):
    from mcp.server.fastmcp import FastMCP
    from etalon.mcp.evidence import register

    server = FastMCP("advice-contract")
    register(server)
    locator = project["root"] / "CURRENT.json"
    locator.write_text(json.dumps({"entry": "MAIN.json"}), encoding="utf-8")
    prepared = cli_call(tmp_path, "prepare", locator, project["request"])
    assert prepared["launch_required"]
    handle = {"host": "native-fixture", "agent_id": "fresh-agent"}
    assert mcp_call(server, "bind", locator, {"handle": handle})["handle"] == handle
    recovered = cli_call(tmp_path, "prepare", locator, project["request"])
    assert recovered["handle"] == handle and not recovered["launch_required"]
    reported = mcp_call(server, "report", prepared["advisor_entry"], project["report"])
    assert reported["notify_main"]
    collected = cli_call(tmp_path, "collect", locator)
    assert collected["deliver"] and collected["report"]["summary"] == project["report"]["summary"]
    assert not mcp_call(server, "collect", locator)["deliver"]
    decision = {"decision_id": "fresh-feedback", "outcome": "retain", "reason": "Fixture result retained"}
    mcp_call(server, "decide", locator, decision)
    events = project["journal"].store.events()
    assert cli_call(tmp_path, "decide", locator, decision)["reused"]
    final = cli_call(tmp_path, "prepare", locator, project["request"])
    assert final["state"] == "retain" and not final["launch_required"]
    assert project["journal"].store.events() == events
    assert read_entry(project["entry"])["note"]["summary"] == project["note"]["summary"]
    assert not project["journal"].store.actions()
