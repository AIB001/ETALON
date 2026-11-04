"""Callable methods are ordinary note resources over the existing method.run ABI."""
from __future__ import annotations

from copy import deepcopy

from etalon.runtime.operations import validate_arguments
from .notes import _original_selection, original, patch_progress, resolve_method


def publish_method(entry, resource_id, arguments, *, summary, conditions,
                   branch_id=None, publication_id=None, evidence=(), counterevidence=(),
                   parts=None, family=None, definitions=None):
    """Pin executable files and merge one method; no adoption or execution implied.

    arguments uses method.run unchanged: argv, declared source/model files,
    default inputs and optional output paths. Programs consume --request/--result.
    Evidence may describe a local improvement or a limitation; neither is invented.
    """
    if not isinstance(resource_id, str) or not resource_id or len(resource_id) > 160:
        raise ValueError("resource_id must be a nonempty bounded name")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(conditions, dict):
        raise ValueError("declare the useful behavior and its conditions")
    arguments = deepcopy(arguments)
    validate_arguments("method.run", arguments)
    pins = [original(path) for path in arguments["files"]]
    descriptor = {"kind": "method", "form": "executable", "summary": summary,
        "conditions": conditions, "execution": {"operation": "method.run", "arguments": arguments},
        "sources": pins, "source": pins[0], "scientific_admission": "not_inferred"}
    if parts is not None:
        from .meta_context import validate_resource_parts
        descriptor["parts"] = deepcopy(validate_resource_parts(parts))
    if family is not None:
        if not isinstance(family, str) or not family.strip() or len(family) > 160:
            raise ValueError("family must be a nonempty bounded name")
        descriptor["family"] = family
    if definitions is not None:
        from .meta_context import _definition_map
        descriptor["definitions"] = _definition_map(definitions, unknown=True)
    result = patch_progress(entry, {}, branch_id=branch_id, publication_id=publication_id,
        evidence=evidence, counterevidence=counterevidence, resources={resource_id: descriptor})
    from etalon.active.schema import digest
    return {**result, "resource_id": resource_id, "resource_version": digest(descriptor),
            "pointer": "/resources/" + resource_id.replace("~", "~0").replace("/", "~1")}


def resolve_saved_method(entry, pointer, *, branch_id=None, invocation=None, max_bytes=16384, query=None):
    """Resolve one selected method and optionally construct an existing action request.

    invocation supplies request_id, limits and optionally inputs / executable /
    input_files / max_seconds. input_files declares file dependencies by logical
    name without changing the program's free-form inputs.
    Inputs override defaults by key. A host may select a local interpreter after
    relocation; source argv and files are resolved only through pinned originals.
    This prepares no new process and does not infer applicability from retrieval.
    """
    from .evidence_refs import resolve_artifact
    from etalon.runtime.api import prepare_action
    from .notes import byte_size

    result = resolve_method(entry, pointer, branch_id=branch_id, max_bytes=max_bytes)
    from etalon.active.schema import digest
    result["resource_version"] = digest(result["description"])
    if query is not None:
        from .resource_selection import resource_applicability
        result["selection"] = resource_applicability(result["description"], query)
    if invocation is None:
        if byte_size(result) > max_bytes:
            raise ValueError("method applicability exceeds response bound; keep the query compact")
        return _with_related_feedback(result, query, max_bytes)
    if query is not None and result["selection"]["disposition"] != "direct-use":
        raise ValueError("the query does not support unchanged whole-method invocation; select or adapt the applicable parts first")
    if (not isinstance(invocation, dict) or not {"request_id", "limits"} <= invocation.keys()
            or set(invocation) - {"request_id", "limits", "inputs", "executable", "input_files", "max_seconds"}):
        raise ValueError("invocation needs request_id, limits and optional inputs/executable/input_files/max_seconds")
    description = result["description"]
    execution = description.get("execution", {})
    if execution.get("operation") != "method.run":
        raise ValueError("selected description has no callable method.run contract")
    arguments = deepcopy(execution["arguments"])
    _, _, resolver = _original_selection(result["details"], pointer, None)
    sources = description.get("sources", [])
    pins = {ref["path"]: ref for ref in sources}
    if not set(arguments["files"]) <= pins.keys():
        raise ValueError("method source files must have original pins")
    relocated = {}
    for path in arguments["files"]:
        pin = pins[path]
        relocated[path] = str(resolve_artifact({"path": path, **pin["identity"]}, resolver=resolver))
    arguments["files"] = [relocated[p] for p in arguments["files"]]
    arguments["command"] = [relocated.get(p, p) for p in arguments["command"]]
    def resolve_source_paths(value):
        if isinstance(value, dict):
            return {k: resolve_source_paths(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve_source_paths(v) for v in value]
        return relocated.get(value, value) if isinstance(value, str) else value
    # Programs may receive a declared helper/config/model file through inputs.
    # Relocate only verified declared sources, not arbitrary task data paths.
    if "executable" in invocation:
        arguments["command"][0] = invocation["executable"]
    if not isinstance(invocation.get("inputs", {}), dict):
        raise ValueError("invocation inputs must be an object")
    arguments["inputs"] = resolve_source_paths({**arguments["inputs"], **invocation.get("inputs", {})})
    if "input_files" in invocation:
        if not isinstance(invocation["input_files"], dict):
            raise ValueError("invocation input_files must name file dependencies")
        arguments["input_files"] = {**arguments.get("input_files", {}), **invocation["input_files"]}
    if "input_files" in arguments:
        arguments["input_files"] = resolve_source_paths(arguments["input_files"])
    request = {"schema": "etalon-action/1", "request_id": invocation["request_id"],
        "operation": "method.run", "arguments": arguments, "limits": invocation["limits"],
        "evidence_refs": [result["record_id"]]}
    if "max_seconds" in invocation:
        request["max_seconds"] = invocation["max_seconds"]
    # The ordinary action preflight pins actual inputs and preserves idempotency.
    prepared = prepare_action(request)
    result.update(request=prepared["request"], plan_id=prepared["plan_id"], next_tool="etalon_action_submit",
                  record_handle={"tool": "etalon_research_progress", "field": "pending_handles",
                      "when": "after submit, before waiting; save the returned handle in the current task entry"},
                  applicability="declared conditions only; no scientific suitability inferred")
    if byte_size(result) > max_bytes:
        raise ValueError("resolved invocation exceeds response bound; keep descriptors compact")
    return _with_related_feedback(result, query, max_bytes)


def _with_related_feedback(result, query, max_bytes):
    """Optional experience delivery cannot consume the method/request budget.

    The resolver's details already pin the chosen note and descriptor. Never
    chase today's head to add observations about a different method version.
    """
    from .notes import byte_size
    from .resource_selection import feedback_read_link, read_resource_feedback

    parts = result["pointer"].split("/")
    if len(parts) != 3 or parts[:2] != ["", "resources"]:
        return result
    name = parts[2].replace("~1", "/").replace("~0", "~")
    spare = max_bytes - byte_size(result) - 32
    link = feedback_read_link(result["details"], name, query=query,
                              resource_version=result["resource_version"], limit=5)
    feedback = {"status": "not_loaded", "reason": "response_budget", "next_read": link}
    if spare >= 2048:
        try:
            feedback = read_resource_feedback(result["details"], name, query=query,
                resource_version=result["resource_version"], limit=5, max_bytes=min(8192, spare))
        except (ValueError, OSError, RuntimeError) as exc:
            # Exact explicit feedback reads still report verification failures.
            # An unrelated optional receipt never invalidates verified source.
            feedback.update(reason="feedback_read_unavailable", detail=str(exc)[:240])
    enriched = {**result, "related_feedback": feedback}
    if byte_size(enriched) <= max_bytes:
        return enriched
    minimal = {**result, "related_feedback": {"status": "not_loaded", "next_read": link}}
    if byte_size(minimal) <= max_bytes:
        return minimal
    # Preserve the established exact bound, even for a nearly-full invocation.
    minimal = {**result, "related_feedback_omitted": True}
    return minimal if byte_size(minimal) <= max_bytes else result


def call_saved_method(entry, pointer, *, invocation, workspace, branch_id=None, query=None):
    """Optionally submit a saved callable and remember its handle in this note.

    Uses the same runtime as prepare/submit. No graph, evaluation or extra model
    call is required. Repeat the same request to recover its original execution.
    """
    from .method_invocations import call
    return call(entry, pointer, invocation=invocation, workspace=workspace,
                branch_id=branch_id, query=query)


def collect_saved_method(invocation_path):
    """Read an existing invocation and publish its facts; never submit or retry."""
    from .method_invocations import collect
    return collect(invocation_path)
