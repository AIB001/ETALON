"""Optional append-only dispositions and reviewed reopening of exact opportunities.

These are ordinary Artifacts. They never evaluate, execute, select a method or
change a default. Original-byte novelty is mechanical; its scientific relevance
and the truth of a reconsideration condition remain explicit host judgments.
"""
from __future__ import annotations

import base64
import hashlib
import json

from etalon.active.schema import canonical, digest
from .evidence_refs import EvidenceError, validate_ref
from .read_scope import bounded_read
from .schema import Artifact

DISPOSITION = "etalon-opportunity-disposition/1"
REOPEN = "etalon-opportunity-reopen/1"
SCHEMAS = {DISPOSITION, REOPEN}
TERMINAL_OUTCOMES = {"no_change", "retain", "adopt", "rollback"}
MAX_HISTORY = 256
MAX_ORIGINALS = 256
ORIGINAL_PAGE_BYTES = 32768
MAX_ORIGINAL_BYTES = 1024 * 1024
MAX_ORIGINAL_CLOSURE_BYTES = 8 * 1024 * 1024


def _refs(values):
    from .continuation import _refs as check
    values = list(values)
    check(values)
    return [v for _, v in sorted({canonical(validate_ref(r)): validate_ref(r) for r in values}.items())]


def _contains(values, required):
    return {canonical(r) for r in required} <= {canonical(r) for r in values}


def _condition(value):
    from .continuation import _fields, _text
    _fields(value, ("condition_id", "condition"))
    for item in value.values():
        _text(item)


def validate_content(payload):
    """Validate the immutable JSON shape, not the scientific judgment."""
    from .continuation import _fields, _list, _text, MAX_CONTENT_BYTES
    from .improvement import _conditions
    if not isinstance(payload, dict) or payload.get("schema") not in SCHEMAS:
        raise EvidenceError("unknown opportunity state content")
    common = ("schema", "task_id", "scope", "conditions", "method_refs", "opportunity_ref",
              "support_refs", "counter_refs", "original_evidence_refs", "reviewer", "reason", "supersedes")
    disposition = ("previous_state_ref", "outcome", "reconsider_when", "candidate_ref",
                   "evaluation_ref", "resolution_ref", "judgment")
    reopen = ("previous_terminal_ref", "new_evidence_refs", "matched_condition", "new_original_identities")
    _fields(payload, common + (disposition if payload["schema"] == DISPOSITION else reopen))
    if len(canonical(payload).encode("utf-8")) > MAX_CONTENT_BYTES:
        raise EvidenceError("opportunity state exceeds 16 KiB; narrow the evidence")
    for key in ("task_id", "scope", "reviewer", "reason"):
        _text(payload[key])
    _conditions(payload["conditions"])
    validate_ref(payload["opportunity_ref"])
    for key in ("method_refs", "support_refs", "counter_refs", "original_evidence_refs", "supersedes"):
        if _refs(payload[key]) != payload[key]:
            raise EvidenceError("state refs must be canonical, unique and sorted")
    if not payload["method_refs"] or not payload["original_evidence_refs"]:
        raise EvidenceError("opportunity state requires methods and original evidence")
    if payload["supersedes"]:
        raise EvidenceError("state history uses exact predecessors; supersedes must remain empty")
    if payload["schema"] == DISPOSITION:
        if payload["outcome"] not in TERMINAL_OUTCOMES | {"waiting", "awaiting_evidence"}:
            raise EvidenceError("unknown opportunity disposition")
        for key in ("previous_state_ref", "candidate_ref", "evaluation_ref", "resolution_ref"):
            if payload[key] is not None:
                validate_ref(payload[key])
        ids = set()
        for item in _list(payload["reconsider_when"], nonempty=True):
            _condition(item)
            if item["condition_id"] in ids:
                raise EvidenceError("duplicate reconsideration condition")
            ids.add(item["condition_id"])
        measured = payload["evaluation_ref"] is not None or payload["resolution_ref"] is not None
        if measured and (payload["evaluation_ref"] is None or payload["resolution_ref"] is None):
            raise EvidenceError("evaluation and existing resolution must be paired")
        if payload["outcome"] in {"adopt", "rollback"} and not measured:
            raise EvidenceError("adopt/rollback require existing evaluation and resolution authority")
        if payload["outcome"] in {"waiting", "awaiting_evidence"} and measured:
            raise EvidenceError("waiting is not an evaluated terminal outcome")
        if payload["judgment"] != ("existing_resolution" if measured else "bounded_judgment"):
            raise EvidenceError("disposition judgment conflicts with its authority")
        if not payload["support_refs"] and not payload["counter_refs"]:
            raise EvidenceError("disposition requires supporting or negative evidence")
    else:
        validate_ref(payload["previous_terminal_ref"])
        _condition(payload["matched_condition"])
        if not payload["new_evidence_refs"] or _refs(payload["new_evidence_refs"]) != payload["new_evidence_refs"]:
            raise EvidenceError("reopen requires exact new evidence refs")
        for identity in _list(payload["new_original_identities"], nonempty=True):
            from .evidence_refs import _identity
            _fields(identity, ("sha256", "bytes"))
            _identity(identity)
        identities = payload["new_original_identities"]
        if identities != [v for _, v in sorted({canonical(i): i for i in identities}.items())]:
            raise EvidenceError("original identities must be unique and sorted")
    return json.loads(canonical(payload))


def _original_size(identity, budget):
    from .evidence_refs import _identity
    _identity(identity)
    if identity["bytes"] > MAX_ORIGINAL_BYTES:
        raise EvidenceError("original exceeds bounded 1 MiB inspection; publish narrower originals")
    if budget is not None:
        if budget["bytes"] + identity["bytes"] > MAX_ORIGINAL_CLOSURE_BYTES:
            raise EvidenceError("original evidence closure exceeds bounded 8 MiB inspection")
        budget["bytes"] += identity["bytes"]


def _original_json(raw):
    """A malformed JSON envelope cannot fall back to a supposedly novel leaf."""
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        if raw.lstrip(b"\xef\xbb\xbf \t\r\n").startswith((b"{", b"[")):
            raise EvidenceError("original JSON is malformed or truncated") from exc
        return None


def _paged_original(workspace, ref, identity, initial_fragment=None):
    """Read one original through existing small pages, pinning identity throughout."""
    from .evidence import read_evidence
    raw, offset = bytearray(), 0
    while True:
        if offset == 0 and initial_fragment is not None:
            fragment = initial_fragment
        else:
            page = read_evidence(workspace, ref, view="artifact", offset=offset, limit=ORIGINAL_PAGE_BYTES)
            if (page.get("availability") != "available" or page.get("missing") or page.get("truncated")
                    or page.get("verification", {}).get("record") != "verified"
                    or page.get("verification", {}).get("artifact") != "verified"
                    or page.get("ref") != ref):
                raise EvidenceError("original page is unavailable or changed identity")
            fragment = page.get("fragment", {})
        if (fragment.get("encoding") != "base64" or fragment.get("offset") != offset
                or fragment.get("total_bytes") != identity["bytes"]):
            raise EvidenceError("original page offset or total byte count changed")
        try:
            chunk = base64.b64decode(fragment["content"], validate=True)
        except (ValueError, TypeError, KeyError) as exc:
            raise EvidenceError("original page has invalid byte content") from exc
        expected = min(ORIGINAL_PAGE_BYTES, identity["bytes"] - offset)
        end = offset + len(chunk)
        next_offset = end if end < identity["bytes"] else None
        if len(chunk) != expected or fragment.get("next_offset") != next_offset:
            raise EvidenceError("original page is truncated or has inconsistent continuation")
        raw.extend(chunk)
        if next_offset is None:
            break
        offset = next_offset
    if len(raw) != identity["bytes"] or hashlib.sha256(raw).hexdigest() != identity["sha256"]:
        raise EvidenceError("assembled original byte identity mismatch")
    return bytes(raw)


def _material(journal, ref, source_id, external_workspace=None, *, _budget=None):
    """Verify original bytes and fully inspect bounded JSON, including members."""
    ref = validate_ref(ref)
    if ref["source_id"] != source_id or "member" in ref:
        if external_workspace is None:
            raise EvidenceError("external references require an explicit external_workspace")
        from .improvement import read_material
        value = read_material(external_workspace, ref)
        is_artifact = {"path", "sha256", "bytes", "source_kind", "role"} <= set(value["record"])
        if not is_artifact and "member" not in value["ref"] and "sha256" in value["ref"]:
            raise EvidenceError("byte identity requires an Artifact or released member")
        # read_material returns the strengthened byte identity in its ref.
        identity = ({k: value["ref"][k] for k in ("sha256", "bytes")}
                    if "sha256" in value["ref"] else None)
        if identity is None:
            return value["content"], value["record"], None
        _original_size(identity, _budget)
        if value["complete"] and value["content"] is not None:
            return value["content"], value["record"], identity
        raw = _paged_original(external_workspace, value["ref"], identity, value.get("fragment"))
        return _original_json(raw), value["record"], identity
    body = journal.get(ref["record_id"])
    if digest(body) != ref["record_digest"]:
        raise EvidenceError("record digest mismatch")
    if ref["record_id"].startswith("artifact-"):
        if "sha256" in ref and (ref["sha256"], ref["bytes"]) != (body["sha256"], body["bytes"]):
            raise EvidenceError("artifact identity mismatch")
        identity = {k: body[k] for k in ("sha256", "bytes")}
        _original_size(identity, _budget)
        artifact = Artifact(**body)
        path = journal.artifact_path(artifact)
        with path.open("rb") as handle:
            raw = handle.read(MAX_ORIGINAL_BYTES + 1)
        journal.artifact_path(artifact)  # Recheck after reading, including relocated originals.
        if len(raw) != identity["bytes"] or hashlib.sha256(raw).hexdigest() != identity["sha256"]:
            raise EvidenceError("original changed during bounded read")
        return _original_json(raw), body, identity
    if "sha256" in ref:
        raise EvidenceError("byte identity requires an Artifact or released member")
    return body, body, None


def _read(journal, ref, source_id, external_workspace=None, schema=None):
    content, _, _ = _material(journal, ref, source_id, external_workspace)
    if not isinstance(content, dict) or (schema and content.get("schema") != schema):
        raise EvidenceError("wrong or incomplete binding schema: " + str(schema))
    return content


@bounded_read
def read_opportunity_state(journal, ref, *, source_id, external_workspace=None):
    """Read and shape-check one exact state Artifact, without inferring its head."""
    return validate_content(_read(journal, ref, source_id, external_workspace))


def _origins(journal, refs, source_id, external_workspace=None):
    """Ignore wrapper bytes when JSON explicitly links original evidence.

    A new Artifact ID/path/binding around the same bytes is never novel. A JSON
    provenance envelope with exact refs inherits its leaves' identities, so a
    changed title around old originals is not novel either. This does not infer
    scientific semantics from arbitrary changed bytes.
    """
    from .continuation import _exact_refs
    visited, active, identities = set(), set(), {}
    budget = {"bytes": 0}

    def check_envelope(value):
        if isinstance(value, dict):
            if value.get("schema") == "etalon-evidence-ref/1":
                validate_ref(value)
                return
            for key, item in value.items():
                if key.endswith("_ref") and item is not None:
                    validate_ref(item)
                elif key.endswith("_refs"):
                    if not isinstance(item, list):
                        raise EvidenceError("provenance envelope requires exact original refs")
                    for ref in item:
                        validate_ref(ref)
                else:
                    check_envelope(item)
        elif isinstance(value, list):
            for item in value:
                check_envelope(item)

    def visit(ref):
        key = canonical(ref)
        if key in active:
            raise EvidenceError("cyclic original evidence links")
        if key in visited:
            return
        if len(visited) >= MAX_ORIGINALS:
            raise EvidenceError("original evidence closure exceeds bounded history")
        visited.add(key)
        active.add(key)
        content, _, identity = _material(journal, ref, source_id, external_workspace, _budget=budget)
        if identity is not None and content is not None:
            check_envelope(content)
        linked = list(_exact_refs(content)) if content is not None else []
        if linked:
            for child in linked:
                visit(child)
        elif identity is not None:
            identities[canonical(identity)] = identity
        # Method/other metadata records are verified but are not new raw evidence.
        active.remove(key)

    for ref in refs:
        visit(ref)
    return [v for _, v in sorted(identities.items())]


def _context(journal, opportunity_ref, source_id, external_workspace):
    from .improvement import OPPORTUNITY, CONTEXT, validate_content as validate_improvement
    opportunity = _read(journal, opportunity_ref, source_id, external_workspace, OPPORTUNITY)
    context = _read(journal, opportunity["context_ref"], source_id, external_workspace, CONTEXT)
    validate_improvement(opportunity)
    validate_improvement(context)
    if context["status"] != "ready_for_revision" or opportunity["opportunity_key"] != context["opportunity_key"]:
        raise EvidenceError("opportunity context is incomplete or differs")
    for key in ("task_id", "scope", "conditions", "method_refs"):
        if opportunity[key] != context[key]:
            raise EvidenceError("opportunity changes its exact context")
    return opportunity, context


def _history(journal, opportunity_ref, source_id, external_workspace, max_history):
    if type(max_history) is not int or not 1 <= max_history <= 1024:
        raise EvidenceError("max_history must be between 1 and 1024")
    from .method_revision import journal_ref
    # No new table/index. The bound covers all state Artifacts in this journal,
    # because original content is authoritative about opportunity membership.
    with journal.store.connection() as db:
        rows = db.execute("SELECT key FROM metadata WHERE key LIKE 'research:artifact-%' "
                          "AND json_extract(body, '$.bindings.schema') IN (?, ?) "
                          "ORDER BY key LIMIT ?", (DISPOSITION, REOPEN, max_history + 1)).fetchall()
    if len(rows) > max_history:
        raise EvidenceError("opportunity state history exceeds bounded coverage; cannot infer open")
    entries = []
    for row in rows:
        ref = journal_ref(journal, row[0][len("research:"):], source_id=source_id)
        content = read_opportunity_state(journal, ref, source_id=source_id, external_workspace=external_workspace)
        if content["opportunity_ref"] == opportunity_ref:
            entries.append((ref, content))
    children = {}
    for ref, content in entries:
        parent = content.get("previous_state_ref") if content["schema"] == DISPOSITION else content["previous_terminal_ref"]
        key = canonical(parent)
        if key in children:
            raise EvidenceError("ambiguous concurrent opportunity heads require review")
        children[key] = (ref, content)
    ordered, key = [], canonical(None)
    while key in children:
        item = children.pop(key)
        ordered.append(item)
        key = canonical(item[0])
    if children:
        raise EvidenceError("disconnected, cyclic or forged opportunity history")
    return ordered


def _authority(journal, content, context, source_id, workspace):
    """Require already-published method evaluation and resolution selection."""
    candidate = content["candidate_ref"]
    if candidate is not None:
        version = _read(journal, candidate, source_id, workspace, "etalon-method-version/1")
        if context["target_ref"]["record_id"] not in version["parents"]:
            raise EvidenceError("candidate must descend from the exact opportunity base")
    if content["evaluation_ref"] is None:
        return
    evaluation = _read(journal, content["evaluation_ref"], source_id, workspace, "etalon-method-evaluation/1")
    resolution = _read(journal, content["resolution_ref"], source_id, workspace, "etalon-research-step/1")
    outcome, base = content["outcome"], context["target_ref"]
    if resolution['task_id'] != content['task_id']:
        raise EvidenceError('evaluated disposition must preserve its exact task')
    if candidate is None or evaluation["subjects"] != [candidate["record_id"]] or evaluation["comparators"] != [base["record_id"]]:
        raise EvidenceError("evaluation must compare the exact candidate and opportunity base")
    if content["evaluation_ref"]["source_id"] != candidate["source_id"] or candidate["source_id"] != base["source_id"]:
        raise EvidenceError("evaluation method IDs require the same explicit source")
    selected = candidate if outcome == "adopt" else base
    if (resolution.get("operation") != "decision" or content["evaluation_ref"] not in resolution["input_refs"]
            or resolution["method_refs"] != [selected]):
        raise EvidenceError("existing resolution does not authorize this evaluated selection")
    if not any(a.get("disposition") == outcome for a in resolution["detail"]["alternatives"]):
        raise EvidenceError("disposition differs from existing resolution outcome")
    if outcome == "adopt" and (not evaluation["result_refs"] or not evaluation["accounting_refs"]):
        raise EvidenceError("adoption requires measured results and accounting")
    uses = [_read(journal, r, source_id, workspace, "etalon-method-use/1") for r in resolution["output_refs"]]
    if outcome == 'retain' and not uses:
        # Historical retain decisions may deliberately select no new use. The
        # checked evaluation/resolution remain authority; do not invent one.
        if evaluation['scope'] != content['scope']:
            raise EvidenceError('retain without a MethodUse must preserve the exact evaluation scope')
        return
    if not any(any(m["method_version_ref"] == selected["record_id"] and m["scope"] == content["scope"]
                   for m in u["selected_methods"]) for u in uses):
        raise EvidenceError("resolution requires existing MethodUse in this exact scope")
    if outcome == "rollback" and (not content["counter_refs"] or not any(u["parents"] for u in uses)):
        raise EvidenceError("rollback requires prior method use and retained counterevidence")


def _apply(journal, content, state, context, opportunity, source_id, workspace):
    validate_content(content)
    for key in ("task_id", "scope", "conditions"):
        if content[key] != opportunity[key]:
            raise EvidenceError("opportunity state changes its exact context")
    if content["method_refs"] != _refs(opportunity["method_refs"]):
        raise EvidenceError("opportunity state changes its bound methods")
    if content["schema"] == DISPOSITION:
        if content["previous_state_ref"] != state["head_ref"] or state["state"] == "terminal":
            raise EvidenceError("disposition needs the current open episode; terminal requires reviewed reopen")
    elif state["state"] != "terminal" or content["previous_terminal_ref"] != state["head_ref"]:
        raise EvidenceError("reopen requires the exact current terminal")
    for name, old in (("support_refs", state["retained_support_refs"]), ("counter_refs", state["retained_counter_refs"]),
                      ("original_evidence_refs", state["seen_evidence_refs"])):
        if not _contains(content[name], old):
            raise EvidenceError("opportunity state must preserve prior " + name)
    for ref in _refs(content["support_refs"] + content["counter_refs"] + content["original_evidence_refs"]):
        _material(journal, ref, source_id, workspace)
    if content["schema"] == DISPOSITION:
        _authority(journal, content, context, source_id, workspace)
        state["outcome"] = content["outcome"]
        state["state"] = "terminal" if content["outcome"] in TERMINAL_OUTCOMES else content["outcome"]
        state["reconsider_when"] = content["reconsider_when"]
    else:
        if content["matched_condition"] not in state["reconsider_when"]:
            raise EvidenceError("reopen must match a specific prior reconsideration condition")
        if content["original_evidence_refs"] != state["seen_evidence_refs"]:
            raise EvidenceError("reopen original evidence must exactly preserve the prior evidence set")
        old = {canonical(i) for i in _origins(journal, state["seen_evidence_refs"], source_id, workspace)}
        novel = _origins(journal, content["new_evidence_refs"], source_id, workspace)
        if not novel or any(canonical(i) in old for i in novel):
            raise EvidenceError("reopen requires unseen original evidence; IDs, wrappers and metadata are insufficient")
        if novel != content["new_original_identities"]:
            raise EvidenceError("reopen original identity claims differ from verified bytes")
        state["state"], state["outcome"] = "reopened", None
    state["retained_support_refs"] = _refs(state["retained_support_refs"] + content["support_refs"])
    state["retained_counter_refs"] = _refs(state["retained_counter_refs"] + content["counter_refs"])
    state["seen_evidence_refs"] = _refs(state["seen_evidence_refs"] + content["support_refs"] + content["counter_refs"]
                                        + content["original_evidence_refs"] + content.get("new_evidence_refs", []))


def _state_from_entries(journal, opportunity_ref, source_id, workspace, opportunity, context, entries):
    state = dict(state="open", outcome=None, opportunity_ref=opportunity_ref, head_ref=None, terminal_ref=None,
                 episode_ref=None, request_id="revision-" + opportunity["opportunity_key"], history_refs=[],
                 terminal_refs=[], retained_support_refs=[], retained_counter_refs=[], reconsider_when=[],
                 seen_evidence_refs=_refs(context["problem_refs"] + context["evidence_refs"]))
    for ref, content in entries:
        _apply(journal, content, state, context, opportunity, source_id, workspace)
        state["head_ref"] = ref
        state["history_refs"].append(ref)
        if state["state"] == "terminal":
            state["terminal_ref"] = ref
            state["terminal_refs"].append(ref)
        elif content["schema"] == REOPEN:
            state["episode_ref"] = ref
            state["request_id"] = "revision-" + opportunity["opportunity_key"] + "-reopen-" + digest(content)
    state["terminal"] = state["state"] == "terminal"
    state["execution"] = "not_performed"
    state["coverage"] = "complete_within_explicit_journal"
    return state, opportunity, context, entries


def _resolve(journal, opportunity_ref, source_id, workspace, max_history):
    opportunity, context = _context(journal, opportunity_ref, source_id, workspace)
    entries = _history(journal, opportunity_ref, source_id, workspace, max_history)
    return _state_from_entries(journal, opportunity_ref, source_id, workspace, opportunity, context, entries)


def _predecessors(journal, content, source_id, workspace, max_history):
    """Validate a published chain without assuming the preflight reader is a DB.

    This checks only the explicitly cited chain. Detecting concurrent sibling
    heads still requires resolve_opportunity_state against the local journal.
    """
    entries, seen = [], set()
    ref = content.get("previous_state_ref") if content["schema"] == DISPOSITION else content["previous_terminal_ref"]
    while ref is not None:
        if len(entries) >= max_history or canonical(ref) in seen:
            raise EvidenceError("predecessor history is cyclic or exceeds bounded coverage")
        seen.add(canonical(ref))
        item = read_opportunity_state(journal, ref, source_id=source_id, external_workspace=workspace)
        if item["opportunity_ref"] != content["opportunity_ref"]:
            raise EvidenceError("predecessor belongs to a different opportunity")
        entries.append((ref, item))
        ref = item.get("previous_state_ref") if item["schema"] == DISPOSITION else item["previous_terminal_ref"]
    entries.reverse()
    opportunity, context = _context(journal, content["opportunity_ref"], source_id, workspace)
    return _state_from_entries(journal, content["opportunity_ref"], source_id, workspace, opportunity, context, entries)


@bounded_read
def resolve_opportunity_state(journal, opportunity_ref, *, source_id, external_workspace=None, max_history=MAX_HISTORY):
    """Read the conservative unique head; missing/corrupt/overflow/conflict raises.

    With no optional state records the original request ID is returned unchanged.
    No execution/proposal is requested. A reopened episode gets a derived ID.
    """
    return _resolve(journal, opportunity_ref, source_id, external_workspace, max_history)[0]


@bounded_read
def validate_local_links(journal, content, source_id, *, external_workspace=None, max_history=MAX_HISTORY):
    """Validate direct publications too; never let a hand-built payload bypass checks."""
    content = validate_content(content)
    if hasattr(journal, "store"):
        state, opportunity, context, entries = _resolve(journal, content["opportunity_ref"], source_id, external_workspace, max_history)
    else:
        state, opportunity, context, entries = _predecessors(journal, content, source_id, external_workspace, max_history)
    if any(saved == content for _, saved in entries):
        return  # Exact replay; all prior states and original bytes were rechecked.
    _apply(journal, content, state, context, opportunity, source_id, external_workspace)


def _builder_state(journal, opportunity_ref, predecessor, source_id, workspace, max_history):
    _, opportunity, context, entries = _resolve(journal, opportunity_ref, source_id, workspace, max_history)
    if predecessor is None:
        entries = []
    else:
        positions = [i for i, (ref, _) in enumerate(entries) if ref == predecessor]
        if len(positions) != 1:
            raise EvidenceError("predecessor is not an exact state in this opportunity history")
        entries = entries[:positions[0] + 1]
    return _state_from_entries(journal, opportunity_ref, source_id, workspace, opportunity, context, entries)


@bounded_read
def build_opportunity_disposition(journal, *, opportunity_ref, outcome, reason, reviewer,
                                  support_refs, counter_refs=(), reconsider_when, source_id,
                                  previous_state_ref=None, candidate_ref=None, evaluation_ref=None,
                                  resolution_ref=None, external_workspace=None, max_history=MAX_HISTORY):
    """Build a terminal or waiting judgment; no candidate/evaluation is fabricated.

    Supply previous_state_ref when closing a reopened or waiting episode. Exact
    retries should republish the previously built payload through record_content.
    """
    state, opportunity, _, _ = _builder_state(journal, opportunity_ref, previous_state_ref, source_id, external_workspace, max_history)
    content = {k: opportunity[k] for k in ("task_id", "scope", "conditions")}
    content.update(schema=DISPOSITION, opportunity_ref=opportunity_ref, method_refs=_refs(opportunity["method_refs"]),
                   previous_state_ref=previous_state_ref, outcome=outcome, reason=reason, reviewer=reviewer,
                   support_refs=_refs(state["retained_support_refs"] + list(support_refs)),
                   counter_refs=_refs(state["retained_counter_refs"] + list(counter_refs)),
                   original_evidence_refs=state["seen_evidence_refs"], reconsider_when=list(reconsider_when),
                   candidate_ref=candidate_ref, evaluation_ref=evaluation_ref, resolution_ref=resolution_ref,
                   judgment="existing_resolution" if evaluation_ref or resolution_ref else "bounded_judgment", supersedes=[])
    validate_local_links(journal, content, source_id, external_workspace=external_workspace, max_history=max_history)
    return validate_content(content)


@bounded_read
def build_opportunity_reopen(journal, *, opportunity_ref, previous_terminal_ref, new_evidence_refs,
                             matched_condition, reviewer, reason, source_id, external_workspace=None,
                             max_history=MAX_HISTORY):
    """Review a specific changed condition with previously unseen original bytes.

    The host is responsible for relevance and the asserted match. A different
    hash is necessary, never sufficient evidence of scientific improvement.
    """
    state, opportunity, _, _ = _builder_state(journal, opportunity_ref, previous_terminal_ref, source_id, external_workspace, max_history)
    new_refs = _refs(new_evidence_refs)
    content = {k: opportunity[k] for k in ("task_id", "scope", "conditions")}
    content.update(schema=REOPEN, opportunity_ref=opportunity_ref, method_refs=_refs(opportunity["method_refs"]),
                   previous_terminal_ref=previous_terminal_ref, new_evidence_refs=new_refs,
                   matched_condition=matched_condition, reviewer=reviewer, reason=reason,
                   support_refs=state["retained_support_refs"], counter_refs=state["retained_counter_refs"],
                   original_evidence_refs=state["seen_evidence_refs"],
                   new_original_identities=_origins(journal, new_refs, source_id, external_workspace), supersedes=[])
    validate_local_links(journal, content, source_id, external_workspace=external_workspace, max_history=max_history)
    return validate_content(content)
