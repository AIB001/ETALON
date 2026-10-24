"""Synthetic fixtures only: reviewed grouping is not scientific validation."""
import copy
import json
from pathlib import Path

import pytest

from etalon.active import CampaignSpec, CampaignStore, Endpoint
from etalon.active.store import StateError
from etalon.research import Artifact, ResearchJournal
from etalon.research import defect_family as family
from etalon.research import improvement
from etalon.research.continuation import record_content
from etalon.research.evidence_refs import EvidenceError, file_identity, make_ref
from etalon.research.method_revision import journal_ref


@pytest.fixture
def case(tmp_path):
    store = CampaignStore(tmp_path / "synthetic.sqlite")
    store.configure(CampaignSpec("audit", 0, "wall_second", "synthetic-only"),
        [Endpoint("audit", "synthetic", "unmeasured", "count", "fixture/1", 0, queryable=False)])
    journal = ResearchJournal(store)
    extra_sources = []

    def ref(record_id):
        return journal_ref(journal, record_id, source_id="research")

    def artifact(name, text):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return ref(journal.record(Artifact(str(path), **file_identity(path), source_kind="fixture",
                                          role="diagnostic", bindings={})))

    def save(value):
        return ref(record_content(journal, value, tmp_path / "content", source_id="research",
                                  source_kind="fixture", external_workspace=workspace()))

    def workspace():
        path = tmp_path / "workspace.json"
        path.write_text(json.dumps({"schema": "etalon-evidence-workspace/1", "workspace_id": "synthetic",
            "sources": [{"source_id": "research", "kind": "journal", "location": str(store.path),
                "identity": {"kind": "snapshot_sha256", "value": file_identity(store.path)["sha256"]}},
                *extra_sources],
            "location_map": []}), encoding="utf-8")
        return path

    target = artifact("method.txt", "Synthetic reader implementation, no execution")
    evidence = artifact("receipt.txt", "Synthetic failure receipt: wrong schema reader")
    problem = artifact("problem.txt", "Synthetic diagnosis label")
    review = artifact("review.txt", "Synthetic host review: same issue under explicit conditions")
    conditions = {"endpoint": "synthetic-released/1", "operation": "read"}

    def member(*, task_id="fixture-task", scope="synthetic-reader", cond=None, target_ref=None,
               evidence_ref=None, problem_ref=None, relation="same_issue", counter_refs=()):
        context = improvement.build_revision_context(workspace(), task_id=task_id, scope=scope,
            conditions=conditions if cond is None else cond, target_ref=target_ref or target,
            problem_refs=[problem_ref or problem], evidence_refs=[evidence_ref or evidence])
        context_ref = save(context)
        opportunity = improvement.register_opportunity(journal, context_ref, tmp_path / "content",
            source_id="research", source_kind="fixture")
        return family.review_defect_member(workspace(), context_ref=context_ref,
            opportunity_ref=opportunity["opportunity_ref"], relation=relation,
            grouping_reason="Explicit synthetic review; evidence is not a measured improvement",
            review_refs=[review], counter_refs=counter_refs)

    def build(members, **changes):
        options = dict(task_id="fixture-task", scope="synthetic-reader", conditions=conditions,
            capability_id="etalon.synthetic.reader", target_ref=target,
            mechanism_hypothesis="Synthetic hypothesis: typed reader rejects generic evidence", members=members)
        options.update(changes)
        return family.build_defect_family(workspace(), **options)

    return dict(journal=journal, root=tmp_path, artifact=artifact, save=save, workspace=workspace,
                member=member, build=build, target=target, evidence=evidence, review=review,
                problem=problem, conditions=conditions, extra_sources=extra_sources)


def test_repeated_review_publication_is_idempotent_and_wording_is_not_family_identity(case):
    member = case["member"]()
    events = case["journal"].store.events()
    value = case["build"]([member, copy.deepcopy(member)])
    assert value["members"] == [member]
    assert len(value["accepted_issue_keys"]) == 1
    assert case["journal"].store.events() == events
    ref = case["save"](value)
    events = case["journal"].store.events()
    assert case["save"](value) == ref
    assert case["journal"].store.events() == events
    renamed = case["build"]([member], mechanism_hypothesis="Renamed synthetic hypothesis")
    assert renamed["family_key"] == value["family_key"]
    assert family.read_defect_family(case["workspace"](), ref) == value
    member["grouping_reason"] = "Changed caller-owned input"
    assert value["members"][0]["grouping_reason"] != member["grouping_reason"]
    assert not case["journal"].store.actions()


def test_renamed_original_bytes_and_problem_labels_do_not_create_another_issue(case):
    original = case["member"]()
    evidence_alias = case["artifact"]("renamed-receipt.txt", "Synthetic failure receipt: wrong schema reader")
    problem_alias = case["artifact"]("renamed-problem.txt", "A different synthetic display label")
    alias = case["member"](evidence_ref=evidence_alias, problem_ref=problem_alias)
    assert original["context_ref"] != alias["context_ref"]
    assert original["opportunity_ref"] != alias["opportunity_ref"]
    assert original["issue_key"] == alias["issue_key"]
    one, renamed, both = (case["build"](rows) for rows in ([original], [alias], [alias, original]))
    assert one["family_key"] == renamed["family_key"] == both["family_key"]
    assert len(both["accepted_issue_keys"]) == 1 and len(both["members"]) == 2
    assert {r["record_id"] for m in both["members"] for r in m["problem_refs"]} == {
        case["problem"]["record_id"], problem_alias["record_id"]}
    case["save"](both)


@pytest.mark.parametrize("changes", [
    {"task_id": "another-task"}, {"scope": "another-domain"},
    {"cond": {"endpoint": "other/1", "operation": "read"}},
])
def test_same_issue_requires_exact_task_scope_and_all_conditions(case, changes):
    original = case["member"]()
    other = case["member"](**changes)
    with pytest.raises(EvidenceError, match="task/scope/conditions mismatch"):
        case["build"]([original, other])


def test_target_mismatch_is_not_an_accepted_member(case):
    target = case["artifact"]("other-target.txt", "Synthetic other target")
    other = case["member"](target_ref=target)
    with pytest.raises(EvidenceError, match="target mismatch"):
        case["build"]([other])


def test_cross_domain_reference_is_retained_without_acceptance(case):
    original = case["member"]()
    other = case["member"](task_id="cross-domain", scope="unrelated-synthetic-domain",
        cond={"endpoint": "different/1", "operation": "score"}, relation="reference_only")
    value = case["build"]([original, other])
    assert value["accepted_issue_keys"] == [original["issue_key"]]
    assert other["accepted"] is False and other in value["members"]
    ref = case["save"](value)
    assert family.read_defect_family(case["workspace"](), ref) == value
    with pytest.raises(EvidenceError, match="explicitly accepted"):
        case["build"]([other])


@pytest.mark.parametrize("changes", [{"scope": "unknown"}, {"cond": {"endpoint": "unknown"}}])
def test_unknown_scope_or_conditions_never_establish_same_issue(case, changes):
    with pytest.raises(EvidenceError, match="unknown scope/conditions"):
        case["member"](**changes)
    reference = case["member"](**changes, relation="reference_only")
    original = case["member"]()
    assert reference in case["build"]([original, reference])["members"]


def test_opportunity_context_mismatch_and_forged_member_bindings_are_rejected(case):
    first = case["member"]()
    different = case["member"](scope="different-scope")
    with pytest.raises(EvidenceError, match="exact complete context identity"):
        family.review_defect_member(case["workspace"](), context_ref=first["context_ref"],
            opportunity_ref=different["opportunity_ref"], relation="same_issue",
            grouping_reason="Synthetic inconsistent review", review_refs=[case["review"]])
    forged = copy.deepcopy(case["build"]([first]))
    forged["members"][0]["problem_refs"] = [case["review"]]
    family.validate_content(forged)  # Shape alone does not certify original content.
    with pytest.raises(EvidenceError, match="differs from verified"):
        case["save"](forged)


def test_bad_exact_ref_and_changed_original_bytes_fail_before_reuse(case):
    member = case["member"]()
    value = case["build"]([member])
    ref = case["save"](value)
    bad = {**ref, "record_digest": "0" * 64}
    with pytest.raises(EvidenceError):
        family.read_defect_family(case["workspace"](), bad)
    original_path = Path(case["journal"].get(case["evidence"]["record_id"])["path"])
    original_path.write_text("Changed synthetic original", encoding="utf-8")
    with pytest.raises(EvidenceError):
        family.read_defect_family(case["workspace"](), ref)
    with pytest.raises(StateError, match="artifact"):
        family.validate_local_links(case["journal"], value, "research")


def test_family_revision_retains_negative_and_conflicting_reviews(case):
    original = case["member"]()
    negative = case["artifact"]("negative.txt", "Synthetic counterexample; hypothesis not established")
    rejected = case["member"](relation="rejected", counter_refs=[negative])
    prior = case["build"]([original, rejected])
    prior_ref = case["save"](prior)
    newer = case["build"]([original], prior_family_ref=prior_ref,
        mechanism_hypothesis="Reconsidered hypothesis still retains the counterexample")
    assert newer["family_key"] == prior["family_key"]
    assert newer["supersedes"] == [prior_ref] and rejected in newer["members"]
    assert rejected["counter_refs"] == [negative] and not rejected["accepted"]
    ref = case["save"](newer)
    assert family.read_defect_family(case["workspace"](), ref) == newer
    dropped = {**newer, "members": [original]}
    with pytest.raises(EvidenceError, match="retain prior reviews"):
        case["save"](dropped)
    with pytest.raises(EvidenceError, match="same_issue member task/scope/conditions mismatch"):
        case["build"]([original], prior_family_ref=prior_ref, scope="broadened")


def test_new_reviewed_evidence_can_extend_a_family_without_changing_its_identity(case):
    original = case["member"]()
    first = case["build"]([original])
    first_ref = case["save"](first)
    new_evidence = case["artifact"]("new-receipt.txt", "Synthetic distinct failing receipt under the same conditions")
    other = case["member"](evidence_ref=new_evidence)
    extended = case["build"]([other], prior_family_ref=first_ref)
    assert extended["family_key"] == first["family_key"]
    assert len(extended["accepted_issue_keys"]) == 2 and original in extended["members"]
    case["save"](extended)


def test_external_members_use_selected_original_bytes_not_the_envelope_identity(case):
    root = case["root"]
    members = []
    for name, text in (("one", "Synthetic failure receipt: wrong schema reader"),
                       ("alias", "Synthetic failure receipt: wrong schema reader"),
                       ("other", "Synthetic distinct receipt in the same envelope")):
        path = root / (name + "-member.txt")
        path.write_text(text, encoding="utf-8")
        members.append({"member": name, "path": str(path), **file_identity(path), "visibility": "released"})
    envelope = {"role": "diagnostic", "members": members, "fixture": "synthetic external envelope"}
    manifest = root / "external.json"
    manifest.write_text(json.dumps({"schema": "etalon-artifact-manifest/1", "records": [{
        "record_id": "envelope", "record_type": "data_snapshot", "visibility": "released", "record": envelope}]}),
        encoding="utf-8")
    case["extra_sources"].append({"source_id": "external", "kind": "artifact_manifest",
        "location": str(manifest), "identity": {"kind": "snapshot_sha256", "value": file_identity(manifest)["sha256"]}})
    refs = [{**make_ref("external", "envelope", envelope), "member": item["member"]} for item in members]
    one, alias, other = (case["member"](evidence_ref=ref) for ref in refs)
    assert one["issue_key"] == alias["issue_key"] != other["issue_key"]
    assert one["issue_key"] == case["member"]()["issue_key"]
    value = case["build"]([one, alias, other])
    with pytest.raises(EvidenceError, match="verified evidence workspace"):
        record_content(case["journal"], value, root / "content", source_id="research", source_kind="fixture")
    ref = case["save"](value)
    assert family.read_defect_family(case["workspace"](), ref) == value
    Path(members[0]["path"]).write_text("Tampered synthetic member", encoding="utf-8")
    with pytest.raises(EvidenceError, match="mismatch"):
        family.read_defect_family(case["workspace"](), ref)
