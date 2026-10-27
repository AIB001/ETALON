"""Optional execution attribution and result publication, without a research loop.

Bind a host's existing MethodUse once, then consume durable runtime events. A
publication failure never changes the calculation outcome or dispatches work.
Scientific evaluation and scoped activation remain explicit host decisions.
"""
from __future__ import annotations

from pathlib import Path

from etalon.active.schema import digest
from etalon.host import consume_events
from etalon.runtime.files import _consumer_lock, _read, _save, file_identity
from .continuation import STEP, read_content
from .evidence import read_evidence
from .evidence_refs import EvidenceError, make_ref, validate_ref
from .publication import prepare_publication, resume_publication
from .research_context import load_entry
from .read_scope import bounded_read


def _record(workspace, ref, schema):
    result = read_evidence(workspace, ref)
    value = result.get("record", {})
    if result.get("availability") != "available" or value.get("schema") != schema:
        raise EvidenceError("method binding requires an available " + schema)
    return value


def _execution(node):
    return digest({k: node[k] for k in ("operation", "arguments")})


def validate_binding(binding, node):
    from etalon.runtime.schema import absolute, fields, text
    fields(binding, {"entry", "task_id", "branch_id", "use_refs", "method_refs", "execution_identity"},
           {"evaluation_plan_ref"})
    absolute(binding["entry"])
    for name in ("task_id", "branch_id"):
        text(binding[name], name)
    for key in ("use_refs", "method_refs"):
        if not isinstance(binding[key], list) or not binding[key]:
            raise EvidenceError("bind an explicit existing method selection")
        for ref in binding[key]:
            validate_ref(ref)
    if binding.get("evaluation_plan_ref") is not None:
        validate_ref(binding["evaluation_plan_ref"])
    if binding["execution_identity"] != _execution(node):
        raise EvidenceError("changed method arguments require an explicit updated research binding")
    return binding


@bounded_read
def bind_method_execution(node, *, entry, branch_id, use_refs=None, evaluation_plan_ref=None):
    """Return an action request or workflow node with optional research metadata.

MethodUse records the host's selection, including partial use. The execution
receipt separately records what ran; neither reading nor execution proves that
every instruction in a skill was followed. This helper does not write or run.
"""
    workspace, cp_ref = load_entry(entry)
    cp = read_content(workspace, cp_ref)
    if not any(b["branch_id"] == branch_id for b in cp["branches"]):
        raise EvidenceError("research binding needs an existing branch")
    inferred = use_refs is None
    if use_refs is None:
        from .meta_context import read_task_workset
        active = read_task_workset(workspace, cp_ref)
        use_refs = []
        if active["status"] == "active":
            activation = read_content(workspace, active["activation_ref"])
            for ref in activation["input_refs"]:
                record = read_evidence(workspace, ref).get("record", {})
                if record.get("schema") == "etalon-method-use/1":
                    use_refs.append(ref)
    methods = {}
    for ref in use_refs:
        use = _record(workspace, ref, "etalon-method-use/1")
        if not any(s["kind"] == "task" and s["id"] == cp["task_id"] for s in use["subject_refs"]):
            raise EvidenceError("method selection must include the same task")
        for selected in use["selected_methods"]:
            # References within MethodUse are local to its authoritative journal.
            from .evidence_refs import EvidenceResolver
            from etalon.active import CampaignStore
            from .journal import ResearchJournal
            resolver = EvidenceResolver(workspace, ref["source_id"])
            journal = ResearchJournal(CampaignStore(resolver.resolve_source(use["subject_refs"][0]["scope"]), read_only=True))
            value = journal.get(selected["method_version_ref"])
            if inferred and node["operation"] == "method.run" and node["arguments"]["method_id"] not in {
                    selected["method_version_ref"], value["name"]}:
                raise EvidenceError("requested program differs from the active method; select it explicitly")
            method = make_ref(ref["source_id"], selected["method_version_ref"], value)
            _record(workspace, method, "etalon-method-version/1")
            methods[digest(method)] = method
    if evaluation_plan_ref is not None:
        plan = read_content(workspace, evaluation_plan_ref)
        if plan.get("operation") != "evaluation_plan" or plan["task_id"] != cp["task_id"]:
            raise EvidenceError("evaluation plan must belong to this task")
    binding = dict(entry=str(Path(entry).absolute()), task_id=cp["task_id"], branch_id=branch_id,
                   use_refs=list(use_refs), method_refs=list(methods.values()), execution_identity=_execution(node))
    if evaluation_plan_ref is not None:
        binding["evaluation_plan_ref"] = evaluation_plan_ref
    return {**node, "research": validate_binding(binding, node)}


def collect_method_results(workspace, job_id, *, limit=100):
    """Consume one bounded event page; retry only archival work on reentry.

The cursor and publication intent, not the last-100-event status view, are the
recovery boundary. No model, evaluation, experiment or method promotion runs.
"""
    from etalon.runtime.store import RuntimeStore
    store = RuntimeStore(workspace)
    job = store.get(job_id)
    nodes = {n["id"]: n for n in job["nodes"]}
    publications = []

    def handle(event, request_id):
        node = nodes[event["body"]["node_id"]]
        binding = node["definition"].get("research")
        if binding is None:
            return
        validate_binding(binding, node["definition"])
        root = store.job_root(job_id) / "research" / digest(request_id)[:16]
        root.mkdir(parents=True, exist_ok=True)
        entry = Path(binding["entry"])
        # Serialize this adapter's writers without taking the publisher's lock.
        with _consumer_lock(entry.parent / (entry.name + ".method-results")):
            saved = root / "request.json"
            if saved.exists():
                request = _read(saved)
            else:
                ws, cp_ref = load_entry(entry)
                cp = read_content(ws, cp_ref)
                if cp["task_id"] != binding["task_id"] or not any(
                        b["branch_id"] == binding["branch_id"] for b in cp["branches"]):
                    raise EvidenceError("entry switched tasks; retain result for its original research scope")
                # Preserve actual result and cost bases, including failures and
                # native artifact identities. Bulk files stay in native archives.
                receipt = {"job_id": job_id, "node_id": node["id"], "event_ref": event["event_ref"],
                    "operation": node["definition"]["operation"], "parameters": node["parameters"],
                    "state": node["state"], "result": node["result"], "evidence": node["evidence"],
                    "costs": node["costs"], "error": node["error"], "receipts": store.receipts(job_id, node["id"]),
                    "research": binding, "scientific_admission": "not_inferred"}
                path = root / "receipt.json"
                _save(path, receipt)
                alias = {"publication_ref": "execution"}
                # Aliases are resolved/validated by the existing publisher.
                step = dict(schema=STEP, task_id=cp["task_id"], question=cp["question"],
                    branch_id=binding["branch_id"], operation="node", reason="Completed execution feedback",
                    input_refs=[binding["evaluation_plan_ref"]] if binding.get("evaluation_plan_ref") else [],
                    output_refs=[alias], method_refs=binding["method_refs"], accounting_refs=[alias],
                    source_watermarks=[], supersedes=[], detail=dict(node_id=node["id"], workflow_ref=None,
                    capability_id=node["definition"]["operation"], state="completed" if node["state"] == "verified" else "failed",
                    use_refs=binding["use_refs"], receipt_refs=[alias], identities=[], action_handles=[]))
                request = dict(entry=str(entry), directory=str(store.workspace / "research-publications"), operation_id=request_id,
                    expected_entry_identity=file_identity(entry), results=[dict(name="execution", path=str(path),
                    identity=file_identity(path), source_kind="existing_real_artifact", role="diagnostic",
                    bindings={"schema": "etalon-method-feedback/1", "kind": "cost_receipt",
                              "evidence_refs": binding["use_refs"] + binding["method_refs"]})],
                    steps=[dict(name="execution_node", content=step)],
                    checkpoint_additions={"method_refs": binding["method_refs"]})
                _save(saved, request)
            prepared = prepare_publication(**request)
            result = resume_publication(prepared["intent_path"])
            publications.append({"node_id": node["id"], "intent_path": prepared["intent_path"],
                                 "status": result["status"], "checkpoint_ref": result.get("checkpoint_ref")})

    page = consume_events({"kind": "runtime", "workspace": str(store.workspace), "job_id": job_id},
        state_path=store.job_root(job_id) / "research-cursor.json", handler_method_version="method-publication/1",
        handler=handle, event_types=["node_verified", "node_failed"], limit=limit)
    return {"status": "pending" if page["has_more"] else "caught_up", "publications": publications,
            "cursor": page["next_cursor"], "has_more": page["has_more"], "execution": "not_performed"}


def execution_evaluation_inputs(journal, *, entry, node_refs):
    """Connect published executions to an existing writer's comparison plan.

Reuse immutable receipt Artifacts by identity; retain external node/use refs.
No observation labels, scientific conclusion or independent-validation status
are inferred. Returned fields feed composition.record_evaluation directly.
"""
    from .schema import Artifact
    from .evidence_refs import EvidenceResolver, load_workspace, resolve_artifact
    workspace, cp_ref = load_entry(entry)
    ws = load_workspace(workspace)
    for source in ws.manifest["sources"]:
        if (source["identity"]["kind"] == "snapshot_sha256"
                and (ws.base / source["location"]).resolve() == journal.store.path.resolve()):
            raise EvidenceError("use a writer journal, not a frozen published source")
    cp = read_content(workspace, cp_ref)
    results, costs, uses = {}, {}, {}
    for ref in node_refs:
        node = read_content(workspace, ref)
        if (node.get("operation") != "node" or node["task_id"] != cp["task_id"]
                or node["detail"]["state"] not in {"completed", "reused", "failed", "partial"}):
            raise EvidenceError("evaluation requires observed nodes of the current task")
        for use in node["detail"]["use_refs"]:
            uses[digest(use)] = use
        for refs, target in ((node["output_refs"], results), (node["accounting_refs"], costs)):
            for item in refs:
                value = read_evidence(workspace, item)
                if value.get("availability") != "available":
                    raise EvidenceError("execution evidence unavailable")
                artifact = Artifact(**value["record"])
                original = resolve_artifact(artifact, resolver=EvidenceResolver(ws, item["source_id"]))
                if str(original) != artifact.path:
                    from dataclasses import replace
                    artifact = replace(artifact, path=str(original),
                                       bindings={**artifact.bindings, "source_ref": item})
                # The journal rechecks original bytes and idempotently reuses
                # the same Artifact; copying metadata is not another run.
                target[artifact.id] = journal.record(artifact)
    if not node_refs:
        raise EvidenceError("select the executions being evaluated")
    return dict(result_refs=list(results), accounting_refs=list(costs), use_refs=list(uses.values()),
                node_refs=list(node_refs), external_workspace=workspace)
