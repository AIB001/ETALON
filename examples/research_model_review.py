"""Review existing reports through published refs; no science/checker is run.

Usage (paths and refs must belong to the actual published workspace):
  python examples/research_model_review.py --workspace workspace.json \
    --request request.json --output model-review.json

request.json contains:
  before_ref, after_ref: existing model Artifact EvidenceRefs
  revision_kind: refactor or knowledge_revision
  mappings: [{before_rule_ids, after_rule_ids, operation, reason}]
  cases: [{case_id, case_ref, role, before: [{rule_id, observation_ref,
            pointer, execution_ref}], after: [same]}]
  evidence_refs, decision_refs: optional exact existing references
  original_paths: optional [{ref: report EvidenceRef, path: actual local path}]

The reports need not contain a special model schema: pointers select existing
values, and the model's frozen verdict_map interprets them. For example the
method-quality mapping may associate criteria_passed with TRUE, criteria_failed
with FALSE, insufficient with UNKNOWN and incomparable with NA. A diagnosis
prediction uses its own separate checker/criterion, not the quality outcome.

The referenced existing node must bind case/checker inputs, output and receipt.
No TRUE/FALSE value is supplied by this script or accepted in the case request.
The output is an ordinary review Artifact payload; publish it using the existing
record_json/research-step/checkpoint interfaces. This script does not mutate the
journal, create a new opportunity, reopen one, or modify prior evidence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from etalon.active.schema import canonical
from etalon.research.evidence_refs import EvidenceError, file_identity
from etalon.research.improvement import read_material
from etalon.research.read_scope import bounded_read
from etalon.research.research_model import recorded_rule_assessment, review_model_revision


@bounded_read
def review_existing_reports(workspace, request):
    allowed = {"before_ref", "after_ref", "revision_kind", "mappings", "cases",
               "evidence_refs", "decision_refs", "original_paths"}
    if not isinstance(request, dict) or set(request) - allowed:
        raise EvidenceError("unsupported recorded-report request fields")
    for row in request.get("original_paths", []):
        if set(row) != {"ref", "path"}:
            raise EvidenceError("original path needs its exact published reference")
        material = read_material(workspace, row["ref"])
        original = material["ref"] if "member" in row["ref"] else material["record"]
        if file_identity(Path(row["path"])) != {k: original[k] for k in ("sha256", "bytes")}:
            raise EvidenceError("provided report path differs from the published original")
    assessments = []
    for case in request["cases"]:
        if set(case) != {"case_id", "case_ref", "role", "before", "after"}:
            raise EvidenceError("recorded witness case needs exact before/after selectors")
        item = {k: case[k] for k in ("case_id", "case_ref", "role")}
        for side in ("before", "after"):
            item[side] = [recorded_rule_assessment(workspace, model_ref=request[side + "_ref"],
                case_ref=case["case_ref"], **selector) for selector in case[side]]
        assessments.append(item)
    return review_model_revision(workspace, **{k: request[k] for k in (
        "before_ref", "after_ref", "revision_kind", "mappings")}, assessments=assessments,
        evidence_refs=request.get("evidence_refs", []), decision_refs=request.get("decision_refs", []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
    result = review_existing_reports(args.workspace, request)
    path = Path(args.output)
    raw = (canonical(result) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != raw:
            raise EvidenceError("review output already exists with different content")
    else:
        with path.open("xb") as handle:
            handle.write(raw)
    print(json.dumps({"output": str(path.absolute()), "coverage": result["coverage"],
                      "evidence_novelty": result["evidence_novelty"], "boundary": result["boundary"]}))


if __name__ == "__main__":
    main()
