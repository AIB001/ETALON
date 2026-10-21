"""Optional revision Artifact helpers; never an execution gate or a new ledger.

Build accepts source-qualified references without reading them. Record verifies
local method lineage and producer attribution before publishing immutable JSON.
External evidence stays explicitly unresolved here; use evidence.read_evidence
to inspect it, or register a local source-envelope Artifact when appropriate.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from etalon.active.schema import canonical, digest
from etalon.boundary.prism_v1 import file_identity
from .evidence_refs import make_ref, validate_ref
from .schema import Artifact, nonempty


def journal_ref(journal, record_id: str, *, source_id: str) -> dict:
    """Bind a record to the caller's explicit workspace source ID, not its path.

    The caller owns source-ID/workspace identity binding. This does not infer a
    globally stable ID from a mutable database hash or register a new source.
    """
    return make_ref(source_id, record_id, journal.get(record_id))


def build_method_revision(*, problem_refs, target_kind, base_method_ref,
                          producer_method_ref, producer_use_ref,
                          change_summary, mechanism_hypothesis, scope,
                          candidate_version_refs=(), counterexamples=(),
                          trial_refs=(), accounting_refs=()) -> dict:
    """Build JSON content, including reasoned no-change (empty candidates).

    No journal, files, tools, model, registration or trial is required. Unknown
    costs belong in accounting artifacts, never invented numeric zeroes.
    """
    if target_kind not in {"S", "U", "G"}:
        raise ValueError("target_kind must be S, U or G")
    for value in (change_summary, mechanism_hypothesis, scope):
        nonempty(value)

    def refs(values):
        if not isinstance(values, (list, tuple)):
            raise ValueError("references must be a sequence")
        result = [validate_ref(ref) for ref in values]
        if len({canonical(ref) for ref in result}) != len(result):
            raise ValueError("duplicate reference")
        return result

    problems = refs(problem_refs)
    if not problems:
        raise ValueError("a revision needs a concrete problem reference")
    if not isinstance(counterexamples, (list, tuple)):
        raise ValueError("counterexamples must be a sequence of text")
    for item in counterexamples:
        nonempty(item)
    payload = dict(schema="etalon-method-revision/1", problem_refs=problems,
                   target_kind=target_kind, base_method_ref=validate_ref(base_method_ref),
                   producer_method_ref=validate_ref(producer_method_ref),
                   producer_use_ref=validate_ref(producer_use_ref),
                   candidate_version_refs=refs(candidate_version_refs),
                   change_summary=change_summary, mechanism_hypothesis=mechanism_hypothesis,
                   scope=scope, counterexamples=list(counterexamples),
                   trial_refs=refs(trial_refs), accounting_refs=refs(accounting_refs))
    return json.loads(canonical(payload))


def record_method_revision(journal, payload: dict, directory: str | Path, *,
                           source_id: str, source_kind: str,
                           role: str = "diagnostic") -> str:
    """Publish content-addressed JSON, then record an ordinary Artifact.

    Replays in the same directory return the same Artifact ID/event. File and
    DB are separate commits: a failed DB write leaves a verifiable orphan for
    retry; no files or facts are rolled back/deleted. Local reference metadata
    is checked; this does not attest native method application or external files.
    Method references must be local: import an external complete snapshot and
    explicit provenance envelope first, or use build_method_revision alone.
    """
    nonempty(source_id)
    if not isinstance(payload, dict) or payload.get("schema") != "etalon-method-revision/1":
        raise ValueError("expected etalon-method-revision/1")
    content = build_method_revision(**{k: v for k, v in payload.items() if k != "schema"})

    def local(ref, schema=None):
        if ref["source_id"] != source_id:
            raise ValueError("method references require local snapshots and source envelopes")
        body = journal.get(ref["record_id"])
        if digest(body) != ref["record_digest"]:
            raise ValueError("record digest mismatch")
        if schema and body.get("schema") != schema:
            raise ValueError("wrong referenced method record type")
        return body

    base = local(content["base_method_ref"], "etalon-method-version/1")
    producer = local(content["producer_method_ref"], "etalon-method-version/1")
    use = local(content["producer_use_ref"], "etalon-method-use/1")
    producer_id = content["producer_method_ref"]["record_id"]
    if producer_id not in {r["method_version_ref"] for r in use["selected_methods"]}:
        raise ValueError("producer use must attribute the producer method; read is not applied")
    target_role = {"S": "domain_skill", "U": "usage_method", "G": "improvement_method"}[content["target_kind"]]
    if target_role not in base["roles"] or "improvement_method" not in producer["roles"]:
        raise ValueError("target/producer roles do not match revision kind")
    base_id = content["base_method_ref"]["record_id"]
    for ref in content["candidate_version_refs"]:
        candidate = local(ref, "etalon-method-version/1")
        if ref["record_id"] == base_id or base_id not in candidate["parents"]:
            raise ValueError("candidate must descend from the target base")
        if producer_id != base_id and producer_id in candidate["parents"]:
            raise ValueError("producer is not a target parent")
        if target_role not in candidate["roles"]:
            raise ValueError("candidate target role mismatch")
    external = []
    for field in ("problem_refs", "trial_refs", "accounting_refs"):
        for ref in content[field]:
            if ref["source_id"] == source_id:
                body = local(ref)
                if field == "accounting_refs" and body.get("bindings", {}).get("kind") not in {
                    "accounting", "cost_receipt", "resource_accounting"
                }:
                    raise ValueError("accounting ref must identify an accounting artifact")
            else:
                external.append(ref)
    data = (canonical(content) + "\n").encode("utf-8")
    directory = Path(directory).absolute()
    path = directory / (digest(content) + ".json")
    item = Artifact(str(path), sha256=hashlib.sha256(data).hexdigest(),
                    bytes=len(data), source_kind=source_kind, role=role,
                    bindings={"kind": "method_revision", "schema": content["schema"],
                              "source_id": source_id, "reference_verification":
                              "local_metadata_checked_external_unresolved" if external else "local_metadata_checked"})
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("immutable revision file conflicts")
    else:
        # Publish a fully written file without replacing an existing destination.
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".revision-", delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("immutable revision file conflicts")
        finally:
            temporary.unlink()
    if file_identity(path) != {"sha256": item.sha256, "bytes": item.bytes}:
        raise ValueError("published revision changed")
    return journal.record(item)
