"""Bounded capability evidence over explicit decisions and evaluation contracts.

This is a read-only projection, not a capability score, registry, activation or
scientific independence certificate. Repeated observations do not become new
cases merely because their summaries, labels or evidence aliases differ.
"""
from __future__ import annotations

import json
from collections import Counter

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, validate_ref
from .evaluation_contract import evaluate_contract
from .read_scope import bounded_read
from .opportunity_state import _material

SCHEMA = "etalon-capability-profile/1"
MAX_STUDIES = 32


def _key(ref):
    validate_ref(ref)
    return {k: ref[k] for k in ("source_id", "record_id", "record_digest", "member") if k in ref}


def _keys(refs):
    return sorted((_key(ref) for ref in refs), key=canonical)


def _body(workspace, ref, schema=None):
    body, _, _ = _material(None, ref, None, external_workspace=workspace)
    if not isinstance(body, dict):
        raise EvidenceError("profile needs a complete bounded structured original")
    if schema and body.get("schema") != schema:
        raise EvidenceError("wrong profile evidence schema")
    return body


@bounded_read
def build_capability_profile(workspace=None, *, studies, scope, entry=None, read_context=None):
    """Re-evaluate exact contracts; group by structure and declared version refs.

    studies = [{decision_ref, update_ref, evaluation_contract_ref, case_ref,
                case_id, family_id, repeat_id}]. case_ref is an ordinary object
    carrying the same case_id and family_id. These group labels are attributable
    declarations, not proof of molecular dissimilarity or independent sampling.
    A null update/contract is a visible gap, not a passed case. Never infer the
    globally latest update from a bounded explicit list.
    """
    from .entry import resolve_read_workspace
    from .continuation import _exact_refs
    from .research_decision import DECISION, UPDATE, validate_links

    if not isinstance(scope, str) or not scope.strip():
        raise EvidenceError("explicit profile scope required")
    if not isinstance(studies, list) or not 1 <= len(studies) <= MAX_STUDIES:
        raise EvidenceError("profile needs 1..32 explicit studies")
    workspace = resolve_read_workspace(workspace, entry=entry, read_context=read_context)
    rows, duplicates, seen, groups = [], [], {}, {}
    for study in studies:
        if not isinstance(study, dict) or set(study) != {
                "decision_ref", "update_ref", "evaluation_contract_ref", "case_ref",
                "case_id", "family_id", "repeat_id"}:
            raise EvidenceError("study needs exact decision/update/contract/case references and labels")
        for key in ("case_id", "family_id", "repeat_id"):
            if not isinstance(study[key], str) or not study[key].strip():
                raise EvidenceError("explicit case, family and repeat labels required")
        decision = _body(workspace, study["decision_ref"], DECISION)
        validate_links(workspace, decision)
        case = _body(workspace, study["case_ref"])
        if not any(_key(ref) == _key(study["case_ref"]) for ref in _exact_refs(decision)):
            raise EvidenceError("case identity is not linked to this decision")
        if any(case.get(key) != study[key] for key in ("case_id", "family_id")):
            raise EvidenceError("case grouping differs from original case declaration")
        update, report, gaps = None, None, []
        if study["update_ref"] is not None:
            update = _body(workspace, study["update_ref"], UPDATE)
            validate_links(workspace, update)
            if _key(update["decision_ref"]) != _key(study["decision_ref"]):
                raise EvidenceError("update belongs to another decision")
        else:
            gaps.append("decision has no supplied outcome update")
        if study["evaluation_contract_ref"] is not None:
            report = evaluate_contract(workspace, contract_ref=study["evaluation_contract_ref"])
            if report["task_id"] != decision["task_id"]:
                raise EvidenceError("evaluation belongs to another task")
            linked = decision.get("evaluation_refs", []) + (update.get("evaluation_refs", []) if update else [])
            linked_contract = any(_key(ref) == _key(study["evaluation_contract_ref"]) for ref in linked)
            if not linked_contract:
                for ref in linked:
                    body = _body(workspace, ref)
                    if body.get("schema") == "etalon-evaluation-report/1":
                        linked_contract |= body.get("contract_digest") == report["contract_digest"]
            if not linked_contract:
                raise EvidenceError("evaluation contract is not linked to this decision or update")
        else:
            gaps.append("no evaluation contract supplied")
        attribution = decision["attribution"]
        if attribution["model_ref"] is None:
            gaps.append("model identity unknown")
        if not attribution["verifier_refs"]:
            gaps.append("verifier identity unknown")
        if decision.get("gaps"):
            gaps.append("decision retains declared gaps")
        if update and update.get("gaps"):
            gaps.append("outcome update retains declared gaps")
        versions = {
            "model": _key(attribution["model_ref"]) if attribution["model_ref"] else None,
            "skills": _keys(attribution["skill_refs"]),
            "methods": _keys(decision["method_refs"]),
            "verifiers": _keys(attribution["verifier_refs"]),
            "evaluation_subjects": _keys(report["contract"]["subjects"]) if report else [],
            "evaluation_comparators": _keys(report["contract"]["comparators"]) if report else [],
        }
        grouping = dict(structure=decision["structure"], scope=decision["scope"],
                        versions=versions, rules_digest=report["rules_digest"] if report else None)
        group_id = digest(grouping)
        # Preserve the measured result bytes, even when they contain provenance
        # links. Following those links instead would merge different results
        # made from the same inputs. An altered JSON wrapper is a different byte
        # bundle, not proof of another observation or independent execution.
        budget, originals = {"bytes": 0}, {}
        for item in report["contract"]["inputs"] if report else []:
            if item["kind"] == "result":
                _, _, identity = _material(None, item["ref"], None, external_workspace=workspace, _budget=budget)
                if identity is not None:
                    originals[canonical(identity)] = identity
        identities = [originals[k] for k in sorted(originals)]
        observation = dict(originals=identities) if identities else {
            "decision": _key(study["decision_ref"]), "measurement": "unavailable"}
        observation_key = digest(observation)
        row = dict(study=study, task_id=decision["task_id"], group_id=group_id,
                   observation_key=observation_key,
                   result_originals=identities,
                   status=report["status"] if report else "insufficient",
                   families=report["families"] if report else {},
                   contract_digest=report["contract_digest"] if report else None,
                   freeze_status=report["freeze_status"] if report else "not_evaluated",
                   disposition=update["disposition"] if update else None,
                   prediction_judgments=update["judgments"] if update else [],
                   judgment_authority="attributable_host_judgment_not_independent_truth",
                   metrics=report["metrics"] if report else [], gaps=gaps,
                   counter_refs=update["counter_refs"] if update else decision["counter_refs"])
        row["reused_result_bundle"] = observation_key in seen
        if observation_key in seen:
            duplicates.append(dict(study=study, original_row=seen[observation_key], assessment_row=len(rows),
                reason="same verified result bytes; assessment retained in its own version group"))
        else:
            seen[observation_key] = len(rows)
        rows.append(row)
        groups.setdefault(group_id, dict(grouping=grouping, indices=[]))["indices"].append(len(rows) - 1)

    summaries = []
    for group_id, group in groups.items():
        members = [rows[index] for index in group["indices"]]
        statuses = Counter(row["status"] for row in members)
        # A case is a declared cluster, not its repeat count. Discrepant case
        # manifests remain visible; no independence count is invented.
        cases = {(r["study"]["family_id"], r["study"]["case_id"]) for r in members}
        families = {r["study"]["family_id"] for r in members}
        family_statuses = {}
        for family in ("regression", "retention", "funnel", "cost"):
            family_statuses[family] = dict(Counter(
                row["families"].get(family, {}).get("status", "not_declared") for row in members))
        summaries.append(dict(group_id=group_id, **group, assessments=len(members),
            distinct_result_bundles=len({r["observation_key"] for r in members if r["result_originals"]}),
            declared_case_clusters=len(cases), declared_family_clusters=len(families),
            status_counts=dict(statuses), family_status_counts=family_statuses,
            incomplete_observations=sum(bool(row["gaps"]) or row["status"] == "insufficient" for row in members),
            prediction_judgment_counts=dict(Counter(j["status"] for row in members for j in row["prediction_judgments"])),
            independent_case_count=None, transferable_benefit="not_established_by_aggregation"))
    result = dict(schema=SCHEMA, scope=scope, rows=rows, groups=summaries,
        duplicate_observations=duplicates, requested_studies=len(studies), unique_observations=None,
        distinct_result_bundles=len({r["observation_key"] for r in rows if r["result_originals"]}),
        status_count_basis="explicit assessments, including repeated evaluations; not independent outcomes",
        observation_identity="result byte bundles only; no inference of unique observations or independent runs",
        coverage="explicit_studies_only_not_global_latest", execution="not_performed", adoption="not_performed",
        case_grouping="verified_original_declarations_not_certified_independence",
        uncertainty="no_confidence_interval_or_statistical_noninferiority_inferred",
        total_cost=None, cost_scope="named metric observations only; incomplete lifecycle cost is unknown",
        independent_validation="not_established", scientific_truth="not_certified")
    if len(canonical(result).encode("utf-8")) > 512 * 1024:
        raise EvidenceError("profile exceeds 512 KiB; request fewer explicit studies")
    return json.loads(canonical(result))
