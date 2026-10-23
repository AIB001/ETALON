"""Server-side entry resolution and portable read receipts, not access tokens.

Receipts pin the published workspace definition and checkpoint. Source identities
and artifact relocation remain the responsibility of the existing evidence API.
No SSH, copying, discovery, execution or mutable session registry is involved.
"""
from __future__ import annotations

import json
from pathlib import Path

from etalon.active.schema import digest
from .evidence_refs import EvidenceError, load_workspace, validate_ref

READ_CONTEXT = "etalon-research-read-context/1"


def resolve_entry(entry):
    """Accept the existing entry, a one-hop locator, or a pinned read context."""
    origin, base = None, None
    for _ in range(2):
        if isinstance(entry, (str, Path)):
            try:
                # Preserve a caller's current-entry symlink as the refresh locator;
                # pin the resolved workspace separately below.
                origin = Path(entry).absolute()
                if origin.stat().st_size > 65536:
                    raise EvidenceError("research entry exceeds 64 KiB")
                entry = json.loads(origin.read_text(encoding="utf-8-sig"))
                base = origin.parent
            except OSError as exc:
                raise EvidenceError(f"entry_unavailable: {origin}; resolve the locator on the evidence server") from exc
        if not isinstance(entry, dict):
            raise EvidenceError("research entry requires workspace and checkpoint_ref")
        if entry.get("schema") == READ_CONTEXT:
            ws = workspace_from_context(entry)
            return ws, validate_ref(entry["checkpoint_ref"]), entry.get("entry_path"), entry
        if "workspace" in entry and "checkpoint_ref" in entry:
            workspace = entry["workspace"]
            if isinstance(workspace, (str, Path)):
                workspace = Path(workspace)
                if not workspace.is_absolute() and base is not None:
                    workspace = base / workspace
                workspace = workspace.resolve(strict=True)
            return workspace, validate_ref(entry["checkpoint_ref"]), str(origin) if origin else None, None
        if set(entry) <= {"entry", "transport"} and isinstance(entry.get("entry"), str):
            path = Path(entry["entry"])
            entry = base / path if base and not path.is_absolute() else path
            continue
        raise EvidenceError("research entry requires workspace and checkpoint_ref")
    raise EvidenceError("entry_locator_depth: only one locator hop is supported")


def make_read_context(workspace, checkpoint_ref, *, entry_path=None, policy=None, purpose="research"):
    # In-memory workspaces remain supported by the SDK, but cannot be silently
    # serialized into a remote authority. Their caller already owns the object.
    if not isinstance(workspace, (str, Path)):
        return None
    path = Path(workspace).resolve(strict=True)
    ws = load_workspace(path)
    value = {"schema": READ_CONTEXT, "workspace": str(path),
             "workspace_digest": digest(ws.manifest), "checkpoint_ref": validate_ref(checkpoint_ref),
             "policy": policy or {}, "purpose": purpose, "entry_path": entry_path}
    return {**value, "digest": digest(value)}


def workspace_from_context(context, *, apply_policy=True):
    if not isinstance(context, dict) or set(context) != {
        "schema", "workspace", "workspace_digest", "checkpoint_ref", "policy", "purpose", "entry_path", "digest"
    } or context.get("schema") != READ_CONTEXT:
        raise EvidenceError("invalid_read_context: use the receipt returned by research_context")
    if context["digest"] != digest({k: v for k, v in context.items() if k != "digest"}):
        raise EvidenceError("invalid_read_context: digest mismatch")
    validate_ref(context["checkpoint_ref"])
    if not isinstance(context["workspace"], str) or not Path(context["workspace"]).is_absolute():
        raise EvidenceError("invalid_read_context: workspace must be server-absolute")
    try:
        ws = load_workspace(context["workspace"])
    except OSError as exc:
        raise EvidenceError("read_context_unavailable: pinned workspace is missing; resolve the entry again") from exc
    if digest(ws.manifest) != context["workspace_digest"]:
        raise EvidenceError("stale_read_context: workspace changed; resolve the entry again")
    if not apply_policy:
        return ws
    from .graph_memory import restrict_workspace
    return restrict_workspace(ws, policy=context["policy"], purpose=context["purpose"])[0]


def resolve_read_workspace(workspace=None, *, entry=None, read_context=None):
    """Exactly one selector; old workspace calls retain their original behavior."""
    if sum(x is not None for x in (workspace, entry, read_context)) != 1:
        raise EvidenceError("supply exactly one of workspace, entry or read_context")
    if read_context is not None:
        return workspace_from_context(read_context)
    if entry is not None:
        return resolve_entry(entry)[0]
    return workspace


def continuation_status(read_context):
    """An updated entry is a signal to refresh, never a silent snapshot switch."""
    if read_context is None or not read_context.get("entry_path"):
        return "not_inspected"
    try:
        workspace, checkpoint, _, _ = resolve_entry(read_context["entry_path"])
        return "unchanged" if (checkpoint == read_context["checkpoint_ref"] and
            digest(load_workspace(workspace).manifest) == read_context["workspace_digest"]) else "advanced"
    except (OSError, ValueError):
        return "unavailable"
