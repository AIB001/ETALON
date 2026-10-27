"""Three-component capability discovery and opt-in memory of verified outputs.

Readers invoke existing producer/consumer validators, never run/submit/retry.
Recording is a separate, explicit Journal operation. Scientific labels and
charges remain with the original admission and accounting interfaces.
"""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, file_identity


def capabilities():
    """Installed interface declarations, not availability probes or a task router."""
    quarry_skills = ["molquarry-" + name for name in (
        "analogue-search", "assay-literature", "compound-sourcing", "selectivity-evidence",
        "structure-templates", "target-modulators")]
    return {"schema": "etalon-component-catalog/1", "capabilities": [
        {"id": "molquarry.data", "owner": "MolQuarry", "views": ["E"],
         "operations": ["query", "collect", "download", "import_catalog", "search_catalog", "sourcing", "bundle", "knowledge"],
         "entrypoints": ["etalon.data.service.plan_data", "etalon.data.service.run_data", "etalon.data.artifacts.read_snapshot"],
         "skills": quarry_skills, "memory_reader": "data_snapshot", "integration": "existing_consumer",
         "boundary": "Source evidence, agent-authored scoped knowledge and sourcing leads; no purchase, chemical synthesis or lab execution."},
        {"id": "molcascade.screen", "owner": "MolCascade", "views": ["X", "R"],
         "operations": ["plan", "run", "read", "metrics", "recall", "export_shortlist", "handoff"],
         "entrypoints": ["etalon.boundary.screen.Screen"], "skills": [],
         "memory_reader": "screen_result", "integration": "existing_consumer",
         "boundary": "Keep stage contracts, candidate/pose identity and zero-survivor versus failure distinct."},
        {"id": "etalon.library", "owner": "ETALON", "views": ["E"],
         "operations": ["prepare_library", "import_candidates"],
         "entrypoints": ["etalon.data.library.prepare_library", "etalon.data.library.import_candidates"],
         "skills": [], "memory_reader": "data_snapshot", "integration": "existing_consumer",
         "boundary": "Cross-component source-to-candidate mapping; no automatic assay admission."},
        {"id": "prism.design", "owner": "PRISM", "views": ["E", "X", "R"],
         "operations": ["read_bundle", "import_bundle", "verify_import"],
         "entrypoints": ["etalon.data.design.read_bundle", "etalon.data.design.import_bundle", "etalon.data.design.verify_import"],
         "skills": [], "memory_reader": "design_bundle", "integration": "existing_consumer",
         "boundary": "Producer owns chemistry and QC; import retains all slots, failures, states and poses, not affinity labels."},
        {"id": "prism.legacy_mmpbsa", "owner": "PRISM", "views": ["X", "R"],
         "operations": ["prepared_handoff", "MD", "MM-PBSA"],
         "entrypoints": ["etalon.runtime.executors.prepare_executor(kind='prism')", "etalon.active.prism.PrismV1Executor"],
         "skills": [], "memory_reader": None, "integration": "existing_asset_executor",
         "boundary": "Legacy asset/prism and prism-mmpbsa/1 protocol; native environment and handoff required. Not the independent PRISM bridge; no new execution acceptance in this batch."},
        {"id": "prism.bridge_v1", "owner": "PRISM", "views": ["X", "R"],
         "operations": ["prepare", "submit", "read", "recover"],
         "entrypoints": ["etalon.runtime.api.prepare_action", "etalon.runtime.api.submit_action", "etalon.runtime.api.read_action_result"],
         "skills": [], "memory_reader": None, "integration": "existing_restricted_bridge",
         "boundary": "Existing contact-count engineering protocol only. Link original runtime handles and receipts; no new dispatch adapter here."},
        {"id": "prism.fixed_pool", "owner": "PRISM", "views": ["X", "R"],
         "operations": ["fixed_pose_scoring", "receptor_preparation"],
         "entrypoints": ["etalon.research.pool.execute", "etalon.research.preparation"],
         "skills": [], "memory_reader": None, "integration": "existing_science_consumer",
         "boundary": "Existing frozen protocol, identity and ledger remain authoritative; R4b is not resumed by recording nodes."},
        {"id": "prism.general_fep_lab", "owner": "PRISM", "views": ["X", "R"],
         "operations": ["general_FEP", "lab_handoff"], "entrypoints": [], "skills": [],
         "memory_reader": None, "integration": "not_connected",
         "boundary": "No general ETALON producer/consumer closure; a proposed node is not an executable capability."}
    ], "method_ownership": {"component_methods": "Keep component-native instructions in their component.",
        "cross_component": "ETALON evidence synthesis, experiment design, continuation and comparison.",
        "workflow": "Optional content; host may skip, partly adopt, replace or stop nodes."}}


def _files(paths):
    paths = sorted(set(Path(p).absolute() for p in paths))
    if len(paths) > 60:
        raise EvidenceError("component receipt exceeds bounded file linking; publish a smaller explicit view")
    return [{"path": str(p), **file_identity(p)} for p in paths]


def inspect_component(capability_id, location, *, run_id=None):
    """Read one explicit existing component output, without mutation or networking."""
    root = Path(location).absolute()
    identities = []
    if capability_id in {"molquarry.data", "etalon.library"}:
        from etalon.data.artifacts import read_snapshot
        snapshot = read_snapshot(root)
        allowed = {"library"} if capability_id == "etalon.library" else {
            "query", "collect", "import_catalog", "search_catalog", "sourcing", "download", "bundle"}
        if snapshot["kind"] not in allowed:
            raise EvidenceError("snapshot kind differs from component capability")
        files = _files([root / "snapshot.json", *(root / p for p in snapshot["files"])])
        native = {k: snapshot[k] for k in ("kind", "snapshot_id", "result", "usage", "infrastructure") if k in snapshot}
        state = "partial" if snapshot["result"].get("status") == "partial" else "complete"
        identities = [{"kind": "source", "value": snapshot["snapshot_id"]}]
        for relative, pin in snapshot["files"].items():
            if file_identity(root / relative) != pin:
                raise EvidenceError("data snapshot changed during inspection")
    elif capability_id == "molcascade.screen":
        from etalon.boundary.screen import Screen
        screen = Screen.open_existing(root)
        result = screen.state(run_id)
        if result is None:
            raise EvidenceError("no such screen run; absent output is not a successful empty result")
        from molcascade.artifacts.store import LocalArtifactStore
        store = LocalArtifactStore(root, create=False)
        paths = [root / "runs" / (run_id + ".json")]
        manifests = []
        for stage in result.committed:
            directory = store.artifact_directory(stage.artifact_id, verify=True)
            manifest = store.get_manifest(stage.artifact_id)
            manifests.append(manifest.model_dump(mode="json"))
            paths.extend(p for p in directory.rglob("*") if p.is_file())
        files = _files(paths)
        native = {"result": result.as_dict(), "manifests": manifests, "infrastructure": screen.infra.provenance()}
        state = ("waiting" if not result.terminal else "failed" if result.outcome == "failed" else "complete")
        identities = [{"kind": "attempt", "value": run_id}, {"kind": "protocol", "value": result.revision_id}]
        # The original committed files retain the complete source/candidate/pose rows.
        if screen.state(run_id).as_dict() != result.as_dict():
            raise EvidenceError("screen state changed during inspection")
        for stage in result.committed:
            store.artifact_directory(stage.artifact_id, verify=True)
    elif capability_id == "prism.design":
        from etalon.data.design import read_bundle
        checked = read_bundle(root)
        bundle = checked["bundle"]
        files = _files([root, *(root.parent / p for p in checked["references"])])
        native = {"bundle_id": bundle["bundle_id"], "design_id": bundle["design_id"],
                  "generation_action_id": bundle["generation_action_id"], "source": bundle["source"],
                  "receptor": bundle["receptor"], "candidates": bundle["candidates"],
                  "schema_sha256": checked["schema_sha256"]}
        state = "complete"  # The bundle read completed; slot QC statuses are kept in native.
        identities = [{"kind": "source", "value": bundle["bundle_id"]},
                      {"kind": "attempt", "value": bundle["generation_action_id"]}]
        for row in bundle["candidates"]:
            identities.append({"kind": "candidate", "value": row["candidate_id"]})
            for key, kind in (("molecule_id", "molecule"), ("chemical_state_id", "chemical_state"), ("pose_id", "pose")):
                if row["identity"] is not None:
                    identities.append({"kind": kind, "value": row["identity"][key]})
        if read_bundle(root)["bundle_file_sha256"] != checked["bundle_file_sha256"]:
            raise EvidenceError("design bundle changed during inspection")
    else:
        raise EvidenceError("no read adapter for this capability; use its existing SDK and explicit node receipts")
    return {"schema": "etalon-component-read/1", "capability_id": capability_id, "location": str(root),
            "state": state, "native": native, "files": files, "identities": identities,
            "volatile_paths": [str(root / "runs" / (run_id + ".json"))] if capability_id == "molcascade.screen" else [],
            "verification": "existing_component_reader", "activity": "read_existing",
            "scientific_admission": "not_performed", "new_execution_cost": None}


def _freeze_bytes(path, pin, directory):
    """Capture a small mutable status receipt, retaining its exact original bytes."""
    import os
    import tempfile
    directory = Path(directory).absolute() / "status-snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    content = Path(path).read_bytes()
    import hashlib
    if {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)} != {k: pin[k] for k in ("sha256", "bytes")}:
        raise EvidenceError("component state changed before snapshot")
    target = directory / (pin["sha256"] + ".json")
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.read_bytes() != content:
                raise EvidenceError("immutable status snapshot conflict")
    finally:
        temporary.unlink()
    return str(target)


def record_json(journal, payload, directory, *, source_kind, bindings=None):
    """Immutable ordinary Artifact; complete bytes retained, no custom DB table."""
    from etalon.data.artifacts import write_json
    from .schema import Artifact
    content = json.loads(canonical(payload))
    directory = Path(directory).absolute()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest(content) + ".json")
    if not path.exists():
        try:
            write_json(path, content)
        except FileExistsError:
            pass
    expected = (json.dumps(content, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    # write_json uses native text newlines; compare the actual publication format.
    import os
    expected = expected.replace(b"\n", os.linesep.encode())
    if path.read_bytes() != expected:
        raise EvidenceError("immutable component record conflict")
    artifact = Artifact(str(path), **file_identity(path), source_kind=source_kind, role="diagnostic",
                        bindings=bindings or {})
    return journal.record(artifact)


def record_component_step(journal, *, capability_id, location, directory, source_id, source_kind,
                          task_id, question, branch_id, node_id, reason, run_id=None,
                          workflow_ref=None, input_refs=(), method_refs=(), use_refs=(),
                          accounting_refs=(), supersedes=()):
    """Inspect existing outputs and attach them to one node. Never runs a component.

    This step is reused/partial/failed/waiting, never a claim that reading produced
    new science. Existing records and costs are referenced, not charged again.
    """
    from .composition import node_step
    from .continuation import record_content
    from .method_revision import journal_ref
    from .schema import Artifact
    report = inspect_component(capability_id, location, run_id=run_id)
    # Reject changed bytes before appending any records. Failed DB writes can leave
    # earlier valid Artifacts, recoverable by their existing idempotent identities.
    for pin in report["files"]:
        if file_identity(pin["path"]) != {k: pin[k] for k in ("sha256", "bytes")}:
            raise EvidenceError("component bytes changed before recording")
    refs = []
    for pin in report["files"]:
        bindings = {"component_capability": capability_id, "activity": "read_existing"}
        linked = pin
        if pin["path"] in report["volatile_paths"]:
            linked = {**pin, "path": _freeze_bytes(pin["path"], pin, directory)}
            bindings.update(original_path=pin["path"], original_sha256=pin["sha256"], state_semantics="historical_snapshot_not_heartbeat")
        rid = journal.record(Artifact(**linked, source_kind=source_kind, role="diagnostic", bindings=bindings))
        refs.append(journal_ref(journal, rid, source_id=source_id))
    report_id = record_json(journal, {**report, "evidence_refs": refs}, directory,
                           source_kind=source_kind, bindings={"component_capability": capability_id,
                           "kind": "component_receipt", "activity": "read_existing"})
    receipt = journal_ref(journal, report_id, source_id=source_id)
    # An explicit summary is bounded; full identities remain in the original receipt.
    identities = [{**i, "basis_ref": receipt} for i in report["identities"][:32]]
    step = node_step(task_id=task_id, question=question, branch_id=branch_id, node_id=node_id,
        capability_id=capability_id, workflow_ref=workflow_ref,
        state="reused" if report["state"] == "complete" else report["state"], reason=reason,
        input_refs=input_refs, output_refs=[receipt], method_refs=method_refs, use_refs=use_refs,
        receipt_refs=[receipt], identities=identities, accounting_refs=accounting_refs, supersedes=supersedes)
    # Link all original files via inputs so continuation checks each identity; the
    # ordinary receipt is intentionally not a new projected schema or execution.
    step["input_refs"] = list(input_refs) + refs
    rid = record_content(journal, step, directory, source_id=source_id, source_kind=source_kind)
    return {"step_ref": journal_ref(journal, rid, source_id=source_id), "receipt_ref": receipt,
            "artifact_refs": refs, "state": step["detail"]["state"],
            "identity_count": len(report["identities"]), "step_identity_summary_truncated": len(report["identities"]) > 32}
