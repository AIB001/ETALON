"""Small, read-only bridges between pinned qualifications and endpoint evaluation.

No parser, scientific admission, scheduler, or second record store lives here.
Domain errors are diagnostic data; changed originals and inaccessible evidence
are never converted into successful qualification.
"""
from __future__ import annotations

from etalon.active.schema import digest
from .evidence import _collect, _visible
from .evidence_refs import EvidenceError, EvidenceResolver, load_workspace, validate_ref
from .input_qualification import read_input_qualification


def boundary_diagnostic(phase, error, *, basis_refs=()):
    return {"phase": phase, "status": "unknown", "error_type": type(error).__name__,
            "reason": str(error)[:2000], "basis_refs": list(basis_refs),
            "repair_performed": False}


def qualification_for_pose(ref, *, workspace, predicted_identity, reference_identity,
                           pose_index, reference_index=0):
    """Recheck an exact released qualification and its original-source bindings.

The report must describe this exact file and record, not another pose from the
same molecule. Equality of a chemical name is not sufficient for this join.
"""
    ref = validate_ref(ref)
    if "member" in ref or "selector" in ref:
        raise EvidenceError("endpoint qualification requires a whole Artifact ref")
    ws = load_workspace(workspace)
    records, _, _ = _collect(ws, [ref["source_id"]])
    row = records.get(ref["source_id"], {}).get(ref["record_id"])
    if row is None or row.get("record_type") != "artifact":
        raise EvidenceError("qualification Artifact is unavailable")
    if digest(row["record"]) != ref["record_digest"] or not _visible(row):
        raise EvidenceError("qualification identity or visibility mismatch")
    body = row["record"]
    if "sha256" in ref and any(ref[k] != body[k] for k in ("sha256", "bytes")):
        raise EvidenceError("qualification ref file identity mismatch")
    report = read_input_qualification(body, workspace=ws, purpose="evaluation",
        resolver=EvidenceResolver._from_validated(ws, ref["source_id"]))
    names = {stage["name"]: stage for stage in report["stages"]}
    endpoint = report["request"]["endpoint"]
    prediction, reference = names[endpoint["prediction"]], names[endpoint["reference"]]
    if (prediction["source_identity"] != predicted_identity
            or reference["source_identity"] != reference_identity
            or prediction["record_index"] != pose_index
            or reference["record_index"] != reference_index):
        raise EvidenceError("qualification does not bind the requested endpoint files/slots")
    return {"qualification_ref": ref, "input_sha256": report["input_sha256"],
            "eligibility": report["endpoint_eligibility"], "parser": report["parser"],
            "source_refs": report["evidence_refs"]}
