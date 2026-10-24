"""Opt-in visible-evidence snapshots and ordered publication in the existing H.

The API controls its own publication, not arbitrary host reads or model knowledge.
Delivery preparation, host acknowledgement, and consumption are distinct facts.
"""
from __future__ import annotations

import json
import base64
from pathlib import Path

from etalon.active.schema import canonical, digest
from etalon.active.store import CampaignStore
from .continuation import _fields, _list, _refs, _text, _exact_refs
from .evidence_refs import EvidenceError, file_identity, make_ref, validate_ref
from .improvement import read_material
from .read_scope import bounded_read
from .schema import Artifact

EVIDENCE_WINDOW = "etalon-evidence-window/1"
OUTCOME_RELEASE = "etalon-outcome-release/1"
SCHEMAS = {EVIDENCE_WINDOW, OUTCOME_RELEASE}
MAX_BYTES = 16384
MAX_CLOSURE = 128
BOUNDARY = "API publication order only; host filesystem and pretrained knowledge are not sandboxed"


def _same(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return {k: left[k] for k in ("record_id", "record_digest", "member") if k in left} == {
        k: right[k] for k in ("record_id", "record_digest", "member") if k in right}


def _snapshot(value):
    return digest({k: v for k, v in value.items() if k != "snapshot_digest"})


def _ref_keys(refs):
    return {canonical({k: ref[k] for k in ("source_id", "record_id", "record_digest", "member") if k in ref})
            for ref in refs}


def outcome_slot(*, outcome_id, action_ref, protocol_ref, input_refs, method_refs,
                 producer_id, receipt_contract, expected_artifact=None):
    """Freeze an existing retained original or a future producer contract.

Future bytes need not exist. The slot binds an existing planned action Artifact;
metadata attribution is not verification that a native scientific tool ran.
"""
    value = dict(outcome_id=outcome_id, kind="retained_artifact" if expected_artifact else "future_execution",
        action_ref=action_ref, protocol_ref=protocol_ref, input_refs=list(input_refs),
        method_refs=list(method_refs), producer_id=producer_id, receipt_contract=receipt_contract,
        expected_artifact=expected_artifact.as_dict() if isinstance(expected_artifact, Artifact) else expected_artifact)
    _slot(value)
    return json.loads(canonical(value))


def _slot(value):
    _fields(value, ("outcome_id", "kind", "action_ref", "protocol_ref", "input_refs", "method_refs",
                    "producer_id", "receipt_contract", "expected_artifact"))
    for key in ("outcome_id", "producer_id", "receipt_contract"):
        _text(value[key])
    for key in ("action_ref", "protocol_ref"):
        validate_ref(value[key])
    _refs(value["input_refs"], nonempty=True)
    _refs(value["method_refs"])
    if value["kind"] == "retained_artifact":
        artifact = Artifact(**value["expected_artifact"])
        if artifact.role != "independent_validation":
            raise EvidenceError("retained outcome needs independent_validation role; sealed originals remain sealed")
    elif value["kind"] != "future_execution" or value["expected_artifact"] is not None:
        raise EvidenceError("unknown outcome slot kind")


def validate_content(payload):
    if not isinstance(payload, dict) or len(canonical(payload).encode()) > MAX_BYTES:
        raise EvidenceError("evidence window content exceeds 16 KiB or is not an object")
    common = ("schema", "task_id", "branch_id", "workset_ref", "method_refs", "supersedes", "boundary")
    if payload.get("schema") == EVIDENCE_WINDOW:
        _fields(payload, (*common, "timing_mode", "visible_refs", "excluded_refs", "coverage",
            "expected_outcomes", "evaluation_contract_ref", "snapshot_digest"))
        if payload["timing_mode"] not in {"retrospective", "host_declared", "controlled"}:
            raise EvidenceError("explicit timing mode required")
        _refs(payload["visible_refs"])
        _refs(payload["excluded_refs"])
        coverage = payload["coverage"]
        _fields(coverage, ("scope", "included_count", "excluded_count", "missing"))
        _text(coverage["scope"])
        for value in _list(coverage["missing"]):
            _text(value)
        if (coverage["included_count"] != len(payload["visible_refs"])
                or coverage["excluded_count"] != len(payload["excluded_refs"])):
            raise EvidenceError("coverage counts do not match exact refs")
        for slot in _list(payload["expected_outcomes"]):
            _slot(slot)
        if len({s["outcome_id"] for s in payload["expected_outcomes"]}) != len(payload["expected_outcomes"]):
            raise EvidenceError("duplicate outcome slot")
        if payload["evaluation_contract_ref"] is not None:
            validate_ref(payload["evaluation_contract_ref"])
        if payload["timing_mode"] == "controlled" and (not payload["expected_outcomes"]
                or payload["workset_ref"] is None or payload["evaluation_contract_ref"] is None
                or coverage["missing"]):
            raise EvidenceError("controlled window needs a workset, evaluation contract, slots and complete explicit coverage")
        if payload["snapshot_digest"] != _snapshot(payload):
            raise EvidenceError("visible information snapshot digest mismatch")
    elif payload.get("schema") == OUTCOME_RELEASE:
        _fields(payload, (*common, "window_ref", "decision_ref", "outcomes", "release_key",
                          "window_delivery", "outcome_delivery", "consumption"))
        validate_ref(payload["window_ref"])
        validate_ref(payload["decision_ref"])
        for item in _list(payload["outcomes"], nonempty=True):
            _fields(item, ("outcome_id", "artifact_ref", "receipt_ref", "slot_digest"))
            _text(item["outcome_id"])
            validate_ref(item["artifact_ref"])
            validate_ref(item["receipt_ref"])
        if (payload["window_delivery"], payload["outcome_delivery"], payload["consumption"]) != (
                "host_acknowledged", "prepared_for_return", "not_attested"):
            raise EvidenceError("delivery acknowledgement cannot establish consumption")
        if payload["release_key"] != digest({k: payload[k] for k in ("window_ref", "decision_ref", "outcomes")}):
            raise EvidenceError("release identity mismatch")
    else:
        raise EvidenceError("unknown evidence window schema")
    for key in ("task_id", "branch_id"):
        _text(payload[key])
    if payload["workset_ref"] is not None:
        validate_ref(payload["workset_ref"])
    _refs(payload["method_refs"])
    _refs(payload["supersedes"])
    if payload["boundary"] != BOUNDARY:
        raise EvidenceError("operational timing cannot claim an unseen-information guarantee")
    return json.loads(canonical(payload))


class _Reader:
    def __init__(self, read):
        self.read, self.cache = read, {}
        self.budget = {"bytes": 0}

    def material(self, ref):
        validate_ref(ref)
        key = canonical(ref)
        if key not in self.cache:
            if len(self.cache) >= MAX_CLOSURE:
                raise EvidenceError("evidence closure exceeds bounded read budget")
            value = self.read(ref)
            selected = value.get("ref", {}) if "member" in ref else value["record"]
            if "sha256" in selected:
                from .opportunity_state import _original_size
                _original_size({k: selected[k] for k in ("sha256", "bytes")}, self.budget)
            self.cache[key] = value
        return self.cache[key]

    def body(self, ref, schema=None):
        item = self.material(ref)
        value = item.get("content")
        if not item.get("complete") or not isinstance(value, dict) or schema and value.get("schema") != schema:
            raise EvidenceError("complete original of the expected schema required")
        return value

    def identities(self, refs):
        result, pending, seen = set(), list(refs), set()
        while pending:
            ref = pending.pop()
            key = canonical(ref)
            if key in seen:
                continue
            seen.add(key)
            material = self.material(ref)
            record = material["record"]
            result.add(("record", ref["record_id"], ref["record_digest"], ref.get("member")))
            selected = material.get("ref", {}) if "member" in ref else record
            if "sha256" in selected:
                result.add(("bytes", selected["sha256"], selected["bytes"]))
            if not material.get("complete"):
                prefix = material.get("prefix", b"")
                fragment = material.get("fragment", {})
                if fragment.get("encoding") == "base64":
                    prefix = base64.b64decode(fragment["content"])[:100]
                if (record.get("bindings", {}).get("schema") or str(ref.get("member", record.get("path", ""))).lower().endswith((".json", ".jsonl"))
                        or prefix.lstrip().startswith((b"{", b"["))):
                    raise EvidenceError("incomplete JSON evidence closure; publish a bounded complete original")
            # Follow exact linked originals, including enclosing Artifact bindings.
            if "member" not in ref:
                pending.extend(_exact_refs(record))
                pending.extend(material.get("parent_refs", []))
            if material.get("complete") and isinstance(material.get("content"), (dict, list)):
                pending.extend(_exact_refs(material["content"]))
        return result


def _workspace_material(workspace, ref, budget=None):
    from .opportunity_state import _material
    content, record, identity = _material(None, ref, None, workspace, _budget=budget)
    material = dict(ref={**ref, **identity, "selector": "bytes"} if identity else ref,
                    record=record, content=content, complete=True)
    if identity is not None and content is None and identity["bytes"] <= 32768:
        material["fragment"] = read_material(workspace, ref).get("fragment")
    if material["record"].get("parents") and "member" not in ref:
        from .evidence import read_evidence
        lineage = read_evidence(workspace, ref, view="lineage", limit=64)
        if lineage.get("missing") or lineage.get("next_offset") is not None:
            raise EvidenceError("parent evidence requires a complete bounded lineage")
        resolved = {r["record_id"]: r for r in lineage.get("items", []) if r["source_id"] == ref["source_id"]}
        if not set(material["record"]["parents"]) <= set(resolved):
            raise EvidenceError("exact parent originals unavailable")
        material["parent_refs"] = [resolved[rid] for rid in material["record"]["parents"]]
    return material


def _workspace_reader(workspace):
    budget = {"bytes": 0}
    return _Reader(lambda ref: _workspace_material(workspace, ref, budget))


def _local_reader(journal, source_id, workspace=None):
    budget = {"bytes": 0}
    def read(ref):
        if ref["source_id"] != source_id or "member" in ref:
            if workspace is None:
                raise EvidenceError("external evidence needs an explicit workspace")
            return _workspace_material(workspace, ref, budget)
        body = journal.get(ref["record_id"])
        if digest(body) != ref["record_digest"]:
            raise EvidenceError("record reference substitution or digest mismatch")
        if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body.get("sha256"), body.get("bytes")):
            raise EvidenceError("artifact reference identity mismatch")
        if not ref["record_id"].startswith("artifact-"):
            return dict(record=body, content=body, complete=True)
        path = journal.artifact_path(Artifact(**body))
        protected = False
        if hasattr(journal, "store"):
            with journal.store.connection() as db:
                protected = db.execute("SELECT 1 FROM metadata WHERE key LIKE 'research:artifact-%' "
                "AND json_extract(body,'$.sha256')=? AND json_extract(body,'$.bytes')=? "
                "AND json_extract(body,'$.role') IN ('sealed_holdout','independent_validation') LIMIT 1",
                (body["sha256"], body["bytes"])).fetchone() is not None
        from .opportunity_state import _material, _original_json, _original_size
        if hasattr(journal, "store"):
            content, body, identity = _material(journal, ref, source_id, workspace, _budget=budget)
        else:
            identity = {k: body[k] for k in ("sha256", "bytes")}
            _original_size(identity, budget)
            content = _original_json(path.read_text(encoding="utf-8-sig").encode())
        raw = b""
        if content is None and body["bytes"] <= 32768:
            raw = path.read_bytes() if hasattr(path, "read_bytes") else path.read_text(encoding="utf-8-sig").encode()
        journal.artifact_path(Artifact(**body))  # Recheck after parsing, including local readers.
        result = dict(record=body, content=content, complete=True,
                    protected_original=protected,
                    parent_refs=[make_ref(source_id, rid, journal.get(rid)) for rid in body.get("parents", [])])
        if content is None and body["bytes"] <= 32768:
            result["fragment"] = {"encoding": "base64", "content": base64.b64encode(raw).decode(), "next_offset": None}
        return result
    return _Reader(read)


def _verify_window(reader, payload):
    payload = validate_content(payload)
    for ref in _exact_refs(payload):
        reader.material(ref)
    visible = reader.identities(payload["visible_refs"])
    excluded = reader.identities(payload["excluded_refs"])
    for slot in payload["expected_outcomes"]:
        if slot["expected_artifact"]:
            expected = slot["expected_artifact"]
            excluded.add(("bytes", expected["sha256"], expected["bytes"]))
        else:
            action = reader.body(slot["action_ref"], "etalon-research-step/1")
            if (action.get("task_id"), action.get("branch_id")) != (payload["task_id"], payload["branch_id"]):
                raise EvidenceError("future outcome action has wrong task or branch")
            if action.get("operation") != "node" or action.get("detail", {}).get("state") != "planned":
                raise EvidenceError("future outcome slot needs an existing planned node")
            if _ref_keys(slot["input_refs"]) != _ref_keys(action["input_refs"]):
                raise EvidenceError("future outcome action does not bind frozen inputs")
            if _ref_keys(slot["method_refs"]) != _ref_keys(action["method_refs"]):
                raise EvidenceError("future outcome action does not bind frozen methods")
        if payload["evaluation_contract_ref"] is not None:
            contract = reader.body(payload["evaluation_contract_ref"])
            protocol = contract.get("protocol_ref", contract.get("detail", {}).get("protocol_ref"))
            if protocol is None or _ref_keys([protocol]) != _ref_keys([slot["protocol_ref"]]):
                raise EvidenceError("frozen outcome protocol differs from the evaluation contract")
    if visible & excluded:
        raise EvidenceError("excluded evaluation originals cannot be relabelled as conditioning")
    for item in reader.cache.values():
        record = item["record"]
        selected = item.get("ref", {}) if "member" in item.get("ref", {}) else record
        if (record.get("role") in {"sealed_holdout", "independent_validation"} or item.get("protected_original")) and (
                "bytes", selected.get("sha256"), selected.get("bytes")) in visible:
            raise EvidenceError("evaluation-only originals cannot enter the visible conditioning window")
    return payload


@bounded_read
def build_evidence_window(workspace, *, task_id, branch_id, workset_ref, visible_refs,
        timing_mode, coverage_scope, excluded_refs=(), missing=(), expected_outcomes=(),
        evaluation_contract_ref=None, method_refs=(), supersedes=()):
    payload = dict(schema=EVIDENCE_WINDOW, task_id=task_id, branch_id=branch_id,
        workset_ref=workset_ref, visible_refs=list(visible_refs), excluded_refs=list(excluded_refs),
        timing_mode=timing_mode, coverage=dict(scope=coverage_scope, included_count=len(visible_refs),
        excluded_count=len(excluded_refs), missing=list(missing)), expected_outcomes=list(expected_outcomes),
        evaluation_contract_ref=evaluation_contract_ref, method_refs=list(method_refs),
        supersedes=list(supersedes), boundary=BOUNDARY)
    payload["snapshot_digest"] = _snapshot(payload)
    return _verify_window(_workspace_reader(workspace), payload)


@bounded_read
def read_evidence_window(workspace, ref):
    reader = _workspace_reader(workspace)
    return _verify_window(reader, reader.body(ref, EVIDENCE_WINDOW))


def _decision(reader, decision):
    ref = decision.get("evidence_window_ref")
    if ref is None:
        return None
    window = _verify_window(reader, reader.body(ref, EVIDENCE_WINDOW))
    if any(decision.get(k) != window[k] for k in ("task_id", "branch_id", "workset_ref")):
        raise EvidenceError("decision task, branch or exact workset differs from evidence window")
    for item in decision.get("input_refs", []):
        if not any(_same(item, visible) for visible in window["visible_refs"]):
            raise EvidenceError("decision input is outside its exact visible evidence snapshot")
    if window["timing_mode"] == "controlled":
        if _ref_keys(decision.get("method_refs", [])) != _ref_keys(window["method_refs"]):
            raise EvidenceError("decision methods differ from the frozen window")
        if not any(_same(window["evaluation_contract_ref"], ref) for ref in decision.get("evaluation_refs", [])):
            raise EvidenceError("decision must freeze the window's exact evaluation contract")
        selected = [a.get("execution_ref") for a in decision.get("actions", [])
                    if a.get("disposition") == "selected" and a.get("execution_ref") is not None]
        if any(not any(_same(slot["action_ref"], ref) for ref in selected) for slot in window["expected_outcomes"]):
            raise EvidenceError("outcome slots must belong to the decision's selected actions")
    # Resolve aliases as well as exact selectors; role changes do not change bytes.
    inputs = reader.identities(decision.get("input_refs", []))
    excluded = reader.identities(window["excluded_refs"])
    for slot in window["expected_outcomes"]:
        if slot["expected_artifact"]:
            body = slot["expected_artifact"]
            excluded.add(("bytes", body["sha256"], body["bytes"]))
    if inputs & excluded:
        raise EvidenceError("evaluation originals cannot be relabelled as decision conditioning")
    return window


@bounded_read
def validate_decision_window(journal, decision, source_id, external_workspace=None):
    return _decision(_local_reader(journal, source_id, external_workspace), decision)


def _sequence(db, ref):
    """Reject missing, duplicate or rewritten history; never trust caller sequence."""
    from .journal import ResearchJournal
    body = ResearchJournal._get(db, ref["record_id"])
    if digest(body) != ref["record_digest"]:
        raise EvidenceError("durable reference digest mismatch")
    matches = []
    for row in db.execute("SELECT sequence,body FROM events WHERE kind='research_recorded' ORDER BY sequence"):
        event = json.loads(row["body"])
        if event.get("id") == ref["record_id"]:
            if event.get("record") != body:
                raise EvidenceError("ambiguous research chronology: event record differs")
            matches.append(row["sequence"])
    if len(matches) != 1:
        raise EvidenceError("ambiguous research chronology: one durable record event required")
    return matches[0]


def _event_once(db, kind, body):
    found = []
    for row in db.execute("SELECT sequence,body FROM events WHERE kind=?", (kind,)):
        if json.loads(row["body"]) == body:
            found.append(row["sequence"])
    if len(found) > 1:
        raise EvidenceError("ambiguous repeated delivery chronology")
    if not found:
        CampaignStore._event(db, kind, body)


@bounded_read
def deliver_evidence_window(journal, window_ref, *, source_id, external_workspace=None):
    """Prepare an exact API response. Return preparation is not receipt/consumption."""
    reader = _local_reader(journal, source_id, external_workspace)
    if window_ref["source_id"] != source_id:
        raise EvidenceError("controlled delivery needs its local journal source")
    window = _verify_window(reader, reader.body(window_ref, EVIDENCE_WINDOW))
    materials = [{"ref": ref, **{k: v for k, v in reader.material(ref).items()
                  if k in {"record", "content", "complete", "fragment"}}} for ref in window["visible_refs"]]
    if any(not material["complete"] or (reader.material(material["ref"]).get("ref", {})
            if "member" in material["ref"] else material["record"]).get("bytes", 0) > 32768 for material in materials):
        raise EvidenceError("controlled delivery requires complete bounded materials; narrow the visible window")
    token = digest({"window_ref": window_ref, "snapshot_digest": window["snapshot_digest"]})
    body = dict(window_ref=window_ref, token=token, state="prepared_for_return")
    with journal.store.connection(write=True) as db:
        _sequence(db, window_ref)
        _event_once(db, "evidence_window_delivery_prepared", body)
    return dict(window_ref=window_ref, token=token, state="prepared_for_return", materials=materials,
                consumption="not_attested", boundary=BOUNDARY)


def acknowledge_evidence_window(journal, window_ref, *, token, source_id, external_workspace=None):
    """Explicit host acknowledgement after the delivery API returned; no model telemetry."""
    if window_ref["source_id"] != source_id:
        raise EvidenceError("controlled acknowledgement needs its local journal source")
    reader = _local_reader(journal, source_id, external_workspace)
    window = _verify_window(reader, reader.body(window_ref, EVIDENCE_WINDOW))
    if token != digest({"window_ref": window_ref, "snapshot_digest": window["snapshot_digest"]}):
        raise EvidenceError("delivery token differs from frozen snapshot")
    prepared = dict(window_ref=window_ref, token=token, state="prepared_for_return")
    ack = dict(window_ref=window_ref, token=token, state="host_acknowledged", consumption="not_attested")
    with journal.store.connection(write=True) as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM events WHERE kind='evidence_window_delivery_prepared'")]
        if rows.count(prepared) != 1:
            raise EvidenceError("delivery must be prepared exactly once before acknowledgement")
        _event_once(db, "evidence_window_delivery_acknowledged", ack)
    return ack


def _chronology(db, window_ref, decision_ref, snapshot_digest):
    w, d = _sequence(db, window_ref), _sequence(db, decision_ref)
    if w >= d:
        raise EvidenceError("durable window must precede decision")
    token = digest({"window_ref": window_ref, "snapshot_digest": snapshot_digest})
    expected = {
        "evidence_window_delivery_prepared": dict(window_ref=window_ref, token=token, state="prepared_for_return"),
        "evidence_window_delivery_acknowledged": dict(window_ref=window_ref, token=token,
            state="host_acknowledged", consumption="not_attested")}
    sequences = {kind: [] for kind in expected}
    for row in db.execute("SELECT sequence,kind,body FROM events WHERE kind IN "
            "('evidence_window_delivery_prepared','evidence_window_delivery_acknowledged')"):
        body = json.loads(row["body"])
        if body.get("window_ref") == window_ref:
            if body != expected[row["kind"]]:
                raise EvidenceError("ambiguous delivery event identity")
            sequences[row["kind"]].append(row["sequence"])
    prep, acks = (sequences[k] for k in expected)
    if len(prep) != 1 or len(acks) != 1 or not w < prep[0] < acks[0] < d:
        raise EvidenceError("acknowledged window delivery must precede decision")
    return w, d


def _outcome_bindings(window, slot):
    return dict(outcome_slot_digest=digest(slot), task_id=window["task_id"], branch_id=window["branch_id"],
        producer_id=slot["producer_id"], receipt_contract=slot["receipt_contract"],
        protocol_ref=slot["protocol_ref"], input_refs=slot["input_refs"], method_refs=slot["method_refs"])


def expected_outcome_bindings(window, outcome_id):
    """Bindings for the producer's existing Artifact/receipt, not an executor."""
    validate_content(window)
    matches = [s for s in window["expected_outcomes"] if s["outcome_id"] == outcome_id]
    if len(matches) != 1:
        raise EvidenceError("unknown frozen outcome slot")
    return _outcome_bindings(window, matches[0])


def _check_outcome(reader, window, slot, artifact, receipt_ref):
    if artifact.role != "independent_validation":
        raise EvidenceError("outcomes need independent_validation role; sealed originals remain sealed")
    if slot["kind"] == "retained_artifact":
        if canonical(artifact.as_dict()) != canonical(slot["expected_artifact"]):
            raise EvidenceError("retained outcome reference substitution")
    else:
        expected = _outcome_bindings(window, slot)
        if any(artifact.bindings.get(k) != v for k, v in expected.items()):
            raise EvidenceError("future outcome does not match frozen producer/input/protocol contract")
        receipt = reader.body(receipt_ref)
        if any(receipt.get(k) != v for k, v in expected.items()):
            raise EvidenceError("outcome receipt does not match frozen slot contract")
        if receipt.get("artifact_identity") != {"sha256": artifact.sha256, "bytes": artifact.bytes}:
            raise EvidenceError("outcome receipt does not identify exact result bytes")
    reader.material(receipt_ref)


def release_outcomes(journal, *, window_ref, decision_ref, outcomes, directory, source_id,
                     source_kind, external_workspace=None):
    """Atomically register exact outcomes and an ordinary release Artifact in H.

outcomes=[{outcome_id, artifact: Artifact, receipt_ref: EvidenceRef}]. Existing
producer execution/fees are untouched. A failed append retains immutable orphan
bytes and an error event; the same exact successful release is idempotent.
"""
    reader = _local_reader(journal, source_id, external_workspace)
    if window_ref["source_id"] != source_id or decision_ref["source_id"] != source_id:
        raise EvidenceError("controlled release requires window and decision in the same local journal")
    window = _verify_window(reader, reader.body(window_ref, EVIDENCE_WINDOW))
    decision = reader.body(decision_ref, "etalon-research-decision/1")
    from .research_decision import validate_local_links as validate_decision
    validate_decision(journal, decision, source_id, external_workspace=external_workspace)
    if window["timing_mode"] != "controlled" or not _same(decision.get("evidence_window_ref", {}), window_ref):
        raise EvidenceError("controlled release requires the decision's exact controlled window")
    _decision(reader, decision)
    slots = {s["outcome_id"]: s for s in window["expected_outcomes"]}
    if len(outcomes) != len(slots) or {o["outcome_id"] for o in outcomes} != set(slots):
        raise EvidenceError("release must preserve every frozen outcome, including failures")
    items = []
    for item in outcomes:
        _fields(item, ("outcome_id", "artifact", "receipt_ref"))
        artifact = item["artifact"]
        journal.artifact_path(artifact)
        _check_outcome(reader, window, slots[item["outcome_id"]], artifact, item["receipt_ref"])
        items.append(dict(outcome_id=item["outcome_id"], artifact_ref=make_ref(source_id, artifact.id, artifact.as_dict()),
            receipt_ref=item["receipt_ref"], slot_digest=digest(slots[item["outcome_id"]])))
    items.sort(key=lambda item: item["outcome_id"])
    payload = dict(schema=OUTCOME_RELEASE, task_id=window["task_id"], branch_id=window["branch_id"],
        workset_ref=window["workset_ref"], method_refs=window["method_refs"], supersedes=[], boundary=BOUNDARY,
        window_ref=window_ref, decision_ref=decision_ref, outcomes=items,
        window_delivery="host_acknowledged", outcome_delivery="prepared_for_return", consumption="not_attested")
    payload["release_key"] = digest({k: payload[k] for k in ("window_ref", "decision_ref", "outcomes")})
    validate_content(payload)
    from .publication import _save
    path = Path(directory).absolute() / (digest(payload) + ".json")
    _save(path, payload)
    release = Artifact(str(path), **file_identity(path), source_kind=source_kind, role="independent_validation",
        bindings={"schema": OUTCOME_RELEASE, "source_id": source_id, "task_id": window["task_id"]})
    ref = make_ref(source_id, release.id, release.as_dict())
    try:
        with journal.store.connection(write=True) as db:
            _, decision_sequence = _chronology(db, window_ref, decision_ref, window["snapshot_digest"])
            old = db.execute("SELECT body FROM metadata WHERE key=?", ("research:" + release.id,)).fetchone()
            if old is not None:
                if old[0] != canonical(release.as_dict()):
                    raise EvidenceError("release identity changed")
                for item in items:
                    if not decision_sequence < _sequence(db, item["artifact_ref"]) < _sequence(db, ref):
                        raise EvidenceError("ambiguous released outcome chronology")
                return ref
            hashes = {(o["artifact"].sha256, o["artifact"].bytes) for o in outcomes}
            for row in db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:artifact-%'"):
                body = json.loads(row["body"])
                if (body["sha256"], body["bytes"]) in hashes:
                    raise EvidenceError("outcome already recorded: retrospective or ambiguous release required")
            for item in items:
                if item["receipt_ref"]["source_id"] != source_id:
                    raise EvidenceError("controlled release needs a local durable receipt")
                receipt_at = _sequence(db, item["receipt_ref"])
                if slots[item["outcome_id"]]["kind"] == "future_execution" and receipt_at <= decision_sequence:
                    raise EvidenceError("future execution receipt must follow the frozen decision")
            for item in outcomes:
                artifact = item["artifact"]
                journal.artifact_path(artifact)
                journal._artifacts(db, artifact.parents)
                journal._put(db, artifact.id, artifact.as_dict())
            journal.artifact_path(release)
            journal._put(db, release.id, release.as_dict())
    except Exception as exc:
        with journal.store.connection(write=True) as db:
            CampaignStore._event(db, "evidence_outcome_release_failed", dict(window_ref=window_ref,
                decision_ref=decision_ref, release_key=payload["release_key"], error=str(exc)[:300]))
        raise
    return ref


@bounded_read
def validate_local_links(journal, content, source_id, external_workspace=None):
    payload = validate_content(content)
    reader = _local_reader(journal, source_id, external_workspace)
    if payload["schema"] == EVIDENCE_WINDOW:
        return _verify_window(reader, payload)
    window = _verify_window(reader, reader.body(payload["window_ref"], EVIDENCE_WINDOW))
    decision = reader.body(payload["decision_ref"], "etalon-research-decision/1")
    for key in ("task_id", "branch_id", "workset_ref", "method_refs"):
        if payload[key] != window[key]:
            raise EvidenceError("release task, branch, workset or method binding changed")
    if window["timing_mode"] != "controlled" or not _same(decision.get("evidence_window_ref", {}), payload["window_ref"]):
        raise EvidenceError("release must link the controlled decision window")
    _decision(reader, decision)
    slots = {s["outcome_id"]: s for s in window["expected_outcomes"]}
    if len(payload["outcomes"]) != len(slots) or {o["outcome_id"] for o in payload["outcomes"]} != set(slots):
        raise EvidenceError("release changed frozen outcome coverage")
    for item in payload["outcomes"]:
        slot = slots[item["outcome_id"]]
        artifact = Artifact(**reader.material(item["artifact_ref"])["record"])
        if item["slot_digest"] != digest(slot):
            raise EvidenceError("release changed frozen outcome contract")
        _check_outcome(reader, window, slot, artifact, item["receipt_ref"])
    if not hasattr(journal, "store"):
        return payload  # Publication preflight checks links; actual append checks durable order.
    with journal.store.connection() as db:
        if payload["window_ref"]["source_id"] != source_id or payload["decision_ref"]["source_id"] != source_id:
            raise EvidenceError("controlled release requires window and decision in the same local journal")
        _, decision_sequence = _chronology(db, payload["window_ref"], payload["decision_ref"], window["snapshot_digest"])
        for item in payload["outcomes"]:
            if item["receipt_ref"]["source_id"] != source_id or item["artifact_ref"]["source_id"] != source_id:
                raise EvidenceError("controlled release needs a local durable receipt and outcome")
            if _sequence(db, item["artifact_ref"]) <= decision_sequence:
                raise EvidenceError("outcome must follow frozen decision")
            if (slots[item["outcome_id"]]["kind"] == "future_execution"
                    and _sequence(db, item["receipt_ref"]) <= decision_sequence):
                raise EvidenceError("future execution receipt must follow the frozen decision")
            body = reader.material(item["artifact_ref"])["record"]
            for row in db.execute("SELECT key,body FROM metadata WHERE key LIKE 'research:artifact-%' "
                    "AND json_extract(body,'$.sha256')=? AND json_extract(body,'$.bytes')=?", (body["sha256"], body["bytes"])):
                earlier = json.loads(row["body"])
                earlier_ref = make_ref(source_id, row["key"].removeprefix("research:"), earlier)
                if _sequence(db, earlier_ref) <= decision_sequence:
                    raise EvidenceError("earlier original bytes cannot become a prospective release")
    return payload
