"""Publish an explicitly reviewed resource plan using existing archival APIs.

This CLI never infers applicability, submits science, starts a chat or runs RSI.
The JSON plan contains profile, resources, decisions, reviewer, rationale,
template, mode and optional fallback_reason. See the research-index skill.
"""
import argparse
import json
from pathlib import Path

from etalon.active.schema import digest
from etalon.research.entry import resolve_entry
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.meta_context import (
    make_task_profile, index_resources, review_adaptation, build_task_workset,
    read_task_workset, publish_task_workset,
)
from etalon.research.publication import resume_publication, _save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    identity = {"entry": str(args.entry.absolute()), "operation_id": args.operation_id}
    accepted = args.directory / ("accepted-plan-" + digest(identity) + ".json")
    if accepted.exists():
        saved = json.loads(accepted.read_text(encoding="utf-8"))
        if saved["plan_digest"] != digest(plan):
            raise EvidenceError("operation id reused for a different plan")
    else:
        entry_identity = file_identity(args.entry)
        ws, cp, _, _ = resolve_entry(args.entry)
        profile = make_task_profile(**plan["profile"])
        index = index_resources(plan["resources"], workspace=ws)
        review = review_adaptation(profile, index, decisions=plan["decisions"],
            reviewer=plan["reviewer"], rationale=plan["rationale"])
        previous = read_task_workset(ws, cp)
        workset = build_task_workset(profile, index, review, template=plan["template"],
            mode=plan.get("mode", "selected"), fallback_reason=plan.get("fallback_reason"),
            parent_workset_ref=previous.get("ref"))
        saved = {"workset": workset, "entry_identity": entry_identity, "plan_digest": digest(plan)}
        _save(accepted, saved)
    prepared = publish_task_workset(args.entry, saved["workset"],
        operation_id=args.operation_id, directory=args.directory,
        expected_entry_identity=saved["entry_identity"])
    report = prepared if args.prepare_only else resume_publication(prepared["intent_path"])
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
