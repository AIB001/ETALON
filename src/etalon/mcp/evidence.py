"""Read-only evidence access; validation and paging belong to the shared SDK."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from etalon.mcp._common import Cost, absolute_path, ok, tool


def _entry_argument(entry):
    if isinstance(entry, str):
        return absolute_path(entry, label="entry")
    if isinstance(entry, dict):
        for key in ("entry", "workspace"):
            if key in entry:
                absolute_path(entry[key], label=key)
    return entry


def _context_page(payload, max_bytes):
    from etalon.active.schema import canonical
    from etalon.research.evidence_refs import EvidenceError
    result = canonical(dict(payload, ok=True))
    if len(result.encode("utf-8")) > max_bytes:
        raise EvidenceError("context plus MCP envelope exceeds max_bytes; increase budget or narrow the view")
    return result


def register(mcp: Any) -> None:
    @mcp.tool()
    @tool(Cost.CHEAP)
    def etalon_research_handoff(entry: str, destination: str,
                               publication_intents: list[str] | None = None,
                               limits: dict | None = None,
                               skill_bundles: list[dict] | None = None) -> str:
        """CHEAP. Export a complete released entry into a NEW local directory.

        Reuses frozen sources, checkpoint, explicit location maps and original
        hashes. Copies known publication failures as inert history. Bounds fail
        closed; no network transport, active intent restoration or execution is
        performed. Existing destinations are refused; failed partial exports
        remain available for inspection. Returns a verified handoff receipt.
        skill_bundles optionally provides exact archived {slug, manifest_sha256,
        root} bundles when the local library has changed; versions never substitute.
        """
        from etalon.research.handoff import plan_research_handoff, export_research_handoff
        source = absolute_path(entry, label="entry")
        target = absolute_path(destination, label="destination")
        intents = [absolute_path(p, label="publication_intent") for p in publication_intents or []]
        return ok(**export_research_handoff(
            plan_research_handoff(source, publication_intents=intents, limits=limits,
                                 skill_bundles=skill_bundles or ()), target))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_research_handoff_verify(directory: str, verify_originals: bool = True) -> str:
        """FREE. Verify copied bytes, checkpoint and full released record coverage.

        By default also rehash originals. Set verify_originals=False only for
        an explicit exported-bundle-only check after physical relocation; that
        result does not establish remote-original or live-authority validity.
        Never starts science, resumes a publication or grants permission.
        """
        from etalon.research.handoff import verify_research_handoff
        return ok(**verify_research_handoff(absolute_path(directory, label="directory"),
                                           verify_originals=verify_originals))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_resource_usage_read(ref: dict, workspace: str | None = None,
                                  entry: str | dict | None = None,
                                  read_context: dict | None = None) -> str:
        """FREE. Verify workset resource/part -> use -> node -> evaluation -> choice.

        Supply exactly one workspace, entry or pinned read_context. Separate
        selected state, present-byte verification and historical host reports.
        Missing historical consumption stays unknown; joins do not prove gain.
        """
        from etalon.research.entry import resolve_read_workspace
        from etalon.research.resource_usage import read_resource_usage
        selected = resolve_read_workspace(
            absolute_path(workspace, label="workspace") if workspace is not None else None,
            entry=_entry_argument(entry), read_context=read_context)
        return ok(**read_resource_usage(selected, ref))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_research_context(entry: str | dict, node_id: str | None = None,
                                question: Annotated[str | None, Field(max_length=4096)] = None,
                                purpose: str = "research", policy: dict | None = None,
                                max_bytes: Annotated[int, Field(ge=4096, le=65536)] = 16384,
                                conditions: dict | None = None,
                                view: Literal["summary", "node", "full", "resources", "branch"] = "full",
                                branch_id: str | None = None,
                                cursor: str | None = None,
                                limit: Annotated[int, Field(ge=1, le=50)] = 20,
                                view_options: dict | None = None,
                                scope: dict | None = None) -> str:
        """FREE. Build bounded decision context from a published research entry.

        Includes source selection, counterevidence coverage and existing branch/pause
        state. Policy can narrow the published scope; it is not an authorization grant.
        Does not write memory, submit science, adopt a method or trigger RSI.
        summary is a compact continuation card; node requires node_id and follows
        explicit dependencies; full preserves the original bounded graph selection.
        max_bytes is 4096..65536. Expand returned refs with etalon_evidence_read; question
        is descriptive and does not perform semantic ranking.
        entry may be a server path, a one-hop {entry: server_path} locator, or the
        returned read_context for a pinned view. Follow-up evidence calls accept
        read_context directly; never guess a workspace or open server files locally.
        resources inventories cross-type originals without activation; view_options
        allows source_ids, kinds, skill_refs, scan_limit. branch requires branch_id;
        options allows usage_refs, max_nodes, as_of. Both views paginate with cursor
        and limit and retain coverage gaps. They never infer skill consumption.
        summary/node/full may take an explicit scope with root_refs or branch_id,
        and ordinary_hops 0 or 1. Its coverage receipt concerns that neighborhood,
        not the whole archive; counter relations are checked separately from excerpts.
        """
        from etalon.research.evidence_refs import EvidenceError
        options = {} if view_options is None else view_options
        if not isinstance(options, dict):
            raise EvidenceError("view_options must be an object")
        if view in {"resources", "branch"}:
            if scope is not None:
                raise EvidenceError("graph scope belongs to summary, node or full context views")
            if node_id is not None or question is not None:
                raise EvidenceError("node_id/question belong to the existing research context views")
            if view == "resources":
                if branch_id is not None or conditions is not None or policy is not None:
                    raise EvidenceError("resources uses entry/read_context scope; branch/conditions/policy are not catalog selection")
                if set(options) - {"source_ids", "kinds", "skill_refs", "scan_limit"}:
                    raise EvidenceError("unknown resources view option")
                from etalon.research.resource_catalog import build_resource_catalog
                # Default research inherits a pinned context's purpose; an explicit
                # nondefault purpose is checked by the SDK against that context.
                return _context_page(build_resource_catalog(entry=_entry_argument(entry),
                    purpose=None if purpose == "research" else purpose,
                    limit=limit, cursor=cursor, max_bytes=max_bytes, **options), max_bytes)
            if set(options) - {"usage_refs", "max_nodes", "as_of"}:
                raise EvidenceError("unknown branch view option")
            from etalon.research.branch_context import build_branch_context
            return _context_page(build_branch_context(_entry_argument(entry), branch_id=branch_id,
                conditions=conditions, purpose=purpose, policy=policy, max_bytes=max_bytes,
                limit=limit, cursor=cursor, **options), max_bytes)
        if branch_id is not None or cursor is not None or options or limit != 20:
            raise EvidenceError("branch_id/cursor/limit/view_options require resources or branch view")
        from etalon.research.research_context import build_research_context
        return ok(**build_research_context(_entry_argument(entry),
            node_id=node_id, question=question, purpose=purpose, policy=policy,
            max_bytes=max_bytes, conditions=conditions, view=view, scope=scope))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_research_graph(workspace: str, checkpoint_ref: dict,
                              purpose: str = "research", policy: dict | None = None,
                              max_nodes: Annotated[int, Field(ge=1, le=128)] = 64,
                              max_bytes: Annotated[int, Field(ge=4096, le=262144)] = 65536,
                              scope: dict | None = None) -> str:
        """FREE. Project Search/Judge/Quick/Deep from existing research records.

        Nodes keep evidence identity and cross-workspace references; failed and skipped
        branches remain visible. A historical projection is not a new execution trace.
        Bounds: 1..128 nodes, 4096..262144 serialized bytes.
        Optional scope selects exact root_refs or a checkpoint branch_id, with
        ordinary_hops 0 or 1. Scoped coverage never certifies the whole archive.
        """
        from etalon.research.graph_memory import build_research_graph
        return ok(**build_research_graph(absolute_path(workspace, label="workspace"),
            checkpoint_ref=checkpoint_ref, purpose=purpose, policy=policy,
            max_nodes=max_nodes, max_bytes=max_bytes, scope=scope))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_evidence_query(workspace: str | None = None, query: Annotated[str, Field(max_length=4096)] = "",
                              filters: dict | None = None,
                              limit: Annotated[int, Field(ge=1, le=50)] = 10,
                              cursor: str | None = None, entry: str | dict | None = None,
                              read_context: dict | None = None) -> str:
        """FREE. Query an explicit released-evidence manifest; no database is created.

        Reading known refs directly is also allowed. Coverage and warnings distinguish
        missing sources from no matches. This does not dispatch or select a method.
        limit is 1..50 records per page; use next_cursor when present.
        Supply exactly one of workspace, entry or the returned read_context.
        read_context preserves the selected scope and checkpoint across remote reads.
        """
        from etalon.research.evidence import query_evidence

        return ok(**query_evidence(absolute_path(workspace, label="workspace") if workspace is not None else None,
                                  query=query, filters=filters, limit=limit, cursor=cursor,
                                  entry=_entry_argument(entry), read_context=read_context))

    @mcp.tool()
    @tool(Cost.FREE)
    def etalon_evidence_read(workspace: str | None = None, ref: dict | None = None,
                             view: Literal["record", "summary", "lineage", "artifact"] = "record",
                             member: str | None = None, selector: str | None = None,
                             offset: Annotated[int, Field(ge=0, le=2**63 - 1)] = 0,
                             limit: Annotated[int, Field(ge=1, le=32768)] = 100,
                             entry: str | dict | None = None, read_context: dict | None = None) -> str:
        """FREE. Read a known evidence ref with bounded record/summary/lineage/artifact views.

        Metadata access is not original-byte verification. Original identities, roles,
        and costs are retained; no model, network or scientific execution is started.
        limit is 1..32768; artifact offset/limit are byte counts. Page with the
        returned fragment.next_offset rather than requesting a larger limit.
        Supply ref and exactly one of workspace, entry or the returned read_context.
        Prefer read_context after etalon_research_context, including on a remote server.
        """
        from etalon.research.evidence import read_evidence

        return ok(**read_evidence(absolute_path(workspace, label="workspace") if workspace is not None else None, ref,
                                 view=view, member=member, selector=selector,
                                 offset=offset, limit=limit, entry=_entry_argument(entry), read_context=read_context))
