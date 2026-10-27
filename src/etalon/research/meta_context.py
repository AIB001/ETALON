"""Optional cross-type discovery and task-local adaptation, never an executor.

Small versioned metadata is packed in one ordinary Artifact. Scientific originals
stay behind exact evidence references; publication/checkpoints remain authoritative.
"""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, file_identity, validate_ref

PROFILE = "etalon-task-profile/1"
RESOURCE = "etalon-resource-descriptor/1"
INDEX = "etalon-resource-index/1"
REVIEW = "etalon-adaptation-review/1"
WORKSET = "etalon-task-workset/1"
KINDS = {"skill", "method", "workflow", "claim", "evidence", "experience", "verifier", "template"}
MAX_BYTES = 32768
MAX_RESOURCES = 64


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError("explicit nonempty text required")
    return value


def _strings(value):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value) or len(set(value)) != len(value):
        raise EvidenceError("distinct explicit strings required")
    return value


def _seal(value):
    value = json.loads(canonical(value))
    result = {**value, "digest": digest(value)}
    if len(canonical(result).encode()) > MAX_BYTES:
        raise EvidenceError("meta metadata exceeds 32 KiB; use a smaller explicit scope")
    return result


def _check(value, schema):
    if not isinstance(value, dict) or value.get("schema") != schema or value.get("digest") != digest({k: v for k, v in value.items() if k != "digest"}):
        raise EvidenceError("meta schema or digest mismatch: " + schema)
    if len(canonical(value).encode()) > MAX_BYTES:
        raise EvidenceError("meta metadata exceeds 32 KiB")


def _definition_map(value, *, unknown=False):
    """Definition identities, not labels or inferred scientific equivalence."""
    if not isinstance(value, dict) or len(value) > 64:
        raise EvidenceError("definitions require a bounded name/digest mapping")
    for name, identity in value.items():
        _text(name)
        if identity is None and unknown:
            continue
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity):
            raise EvidenceError("definition identity must be a SHA256 or explicit unknown")
    return dict(value)


def compare_definitions(expected, actual):
    """Check requested meanings only; absent legacy meanings remain unknown."""
    expected = _definition_map(expected, unknown=True)
    actual = _definition_map(actual, unknown=True)
    return {status: sorted(key for key, value in expected.items() if (
        "missing" if value is None or actual.get(key) is None else
        "matched" if value == actual[key] else "mismatched") == status)
        for status in ("missing", "mismatched", "matched")}


def make_task_profile(*, task_id, question, conditions, critical_conditions, available_stages,
                      definitions=None):
    """Unknown conditions are null, never inferred from a task or dataset name."""
    _text(task_id); _text(question)
    _strings(critical_conditions); _strings(available_stages)
    if not isinstance(conditions, dict) or any(not isinstance(k, str) or not k or v is not None and (not isinstance(v, str) or not v) for k, v in conditions.items()):
        raise EvidenceError("profile conditions need strings or explicit null unknowns")
    if not critical_conditions or not set(critical_conditions) <= set(conditions):
        raise EvidenceError("critical conditions must be declared, including unknowns")
    extra = {} if definitions is None else {"definitions": _definition_map(definitions, unknown=True)}
    return _seal(dict(schema=PROFILE, task_id=task_id, question=question,
        conditions=conditions, critical_conditions=critical_conditions, available_stages=available_stages, **extra))


def describe_resource(*, resource_id, kind, content_ref, summary, parts,
                      support_refs=(), counter_refs=(), visibility="research"):
    """Describe declared applicability; inferred conditions belong in review evidence.

    Parts name exact skill members when kind=skill. dependencies use resource/part
    keys, e.g. {resource_id: 'geometry', part_id: 'parser'}. No wildcard scope.
    """
    _text(resource_id); _text(summary)
    if kind not in KINDS or visibility not in {"research", "evaluation_only"}:
        raise EvidenceError("unknown resource kind or visibility")
    if kind == "skill":
        if set(content_ref) - {"slug", "manifest_sha256", "bundle_ref"} or not {"slug", "manifest_sha256"} <= set(content_ref):
            raise EvidenceError("skill needs exact slug and manifest identity")
        _text(content_ref["slug"])
        if len(content_ref["manifest_sha256"]) != 64:
            raise EvidenceError("skill needs a manifest hash")
    else:
        validate_ref(content_ref)
    if not isinstance(parts, list) or not 1 <= len(parts) <= 32:
        raise EvidenceError("resource requires 1..32 explicit parts")
    ids = set()
    for part in parts:
        required = {"part_id", "conditions", "requires", "irrelevant", "available_at", "dependencies"}
        if not required <= set(part) or set(part) - required - {"definitions"}:
            raise EvidenceError("part requires conditions, requires, irrelevant, available_at and dependencies")
        pid = _text(part["part_id"])
        if pid in ids:
            raise EvidenceError("duplicate resource part")
        ids.add(pid)
        _strings(part["requires"]); _strings(part["irrelevant"])
        _text(part["available_at"])
        if not isinstance(part["conditions"], dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in part["conditions"].items()):
            raise EvidenceError("resource conditions require explicit strings")
        if set(part["irrelevant"]) & (set(part["requires"]) | set(part["conditions"])):
            raise EvidenceError("a condition cannot be both relevant and irrelevant")
        if "definitions" in part:
            _definition_map(part["definitions"])
            if set(part["irrelevant"]) & {"definition:" + key for key in part["definitions"]}:
                raise EvidenceError("a declared definition cannot also be irrelevant")
        if not isinstance(part["dependencies"], list):
            raise EvidenceError("dependencies must be explicit")
        for dep in part["dependencies"]:
            _key(dep)
    refs = [[validate_ref(r) for r in values] for values in (support_refs, counter_refs)]
    return _seal(dict(schema=RESOURCE, resource_id=resource_id, kind=kind, content_ref=content_ref,
        summary=summary, parts=parts, support_refs=refs[0], counter_refs=refs[1], visibility=visibility))


def _key(row):
    if not isinstance(row, dict) or set(row) != {"resource_id", "part_id"}:
        raise EvidenceError("resource/part key required")
    return _text(row["resource_id"]), _text(row["part_id"])


def _resource_check(resource):
    _check(resource, RESOURCE)
    rebuilt = describe_resource(**{k: v for k, v in resource.items() if k not in {"schema", "digest"}})
    if rebuilt != resource:
        raise EvidenceError("resource descriptor changed")


def _record(workspace, ref, collection=None):
    from .evidence import _read_evidence
    value = _read_evidence(workspace, ref, limit=32, collection=collection)
    if value.get("availability") != "available" or value.get("missing") or "record" not in value:
        raise EvidenceError("resource reference unavailable or outside published scope")
    return value["record"]


def _material(workspace, ref):
    from .improvement import read_material
    result = read_material(workspace, ref)
    if not result["complete"] or not isinstance(result["content"], dict):
        raise EvidenceError("meta artifact unavailable, incomplete or not JSON")
    return result["content"]


def verify_resource(resource, workspace=None, *, _collection=None):
    """Check original identity, without treating visibility as an access grant."""
    _resource_check(resource)
    if resource["kind"] == "skill":
        from etalon.skills import snapshot
        from .skill_sources import resolve_skill_bundle
        ref = resource["content_ref"]
        manifest, members = snapshot(ref["slug"], bundle_ref=resolve_skill_bundle(ref, workspace))
        if manifest["manifest_sha256"] != ref["manifest_sha256"]:
            raise EvidenceError("skill resource version changed")
        if not {p["part_id"] for p in resource["parts"]} <= set(members):
            raise EvidenceError("skill part is not an exact bundle member")
    else:
        _verify_evidence(workspace, resource["content_ref"], _collection)
    for ref in [*resource["support_refs"], *resource["counter_refs"]]:
        _verify_evidence(workspace, ref, _collection)


def _verify_evidence(workspace, ref, collection=None):
    from .evidence import _read_evidence
    body = _record(workspace, ref, collection)
    if body.get("role") == "sealed_holdout":
        raise EvidenceError("sealed resource cannot enter a workset")
    if "path" in body or "member" in ref or "sha256" in ref:
        checked = _read_evidence(workspace, ref, view="artifact", limit=1, collection=collection)
        if checked.get("verification", {}).get("artifact") != "verified":
            raise EvidenceError("resource original bytes unavailable")


def _dependencies(workset):
    """Expose opaque Artifact content dependencies to existing visibility readers."""
    refs = {}
    for resource in workset["index"]["resources"]:
        values = [*resource["support_refs"], *resource["counter_refs"]]
        if resource["kind"] != "skill":
            values.append(resource["content_ref"])
        for ref in values:
            refs[canonical(ref)] = ref
    if workset["parent_workset_ref"]:
        ref = workset["parent_workset_ref"]
        refs[canonical(ref)] = ref
    return [refs[key] for key in sorted(refs)]


def _verify_resources(resources, workspace):
    """One bounded frozen source view per call, with original bytes rechecked."""
    from .evidence import _collect
    from .evidence_refs import load_workspace
    from .continuation import _check_frozen_view
    refs = _dependencies({"index": {"resources": resources}, "parent_workset_ref": None})
    collection, ws = None, workspace
    if refs:
        ws = load_workspace(workspace)
        if all(s["identity"]["kind"] == "snapshot_sha256" for s in ws.manifest["sources"]):
            view = _collect(ws, sorted({r["source_id"] for r in refs}))
            if not view[2] and all(c["status"] == "complete" for c in view[1]):
                collection = view
    for resource in resources:
        verify_resource(resource, ws, _collection=collection)
    if collection is not None:
        _check_frozen_view(ws, *collection[:2])


def index_resources(resources, *, workspace=None, purpose="research"):
    """Build a bounded explicit catalog; no ambient scan or global completeness claim.

    Filter evaluation-only descriptors before exposing summaries. The caller must
    supply an already purpose-restricted evidence workspace for other access rules.
    """
    if purpose not in {"research", "evaluation"} or not isinstance(resources, list) or len(resources) > MAX_RESOURCES:
        raise EvidenceError("explicit bounded resources and purpose required")
    visible, hidden, ids = [], 0, set()
    for resource in resources:
        if resource.get("visibility") == "evaluation_only" and purpose != "evaluation":
            hidden += 1
            continue
        _resource_check(resource)
        if resource["resource_id"] in ids:
            raise EvidenceError("duplicate resource identity; choose a version explicitly")
        ids.add(resource["resource_id"])
        visible.append(resource)
    _verify_resources(visible, workspace)
    return _seal(dict(schema=INDEX, resources=sorted(visible, key=lambda r: r["resource_id"]), purpose=purpose,
        coverage={"scope": "explicit descriptors only", "status": "complete", "selected_count": len(visible),
                  "hidden_count": hidden, "global_search": False}))


def _index_check(index):
    _check(index, INDEX)
    if index["purpose"] not in {"research", "evaluation"} or len(index["resources"]) > MAX_RESOURCES:
        raise EvidenceError("invalid index scope")
    ids = set()
    for resource in index["resources"]:
        _resource_check(resource)
        if resource["resource_id"] in ids or index["purpose"] == "research" and resource["visibility"] != "research":
            raise EvidenceError("duplicate or hidden indexed resource")
        ids.add(resource["resource_id"])


def prepare_adaptation_context(profile, index):
    """Mechanical condition checks, not scientific applicability inference."""
    _check(profile, PROFILE); _index_check(index)
    if make_task_profile(**{k: v for k, v in profile.items() if k not in {"schema", "digest"}}) != profile:
        raise EvidenceError("invalid task profile")
    candidates = []
    for resource in index["resources"]:
        for part in resource["parts"]:
            checks = {}
            keys = set(profile["critical_conditions"]) | set(part["requires"]) | set(part["conditions"])
            for key in sorted(keys):
                actual, expected = profile["conditions"].get(key), part["conditions"].get(key)
                checks[key] = ("not_applicable" if key in part["irrelevant"] else
                    "unknown" if actual is None or expected is None else "match" if actual == expected else "conflict")
            checks["available_at"] = "match" if part["available_at"] in profile["available_stages"] else "conflict"
            # A matching dataset/task name never establishes matching meanings.
            # Portable parts may explicitly mark definition:<name> irrelevant;
            # old descriptors with no declaration remain unknown, not portable.
            wanted, declared = profile.get("definitions", {}), part.get("definitions", {})
            for key in sorted(set(wanted) | set(declared)):
                name = "definition:" + key
                actual, expected = wanted.get(key), declared.get(key)
                checks[name] = ("not_applicable" if name in part["irrelevant"] else
                    "unknown" if actual is None or expected is None else
                    "match" if actual == expected else "conflict")
            status = "conflict" if "conflict" in checks.values() else "unknown" if "unknown" in checks.values() else "match"
            candidates.append(dict(resource_id=resource["resource_id"], part_id=part["part_id"],
                applicability=status, checks=checks))
    return dict(profile_digest=profile["digest"], index_digest=index["digest"], candidates=candidates,
                coverage=index["coverage"], automatic_adoption=False)


def review_adaptation(profile, index, *, decisions, reviewer, rationale):
    """Validate an explicit host/agent review, including every considered part."""
    _text(reviewer); _text(rationale)
    context = prepare_adaptation_context(profile, index)
    candidates = {(c["resource_id"], c["part_id"]): c for c in context["candidates"]}
    chosen = set()
    for row in decisions:
        if set(row) != {"resource_id", "part_id", "disposition", "reason", "reconsider_when"}:
            raise EvidenceError("review requires explicit disposition, reason and reconsider condition")
        key = _key({k: row[k] for k in ("resource_id", "part_id")})
        if key not in candidates or key in chosen:
            raise EvidenceError("unknown or duplicate review part")
        chosen.add(key)
        _text(row["reason"]); _text(row["reconsider_when"])
        if row["disposition"] not in {"reuse", "adapt", "reference", "exclude"}:
            raise EvidenceError("invalid review disposition")
        if row["disposition"] == "reuse" and candidates[key]["applicability"] != "match":
            raise EvidenceError("unknown/conflicting conditions cannot be reused unchanged")
    if chosen != set(candidates):
        raise EvidenceError("review must account for all indexed parts")
    return _seal(dict(schema=REVIEW, **context, decisions=decisions, reviewer=reviewer, rationale=rationale))


def build_task_workset(profile, index, review, *, template, mode="selected", fallback_reason=None, parent_workset_ref=None):
    """Freeze metadata; adapt/reference are advice, never enabled execution parts."""
    _check(review, REVIEW)
    if index.get("purpose") != "research":
        raise EvidenceError("research worksets cannot contain an evaluation index")
    expected = review_adaptation(profile, index, decisions=review["decisions"], reviewer=review["reviewer"], rationale=review["rationale"])
    if review != expected:
        raise EvidenceError("review is not bound to current profile/index")
    template_key = _key(template)
    if mode not in {"selected", "template"} or mode == "template" and not fallback_reason or mode == "selected" and fallback_reason is not None:
        raise EvidenceError("template fallback requires an explicit reason")
    if fallback_reason is not None:
        _text(fallback_reason)
    resources = {r["resource_id"]: r for r in index["resources"]}
    reused = {(d["resource_id"], d["part_id"]) for d in review["decisions"] if d["disposition"] == "reuse"}
    if template_key not in reused or resources[template_key[0]]["kind"] != "template":
        raise EvidenceError("task template must be compatible and explicitly selected")
    for rid, pid in reused:
        part = next(p for p in resources[rid]["parts"] if p["part_id"] == pid)
        if not {_key(dep) for dep in part["dependencies"]} <= reused:
            raise EvidenceError("selected part has an unselected dependency")
    if mode == "template":
        closure = {template_key}
        while True:
            deps = {_key(dep) for rid, pid in closure for p in resources[rid]["parts"] if p["part_id"] == pid for dep in p["dependencies"]}
            if deps <= closure:
                break
            closure |= deps
        if reused != closure:
            raise EvidenceError("template mode can enable only template and declared dependencies")
    if parent_workset_ref is not None:
        validate_ref(parent_workset_ref)
    return _seal(dict(schema=WORKSET, task_id=profile["task_id"], profile=profile, index=index, review=review,
        template=template, mode=mode, fallback_reason=fallback_reason, parent_workset_ref=parent_workset_ref,
        enabled=[dict(resource_id=r, part_id=p) for r, p in sorted(reused)],
        execution_permission="not_granted", automatic_retry=False))


def validate_workset(workset, *, workspace=None, verify=False):
    _check(workset, WORKSET)
    rebuilt = build_task_workset(workset["profile"], workset["index"], workset["review"],
        template=workset["template"], mode=workset["mode"], fallback_reason=workset["fallback_reason"],
        parent_workset_ref=workset["parent_workset_ref"])
    if rebuilt != workset:
        raise EvidenceError("workset differs from reviewed selection")
    if verify:
        # Even excluded descriptors contributed to the decision. Preserve failures
        # rather than silently converting missing sources into an empty catalog.
        _verify_resources(workset["index"]["resources"], workspace)
    return workset


def read_task_workset(workspace, checkpoint_ref, *, verify=True):
    """Resolve only checkpoint-reachable, explicitly marked activation decisions."""
    from .continuation import read_content, _content_result, _check_frozen_view
    from .evidence import query_evidence, _collect, _read_evidence
    from .evidence_refs import load_workspace
    cp = read_content(workspace, checkpoint_ref)
    # Most legacy entries have no meta artifacts. Avoid rereading their entire
    # step histories merely to establish that fact. This is discovery, not a head
    # selection: activation below still comes exclusively from the checkpoint.
    page = query_evidence(workspace, query=WORKSET, filters={"record_types": ["artifact"]}, limit=1)
    if not page["items"]:
        if page.get("warnings") or any(c.get("status") != "complete" for c in page["coverage"]):
            raise EvidenceError("workset discovery coverage unavailable")
        return {"status": "legacy_unscoped", "task_id": cp["task_id"]}
    ws = load_workspace(workspace)
    frozen = all(s["identity"]["kind"] == "snapshot_sha256" for s in ws.manifest["sources"])
    collection = _collect(ws) if frozen else None
    def record(ref):
        if collection is None:
            return _record(workspace, ref)
        result = _read_evidence(ws, ref, limit=32, collection=collection)
        if result.get("availability") != "available" or result.get("missing") or "record" not in result:
            raise EvidenceError("activation record unavailable")
        return result["record"]
    def content(ref):
        if collection is None:
            return read_content(workspace, ref)
        return _content_result(_read_evidence(ws, ref, view="artifact", limit=MAX_BYTES, collection=collection))
    activations = {}
    for step_ref in cp["step_refs"]:
        step = content(step_ref)
        if step.get("operation") != "decision":
            continue
        found = []
        for ref in step["output_refs"]:
            body = record(ref)
            if body.get("bindings", {}).get("meta_activation") == WORKSET:
                found.append(ref)
        if not found:
            continue
        if len(found) != 1 or step["task_id"] != cp["task_id"]:
            raise EvidenceError("ambiguous or cross-task workset activation")
        value = validate_workset(_material(workspace, found[0]), workspace=workspace, verify=False)
        if record(found[0]).get("bindings", {}).get("evidence_refs") != _dependencies(value):
            raise EvidenceError("workset Artifact must bind its complete evidence dependencies")
        if value["task_id"] != cp["task_id"]:
            raise EvidenceError("workset task differs from checkpoint")
        activations[digest(step_ref)] = dict(ref=found[0], activation_ref=step_ref, step=step, workset=value)
    if not activations:
        if collection is not None:
            _check_frozen_view(ws, *collection[:2])
        return {"status": "legacy_unscoped", "task_id": cp["task_id"]}
    superseded = set()
    for key, item in activations.items():
        parent = item["workset"]["parent_workset_ref"]
        if any(digest(r) not in activations for r in item["step"]["supersedes"]):
            raise EvidenceError("activation supersedes a non-predecessor or another task")
        predecessors = [activations[digest(r)] for r in item["step"]["supersedes"]]
        if parent is None:
            if predecessors:
                raise EvidenceError("root workset cannot supersede an activation")
        elif len(predecessors) != 1 or predecessors[0]["ref"] != parent:
            raise EvidenceError("activation parent missing or conflicts with workset lineage")
        for prior in predecessors:
            prior_key = digest(prior["activation_ref"])
            if prior_key == key:
                raise EvidenceError("cyclic workset activation")
            superseded.add(prior_key)
    heads = [v for k, v in activations.items() if k not in superseded]
    if len(heads) != 1:
        raise EvidenceError("conflicting workset activations require explicit resolution")
    active = heads[0]
    visited, current = set(), active
    while current is not None:
        key = digest(current["activation_ref"])
        if key in visited:
            raise EvidenceError("cyclic workset activation")
        visited.add(key)
        prior = current["step"]["supersedes"]
        current = activations[digest(prior[0])] if prior else None
    if len(visited) != len(activations):
        raise EvidenceError("disconnected workset activation history")
    validate_workset(active["workset"], workspace=workspace, verify=verify)
    if collection is not None:
        _check_frozen_view(ws, *collection[:2])
    return {"status": "active", "task_id": cp["task_id"], **{k: active[k] for k in ("ref", "activation_ref", "workset")}}


def selected_skill_members(workset, *, workspace=None):
    """Exact installed/external bundle members selected by a validated workset."""
    validate_workset(workset)
    enabled = {_key(v) for v in workset["enabled"]}
    result = []
    for resource in workset["index"]["resources"]:
        members = [p["part_id"] for p in resource["parts"] if (resource["resource_id"], p["part_id"]) in enabled]
        if resource["kind"] == "skill" and members:
            # An unselected instruction entry must never be smuggled back through
            # host discovery. The reviewer must include SKILL.md explicitly.
            if "SKILL.md" not in members:
                raise EvidenceError("selected skill export requires an explicitly selected SKILL.md")
            selected = {**resource["content_ref"], "members": sorted(members)}
            if workspace is not None:
                from .skill_sources import resolve_skill_bundle
                bundle_ref = resolve_skill_bundle(resource["content_ref"], workspace)
                if bundle_ref is not None:
                    selected["bundle_ref"] = bundle_ref
                verify_resource(resource, workspace)
            result.append(selected)
    if len({x["slug"] for x in result}) != len(result):
        raise EvidenceError("ambiguous selected skill slug")
    return result


def workset_resource_part(workset, resource_id, part_id):
    """Resolve an exact reviewed part; selection is not evidence of actual use."""
    validate_workset(workset)
    _text(resource_id); _text(part_id)
    resource = next((r for r in workset["index"]["resources"] if r["resource_id"] == resource_id), None)
    part = next((p for p in resource["parts"] if p["part_id"] == part_id), None) if resource else None
    if part is None:
        raise EvidenceError("resource part is outside the reviewed workset")
    decision = next(d for d in workset["review"]["decisions"]
                    if (d["resource_id"], d["part_id"]) == (resource_id, part_id))
    return {"resource": resource, "part": part, "decision": decision,
            "selected": {"resource_id": resource_id, "part_id": part_id} in workset["enabled"]}


def workset_summary(workspace, checkpoint_ref, *, conditions=None, definitions=None):
    result = read_task_workset(workspace, checkpoint_ref)
    if result["status"] != "active":
        return result
    value = result["workset"]
    changed = [key for key, val in (conditions or {}).items() if value["profile"]["conditions"].get(key) != val]
    meanings = compare_definitions(definitions or {}, value["profile"].get("definitions", {}))
    changed_definitions = sorted(meanings["missing"] + meanings["mismatched"])
    incompatible = bool(changed or changed_definitions)
    extra = {} if definitions is None and "definitions" not in value["profile"] else dict(
        definitions=value["profile"].get("definitions", {}), changed_definitions=changed_definitions)
    return dict(status="review_required" if incompatible else "active", changed_conditions=changed,
        task_id=value["task_id"], ref=result["ref"], activation_ref=result["activation_ref"],
        digest=value["digest"], profile_digest=value["profile"]["digest"], mode=value["mode"],
        fallback_reason=value["fallback_reason"], enabled=[] if incompatible else value["enabled"],
        enabled_versions=[] if incompatible else [{**part, "resource_digest": next(
            r["digest"] for r in value["index"]["resources"] if r["resource_id"] == part["resource_id"])}
            for part in value["enabled"]],
        selected_skill_sources=[] if incompatible else selected_skill_members(value, workspace=workspace),
        inactive=[{k: d[k] for k in ("resource_id", "part_id", "disposition")} for d in value["review"]["decisions"] if d["disposition"] != "reuse"],
        details="read ref artifact for exact conditions, reasons, evidence and versions", **extra)


def note_workset_summary(entry, *, task_id, conditions=None, definitions=None):
    """Resolve an optional note pointer to the existing published activation.

    This does not activate an inline workset or create a note-specific ledger.
    """
    binding = entry.get("task_workset")
    if binding is None:
        return None
    if not isinstance(binding, dict) or set(binding) != {"workspace", "checkpoint_ref"}:
        raise EvidenceError("note task_workset needs a published workspace and checkpoint_ref")
    validate_ref(binding["checkpoint_ref"])
    result = workset_summary(binding["workspace"], binding["checkpoint_ref"],
                             conditions=conditions, definitions=definitions)
    if result.get("task_id") != task_id:
        raise EvidenceError("note task_workset belongs to another task")
    return result


def publish_task_workset(entry, workset, *, operation_id, directory, parent_entry=None, expected_entry_identity=None):
    """Use existing recoverable publication; no mutation of a source journal."""
    from .entry import resolve_entry
    from .continuation import read_content, STEP
    from .publication import prepare_publication, _save
    validate_workset(workset)
    root = Path(directory).absolute()
    root.mkdir(parents=True, exist_ok=True)
    # Repeated operation preparation must reuse its original intent after entry
    # advancement; reconstructing it from today's pointer would change parents.
    marker = root / ("workset-operation-" + digest({"entry": str(Path(entry).absolute()), "operation_id": operation_id}) + ".json")
    if marker.exists():
        request = json.loads(marker.read_text(encoding="utf-8"))
        if request["workset_digest"] != workset["digest"]:
            raise EvidenceError("operation id reused for a different workset")
        return prepare_publication(**request["request"])
    ws, cp_ref, _, _ = resolve_entry(parent_entry or entry)
    cp = read_content(ws, cp_ref)
    validate_workset(workset, workspace=ws, verify=True)
    if workset["task_id"] != cp["task_id"]:
        raise EvidenceError("new task requires its own initial checkpoint")
    previous = read_task_workset(ws, cp_ref)
    if workset["parent_workset_ref"] != previous.get("ref"):
        raise EvidenceError("workset parent must be the current active workset")
    path = root / (workset["digest"] + ".json")
    _save(path, workset)
    basis = [previous["activation_ref"]] if previous["status"] == "active" else [cp_ref]
    step = dict(schema=STEP, task_id=cp["task_id"], question=cp["question"], branch_id=cp["branches"][0]["branch_id"],
        operation="decision", reason=workset["review"]["rationale"], input_refs=basis,
        output_refs=[{"publication_ref": "task_workset"}], method_refs=[], accounting_refs=[], source_watermarks=[],
        supersedes=[previous["activation_ref"]] if previous["status"] == "active" else [],
        detail=dict(choice="reuse", alternatives=[], basis_refs=basis, reconsider_when="Task conditions, resource versions or counterevidence change"))
    request = dict(entry=str(Path(entry).absolute()), operation_id=operation_id, directory=str(root / "publications"),
        parent_entry=str(Path(parent_entry).absolute()) if parent_entry else None, expected_entry_identity=expected_entry_identity,
        results=[dict(name="task_workset", path=str(path), identity=file_identity(path), source_kind="existing_real_artifact",
            role="diagnostic", bindings={"schema": WORKSET, "meta_activation": WORKSET, "task_id": cp["task_id"],
                "evidence_refs": _dependencies(workset)})],
        steps=[dict(name="workset_activation", content=step)])
    _save(marker, {"workset_digest": workset["digest"], "request": request})
    return prepare_publication(**request)
