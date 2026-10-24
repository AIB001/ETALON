"""Synthetic integration only: selected member identity and foreign lifecycle bounds."""
import json

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.research import Artifact, ResearchJournal
from etalon.research import defect_family, improvement, opportunity_state
from etalon.research.continuation import record_content
from etalon.research.evidence_refs import file_identity, make_ref
from etalon.research.method_revision import journal_ref


def fixture_case(root, sid):
    store = CampaignStore(root / "research.sqlite")
    store.configure(CampaignSpec("audit", 0, "wall_second", "synthetic"),
        [Endpoint("audit", "synthetic", "unmeasured", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    related_cases, extra_sources = [], []
    conditions = {"endpoint": "synthetic/1", "operation": "read"}

    def ref(rid):
        return journal_ref(journal, rid, source_id=sid)

    def artifact(name, text):
        path = root / name
        path.write_text(text, encoding="utf-8")
        return ref(journal.record(Artifact(str(path), **file_identity(path), source_kind="fixture",
                                          role="diagnostic", bindings={})))

    def workspace():
        sources = [{"source_id": sid, "kind": "journal", "location": str(store.path),
                    "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}}]
        for other in related_cases:
            sources.append({"source_id": other["sid"], "kind": "journal", "location": str(other["journal"].store.path),
                "identity": {"kind": "snapshot_sha256", "value": file_identity(other["journal"].store.path)["sha256"]}})
        path = root / "workspace.json"
        path.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "synthetic-" + sid,
                                   "sources": sources + extra_sources, "location_map": []}), encoding="utf-8")
        return path

    def save(content):
        return ref(record_content(journal, content, root / "content", source_id=sid,
                                  source_kind="fixture", external_workspace=workspace()))

    target = artifact("method.txt", "Synthetic shared method bytes")
    problem = artifact("problem.txt", "Synthetic shared problem bytes")
    evidence = artifact("evidence.txt", "Synthetic shared evidence bytes")
    review = artifact("review.txt", "Synthetic host review only")

    def member(evidence_ref=None, relation="same_issue", scope="synthetic-scope"):
        context = improvement.build_revision_context(workspace(), task_id="synthetic-task", scope=scope,
            conditions=conditions, target_ref=target, problem_refs=[problem], evidence_refs=[evidence_ref or evidence])
        context_ref = save(context)
        opened = improvement.register_opportunity(journal, context_ref, root / "content", source_id=sid,
                                                  source_kind="fixture", external_workspace=workspace())
        return defect_family.review_defect_member(workspace(), context_ref=context_ref,
            opportunity_ref=opened["opportunity_ref"], relation=relation,
            grouping_reason="Synthetic explicit scope/mechanism review", review_refs=[review])

    def close(member):
        return save(opportunity_state.build_opportunity_disposition(journal,
            opportunity_ref=member["opportunity_ref"], outcome="no_change", reason="Synthetic no-change judgment",
            reviewer="fixture-host", support_refs=[evidence], counter_refs=[problem],
            reconsider_when=[{"condition_id": "new-original", "condition": "New original evidence changes diagnosis"}],
            source_id=sid, external_workspace=workspace()))

    def recover(current, members):
        family = defect_family.build_defect_family(workspace(), task_id="synthetic-task", scope="synthetic-scope",
            conditions=conditions, capability_id="synthetic-reader", mechanism_hypothesis="Synthetic shared mechanism",
            members=members)  # Capability family retains each source's exact target.
        family_ref = save(family)
        result = improvement.register_opportunity(journal, current["context_ref"], root / "content", source_id=sid,
            source_kind="fixture", family_ref=family_ref, external_workspace=workspace())
        return result

    return dict(sid=sid, root=root, journal=journal, member=member, close=close, recover=recover,
                workspace=workspace, related_cases=related_cases, extra_sources=extra_sources)


@pytest.mark.parametrize("same_bytes", [False, True])
def test_selected_members_compare_their_own_bytes_when_recovering_terminal(tmp_path, same_bytes):
    case = fixture_case(tmp_path / "local", "local")
    members = []
    for name, text in (("one", "Synthetic original A"),
                       ("two", "Synthetic original A" if same_bytes else "Synthetic original B")):
        path = case["root"] / (name + ".txt")
        path.write_text(text, encoding="utf-8")
        members.append({"member": name, "path": str(path), **file_identity(path), "visibility": "released"})
    body = {"role": "diagnostic", "members": members, "fixture": "synthetic member envelope"}
    manifest = case["root"] / "envelope.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [{"record_id": "envelope",
        "record_type": "data_snapshot", "visibility": "released", "record": body}]}), encoding="utf-8")
    case["extra_sources"].append({"source_id": "members", "kind": "artifact_manifest", "location": str(manifest),
        "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    refs = [{**make_ref("members", "envelope", body), "member": item["member"]} for item in members]
    first = case["member"](refs[0])
    terminal = case["close"](first)
    second = case["member"](refs[1])
    result = case["recover"](second, [first, second])
    assert result["state"] == ("family_terminal" if same_bytes else "family_reconsideration_required")
    assert result["proposal_permission"] == "not_granted"
    prior = result["family"]["related_terminals"][0]
    assert prior["same_original_inputs"] is same_bytes and prior["state"]["terminal_ref"] == terminal
    assert result["family"]["foreign_lifecycle"] == []


@pytest.mark.parametrize("has_terminal", [False, True])
def test_foreign_accepted_member_never_looks_open_from_an_empty_local_history(tmp_path, has_terminal):
    local = fixture_case(tmp_path / "local", "local")
    foreign = fixture_case(tmp_path / "foreign", "foreign")
    old = foreign["member"]()
    terminal = foreign["close"](old) if has_terminal else None
    foreign_before = file_identity(foreign["journal"].store.path)
    local["related_cases"].append(foreign)
    current = local["member"]()
    result = local["recover"](current, [old, current])
    assert result["state"] == "family_review_required"
    assert result["proposal_permission"] == "not_granted"
    assert result["family"]["related_terminals"] == []
    assert result["family"]["foreign_lifecycle"] == [{"opportunity_ref": old["opportunity_ref"],
        "state": "unknown", "coverage": "external_lifecycle_not_checked",
        "reason": "current journal cannot establish the external opportunity head"}]
    assert file_identity(foreign["journal"].store.path) == foreign_before
    if terminal:
        old_state = opportunity_state.resolve_opportunity_state(foreign["journal"], old["opportunity_ref"], source_id="foreign")
        assert old_state["terminal_ref"] == terminal and old_state["terminal"]
    assert not local["journal"].store.actions() and not foreign["journal"].store.actions()


def test_foreign_reference_only_does_not_gate_the_local_opportunity(tmp_path):
    local = fixture_case(tmp_path / "local", "local")
    foreign = fixture_case(tmp_path / "foreign", "foreign")
    reference = foreign["member"](relation="reference_only", scope="another-domain")
    foreign["close"](reference)
    local["related_cases"].append(foreign)
    current = local["member"]()
    result = local["recover"](current, [reference, current])
    assert result["state"] == "resume_pending" and result["family"]["foreign_lifecycle"] == []
    assert result["family"]["related_terminals"] == []
