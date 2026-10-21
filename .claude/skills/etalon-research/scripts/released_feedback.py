"""Append feedback from an existing validation in a caller-owned writable archive.

Optional SDK example. No oracle, fitting, new validation comparison, or dispatch.
Use a copy of any archive whose original bytes must remain unchanged.
"""
import argparse
import json
from pathlib import Path
import sys
import time

from etalon.active import CampaignStore
from etalon.active.schema import digest
from etalon.research import ResearchJournal
from etalon.research.validation import attach_decision_validation
from etalon.research.controller import independent_feedback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--validation", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("database must already exist and be configured")
    # Reserve the output before appending; never overwrite a prior receipt.
    with args.output.open("x", encoding="utf-8") as output:
        started = time.perf_counter()
        result = {"database": str(args.database.absolute()), "validation_id": args.validation,
                  "requested_decision_sha256": args.decision, "monetary_cost": None}
        code = 0
        try:
            # Check the existing archive before opening its writable SDK handle.
            journal = ResearchJournal(CampaignStore(args.database, read_only=True))
            validation = journal.get(args.validation)
            if (validation.get("schema") != "etalon-decision-validation/1"
                    or "decisionvalidation-" + digest(validation) != args.validation):
                raise ValueError("validation schema or identity mismatch")
            if validation["decision_sha256"] != args.decision:
                raise ValueError("independent feedback must bind this frozen decision")
            controller = journal.get(args.controller)
            if (controller.get("schema") != "etalon-improvement-controller/1"
                    or "controller-" + digest(controller) != args.controller):
                raise ValueError("controller schema or identity mismatch")
            journal = ResearchJournal(CampaignStore(args.database))
            # Replay the existing attachment to reuse its role, freeze and file checks.
            checked = attach_decision_validation(
                journal, decision_sha256=validation["decision_sha256"],
                evidence=tuple(validation["evidence"]), release_evidence=validation["release_evidence"],
                protocol=validation["protocol"], comparison=validation["comparison"],
            )
            if checked["id"] != args.validation:
                raise ValueError("validation identity changed")
            feedback = independent_feedback(journal, controller_id=args.controller,
                decision_sha256=args.decision, validation_id=args.validation)
            result.update(status="recorded", feedback_id="controllerfeedback-" + digest(feedback),
                          feedback=feedback, retained_comparison=validation["comparison"])
        except Exception as exc:
            code = 2
            result.update(status="not_completed", error_type=type(exc).__name__, error=str(exc))
        result["local_wall_seconds"] = time.perf_counter() - started
        json.dump(result, output, indent=2, ensure_ascii=False, allow_nan=False)
        output.write("\n")
    print(json.dumps({"status": result["status"], "output": str(args.output)}))
    return code


if __name__ == "__main__":
    sys.exit(main())
