"""Synthetic contract counterexamples, not new molecular scientific evidence."""
import copy
from pathlib import Path

import pytest

from etalon.research import Artifact, MethodVersion
from etalon.research import research_model as rm
from etalon.research.components import record_json
from etalon.research.composition import node_step
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.method_revision import journal_ref
from test_research_continuation import case, publish, save


@pytest.fixture
def prepared(case):
    journal, original, _, root = case
    ref = lambda rid: journal_ref(journal, rid, source_id="research")
    artifact = lambda obj: ref(record_json(journal, obj, root / "artifacts", source_kind="fixture"))
    checker = artifact({"fixture": "Read-only checker identity, never executable"})
    input_ref = artifact({"fixture": "same released input", "target": "fixture"})
    issue = dict(issue_id="quality-gap", statement="Fixture negative observation remains unexplained",
                 witness_refs=[input_ref], status="unresolved", resolution_refs=[])
    rule = dict(rule_id="quality", statement="Fixture declared quality rule", judgment_kind="method_quality",
        status="active", applicability={"conditions": {"target": "fixture"}, "unknowns": []},
        checker_ref=checker, predictor_ref=None, verdict_map=[{"value": v, "verdict": v} for v in sorted(rm.VERDICTS - {"NOT_RUN"})],
        dependencies=[], support_refs=[], counter_refs=[input_ref], issue_ids=[issue["issue_id"]])
    args = dict(model_id="fixture-model", task_id="case", scope="fixture only", conditions={"target": "fixture"},
                rules=[rule], issues=[issue], counter_refs=[input_ref])
    return dict(journal=journal, original=original, root=root, ref=ref, artifact=artifact, checker=checker,
                input=input_ref, rule=rule, issue=issue, args=args)


def models(case, p, change=None):
    before = p["artifact"](rm.build_research_model(publish(case), **p["args"]))
    args = copy.deepcopy(p["args"])
    args["supersedes"] = [before]
    if change:
        change(args)
    after = p["artifact"](rm.build_research_model(publish(case), **args))
    return before, after


def assessment(case, p, verdict="FALSE", rule_id="quality", state="completed", observation=None):
    output = observation or p["artifact"]({"verdict": verdict})
    node = save(case, node_step(task_id="case", question="Fixture normal read", branch_id="normal", node_id=verdict,
        state=state, reason="Fixture receipt, not native molecular computation",
        input_refs=[p["input"], p["checker"]], output_refs=[output], receipt_refs=[output]))
    return dict(rule_id=rule_id, verdict=verdict, observation_ref=output, pointer="/verdict", execution_ref=node)


def mapping(op="unchanged", before="quality", after="quality"):
    return dict(before_rule_ids=[before], after_rule_ids=[after], operation=op, reason="Explicit fixture mapping")


def review(case, p, before, after, *, first=None, second=None, **kwargs):
    first = first or assessment(case, p)
    second = second or first
    return rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="refactor",
        mappings=[mapping()], assessments=[dict(case_id="case-1", case_ref=p["input"], role="counterexample",
                                                before=[first], after=[second])], **kwargs)


def test_manifest_review_and_readback_are_readonly_without_reopen_or_benefit(case, prepared):
    p = prepared
    before, after = models(case, p)
    first = assessment(case, p)
    events = p["journal"].store.events()
    result = review(case, p, before, after, first=first)
    assert p["journal"].store.events() == events
    assert result["coverage"]["status"] == "preserved_on_declared_witnesses"
    assert result["evidence_novelty"] == "none"
    assert result["boundary"]["scientific_benefit"] == "not_established"
    assert result["boundary"]["opportunity_reopen"] == "not_performed"
    assert result["counterexample_obligations"][0]["status"] == "retained_counterexample"
    saved = p["artifact"](result)
    assert rm.read_model_revision(publish(case), saved) == result
    assert not p["journal"].store.actions()


@pytest.mark.parametrize("change", ["rename_issue", "drop_witness", "drop_counter"])
def test_successor_cannot_wash_away_issues_or_counterevidence(case, prepared, change):
    p = prepared
    def mutate(args):
        if change == "rename_issue":
            args["issues"][0]["issue_id"] = "fresh-name"
            args["rules"][0]["issue_ids"] = ["fresh-name"]
        elif change == "drop_witness":
            args["issues"][0]["witness_refs"] = [p["original"]]
        else:
            args["counter_refs"] = []
            args["rules"][0]["counter_refs"] = []
    with pytest.raises(EvidenceError, match="counterevidence|rename/drop"):
        models(case, p, mutate)


def test_false_to_na_is_retained_as_coverage_loss_not_repaired(case, prepared):
    p = prepared
    before, after = models(case, p)
    result = review(case, p, before, after, second=assessment(case, p, "NA"))
    assert result["coverage"]["status"] == "coverage_changed"
    assert result["counterexample_obligations"][0]["status"] == "review_required"
    assert result["retained_issues"][0]["status"] == "unresolved"
    assert result["boundary"]["scientific_benefit"] == "not_established"


def test_unrun_and_unknown_are_gaps_not_improvement(case, prepared):
    p = prepared
    before, after = models(case, p)
    not_run = dict(rule_id="quality", verdict="NOT_RUN", observation_ref=None, pointer=None, execution_ref=None)
    result = review(case, p, before, after, first=not_run, second=not_run)
    assert result["coverage"]["status"] == "incomplete"
    forged = {**not_run, "observation_ref": p["input"]}
    with pytest.raises(EvidenceError, match="NOT_RUN"):
        review(case, p, before, after, first=forged)


@pytest.mark.parametrize("field", ["verdict", "observation_ref", "pointer", "execution_ref"])
def test_verdict_requires_exact_existing_execution_and_observation(case, prepared, field):
    p = prepared
    before, after = models(case, p)
    row = assessment(case, p)
    if field == "verdict":
        row[field] = "TRUE"
    elif field == "observation_ref":
        row[field] = p["artifact"]({"verdict": "FALSE", "unrelated": True})
    elif field == "pointer":
        row[field] = "/absent"
    else:
        row[field] = save(case, node_step(task_id="case", question="Fixture", branch_id="normal", node_id="pending",
            state="planned", reason="Not executed", input_refs=[p["input"]]))
    with pytest.raises(EvidenceError):
        review(case, p, before, after, first=row)


def test_changed_original_bytes_and_forged_ref_are_rejected(case, prepared):
    p = prepared
    before, _ = models(case, p)
    bad = {**before, "record_digest": "0" * 64}
    with pytest.raises(EvidenceError):
        rm.read_research_model(publish(case), bad)
    Path(p["journal"].get(p["checker"]["record_id"])["path"]).write_text("changed", encoding="utf-8")
    with pytest.raises((EvidenceError, ValueError)):
        rm.read_research_model(publish(case), before)


@pytest.mark.parametrize("kind", ["cycle", "unknown", "withdrawn"])
def test_rule_dependencies_must_be_available_acyclic_and_active(case, prepared, kind):
    args = copy.deepcopy(prepared["args"])
    args["rules"][0]["dependencies"] = ["quality" if kind == "cycle" else "other"]
    if kind == "withdrawn":
        other = copy.deepcopy(prepared["rule"])
        other.update(rule_id="other", status="withdrawn")
        args["rules"].append(other)
    with pytest.raises(EvidenceError, match="cyclic|unavailable|withdrawn"):
        rm.build_research_model(publish(case), **args)


def test_unknown_qualification_does_not_become_scientific_false(case, prepared):
    p = prepared
    args = copy.deepcopy(p["args"])
    args["rules"][0]["applicability"] = {"conditions": {"stereochemistry": None}, "unknowns": ["stereochemistry"]}
    p["args"] = args
    before, after = models(case, p)
    with pytest.raises(EvidenceError, match="unknown applicability"):
        review(case, p, before, after)
    result = review(case, p, before, after, first=assessment(case, p, "UNKNOWN"))
    assert result["coverage"]["status"] == "incomplete"


def test_renaming_a_rule_is_neither_new_evidence_nor_content_improvement(case, prepared):
    p = prepared
    before, after = models(case, p, lambda args: args["rules"][0].update(rule_id="renamed"))
    first = assessment(case, p)
    second = {**first, "rule_id": "renamed"}
    result = rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="refactor",
        mappings=[mapping("rename", after="renamed")], assessments=[dict(case_id="case-1", case_ref=p["input"],
        role="counterexample", before=[first], after=[second])])
    assert not result["declared_rule_content_changed"] and result["evidence_novelty"] == "none"
    assert result["retained_issues"][0]["issue_id"] == "quality-gap"


def test_refactor_cannot_change_judgment_type_or_applicability(case, prepared):
    p = prepared
    before, after = models(case, p, lambda args: args["rules"][0].update(judgment_kind="diagnosis_prediction"))
    with pytest.raises(EvidenceError, match="knowledge revision|applicability|standards"):
        rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="refactor",
            mappings=[mapping("specialize")])
    result = rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="knowledge_revision",
        mappings=[mapping("specialize")])
    assert result["evidence_novelty"] == "none" and result["coverage"]["status"] == "incomplete"


def test_aliased_case_cannot_inflate_witness_count(case, prepared):
    p = prepared
    before, after = models(case, p)
    row = assessment(case, p)
    cases = [dict(case_id=n, case_ref=p["input"], role="counterexample", before=[row], after=[row]) for n in ("one", "two")]
    with pytest.raises(EvidenceError, match="inflate coverage"):
        rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="refactor",
            mappings=[mapping()], assessments=cases)


def test_forged_saved_review_and_bounded_originals(case, prepared, monkeypatch):
    p = prepared
    before, after = models(case, p)
    result = review(case, p, before, after)
    forged = copy.deepcopy(result)
    forged["coverage"]["case_count"] = 999
    saved = p["artifact"](forged)
    with pytest.raises(EvidenceError, match="verified originals"):
        rm.read_model_revision(publish(case), saved)
    monkeypatch.setattr(rm, "MAX_BYTES", 100)
    with pytest.raises(EvidenceError, match="32 KiB"):
        rm.read_research_model(publish(case), before)


def test_method_version_checker_verifies_its_content_bytes(case, prepared):
    p = prepared
    checker = p["ref"](p["journal"].record(MethodVersion("fixture checker", ("domain_skill",),
        {"artifact_ref": p["checker"]["record_id"]})))
    args = copy.deepcopy(p["args"])
    args["rules"][0]["checker_ref"] = checker
    manifest = rm.build_research_model(publish(case), **args)
    saved = p["artifact"](manifest)
    Path(p["journal"].get(p["checker"]["record_id"])["path"]).write_text("changed fixture", encoding="utf-8")
    with pytest.raises((EvidenceError, ValueError)):
        rm.read_research_model(publish(case), saved)


def test_assessment_helper_derives_verdict_without_accepting_a_claimed_pass(case, prepared):
    p = prepared
    before, _ = models(case, p)
    row = assessment(case, p)
    actual = rm.recorded_rule_assessment(publish(case), model_ref=before, rule_id="quality", case_ref=p["input"],
        **{k: row[k] for k in ("observation_ref", "pointer", "execution_ref")})
    assert actual == row and actual["verdict"] == "FALSE"


def test_model_bytes_cannot_be_supplied_as_novel_scientific_evidence(case, prepared):
    p = prepared
    before, after = models(case, p)
    with pytest.raises(EvidenceError, match="not new observed evidence"):
        review(case, p, before, after, evidence_refs=[after])


def test_independent_mapping_groups_cannot_hide_swapped_positive_negative(case, prepared):
    p = prepared
    other = copy.deepcopy(p["rule"])
    other.update(rule_id="other", statement="Different independent criterion", issue_ids=[], counter_refs=[])
    p["args"]["rules"].append(other)
    before, after = models(case, p)
    a0, b0 = assessment(case, p, "FALSE"), assessment(case, p, "TRUE", rule_id="other")
    a1, b1 = assessment(case, p, "TRUE"), assessment(case, p, "FALSE", rule_id="other")
    result = rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="refactor",
        mappings=[mapping(), mapping(before="other", after="other")], assessments=[dict(case_id="shared-case",
        case_ref=p["input"], role="counterexample", before=[a0, b0], after=[a1, b1])])
    assert result["coverage"]["status"] == "coverage_changed"
    assert len(result["coverage_losses"]) == 2
    assert result["counterexample_obligations"][0]["before_rule_ids"] == ["quality"]


def test_rule_witness_pairs_cannot_be_replaced_by_cartesian_marginal_coverage(case, prepared):
    p = prepared
    other_case = p["artifact"]({"another": "independent input"})
    other = copy.deepcopy(p["rule"])
    other.update(rule_id="other", statement="Other criterion with another counterexample", issue_ids=[], counter_refs=[other_case])
    p["args"]["rules"].append(other)
    p["args"]["counter_refs"].append(other_case)
    before, after = models(case, p)
    wrong_x = assessment(case, p, rule_id="other")
    wrong_y = assessment(case, {**p, "input": other_case})
    result = rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="refactor",
        mappings=[mapping(), mapping(before="other", after="other")], assessments=[
            dict(case_id="x", case_ref=p["input"], role="counterexample", before=[wrong_x], after=[wrong_x]),
            dict(case_id="y", case_ref=other_case, role="counterexample", before=[wrong_y], after=[wrong_y])])
    assert result["coverage"]["status"] == "incomplete"
    assert not result["coverage"]["missing_counter_refs"] and not any(result["coverage"]["missing_rule_ids"].values())
    pairs = {(r["rule_id"], r["witness_ref"]["record_id"]) for r in result["coverage"]["missing_rule_witnesses"]}
    assert ("quality", p["input"]["record_id"]) in pairs and ("other", other_case["record_id"]) in pairs


def test_issue_cannot_resolve_itself_and_host_claim_never_erases_current_false(case, prepared):
    p = prepared
    def self_resolve(args):
        args["issues"][0].update(status="resolved", resolution_refs=[p["input"]])
    with pytest.raises(EvidenceError, match="resolve their own issue"):
        models(case, p, self_resolve)
    unrelated = p["artifact"]({"host": "claims resolution; not mechanically certified"})
    before, after = models(case, p, lambda args: args["issues"][0].update(status="resolved", resolution_refs=[unrelated]))
    result = review(case, p, before, after)
    assert "quality-gap" in result["unresolved_issue_ids"]
    assert result["issue_resolution_claims"][0]["verification"] == "host_declared_not_certified"
    assert result["issue_resolution_claims"][0]["closes_counterexample_obligation"] is False


def test_unassigned_model_counter_requires_attribution_even_when_case_seen(case, prepared):
    p = prepared
    p["args"]["rules"][0]["counter_refs"] = []
    before, after = models(case, p)
    result = review(case, p, before, after)
    assert result["coverage"]["status"] == "incomplete"
    assert result["coverage"]["unassigned_model_counter_refs"] == [p["input"]]


@pytest.mark.parametrize("verdict", ["UNKNOWN", "NOT_RUN", None])
def test_host_resolved_claim_never_clears_unverified_old_issue(case, prepared, verdict):
    p = prepared
    unrelated = p["artifact"]({"host": "unverified resolution declaration"})
    before, after = models(case, p, lambda args: args["issues"][0].update(status="resolved", resolution_refs=[unrelated]))
    rows = []
    if verdict is not None:
        row = (dict(rule_id="quality", verdict="NOT_RUN", observation_ref=None, pointer=None, execution_ref=None)
               if verdict == "NOT_RUN" else assessment(case, p, verdict))
        rows = [dict(case_id="unresolved", case_ref=p["input"], role="counterexample", before=[row], after=[row])]
    result = rm.review_model_revision(publish(case), before_ref=before, after_ref=after, revision_kind="refactor",
        mappings=[mapping()], assessments=rows)
    assert "quality-gap" in result["unresolved_issue_ids"]
    assert result["issue_resolution_claims"][0]["closes_counterexample_obligation"] is False


def test_intermediate_host_resolution_does_not_launder_ancestral_obligation(case, prepared):
    p = prepared
    unrelated = p["artifact"]({"host": "unverified resolution declaration"})
    _, middle = models(case, p, lambda args: args["issues"][0].update(status="resolved", resolution_refs=[unrelated]))
    args = {k: v for k, v in rm.read_research_model(publish(case), middle).items() if k not in {"schema", "boundary"}}
    args["supersedes"] = [middle]
    last = p["artifact"](rm.build_research_model(publish(case), **args))
    result = rm.review_model_revision(publish(case), before_ref=middle, after_ref=last, revision_kind="refactor", mappings=[mapping()])
    assert "quality-gap" in result["unresolved_issue_ids"]
    assert any(r["obligation"] == "unresolved_issue" for r in result["coverage"]["missing_rule_witnesses"])
