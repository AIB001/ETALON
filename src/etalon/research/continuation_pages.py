"""Optional paged verification of a declared continuation closure.

The existing bounded collector supplies metadata, not a receipt that a host read
originals. Pages use the existing manifest cursor; no state store or execution is
introduced. Legacy resume retains its independent 1024-record verification cap.
"""
import base64
from collections import deque

from etalon.active.schema import canonical, digest
from .continuation import CHECKPOINT, MAX_RESUME_RECORDS, _exact_refs, _status, read_content
from .evidence import MAX_RECORDS, _collect, _read_evidence, _relationships
from .evidence_refs import EvidenceError, make_ref, validate_ref
from .graph_memory import _ids, restrict_workspace
from .graph_scope import _counter_refs
from .read_scope import bounded_read, implementation_identity


def _closure_inventory(workspace, checkpoint_ref, *, policy, purpose, handoff):
    """Index structured references within collector bounds; verify bodies later."""
    ws, policy_receipt = restrict_workspace(workspace, policy=policy, purpose=purpose)
    records, coverage, warnings = collection = _collect(ws)
    checkpoint = read_content(ws, checkpoint_ref)
    if checkpoint.get("schema") != CHECKPOINT:
        raise EvidenceError("closure pages require a research checkpoint")
    universe = {(sid, rid): row for sid, rows in records.items() for rid, row in rows.items()}
    if len(universe) > MAX_RECORDS:
        raise EvidenceError("closure metadata exceeds existing record index bound; narrow published scope")
    issues = list(warnings)
    suppressed = sum(len(_ids(ws, s) - records[s["source_id"]].keys())
        for s in ws.manifest["sources"] if s["source_id"] in records
        and s["kind"] in {"journal", "artifact_manifest"})
    if suppressed:
        issues.append({"reason": "published_records_hidden_or_dependency_suppressed", "count": suppressed})
    if (policy_receipt["excluded_source_count"] or policy_receipt["restricted_record_rule_count"]
            or (policy or {}).get("record_ids")):
        issues.append({"reason": "policy_narrowed_counter_universe"})
    if any("record_ids" in s for s in ws.manifest["sources"]):
        issues.append({"reason": "published_source_record_allowlist_limits_counter_universe"})
    if any(c["status"] != "complete" for c in coverage):
        issues.append({"reason": "counter_search_source_coverage_incomplete"})

    def checked_row(ref):
        ref = validate_ref(ref)
        row = universe.get((ref["source_id"], ref["record_id"]))
        if row is None or digest(row["record"]) != ref["record_digest"]:
            raise EvidenceError("closure reference unavailable, restricted or identity changed")
        if row.get("projection", {}).get("status") == "unavailable":
            raise EvidenceError("closure referenced content projection unavailable")
        body = row["record"]
        if "member" in ref:
            members = [m for m in row.get("members", body.get("members", []))
                if m.get("member") == ref["member"] and m.get("visibility") == "released"]
            if len(members) != 1:
                raise EvidenceError("closure reference member is not uniquely published")
            body = members[0]
        if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body.get("sha256"), body.get("bytes")):
            raise EvidenceError("closure reference artifact identity mismatch")
        return row

    # Counter direction is target -> declaring owner. Match record identity, so
    # a member/hash-qualified envelope cannot erase a reverse declaration.
    def identity(ref):
        return ref["source_id"], ref["record_id"], ref["record_digest"]

    incidence, all_counters = {}, {}
    for (sid, rid), row in sorted(universe.items()):
        owner = make_ref(sid, rid, row["record"])
        for target in [*_counter_refs(row["record"]), *_counter_refs(row.get("projected_content", {}))]:
            checked_row(target)
            relation = {"kind": "refutes", "from_ref": target, "to_ref": owner, "basis_ref": owner}
            key = canonical(relation)
            all_counters[key] = relation
            if len(all_counters) > MAX_RECORDS:
                raise EvidenceError("closure counter index exceeds existing record index bound")
            for ref in (owner, target):
                incidence.setdefault(identity(ref), set()).add(key)

    refs, queue, edges, counters = {}, deque(), {}, {}
    def enqueue(ref):
        checked_row(ref)
        key = canonical(ref)
        if key not in refs:
            if len(refs) >= MAX_RECORDS:
                raise EvidenceError("closure metadata exceeds existing record index bound; narrow published scope")
            refs[key] = ref
            queue.append(ref)
        return key

    enqueue(checkpoint_ref)
    for ref in _exact_refs(checkpoint):
        enqueue(ref)
    while queue:
        ref = queue.popleft()
        row = checked_row(ref)
        for key in sorted(incidence.get(identity(ref), ())):
            relation = counters[key] = all_counters[key]
            enqueue(relation["from_ref"])
            enqueue(relation["to_ref"])
        for target in _relationships(row):
            if isinstance(target, str):
                linked = records.get(ref["source_id"], {}).get(target)
                if (linked is None and row["record"].get("schema") == "etalon-method-evaluation/1"
                        and target in row["record"].get("accounting_refs", [])):
                    encoded = "budget:execution:" + target
                    resource = records.get(ref["source_id"], {}).get(encoded)
                    if resource is not None and resource["record"].get("execution_id") == target:
                        linked, target = resource, encoded
                if linked is None:
                    issue = {"source_id": ref["source_id"], "record_id": target,
                             "reason": "legacy_reference_unavailable"}
                    if issue not in issues:
                        issues.append(issue)
                    continue
                target = make_ref(ref["source_id"], target, linked["record"])
            if isinstance(target, dict):
                enqueue(target)
                edge = {"kind": "declared_reference", "from_ref": ref, "to_ref": target}
                edges[canonical(edge)] = edge
                if len(edges) > MAX_RECORDS:
                    raise EvidenceError("closure relation index exceeds existing record index bound")

    authority = _status(ws, checkpoint["status_guards"], source_id=checkpoint_ref["source_id"])
    source_changes = []
    current = {c["source_id"]: c for c in coverage}
    for old in checkpoint["source_watermarks"]:
        now = current.get(old["source_id"], {})
        if now.get("status") != "complete" or any(now.get(k) != old[k] for k in ("snapshot", "read_watermark")):
            source_changes.append({"reason": "checkpoint_source_watermark_changed", "source_id": old["source_id"]})
    # Each edge/envelope is an explicit row, so required context is fragmented
    # losslessly by the existing transport instead of omitted to fit a page.
    rows = ([{"kind": "reference", "ref": refs[k]} for k in sorted(refs)]
        + [edges[k] for k in sorted(edges)] + [counters[k] for k in sorted(counters)]
        + [{"kind": "coverage_issue", "issue": issue} for issue in issues]
        + [{"kind": "continuation_review", "issue": issue} for issue in source_changes])
    binding = {"workspace": ws.manifest, "checkpoint_ref": checkpoint_ref,
        "coverage": coverage, "policy": policy_receipt, "authority": authority,
        "implementation": implementation_identity(), "rows": rows,
        "read_context": handoff.get("read_context")}
    return {"workspace": ws, "checkpoint": checkpoint, "collection": collection,
        "rows": rows, "manifest_digest": digest(binding), "authority": authority,
        "reference_count": len(refs), "relation_count": len(edges), "counter_relation_count": len(counters),
        "issue_count": len(issues), "review_count": len(source_changes),
        "reference_complete": not issues, "source_count": len(coverage), "universe_count": len(universe)}


@bounded_read
def closure_page(workspace, checkpoint_ref, handoff, *, policy=None, purpose="research",
                 cursor=None, limit=20, max_bytes=16384):
    """One bounded page, never a receipt that prior pages or bodies were read."""
    from .continuation_card import manifest_page
    data = _closure_inventory(workspace, checkpoint_ref, policy=policy, purpose=purpose, handoff=handoff)
    # manifest_page owns the established stamp/index/UTF-8 fragment protocol.
    page = manifest_page({**data, "sections": {"closure": data["rows"]}}, checkpoint_ref, handoff,
        section="closure", cursor=cursor, limit=limit, max_bytes=max_bytes - 1100)
    verified = []
    for item in page["items"]:
        row = item.get("value", data["rows"][item["index"]])
        if row["kind"] != "reference":
            continue
        ref = row["ref"]
        original = data["collection"][0][ref["source_id"]][ref["record_id"]]
        artifact = original["record_type"] == "artifact" or "member" in ref
        result = _read_evidence(data["workspace"], ref, view="artifact" if artifact else "record",
            limit=1, collection=data["collection"])
        if (result.get("availability") != "available" or result.get("missing") or
                artifact and result.get("verification", {}).get("artifact") != "verified"):
            raise EvidenceError("closure page original unavailable or identity changed")
        verified.append(item["index"])
    page["coverage"].update({"original_verification_indices": verified,
        "original_verification_scope": "reference_rows_on_this_page_only",
        "all_pages_original_verification_complete": False,
        "navigation_exhausted": page["next_cursor"] is None,
        "closure_obligation": "retain every consecutive page from index zero under one manifest_digest; verify every reference row, preserve all relations and coverage issues",
        "counter_search_universe": "all_permitted_published_records; opaque_bodies_not_semantically_parsed",
        "counter_search_record_count": data["universe_count"],
        "reference_count": data["reference_count"], "declared_relation_count": data["relation_count"],
        "counter_relation_count": data["counter_relation_count"], "coverage_issue_count": data["issue_count"],
        "continuation_review_count": data["review_count"],
        "legacy_resume_record_limit": MAX_RESUME_RECORDS})
    page["continuation"] = {"authority_state": data["authority"]["state"],
        "review_required": True, "meaning": "page audit does not resolve checkpoint, authority, pending-handle or successor reviews; retain the existing continuation card"}
    page["snapshot_consistency"] = "per_source_and_per_page_reads_not_global_atomic_snapshot"
    page["context_id"] = "research-context-" + digest({k: v for k, v in page.items() if k != "context_id"})
    if len(canonical(page).encode()) > max_bytes:
        raise EvidenceError("closure page controls exceed byte bound")
    return page


@bounded_read
def audit_closure_pages(workspace, checkpoint_ref, handoff, pages, *, policy=None, purpose="research"):
    """Validate an ordered transcript and return a resumable ordinary Artifact body.

    This checks the complete recorded page evidence against the current source
    index. It does not assert that a host consumed bodies or rehash every opaque
    original at this later instant. Retain page bytes alongside this receipt.
    """
    from .continuation_card import _cursor, _stamp
    if not isinstance(pages, list) or not pages:
        raise EvidenceError("closure audit requires consecutive pages starting at zero")
    data = _closure_inventory(workspace, checkpoint_ref, policy=policy, purpose=purpose, handoff=handoff)
    stamp = _stamp(data, handoff, checkpoint_ref, "closure")
    index, offset, verified, page_ids = 0, 0, set(), []
    for page in pages:
        expected_id = "research-context-" + digest({k: v for k, v in page.items() if k != "context_id"})
        if (page.get("context_id") != expected_id or page.get("manifest_digest") != data["manifest_digest"]
                or page.get("section") != "closure" or page.get("checkpoint_ref") != checkpoint_ref
                or page.get("read_context") != handoff.get("read_context")
                or page.get("total_count") != len(data["rows"])):
            raise EvidenceError("closure page identity, source or implementation changed")
        if not page.get("items"):
            raise EvidenceError("closure transcript contains an empty or repeated terminal page")
        page_verified = []
        for item in page["items"]:
            if index >= len(data["rows"]) or item.get("index") != index:
                raise EvidenceError("closure transcript skips, reorders or repeats rows")
            row = data["rows"][index]
            if row["kind"] == "reference":
                page_verified.append(index)
                verified.add(index)
            if "value" in item:
                if offset or item["value"] != row:
                    raise EvidenceError("closure transcript row differs from the pinned index")
                index += 1
            elif "fragment" in item:
                fragment, raw = item["fragment"], canonical(row).encode()
                try:
                    fragment_bytes = base64.b64decode(fragment["content"], validate=True)
                except (ValueError, KeyError):
                    raise EvidenceError("invalid closure transcript fragment") from None
                end = offset + len(fragment_bytes)
                expected_next = None if end == len(raw) else end
                if (not fragment_bytes or fragment.get("offset") != offset or end > len(raw)
                        or fragment_bytes != raw[offset:end] or fragment.get("next_offset") != expected_next):
                    raise EvidenceError("closure transcript fragment is missing or changed")
                if expected_next is None:
                    index, offset = index + 1, 0
                else:
                    offset = end
            else:
                raise EvidenceError("closure transcript row or fragment required")
        if page.get("coverage", {}).get("original_verification_indices") != page_verified:
            raise EvidenceError("closure transcript lacks original verification for a reference row")
        next_cursor = _cursor(stamp, index, offset) if index < len(data["rows"]) else None
        if page.get("next_cursor") != next_cursor:
            raise EvidenceError("closure transcript cursor does not match contiguous progress")
        page_ids.append(page["context_id"])
    exhausted = index == len(data["rows"]) and offset == 0
    closed = exhausted and data["reference_complete"] and len(verified) == data["reference_count"]
    receipt = {"schema": "etalon-continuation-closure-receipt/1", "checkpoint_ref": checkpoint_ref,
        "read_context": handoff.get("read_context"), "manifest_digest": data["manifest_digest"],
        "page_context_ids": page_ids, "page_count": len(pages), "next_index": index, "next_byte_offset": offset,
        "total_count": len(data["rows"]), "navigation_exhausted": exhausted,
        "recorded_original_verification_count": len(verified), "reference_count": data["reference_count"],
        "counter_relation_count": data["counter_relation_count"], "coverage_issue_count": data["issue_count"],
        "declared_reference_and_counter_closure_complete": closed,
        "coverage_status": "closed_declared_scope" if closed else "coverage_insufficient" if exhausted else "in_progress",
        "original_bodies_read_by_host": "not_established", "scientific_closure": "not_established",
        "later_opaque_original_reverification": "not_performed_by_transcript_audit",
        "snapshot_consistency": "per_source_and_per_page_reads_not_global_atomic_snapshot",
        "legacy_resume": {"record_limit": MAX_RESUME_RECORDS,
            "meaning": "this explicit all-page receipt covers the declared closure separately; legacy one-shot truncation, authority, pending and successor reviews are not erased"},
        "next_read": None if exhausted else {"entry": handoff.get("read_context"), "view": "manifest",
            "view_options": {"section": "closure"}, "cursor": next_cursor},
        "execution_permission": "not_granted", "automatic_retry": False}
    receipt["receipt_digest"] = digest(receipt)
    return receipt
